from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


class Operation(Entity, Base):
    __tablename__ = "operations"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    tool_call_id: Mapped[str]
    tool_name: Mapped[str]
    arguments: Mapped[dict] = mapped_column(JSONB)
    effect_status: Mapped[str] = mapped_column(default="not_started")
    status: Mapped[str] = mapped_column(default="created")
    result: Mapped[dict | None] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("run_id", "tool_call_id"),)
