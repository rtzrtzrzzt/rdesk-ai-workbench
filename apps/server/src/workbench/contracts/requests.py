"""HTTP command validation. Passwords retain their exact whitespace."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

from workbench.modules.models.profile import Declaration, Generation

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Username = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyCommand(Command):
    pass


class OwnerCreate(Command):
    username: Username
    password: SecretStr = Field(min_length=12, max_length=1024)


class Login(Command):
    username: Username
    password: SecretStr = Field(min_length=1, max_length=1024)


class WorkspaceCreate(Command):
    name: Name
    root_path: str = Field(min_length=1, max_length=4096)


class ThreadCreate(Command):
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ] = "新对话"


class WorkspaceRename(Command):
    name: Name
    expected_version: int = Field(ge=1)
    request_id: UUID


class WorkspaceLifecycle(Command):
    expected_version: int = Field(ge=1)
    request_id: UUID


class ThreadFork(Command):
    source_message_id: UUID
    include_history: Literal[True]
    request_id: UUID
    title: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
        | None
    ) = None


class ModelCreate(Command):
    name: Name
    provider_type: Literal["openai_compatible", "openai_responses", "anthropic"]
    endpoint: str = Field(min_length=1, max_length=2048)
    model_identifier: str = Field(min_length=1, max_length=256)
    api_key: SecretStr | None = Field(default=None, max_length=8192)
    allow_remote: bool = Field(default=False, strict=True)
    context_window: int = Field(default=8192, ge=1024, le=10_000_000, strict=True)
    generation: Generation = Field(default_factory=Generation)


class ModelUpdate(ModelCreate):
    expected_version: int = Field(ge=1, strict=True)
    clear_api_key: bool = False


class CapabilitiesUpdate(Command):
    expected_version: int = Field(ge=1, strict=True)
    declarations: dict[str, Declaration] = Field(max_length=11)


class AttachmentInput(Command):
    artifact_id: UUID
    version: str = Field(pattern=r"^[a-f0-9]{64}$")
    mode: Literal["text", "extracted_text", "native_image", "native_document", "native_audio"]
    consent_remote: bool = False


class InputPlan(Command):
    model_connection_id: UUID
    artifact_ids: list[UUID] = Field(max_length=8)


class RunOptions(Command):
    sandbox_profile: Literal["native_scoped", "linux_bwrap"] = "native_scoped"
    child_agent_limit: int = Field(default=3, ge=0, le=3, strict=True)
    max_model_rounds: int = Field(default=24, ge=1, le=64, strict=True)
    root_model_attempt_limit: int = Field(default=60, ge=1, le=200, strict=True)
    model_retry_limit: int = Field(default=2, ge=0, le=2, strict=True)
    retry_backoff_seconds: int = Field(default=1, ge=1, le=30, strict=True)
    context_target_ratio: float = Field(default=0.9, ge=0.5, le=0.95, allow_inf_nan=False)


class RunOptionOverrides(Command):
    sandbox_profile: Literal["native_scoped", "linux_bwrap"] | None = None
    child_agent_limit: int | None = Field(default=None, ge=0, le=3, strict=True)
    max_model_rounds: int | None = Field(default=None, ge=1, le=64, strict=True)
    root_model_attempt_limit: int | None = Field(default=None, ge=1, le=200, strict=True)
    model_retry_limit: int | None = Field(default=None, ge=0, le=2, strict=True)
    retry_backoff_seconds: int | None = Field(default=None, ge=1, le=30, strict=True)
    context_target_ratio: float | None = Field(default=None, ge=0.5, le=0.95, allow_inf_nan=False)


class ModelSelectionGuard(Command):
    connection_id: UUID
    version: int = Field(ge=1, strict=True)


class RunCreate(Command):
    input: Annotated[str, StringConstraints(min_length=1, max_length=131072)]
    model_connection_id: UUID | None = None
    expected_model: ModelSelectionGuard | None = None
    request_id: UUID
    deadline_seconds: int | None = Field(default=None, ge=1, le=604800, strict=True)
    attachments: list[AttachmentInput] = Field(default_factory=list, max_length=8)
    options: RunOptions | None = None


class CancelRun(Command):
    request_id: UUID


class QuestionAnswer(Command):
    version: int = Field(ge=1, strict=True)
    answer: (
        Annotated[str, StringConstraints(min_length=1, max_length=65536)]
        | Annotated[
            list[Annotated[str, StringConstraints(min_length=1, max_length=1024)]],
            Field(min_length=1, max_length=128),
        ]
        | dict
    )
    request_id: UUID


class ApprovalDecision(Command):
    version: int = Field(ge=1, strict=True)
    approved: bool = Field(strict=True)
    request_id: UUID
