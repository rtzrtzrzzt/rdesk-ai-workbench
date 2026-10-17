from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


class AgentSlot(Entity, Base):
    __tablename__ = "agent_slots"
    root_run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), unique=True)
    name: Mapped[str]
    deliverable: Mapped[str]
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentMessage(Entity, Base):
    __tablename__ = "agent_messages"
    root_run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    from_run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    to_run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    operation_id: Mapped[str] = mapped_column(unique=True)
    source: Mapped[str] = mapped_column(default="agent")
    content: Mapped[str]
    delivered_attempt_id: Mapped[str | None] = mapped_column(ForeignKey("attempts.id"))
