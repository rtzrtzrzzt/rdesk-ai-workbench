"""The active conversation summary is separate from immutable message history."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


class ThreadContext(Entity, Base):
    __tablename__ = "thread_contexts"
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), unique=True
    )
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    covered_messages: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    summary_sources: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    transfer_scope: Mapped[str | None]
    memory_fingerprint: Mapped[str | None]
    state_artifact_id: Mapped[str | None]
    summary_version: Mapped[int] = mapped_column(default=0, server_default="0")
    model_connection_id: Mapped[str | None]
    context_window: Mapped[int | None]
    used_tokens: Mapped[int | None]
    source: Mapped[str] = mapped_column(default="unavailable", server_default="unavailable")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_compaction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    before_tokens: Mapped[int | None]
    after_tokens: Mapped[int | None]
    request_id: Mapped[str | None]
    request_run_id: Mapped[str | None]
    compaction_status: Mapped[str] = mapped_column(default="idle", server_default="idle")
    compaction_error: Mapped[str | None]
