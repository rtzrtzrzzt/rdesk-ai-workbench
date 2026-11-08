from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator

from workbench.contracts.requests import (
    ApprovalDecision,
    AttachmentInput,
    Command,
    QuestionAnswer,
    RunOptions,
)

# Each entry is implemented within the external workspace boundary. Host processes,
# settings, memory and connected services need their own sharing policy first.
SAFE_TOOLS = frozenset(
    {
        "discover_tools",
        "read_workbench_guide",
        "list_files",
        "find_files",
        "read_file",
        "read_files",
        "search_files",
        "write_file",
        "apply_patch",
        "edit_files",
        "present_file",
        "read_plan",
        "update_plan",
        "ask_user",
        "list_artifacts",
        "read_artifact",
        "spawn_agent",
        "send_agent_message",
        "wait_agents",
        "inspect_agents",
        "release_agent",
    }
)
DEFAULT_TOOLS = [
    "discover_tools",
    "read_workbench_guide",
    "list_files",
    "find_files",
    "read_file",
    "read_files",
    "search_files",
    "write_file",
    "apply_patch",
    "present_file",
    "read_plan",
    "update_plan",
    "ask_user",
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class Definition(Command):
    model_connection_id: UUID
    allowed_tools: list[str] = Field(default_factory=lambda: list(DEFAULT_TOOLS), max_length=64)
    instructions: str = Field(default="", max_length=65536)
    runtime_options: RunOptions = Field(default_factory=lambda: RunOptions(child_agent_limit=0))
    source_workspace_id: UUID | None = None
    source_paths: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("allowed_tools")
    @classmethod
    def supported_tools(cls, value):
        if len(value) != len(set(value)) or set(value) - SAFE_TOOLS:
            raise ValueError("工具必须来自已实现外部作用域隔离的工具目录，且不可重复。")
        return value


class PublishAgent(Command):
    name: Name
    description: str = Field(default="", max_length=4096)
    definition: Definition


class ReviseAgent(PublishAgent):
    expected_version: int = Field(ge=1, strict=True)


class AgentState(Command):
    enabled: bool = Field(strict=True)
    expected_version: int = Field(ge=1, strict=True)


class OwnerAgent(Command):
    id: UUID
    name: str
    description: str
    enabled: bool
    version: int
    revision_id: UUID
    revision_number: int
    definition: Definition


class Capabilities(Command):
    tools: list[str]
    sessions: bool = True
    streaming: bool = True
    inputs: bool = True
    questions: bool = True
    approvals: bool = False
    cancellation: bool = True
    outputs: bool = True
    uploads: bool = True
    host_commands: bool = False
    mcp: bool = False
    media_generation: bool = False
    child_agents: bool = False
    source_snapshot: bool = False


class PublicAgent(Command):
    id: UUID
    name: str
    description: str
    revision_id: UUID
    capabilities: Capabilities


class SessionCreate(Command):
    agent_id: UUID
    title: Name = "新会话"
    request_id: UUID


class SessionView(Command):
    id: UUID
    agent_id: UUID
    revision_id: UUID
    title: str
    created_at: datetime


class RunCreate(Command):
    agent_id: UUID
    session_id: UUID | None = None
    input: str = Field(min_length=1, max_length=1048576)
    request_id: UUID
    deadline_seconds: int | None = Field(default=None, ge=1, le=604800, strict=True)
    attachments: list[AttachmentInput] = Field(default_factory=list, max_length=8)


class RequestCommand(Command):
    request_id: UUID


class RunInput(RequestCommand):
    input: str = Field(min_length=1, max_length=1048576)


class Answer(QuestionAnswer):
    question_id: UUID


class Decision(ApprovalDecision):
    approval_id: UUID


class PublicApproval(Command):
    id: UUID
    version: int
    summary: str
    tool_name: str
    status: str


class PublicQuestion(Command):
    id: UUID
    version: int
    prompt: str
    kind: str
    options: list
    form_schema: dict | None
    status: str
    answer: str | list | dict | None


class PublicRun(Command):
    id: UUID
    session_id: UUID | None
    agent_id: UUID
    revision_id: UUID
    status: str
    created_at: datetime
    last_event_seq: int
    final_message_id: UUID | None = None
    finalized_event_seq: int | None = None
    questions: list[PublicQuestion]
    approvals: list[PublicApproval]
    error: dict | None


class PublicOutput(Command):
    id: UUID
    name: str
    mime_type: str
    byte_size: int
    version: str
    download_url: str


class Outputs(Command):
    final_answer: str | None = None
    run_id: UUID
    messages: list[str]
    artifacts: list[PublicOutput]
