from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from workbench.contracts.base import PublicResponse
from workbench.contracts.requests import Command, RunCreate
from workbench.contracts.responses import Run, RunError, Thread


class ThreadRename(Command):
    title: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    request_id: UUID


class ThreadLifecycle(Command):
    action: Literal["archive", "restore"]
    version: int = Field(ge=1)
    stop_active: bool = False
    request_id: UUID


class ThreadEdit(RunCreate):
    message_id: UUID


class EditedThread(PublicResponse):
    thread: Thread
    run: Run


class QueueItem(PublicResponse):
    id: str
    thread_id: str
    input: str
    position: int
    version: int
    status: Literal["queued", "paused", "dispatched"]
    run_id: str | None
    error: RunError | None
    created_at: datetime


class QueueView(PublicResponse):
    thread_id: str
    paused: bool
    items: list[QueueItem]


class QueueUpdate(Command):
    version: int = Field(ge=1)
    request_id: UUID
    input: str | None = Field(default=None, min_length=1, max_length=1048576)
    position: int | None = Field(default=None, ge=0)
    remove: bool = False


class ResumeRun(Command):
    request_id: UUID
    extend_budget: bool = False


class RequestCommand(Command):
    request_id: UUID


class DeletionPreview(PublicResponse):
    preview_token: str
    thread_count: int
    run_count: int
    active_run_count: int
    thread_ids: list[str]
    run_ids: list[str]


class DeleteThread(Command):
    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: UUID


class DeletionView(PublicResponse):
    id: str
    status: Literal["pending", "completed", "blocked"]
    message: str
