from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)


class Endpoint(Base):
    __tablename__ = "endpoints"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, nullable=False)
    url = Column(String, nullable=False)
    intervalo_minutos = Column(Integer, default=5)
    ativo = Column(Boolean, default=True)
    criado_em = Column(DateTime, default=datetime.utcnow)

    checks = relationship("CheckResult", back_populates="endpoint", cascade="all, delete-orphan")
    alert_logs = relationship("AlertLog", back_populates="endpoint", cascade="all, delete-orphan")


class CheckResult(Base):
    __tablename__ = "check_results"

    id = Column(Integer, primary_key=True, index=True)
    endpoint_id = Column(Integer, ForeignKey("endpoints.id"), nullable=False)
    status = Column(String, nullable=False)  # "up", "down", "degraded"
    latencia_ms = Column(Float, nullable=True)
    http_status_code = Column(Integer, nullable=True)
    erro_msg = Column(String, nullable=True)
    checado_em = Column(DateTime, default=datetime.utcnow)

    endpoint = relationship("Endpoint", back_populates="checks")

    __table_args__ = (
        Index('ix_check_results_endpoint_id_checado_em', 'endpoint_id', 'checado_em'),
    )


class AlertLog(Base):
    """Auditoria de todas as tentativas de alerta (Discord, etc.)."""
    __tablename__ = "alert_logs"

    id = Column(Integer, primary_key=True, index=True)
    endpoint_id = Column(Integer, ForeignKey("endpoints.id"), nullable=False)
    canal = Column(String, nullable=False, default="discord")   # canal de notificação
    status_alerta = Column(String, nullable=False)              # "sent" | "down" | "up"
    sucesso = Column(Boolean, nullable=False)                   # True = webhook OK
    tentativas = Column(Integer, nullable=False, default=1)
    erro_msg = Column(String, nullable=True)                    # detalhes do erro se falhou
    criado_em = Column(DateTime, default=datetime.utcnow)

    endpoint = relationship("Endpoint", back_populates="alert_logs")

    __table_args__ = (
        Index('ix_alert_logs_endpoint_id_criado_em', 'endpoint_id', 'criado_em'),
    )
