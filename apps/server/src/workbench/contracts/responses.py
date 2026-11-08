"""Public response schemas; provider protocol state and credentials stay private."""

from datetime import datetime
from typing import Any, Literal

from workbench.contracts.base import PublicResponse
from workbench.modules.credentials.dto import VaultView
from workbench.modules.extensions.dto import ExtensionView
from workbench.modules.files.dto import FileTask
from workbench.modules.mcp.host_dto import MCPView, SnapshotView
from workbench.modules.media.dto import MediaConnectionView, MediaDeletionResult, MediaTaskView
from workbench.modules.models.profile import Capability, Generation


class UserIdentity(PublicResponse):
    id: str
    username: str


class OwnerSession(PublicResponse):
    user: UserIdentity
    csrf_token: str


class SetupStatus(PublicResponse):
    configured: bool
    database_ready: bool
    version: str


class LiveStatus(PublicResponse):
    status: Literal["alive"]
    version: str


class Workspace(PublicResponse):
    id: str
    name: str
    root_path: str
    archived: bool = False
    removed_at: datetime | None = None
    created_at: datetime
    version: int


class Thread(PublicResponse):
    id: str
    workspace_id: str
    title: str
    title_source: str = "legacy"
    title_naming: dict = {}
    parent_thread_id: str | None = None
    fork_message_id: str | None = None
    created_at: datetime
    version: int
    archived: bool = False
    deleting: bool = False
    latest_run_id: str | None = None
    latest_run_status: str | None = None
    latest_run_phase: str | None = None
    queue_count: int = 0
    is_subagent: bool = False
    agent_parent_thread_id: str | None = None


class StoredAttachment(PublicResponse):
    artifact_id: str
    version: str
    mode: Literal[
        "file", "text", "extracted_text", "native_image", "native_document", "native_audio"
    ]
    source_artifact_id: str | None = None
    source_version: str | None = None
    consent_remote: bool
    consent_target: str | None = None
    filename: str
    mime_type: str


class Message(PublicResponse):
    id: str
    thread_id: str
    run_id: str | None
    role: str
    content: str
    reasoning: str = ""
    phase: Literal["commentary", "final_answer"] | None = None
    status: str
    created_at: datetime
    attachments: list[StoredAttachment]
    forkable: bool = False


class ReasoningChoice(PublicResponse):
    value: str
    label: str
    kind: str


class ModelConnection(PublicResponse):
    id: str
    name: str
    provider_type: Literal["openai_compatible", "openai_responses", "anthropic"]
    endpoint: str
    model_identifier: str
    credential_configured: bool
    allow_remote: bool
    context_window: int
    service_status: Literal["unchecked", "ready", "unavailable"]
    capabilities: dict[str, Capability]
    adapter_routes: dict[str, dict]
    generation: Generation
    reasoning_efforts: list[str]
    reasoning_choices: list[ReasoningChoice] = []
    version: int


class QuestionOption(PublicResponse):
    id: str
    label: str
    description: str | None = None


class Question(PublicResponse):
    id: str
    run_id: str
    version: int
    prompt: str
    kind: Literal["single_choice", "multiple_choice", "text", "form"]
    options: list[QuestionOption]
    status: str
    answer: str | list[str] | dict[str, Any] | None
    form_schema: dict[str, Any] | None


class Approval(PublicResponse):
    id: str
    run_id: str
    version: int
    summary: str
    tool_name: str
    arguments: dict[str, Any]
    status: str
    review: dict[str, Any] | None = None


class RunError(PublicResponse):
    code: str
    message: str


class RunTiming(PublicResponse):
    elapsed_ms: int
    observed_at: datetime
    finished_at: datetime | None


class Run(PublicResponse):
    id: str
    kind: str
    thread_id: str | None
    status: str
    model_connection_id: str | None
    reasoning_effort: str | None = None
    approval_mode: Literal["smart", "manual", "full_access"] = "manual"
    root_run_id: str | None
    parent_run_id: str | None
    state_artifact_id: str | None
    created_at: datetime
    timing: RunTiming | None = None
    last_event_seq: int
    error: RunError | None
    paused_reason: RunError | None = None
    activity: str | None = None
    plan: dict[str, Any] | None = None
    questions: list[Question]
    approvals: list[Approval]


class RunEvent(PublicResponse):
    schema_version: str
    event_id: str
    run_id: str
    seq: int
    type: str
    occurred_at: datetime
    payload: dict[str, Any]


class ConversationSnapshot(PublicResponse):
    run: Run
    messages: list[Message]


class RequestClosure(PublicResponse):
    status: Literal["sealed", "recorded"]


class DatabaseStatus(PublicResponse):
    status: str


class WorkerStatus(PublicResponse):
    status: str
    last_seen_at: datetime | None


class ModelAsset(PublicResponse):
    name: str
    status: Literal["downloaded"]
    verification: Literal["previously_verified"]


class SystemStatus(PublicResponse):
    version: str
    database: DatabaseStatus
    worker: WorkerStatus
    model_assets: list[ModelAsset]


class ErrorDetail(PublicResponse):
    kind: str
    code: str
    message: str
    retryable: bool
    retry_after_ms: int | None
    details_ref: str | None


class ErrorResponse(PublicResponse):
    error: ErrorDetail
    request_id: str | None
    trace_id: str


# Values stored by the currently supported idempotent command services.
class CommandReceipt(PublicResponse):
    id: str | None
    status: Literal["in_progress", "failed"]
    message: str


RequestResult = (
    Run
    | Workspace
    | Thread
    | Question
    | Approval
    | FileTask
    | MCPView
    | SnapshotView
    | VaultView
    | CommandReceipt
    | ExtensionView
    | MediaConnectionView
    | MediaTaskView
    | MediaDeletionResult
    | None
)

ERROR_RESPONSES = {
    status: {"model": ErrorResponse}
    for status in (400, 401, 403, 404, 409, 410, 422, 429, 500, 503)
}
SSE_RESPONSE = {
    200: {
        "description": "Replayable SSE. Each data field is one RunEvent JSON object.",
        "content": {
            "text/event-stream": {
                "schema": {"type": "string"},
                "x-event-schema": {"$ref": "#/components/schemas/RunEvent"},
            }
        },
    }
}


def register_event_schema(schema: dict) -> None:
    """Register the decoded SSE envelope without claiming the stream body is JSON."""
    event = RunEvent.model_json_schema(ref_template="#/components/schemas/{model}")
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    components.update(event.pop("$defs", {}))
    components["RunEvent"] = event
