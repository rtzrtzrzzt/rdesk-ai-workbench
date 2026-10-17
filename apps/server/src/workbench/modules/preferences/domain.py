from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


class RuntimeDefaults(Entity, Base):
    __tablename__ = "runtime_defaults"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    options: Mapped[dict] = mapped_column(JSONB)
    model_connection_id: Mapped[str | None] = mapped_column(ForeignKey("model_connections.id"))


class WorkspaceRuntimeDefaults(Entity, Base):
    __tablename__ = "workspace_runtime_defaults"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), unique=True)
    options: Mapped[dict] = mapped_column(JSONB, default=dict)
    model_connection_id: Mapped[str | None] = mapped_column(ForeignKey("model_connections.id"))
