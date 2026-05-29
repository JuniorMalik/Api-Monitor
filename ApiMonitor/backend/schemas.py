from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class EndpointCreate(BaseModel):
    nome: str
    url: str
    intervalo_minutos: int = 5


class EndpointUpdate(BaseModel):
    nome: Optional[str] = None
    url: Optional[str] = None
    intervalo_minutos: Optional[int] = None
    ativo: Optional[bool] = None


class EndpointSchema(BaseModel):
    id: int
    nome: str
    url: str
    intervalo_minutos: int
    ativo: bool
    criado_em: datetime
    ultimo_status: Optional[str] = None
    ultima_latencia: Optional[float] = None
    uptime_percent: Optional[float] = None  # uptime das últimas 24h

    model_config = {"from_attributes": True}


class UptimeWindow(BaseModel):
    """Uptime calculado por janela de tempo via agregação no PostgreSQL."""
    window: str                         # "24h" | "7d" | "30d"
    total_checks: int
    up_checks: int
    uptime_percent: Optional[float]     # None se não há dados
    avg_latencia_ms: Optional[float]
    p95_latencia_ms: Optional[float]


class EndpointStats(BaseModel):
    endpoint_id: int
    windows: list[UptimeWindow]


class CheckResultSchema(BaseModel):
    id: int
    status: str
    latencia_ms: Optional[float]
    http_status_code: Optional[int]
    erro_msg: Optional[str]
    checado_em: datetime

    model_config = {"from_attributes": True}


class PaginatedHistory(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CheckResultSchema]


class AlertLogSchema(BaseModel):
    id: int
    endpoint_id: int
    canal: str
    status_alerta: str
    sucesso: bool
    tentativas: int
    erro_msg: Optional[str]
    criado_em: datetime

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str

    model_config = {"from_attributes": True}
