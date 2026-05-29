from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, case, and_
from typing import List, Optional
from datetime import datetime, timedelta
import asyncio
import json

import models
import schemas
import scheduler as sched
import auth
from database import engine, get_db

app = FastAPI(title="ApiMonitor")


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        dead = []
        for conn in self.active_connections:
            try:
                await conn.send_text(message)
            except Exception:
                dead.append(conn)
        for d in dead:
            self.disconnect(d)


manager = ConnectionManager()


@app.on_event("startup")
async def startup_event():
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)

    # ① Inicializa ARQ pool ANTES de qualquer coisa que dependa do Redis
    await sched.init_arq_pool()

    # ② Injeta WS manager no worker
    sched.set_ws_manager(manager)

    # ③ Tenta adquirir lock distribuída e iniciar o scheduler (só 1 worker vira líder)
    await sched.start_scheduler()

    async for db in get_db():
        admin_res = await db.execute(select(models.Usuario).filter(models.Usuario.username == "admin"))
        admin = admin_res.scalars().first()
        if not admin:
            db_admin = models.Usuario(username="admin", hashed_password=auth.get_password_hash("admin"))
            db.add(db_admin)
            await db.commit()

        endpoints_res = await db.execute(select(models.Endpoint).filter(models.Endpoint.ativo == True))
        endpoints = endpoints_res.scalars().all()
        for ep in endpoints:
            # add_endpoint_job só faz algo se esta instância for a líder do scheduler
            sched.add_endpoint_job(ep.id, ep.intervalo_minutos)
        break


@app.on_event("shutdown")
async def shutdown_event():
    sched.stop_scheduler()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, db: AsyncSession = Depends(get_db)):
    await websocket.accept()
    try:
        data = await websocket.receive_text()
        msg = json.loads(data)
        if msg.get("type") != "auth" or not msg.get("token"):
            await websocket.close(code=1008)
            return
        user = await auth.get_current_user(msg.get("token"), db)
    except Exception:
        await websocket.close(code=1008)
        return

    manager.active_connections.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ── Helpers de métricas ───────────────────────────────────────────────────────

async def compute_uptime(endpoint_id: int, hours: int, db: AsyncSession) -> dict:
    """
    Calcula uptime real via agregação no PostgreSQL para uma janela de tempo.
    Retorna dict com total_checks, up_checks, uptime_percent, avg_latencia_ms.
    """
    since = datetime.utcnow() - timedelta(hours=hours)

    result = await db.execute(
        select(
            func.count(models.CheckResult.id).label("total"),
            func.sum(
                case(
                    (models.CheckResult.status.in_(["up", "degraded"]), 1),
                    else_=0
                )
            ).label("up_count"),
            func.avg(models.CheckResult.latencia_ms).label("avg_latencia"),
        )
        .where(
            and_(
                models.CheckResult.endpoint_id == endpoint_id,
                models.CheckResult.checado_em >= since,
            )
        )
    )
    row = result.one()
    total = row.total or 0
    up_count = int(row.up_count or 0)
    uptime_pct = round((up_count / total) * 100, 1) if total > 0 else None
    avg_lat = round(float(row.avg_latencia), 1) if row.avg_latencia is not None else None

    return {
        "total_checks": total,
        "up_checks": up_count,
        "uptime_percent": uptime_pct,
        "avg_latencia_ms": avg_lat,
    }


async def build_endpoint_dict(ep, db: AsyncSession):
    last_check_res = await db.execute(
        select(models.CheckResult)
        .filter(models.CheckResult.endpoint_id == ep.id)
        .order_by(models.CheckResult.checado_em.desc())
        .limit(1)
    )
    last_check = last_check_res.scalars().first()

    # Uptime real das últimas 24h calculado pelo Postgres
    uptime_data = await compute_uptime(ep.id, 24, db)

    return {
        "id": ep.id,
        "nome": ep.nome,
        "url": ep.url,
        "intervalo_minutos": ep.intervalo_minutos,
        "ativo": ep.ativo,
        "criado_em": ep.criado_em.isoformat(),
        "ultimo_status": last_check.status if last_check else None,
        "ultima_latencia": last_check.latencia_ms if last_check else None,
        "uptime_percent": uptime_data["uptime_percent"],   # 24h real
    }


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.post("/api/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(models.Usuario).filter(models.Usuario.username == form_data.username))
    user = res.scalars().first()
    if not user or not auth.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Usuário ou senha incorretos")

    access_token_expires = timedelta(minutes=auth.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth.create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


# ── Gestão de Usuários ────────────────────────────────────────────────────────

@app.get("/api/users", response_model=List[schemas.UserResponse])
async def get_users(
    db: AsyncSession = Depends(get_db),
    current_user: models.Usuario = Depends(auth.get_current_user),
):
    res = await db.execute(select(models.Usuario).order_by(models.Usuario.id))
    return res.scalars().all()


@app.post("/api/users", response_model=schemas.UserResponse, status_code=201)
async def create_user(
    user_in: schemas.UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: models.Usuario = Depends(auth.get_current_user),
):
    res = await db.execute(select(models.Usuario).filter(models.Usuario.username == user_in.username))
    if res.scalars().first():
        raise HTTPException(status_code=400, detail="Usuário já existe")
    
    new_user = models.Usuario(
        username=user_in.username,
        hashed_password=auth.get_password_hash(user_in.password)
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user


@app.delete("/api/users/{user_id}")
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.Usuario = Depends(auth.get_current_user),
):
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="Você não pode excluir a si mesmo")
    
    count_res = await db.execute(select(func.count(models.Usuario.id)))
    total_users = count_res.scalar_one()
    if total_users <= 1:
        raise HTTPException(status_code=400, detail="Não é possível excluir o único usuário do sistema")

    res = await db.execute(select(models.Usuario).filter(models.Usuario.id == user_id))
    db_user = res.scalars().first()
    if not db_user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    
    await db.delete(db_user)
    await db.commit()
    return {"ok": True}
# ── Endpoints CRUD ────────────────────────────────────────────────────────────

@app.get("/api/endpoints")
async def get_endpoints(
    db: AsyncSession = Depends(get_db),
    current_user: models.Usuario = Depends(auth.get_current_user),
):
    res = await db.execute(select(models.Endpoint).order_by(models.Endpoint.criado_em.desc()))
    endpoints = res.scalars().all()
    results = []
    for ep in endpoints:
        results.append(await build_endpoint_dict(ep, db))
    return results


@app.post("/api/endpoints", status_code=201)
async def create_endpoint(
    endpoint: schemas.EndpointCreate,
    db: AsyncSession = Depends(get_db),
    current_user: models.Usuario = Depends(auth.get_current_user),
):
    db_ep = models.Endpoint(
        nome=endpoint.nome,
        url=endpoint.url,
        intervalo_minutos=endpoint.intervalo_minutos,
    )
    db.add(db_ep)
    await db.commit()
    await db.refresh(db_ep)
    sched.add_endpoint_job(db_ep.id, db_ep.intervalo_minutos)
    # Ping imediato enfileirado no Redis
    await sched.enqueue_ping(db_ep.id)
    return await build_endpoint_dict(db_ep, db)


@app.put("/api/endpoints/{endpoint_id}")
async def update_endpoint(
    endpoint_id: int,
    endpoint: schemas.EndpointUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: models.Usuario = Depends(auth.get_current_user),
):
    res = await db.execute(select(models.Endpoint).filter(models.Endpoint.id == endpoint_id))
    db_ep = res.scalars().first()
    if not db_ep:
        raise HTTPException(status_code=404, detail="Endpoint não encontrado")
    if endpoint.nome is not None:
        db_ep.nome = endpoint.nome
    if endpoint.url is not None:
        db_ep.url = endpoint.url
    if endpoint.intervalo_minutos is not None:
        db_ep.intervalo_minutos = endpoint.intervalo_minutos
    if endpoint.ativo is not None:
        db_ep.ativo = endpoint.ativo
    await db.commit()
    await db.refresh(db_ep)
    if db_ep.ativo:
        sched.add_endpoint_job(db_ep.id, db_ep.intervalo_minutos)
    else:
        sched.remove_endpoint_job(db_ep.id)
    return await build_endpoint_dict(db_ep, db)


@app.delete("/api/endpoints/{endpoint_id}")
async def delete_endpoint(
    endpoint_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.Usuario = Depends(auth.get_current_user),
):
    res = await db.execute(select(models.Endpoint).filter(models.Endpoint.id == endpoint_id))
    db_ep = res.scalars().first()
    if not db_ep:
        raise HTTPException(status_code=404, detail="Endpoint não encontrado")
    sched.remove_endpoint_job(endpoint_id)
    await db.delete(db_ep)
    await db.commit()
    return {"ok": True}


# ── Histórico paginado com filtro por data ────────────────────────────────────

@app.get("/api/endpoints/{endpoint_id}/history")
async def get_history(
    endpoint_id: int,
    response: Response,
    start: Optional[datetime] = Query(default=None, description="Início do período (ISO 8601)"),
    end: Optional[datetime] = Query(default=None, description="Fim do período (ISO 8601)"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: models.Usuario = Depends(auth.get_current_user),
):
    """
    Retorna o histórico de checks com paginação e filtro por intervalo de data.
    Header X-Total-Count indica o total de registros na janela (sem aplicar limit/offset).
    """
    filters = [models.CheckResult.endpoint_id == endpoint_id]
    if start:
        filters.append(models.CheckResult.checado_em >= start)
    if end:
        filters.append(models.CheckResult.checado_em <= end)

    # Total na janela para o frontend calcular páginas
    count_res = await db.execute(
        select(func.count(models.CheckResult.id)).where(and_(*filters))
    )
    total = count_res.scalar_one()

    # Dados paginados
    data_res = await db.execute(
        select(models.CheckResult)
        .where(and_(*filters))
        .order_by(models.CheckResult.checado_em.desc())
        .limit(limit)
        .offset(offset)
    )
    checks = data_res.scalars().all()

    response.headers["X-Total-Count"] = str(total)

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": c.id,
                "status": c.status,
                "latencia_ms": c.latencia_ms,
                "http_status_code": c.http_status_code,
                "erro_msg": c.erro_msg,
                "checado_em": c.checado_em.isoformat(),
            }
            for c in checks
        ],
    }


# ── Stats por janela de tempo ─────────────────────────────────────────────────

_WINDOW_HOURS = {"24h": 24, "7d": 168, "30d": 720}


@app.get("/api/endpoints/{endpoint_id}/stats")
async def get_stats(
    endpoint_id: int,
    window: str = Query(default="24h", pattern="^(24h|7d|30d)$"),
    db: AsyncSession = Depends(get_db),
    current_user: models.Usuario = Depends(auth.get_current_user),
):
    """
    Retorna estatísticas de uptime calculadas pelo PostgreSQL para a janela solicitada.
    window: "24h" | "7d" | "30d"
    """
    res = await db.execute(select(models.Endpoint).filter(models.Endpoint.id == endpoint_id))
    ep = res.scalars().first()
    if not ep:
        raise HTTPException(status_code=404, detail="Endpoint não encontrado")

    hours = _WINDOW_HOURS[window]
    since = datetime.utcnow() - timedelta(hours=hours)

    result = await db.execute(
        select(
            func.count(models.CheckResult.id).label("total"),
            func.sum(
                case(
                    (models.CheckResult.status.in_(["up", "degraded"]), 1),
                    else_=0
                )
            ).label("up_count"),
            func.avg(models.CheckResult.latencia_ms).label("avg_latencia"),
            func.percentile_cont(0.95).within_group(
                models.CheckResult.latencia_ms
            ).label("p95_latencia"),
        )
        .where(
            and_(
                models.CheckResult.endpoint_id == endpoint_id,
                models.CheckResult.checado_em >= since,
                models.CheckResult.latencia_ms.isnot(None),  # exclui timeouts do p95
            )
        )
    )
    row = result.one()
    total = row.total or 0
    up_count = int(row.up_count or 0)

    return {
        "endpoint_id": endpoint_id,
        "window": window,
        "total_checks": total,
        "up_checks": up_count,
        "uptime_percent": round((up_count / total) * 100, 2) if total > 0 else None,
        "avg_latencia_ms": round(float(row.avg_latencia), 1) if row.avg_latencia is not None else None,
        "p95_latencia_ms": round(float(row.p95_latencia), 1) if row.p95_latencia is not None else None,
    }


# ── Alert Logs ────────────────────────────────────────────────────────────────

@app.get("/api/endpoints/{endpoint_id}/alerts")
async def get_alert_logs(
    endpoint_id: int,
    response: Response,
    sucesso: Optional[bool] = Query(default=None, description="Filtrar por sucesso/falha"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: models.Usuario = Depends(auth.get_current_user),
):
    """Retorna o log de tentativas de alerta (Discord, etc.) para um endpoint."""
    filters = [models.AlertLog.endpoint_id == endpoint_id]
    if sucesso is not None:
        filters.append(models.AlertLog.sucesso == sucesso)

    count_res = await db.execute(
        select(func.count(models.AlertLog.id)).where(and_(*filters))
    )
    total = count_res.scalar_one()

    data_res = await db.execute(
        select(models.AlertLog)
        .where(and_(*filters))
        .order_by(models.AlertLog.criado_em.desc())
        .limit(limit)
        .offset(offset)
    )
    logs = data_res.scalars().all()

    response.headers["X-Total-Count"] = str(total)

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": lg.id,
                "canal": lg.canal,
                "status_alerta": lg.status_alerta,
                "sucesso": lg.sucesso,
                "tentativas": lg.tentativas,
                "erro_msg": lg.erro_msg,
                "criado_em": lg.criado_em.isoformat(),
            }
            for lg in logs
        ],
    }


@app.post("/api/endpoints/{endpoint_id}/check-now")
async def check_now(
    endpoint_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.Usuario = Depends(auth.get_current_user),
):
    res = await db.execute(select(models.Endpoint).filter(models.Endpoint.id == endpoint_id))
    db_ep = res.scalars().first()
    if not db_ep:
        raise HTTPException(status_code=404, detail="Endpoint não encontrado")
    await sched.enqueue_ping(db_ep.id)
    return {"ok": True}


# ── Static ────────────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def serve_index():
    return FileResponse("static/index.html")
