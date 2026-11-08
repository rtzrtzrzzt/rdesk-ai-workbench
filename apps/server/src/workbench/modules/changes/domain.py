from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


class ChangeRecord(Entity, Base):
    __tablename__ = "change_records"
    operation_id: Mapped[str] = mapped_column(ForeignKey("operations.id"), index=True)
    item_index: Mapped[int]
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    root_run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    execution_binding_id: Mapped[str | None] = mapped_column(ForeignKey("execution_bindings.id"))
    path: Mapped[str]
    before_version: Mapped[str | None]
    after_version: Mapped[str | None]
    before_exists: Mapped[bool]
    after_exists: Mapped[bool]
    before_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    after_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    source: Mapped[str]
    attribution: Mapped[str]
    state: Mapped[str] = mapped_column(default="intent")
    gaps: Mapped[list] = mapped_column(JSONB, default=list)
    __table_args__ = (UniqueConstraint("operation_id", "item_index"),)


class CommandCapture(Entity, Base):
    __tablename__ = "command_change_captures"
    operation_id: Mapped[str] = mapped_column(ForeignKey("operations.id"), unique=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    root_path: Mapped[str]
    state: Mapped[str] = mapped_column(default="pending")
    baseline: Mapped[dict] = mapped_column(JSONB, default=dict)
    gaps: Mapped[list] = mapped_column(JSONB, default=list)
