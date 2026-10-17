"""Host-owned persistence; protocol DTOs remain independent of SQLAlchemy."""

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


class MCPConnection(Entity, Base):
    __tablename__ = "mcp_connections"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    configuration: Mapped[dict] = mapped_column(JSONB)
    encrypted_secrets: Mapped[dict] = mapped_column(JSONB, default=dict)
    execution_authorized: Mapped[bool] = mapped_column(default=False)


class MCPSnapshot(Entity, Base):
    __tablename__ = "mcp_snapshots"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("mcp_connections.id"), index=True)
    connection_version: Mapped[int]
    snapshot: Mapped[dict] = mapped_column(JSONB)


class ToolExchange(Entity, Base):
    __tablename__ = "tool_exchanges"
    operation_id: Mapped[str] = mapped_column(ForeignKey("operations.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id"))
    sequence: Mapped[int]
    request: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(default="reserved")
    dispatched: Mapped[bool] = mapped_column(default=False)
    outcome: Mapped[dict | None] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("operation_id", "attempt_id", "sequence"),)
