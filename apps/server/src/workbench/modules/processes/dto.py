from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field
from workbench.contracts.base import PublicResponse
from workbench.contracts.requests import Command


class StartCommand(Command):
    command: str = Field(min_length=1, max_length=20_000)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    max_output_bytes: int = Field(default=32 * 1024**2, ge=1024, le=64 * 1024**2)


class CommandTarget(Command):
    command_id: UUID


class ReadCommand(CommandTarget):
    stream: Literal["stdout", "stderr"] = "stdout"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=8000, ge=1, le=16_000)


class CommandView(PublicResponse):
    id: str
    run_id: str
    command: str
    status: str
    timeout_seconds: int
    max_output_bytes: int
    stop_requested: bool
    owned_resources_closed: bool
    exit_code: int | None
    stdout_bytes: int | None
    stderr_bytes: int | None
    started_at: str | None
    finished_at: str | None
    log_artifacts: dict[str, str]
    error: dict | None
    sandbox: Literal["native_scoped", "linux_bwrap"] = "native_scoped"
    business_outcome: Literal["unverified"] = "unverified"
    created_at: datetime


class CommandOutput(PublicResponse):
    command: CommandView
    stream: Literal["stdout", "stderr"]
    text: str
    requested_offset: int
    start_offset: int
    next_offset: int
    total_bytes: int
    eof: bool
    utf8_boundary_adjusted: bool
    boundary_pending: bool = False
    decode_replacements: bool | int
