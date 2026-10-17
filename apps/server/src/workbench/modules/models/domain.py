from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


class ModelConnection(Entity, Base):
    __tablename__ = "model_connections"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str]
    provider_type: Mapped[str]
    endpoint: Mapped[str]
    model_identifier: Mapped[str]
    encrypted_api_key: Mapped[str | None]
    allow_remote: Mapped[bool] = mapped_column(default=False)
    context_window: Mapped[int] = mapped_column(default=8192)
    service_status: Mapped[str] = mapped_column(default="unchecked")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    generation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class ModelRevision(Entity, Base):
    __tablename__ = "model_revisions"
    connection_id: Mapped[str] = mapped_column(ForeignKey("model_connections.id"), index=True)
    connection_version: Mapped[int]
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB)
    encrypted_api_key: Mapped[str | None]
    __table_args__ = (UniqueConstraint("connection_id", "connection_version"),)
