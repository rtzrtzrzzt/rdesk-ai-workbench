from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from workbench.contracts.base import PublicResponse
from workbench.contracts.requests import Command
from workbench.contracts.responses import Run
from workbench.modules.files.dto import FileTask
from workbench.modules.media.dto import MediaTaskView


class AgentView(PublicResponse):
    agent_id: str
    name: str
    deliverable: str
    root_run_id: str
    released_at: datetime | None
    allowed_tools: list[str]
    paths: list[str]
    mode: str
    run: Run
    result: str | None
    state: Literal["busy", "idle", "stopping", "closed"]
    turn_count: int
    turn_run_ids: list[str]


class AgentTree(PublicResponse):
    root: Run
    agents: list[AgentView]
    limit: int
    occupied: int
    media_tasks: list[MediaTaskView] = Field(default_factory=list)
    file_tasks: list[FileTask] = Field(default_factory=list)


class SteerRun(Command):
    input: str = Field(min_length=1, max_length=1048576)
    request_id: UUID


class InputReceived(PublicResponse):
    id: str
    status: str


class InputDelivery(InputReceived):
    input: str
    created_at: datetime
    delivered_attempt_id: str | None
    processing_attempt_id: str | None = None


class AgentCommunication(PublicResponse):
    id: str
    from_run_id: str
    to_run_id: str
    from_name: str
    to_name: str
    kind: Literal["note", "question", "reply", "instruction", "result"]
    content: str
    reply_to_id: str | None
    status: Literal["queued", "applying", "delivered"]
    created_at: datetime


class AgentCommunications(PublicResponse):
    items: list[AgentCommunication]
    next_cursor: str | None


class AgentFollowup(Command):
    request_id: UUID
    input: str = Field(min_length=1, max_length=131072)


class AgentControl(Command):
    request_id: UUID
