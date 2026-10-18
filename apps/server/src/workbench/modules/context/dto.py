from datetime import datetime
from typing import Literal
from uuid import UUID

from workbench.contracts.base import PublicResponse
from workbench.contracts.requests import Command


class CompactContext(Command):
    request_id: UUID
    model_connection_id: UUID | None = None


class ContextView(PublicResponse):
    thread_id: str
    used_tokens: int | None
    context_window: int | None
    remaining_tokens: int | None
    source: Literal["measured", "tokenizer", "estimated", "unavailable"]
    updated_at: datetime | None
    last_compaction_at: datetime | None
    last_compaction_before_tokens: int | None
    last_compaction_after_tokens: int | None
    compaction_pending: bool
    compaction_status: Literal["idle", "queued", "running", "succeeded", "failed"]
    compaction_error: str | None
    active_run_id: str | None
