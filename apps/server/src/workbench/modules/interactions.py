from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


class Question(Entity, Base):
    __tablename__ = "questions"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    interaction_key: Mapped[str] = mapped_column(unique=True)
    prompt: Mapped[str]
    kind: Mapped[str]
    options: Mapped[list] = mapped_column(JSONB, default=list)
    form_schema: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(default="pending")
    answer: Mapped[dict | None] = mapped_column(JSONB)


class Approval(Entity, Base):
    __tablename__ = "approvals"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey("operations.id"), unique=True)
    summary: Mapped[str]
    tool_name: Mapped[str]
    arguments: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(default="pending")
    approved: Mapped[bool | None]
    parameters_digest: Mapped[str]
    review: Mapped[dict | None] = mapped_column(JSONB)
