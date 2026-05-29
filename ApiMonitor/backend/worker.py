import httpx
import asyncio
import json
import os
import logging
from datetime import datetime, timedelta
from sqlalchemy.future import select
from sqlalchemy import delete
import models
from database import SessionLocal

logger = logging.getLogger(__name__)

from arq.connections import RedisSettings

# ── Pool global de conexões HTTP ─────────────────────────────────────────────
http_client = httpx.AsyncClient(
    timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
    follow_redirects=True,
    limits=httpx.Limits(max_keepalive_connections=50, max_connections=100)
)

# ── Referência ao WS manager (injetada pelo main.py via scheduler.py) ────────
ws_manager = None

def set_ws_manager(manager):
    global ws_manager
    ws_manager = manager


# ── Discord com retry + persistência de auditoria ────────────────────────────
async def send_discord_webhook(
    endpoint_id: int,
    endpoint_nome: str,
    status: str,
    url: str,
    tentativa: int = 0,
):
    """
    Envia alerta no Discord com até 3 tentativas (backoff exponencial).
    Persiste o resultado (sucesso ou falha) na tabela alert_logs para auditoria.
    """
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        return

    color = 0xFF0000 if status == "down" else 0x00FF00
    icon = "🔴" if status == "down" else "🟢"
    msg = f"{icon} **{endpoint_nome}** {'caiu!' if status == 'down' else 'voltou a ficar online!'}"

    payload = {
        "embeds": [{
            "title": f"Status Update: {endpoint_nome}",
            "description": msg,
            "url": url,
            "color": color,
            "timestamp": datetime.utcnow().isoformat()
        }]
    }

    sucesso = False
    erro_final = None

    try:
        resp = await http_client.post(webhook_url, json=payload)

        if resp.status_code == 429 and tentativa < 3:
            # Rate limit do Discord: aguarda e tenta de novo (sem persistir ainda)
            retry_after = float(resp.json().get("retry_after", 2))
            logger.warning(f"Discord rate limit, retry em {retry_after}s (tentativa {tentativa + 1})")
            await asyncio.sleep(retry_after)
            await send_discord_webhook(endpoint_id, endpoint_nome, status, url, tentativa + 1)
            return  # a chamada recursiva vai persistir o resultado final

        elif resp.status_code >= 400:
            erro_final = f"HTTP {resp.status_code}: {resp.text[:300]}"
            logger.error(f"Discord webhook falhou: {erro_final}")

        else:
            sucesso = True
            logger.info(f"Discord webhook enviado com sucesso para '{endpoint_nome}' (status={status})")

    except Exception as e:
        if tentativa < 3:
            wait = 2 ** tentativa  # backoff: 1s, 2s, 4s
            logger.warning(f"Erro no Discord webhook, retry em {wait}s (tentativa {tentativa + 1}): {e}")
            await asyncio.sleep(wait)
            await send_discord_webhook(endpoint_id, endpoint_nome, status, url, tentativa + 1)
            return  # a chamada recursiva vai persistir

        erro_final = str(e)[:500]
        logger.error(f"Discord webhook falhou após {tentativa + 1} tentativas para '{endpoint_nome}': {erro_final}")

    # ── Persiste resultado no AlertLog (sessão própria, separada do ping) ──
    try:
        async with SessionLocal() as db:
            log = models.AlertLog(
                endpoint_id=endpoint_id,
                canal="discord",
                status_alerta=status,
                sucesso=sucesso,
                tentativas=tentativa + 1,
                erro_msg=erro_final,
            )
            db.add(log)
            await db.commit()
    except Exception as db_err:
        # Não deixa falha de log quebrar o fluxo do worker
        logger.error(f"Falha ao persistir AlertLog para endpoint {endpoint_id}: {db_err}")


# ── Tarefa de ping (executada pelo ARQ worker) ────────────────────────────────
async def task_ping_endpoint(ctx, endpoint_id: int):
    """Função principal do worker ARQ: faz o ping e salva o resultado."""
    async with SessionLocal() as db:
        res = await db.execute(select(models.Endpoint).filter(models.Endpoint.id == endpoint_id))
        endpoint = res.scalars().first()
        if not endpoint or not endpoint.ativo:
            return

        status = "down"
        latencia_ms = None
        http_status_code = None
        erro_msg = None

        try:
            start = datetime.utcnow()
            response = await http_client.get(endpoint.url)
            end = datetime.utcnow()
            latencia_ms = (end - start).total_seconds() * 1000
            http_status_code = response.status_code
            status = "degraded" if latencia_ms > 2000 else "up" if response.status_code < 400 else "down"
        except Exception as e:
            erro_msg = str(e)[:500]
            status = "down"

        # Verifica mudança de status para disparar alerta
        last_res = await db.execute(
            select(models.CheckResult)
            .filter(models.CheckResult.endpoint_id == endpoint_id)
            .order_by(models.CheckResult.checado_em.desc())
            .limit(1)
        )
        last_check = last_res.scalars().first()
        last_status = last_check.status if last_check else None

        if last_status and status != last_status:
            if status == "down" or (last_status == "down" and status in ["up", "degraded"]):
                asyncio.create_task(
                    send_discord_webhook(endpoint.id, endpoint.nome, status, endpoint.url)
                )

        check = models.CheckResult(
            endpoint_id=endpoint_id,
            status=status,
            latencia_ms=latencia_ms,
            http_status_code=http_status_code,
            erro_msg=erro_msg,
            checado_em=datetime.utcnow(),
        )
        db.add(check)
        await db.commit()
        await db.refresh(check)

        if ws_manager:
            data = json.dumps({
                "type": "check_result",
                "endpoint_id": endpoint_id,
                "status": status,
                "latencia_ms": latencia_ms,
                "http_status_code": http_status_code,
                "checado_em": check.checado_em.isoformat(),
            })
            await ws_manager.broadcast(data)


async def task_limpeza_db(ctx):
    """Remove registros antigos (> 30 dias) de check_results e alert_logs."""
    async with SessionLocal() as db:
        limite = datetime.utcnow() - timedelta(days=30)

        result_checks = await db.execute(
            delete(models.CheckResult).where(models.CheckResult.checado_em < limite)
        )
        result_alerts = await db.execute(
            delete(models.AlertLog).where(models.AlertLog.criado_em < limite)
        )
        await db.commit()
        logger.info(
            f"Limpeza: {result_checks.rowcount} checks e "
            f"{result_alerts.rowcount} alert_logs removidos (> 30 dias)."
        )


# ── Configuração do ARQ Worker ────────────────────────────────────────────────
class WorkerSettings:
    """
    Configurações do worker ARQ.
    Rode separado com: python -m arq worker.WorkerSettings
    """
    functions = [task_ping_endpoint, task_limpeza_db]
    redis_settings = RedisSettings(host="localhost", port=6379)

    on_startup = None
    on_shutdown = None

    job_timeout = 30
    max_jobs = 50
