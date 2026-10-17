"""Business ownership of native commands; runner files are execution receipts."""

from sqlalchemy import Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from workbench.infrastructure.database import Base, Entity


class CommandExecution(Entity, Base):
    __tablename__ = "command_executions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey("operations.id"), unique=True)
    nonce: Mapped[str]
    command: Mapped[str]
    sandbox_profile: Mapped[str] = mapped_column(default="native_scoped")
    argv: Mapped[list] = mapped_column(JSONB)
    timeout_seconds: Mapped[int]
    max_output_bytes: Mapped[int]
    output_path: Mapped[str] = mapped_column(unique=True)
    status: Mapped[str] = mapped_column(default="reserved", index=True)
    native_identity: Mapped[dict | None] = mapped_column(JSONB)
    receipt: Mapped[dict | None] = mapped_column(JSONB)
    stop_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    resources_closed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    log_artifacts: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[dict | None] = mapped_column(JSONB)
