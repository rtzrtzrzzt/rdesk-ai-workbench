from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


class Thread(Entity, Base):
    __tablename__ = "threads"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(default="新对话")
    archived: Mapped[bool] = mapped_column(default=False, server_default="false")
    deleting: Mapped[bool] = mapped_column(default=False, server_default="false")
    queue_paused: Mapped[bool] = mapped_column(default=False, server_default="false")
    queue_resume_run_id: Mapped[str | None]
    fork_include_anchor: Mapped[bool] = mapped_column(default=True, server_default="true")
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
    reasoning: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(default="completed")
    provider_message: Mapped[dict | None] = mapped_column(JSONB)
    operation_id: Mapped[str | None] = mapped_column(ForeignKey("operations.id"), unique=True)
    attachments: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")


class QueuedInput(Entity, Base):
    __tablename__ = "queued_inputs"
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    position: Mapped[int]
    status: Mapped[str] = mapped_column(default="queued")
    payload: Mapped[dict] = mapped_column(JSONB)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    error: Mapped[dict | None] = mapped_column(JSONB)


class ConversationDeletion(Entity, Base):
    __tablename__ = "conversation_deletions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    target_thread_id: Mapped[str] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(default="pending", index=True)
    intent: Mapped[dict] = mapped_column(JSONB)
    message: Mapped[str] = mapped_column(default="正在停止任务并清理会话。")
