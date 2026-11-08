"""HTTP command validation."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, model_validator

from workbench.contracts.command_limits import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    MAX_COMMAND_TIMEOUT_SECONDS,
)
from workbench.modules.models.profile import Declaration, Generation

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyCommand(Command):
    pass


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
    mode: Literal[
        "file", "text", "extracted_text", "native_image", "native_document", "native_audio"
    ]
    source_artifact_id: UUID | None = None
    source_version: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    consent_remote: bool = False

    @model_validator(mode="after")
    def complete_source(self):
        if (self.source_artifact_id is None) != (self.source_version is None):
            raise ValueError("附件原件标识与版本必须同时提供")
        return self


class InputPlan(Command):
    model_connection_id: UUID
    artifact_ids: list[UUID] = Field(max_length=8)


class RunOptions(Command):
    sandbox_profile: Literal["native_scoped", "linux_bwrap"] = "native_scoped"
    approval_mode: Literal["smart", "manual", "full_access"] = "smart"
    review_reasoning_effort: Literal["default", "disabled", "low", "medium", "xhigh"] = "low"
    command_timeout_seconds: int = Field(
        default=DEFAULT_COMMAND_TIMEOUT_SECONDS, ge=1, le=MAX_COMMAND_TIMEOUT_SECONDS, strict=True
    )
    child_agent_limit: int = Field(default=3, ge=0, le=3, strict=True)
    checkpoint_rounds: int = Field(default=32, ge=1, le=128, strict=True)
    model_output_tokens: int = Field(default=8192, ge=256, le=1048576, strict=True)
    model_output_recovery_tokens: int = Field(default=2048, ge=0, le=8192, strict=True)
    finish_reserve_seconds: int = Field(default=60, ge=0, le=3600, strict=True)
    max_model_rounds: int = Field(default=65536, ge=1, le=65536, strict=True)
    root_model_attempt_limit: int = Field(default=524288, ge=1, le=524288, strict=True)
    model_retry_limit: int = Field(default=2, ge=0, le=2, strict=True)
    retry_backoff_seconds: int = Field(default=1, ge=1, le=30, strict=True)
    context_target_ratio: float = Field(default=0.9, ge=0.5, le=0.95, allow_inf_nan=False)

    @model_validator(mode="after")
    def full_access_execution(self):
        if self.approval_mode == "full_access" and self.sandbox_profile != "native_scoped":
            raise ValueError("完全开放权限需要本机执行模式。")
        return self


class RunOptionOverrides(Command):
    sandbox_profile: Literal["native_scoped", "linux_bwrap"] | None = None
    approval_mode: Literal["smart", "manual"] | None = None
    review_reasoning_effort: Literal["default", "disabled", "low", "medium", "xhigh"] | None = None
    command_timeout_seconds: int | None = Field(
        default=None, ge=1, le=MAX_COMMAND_TIMEOUT_SECONDS, strict=True
    )
    child_agent_limit: int | None = Field(default=None, ge=0, le=3, strict=True)
    checkpoint_rounds: int | None = Field(default=None, ge=1, le=128, strict=True)
    model_output_tokens: int | None = Field(default=None, ge=256, le=1048576, strict=True)
    model_output_recovery_tokens: int | None = Field(default=None, ge=0, le=8192, strict=True)
    finish_reserve_seconds: int | None = Field(default=None, ge=0, le=3600, strict=True)
    max_model_rounds: int | None = Field(default=None, ge=1, le=65536, strict=True)
    root_model_attempt_limit: int | None = Field(default=None, ge=1, le=524288, strict=True)
    model_retry_limit: int | None = Field(default=None, ge=0, le=2, strict=True)
    retry_backoff_seconds: int | None = Field(default=None, ge=1, le=30, strict=True)
    context_target_ratio: float | None = Field(default=None, ge=0.5, le=0.95, allow_inf_nan=False)


class ModelSelectionGuard(Command):
    connection_id: UUID
    version: int = Field(ge=1, strict=True)


class RunCreate(Command):
    input: Annotated[str, StringConstraints(min_length=1, max_length=1048576)]
    model_connection_id: UUID | None = None
    expected_model: ModelSelectionGuard | None = None
    # "disabled" is a task choice; the adapter clears effort and disables thinking.
    reasoning_effort: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,31}$")
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
