"""
scheduler.py — Agendador de enfileiramento de pings.

Ele NÃO executa os pings diretamente. Apenas enfileira jobs no Redis
para que o ARQ worker (worker.py) execute de forma isolada.

Redis Distributed Lock:
  Em ambientes multi-worker (ex: 3 instâncias Uvicorn), apenas 1 instância
  consegue a lock e roda o APScheduler. As demais ficam em standby.
  Se a instância líder cair, o TTL da lock expira em 30s e outra assume.
"""
import asyncio
import logging
from arq import create_pool
from arq.connections import RedisSettings
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

REDIS_SETTINGS = RedisSettings(host="localhost", port=6379)

# Chave da lock distribuída no Redis
SCHEDULER_LOCK_KEY = "apimonitor:scheduler_lock"
SCHEDULER_LOCK_TTL = 30       # segundos — TTL da lock
SCHEDULER_HEARTBEAT = 20      # segundos — intervalo de renovação da lock

scheduler = AsyncIOScheduler(timezone="UTC")
ws_manager = None
arq_pool = None
_is_scheduler_leader = False   # flag local: esta instância ganhou a lock?
_heartbeat_task = None         # asyncio.Task do heartbeat


def set_ws_manager(manager, loop=None):
    global ws_manager
    # Injeta o manager no worker também (para broadcast WS)
    import worker
    worker.ws_manager = manager
    ws_manager = manager


async def init_arq_pool():
    """Inicializa o pool de conexão com o Redis."""
    global arq_pool
    arq_pool = await create_pool(REDIS_SETTINGS)
    logger.info("ARQ pool conectado ao Redis.")


async def enqueue_ping(endpoint_id: int):
    """Enfileira um ping no Redis para o worker processar."""
    if arq_pool:
        await arq_pool.enqueue_job("task_ping_endpoint", endpoint_id)
    else:
        logger.warning(f"ARQ pool não inicializado, ping ignorado para endpoint {endpoint_id}")


async def enqueue_limpeza():
    """Enfileira a limpeza do banco."""
    if arq_pool:
        await arq_pool.enqueue_job("task_limpeza_db")


# ── Distributed Lock ──────────────────────────────────────────────────────────

async def _acquire_scheduler_lock() -> bool:
    """
    Tenta adquirir a lock do scheduler no Redis via SET NX EX.
    Retorna True se esta instância é agora a líder.
    """
    if not arq_pool:
        logger.error("ARQ pool não inicializado — não é possível adquirir lock do scheduler.")
        return False

    # ArqRedis é subclasse de redis.asyncio.Redis: comandos diretos no pool
    acquired = await arq_pool.set(
        SCHEDULER_LOCK_KEY,
        "1",
        nx=True,          # SET only if Not eXists
        ex=SCHEDULER_LOCK_TTL,
    )
    return acquired is not None


async def _renew_scheduler_lock() -> bool:
    """Renova o TTL da lock. Retorna False se a lock sumiu (outro processo assumiu)."""
    if not arq_pool:
        return False
    # Direto no pool (ArqRedis é subclasse de redis.asyncio.Redis)
    result = await arq_pool.expire(SCHEDULER_LOCK_KEY, SCHEDULER_LOCK_TTL)
    return bool(result)


async def _scheduler_heartbeat_loop():
    """
    Loop infinito que renova a lock a cada SCHEDULER_HEARTBEAT segundos.
    Se perder a lock, para o scheduler para evitar duplicação.
    """
    global _is_scheduler_leader
    while True:
        await asyncio.sleep(SCHEDULER_HEARTBEAT)
        renewed = await _renew_scheduler_lock()
        if not renewed:
            logger.warning(
                "Scheduler lock perdida — outro worker assumiu ou Redis reiniciou. "
                "Parando scheduler nesta instância."
            )
            _is_scheduler_leader = False
            if scheduler.running:
                scheduler.shutdown(wait=False)
            return
        logger.debug("Scheduler lock renovada com sucesso.")


# ── Gerenciamento de Jobs ──────────────────────────────────────────────────────

def add_endpoint_job(endpoint_id: int, intervalo_minutos: int):
    """Registra um job recorrente no APScheduler para enfileirar pings."""
    if not _is_scheduler_leader:
        # Esta instância não é líder — não registra jobs no scheduler local
        return
    job_id = f"endpoint_{endpoint_id}"
    scheduler.add_job(
        enqueue_ping,
        trigger=IntervalTrigger(minutes=intervalo_minutos),
        id=job_id,
        args=[endpoint_id],
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=60,
    )


def remove_endpoint_job(endpoint_id: int):
    if not _is_scheduler_leader:
        return
    job_id = f"endpoint_{endpoint_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


async def start_scheduler():
    """
    Tenta adquirir a lock distribuída e, se for líder, inicia o APScheduler.
    Caso outra instância já tenha a lock, loga e retorna sem iniciar.
    """
    global _is_scheduler_leader, _heartbeat_task

    is_leader = await _acquire_scheduler_lock()
    if not is_leader:
        logger.info(
            "Scheduler lock já detida por outra instância — "
            "este worker ficará em standby para o scheduler."
        )
        return

    _is_scheduler_leader = True
    logger.info("Scheduler lock adquirida — esta instância é a líder do scheduler.")

    if not scheduler.running:
        scheduler.start()
        scheduler.add_job(
            enqueue_limpeza,
            trigger=CronTrigger(hour=3, minute=0),
            id="auto_limpeza",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=3600,
        )
        logger.info("APScheduler iniciado.")

    # Inicia heartbeat para manter a lock viva
    _heartbeat_task = asyncio.create_task(_scheduler_heartbeat_loop())


def stop_scheduler():
    global _is_scheduler_leader, _heartbeat_task
    if _heartbeat_task and not _heartbeat_task.done():
        _heartbeat_task.cancel()
    if scheduler.running:
        scheduler.shutdown(wait=False)
    _is_scheduler_leader = False
