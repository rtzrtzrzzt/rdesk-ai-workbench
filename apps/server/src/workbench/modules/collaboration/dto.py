from datetime import datetime
from uuid import UUID

from pydantic import Field

from workbench.contracts.base import PublicResponse
from workbench.contracts.requests import Command
from workbench.contracts.responses import Run


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


class AgentTree(PublicResponse):
    root: Run
    agents: list[AgentView]
    limit: int
    occupied: int


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
