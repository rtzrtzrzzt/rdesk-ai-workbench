from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field
from workbench.contracts.base import PublicResponse
from workbench.contracts.command_limits import MAX_COMMAND_TIMEOUT_SECONDS
from workbench.contracts.requests import Command


class StartCommand(Command):
    command: str = Field(min_length=1, max_length=20_000)
    interactive: bool = Field(
        default=False,
        strict=True,
        description="Linux PTY（24行×80列）；标准错误合并到标准输出，可继续发送输入。",
    )
    timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        le=MAX_COMMAND_TIMEOUT_SECONDS,
        description="单条命令执行时限（秒），不得超过当前任务预算；省略或null继承任务预算。",
    )
    max_output_bytes: int = Field(default=32 * 1024**2, ge=1024, le=64 * 1024**2)


class CommandTarget(Command):
    command_id: UUID


class ReadCommand(CommandTarget):
    stream: Literal["stdout", "stderr"] = "stdout"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=8000, ge=1, le=16_000)


class WriteCommandInput(CommandTarget):
    data: str = Field(default="", max_length=4096, description="输入文本，最多4096 UTF-8字节。")
    eof: bool = Field(
        default=False,
        strict=True,
        description="发送终端当前的canonical EOF；raw模式不支持EOF，可用data发送控制字符。",
    )


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
    interactive: bool = False
    stderr_merged: bool = False
    terminal_rows: int | None = None
    terminal_columns: int | None = None
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
