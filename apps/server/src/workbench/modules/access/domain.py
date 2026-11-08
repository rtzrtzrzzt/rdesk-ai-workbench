from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


class ApiClient(Entity, Base):
    __tablename__ = "api_clients"
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str]
    enabled: Mapped[bool] = mapped_column(default=True)
    requests_per_minute: Mapped[int] = mapped_column(default=120)
    max_active_runs: Mapped[int] = mapped_column(default=3)
    request_window: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_count: Mapped[int] = mapped_column(default=0)


class ApiKey(Entity, Base):
    __tablename__ = "api_keys"
    client_id: Mapped[str] = mapped_column(ForeignKey("api_clients.id"), index=True)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str]
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Grant(Entity, Base):
    __tablename__ = "api_grants"
    client_id: Mapped[str] = mapped_column(ForeignKey("api_clients.id"), index=True)
    service_id: Mapped[str] = mapped_column(ForeignKey("agent_services.id"), index=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    actions: Mapped[list] = mapped_column(JSONB, default=list)
    allowed_tools: Mapped[list | None] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("client_id", "service_id"),)


class ExternalNamespace(Entity, Base):
    __tablename__ = "external_namespaces"
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("api_clients.id"), index=True)
    service_id: Mapped[str] = mapped_column(ForeignKey("agent_services.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), unique=True)
    __table_args__ = (UniqueConstraint("client_id", "service_id"),)


class ExecutionBinding(Entity, Base):
    """Independent of root/parent IDs, which deletion can legitimately rewrite."""

    __tablename__ = "execution_bindings"
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    origin: Mapped[str] = mapped_column(default="api")
    client_id: Mapped[str] = mapped_column(ForeignKey("api_clients.id"), index=True)
    service_id: Mapped[str] = mapped_column(ForeignKey("agent_services.id"), index=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("agent_service_revisions.id"))
    namespace_id: Mapped[str] = mapped_column(ForeignKey("external_namespaces.id"), index=True)
    snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
