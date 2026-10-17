from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


class Thread(Entity, Base):
    __tablename__ = "threads"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(default="新对话")
    parent_thread_id: Mapped[str | None] = mapped_column(ForeignKey("threads.id"), index=True)
    fork_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", use_alter=True, name="fk_threads_fork_message")
    )


class Message(Entity, Base):
    __tablename__ = "messages"
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), index=True)
    role: Mapped[str]
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(default="completed")
    provider_message: Mapped[dict | None] = mapped_column(JSONB)
    operation_id: Mapped[str | None] = mapped_column(ForeignKey("operations.id"), unique=True)
    attachments: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
