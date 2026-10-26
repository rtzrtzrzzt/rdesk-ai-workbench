from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, model_validator

from workbench.contracts.base import PublicResponse
from workbench.contracts.requests import Command
from workbench.modules.files.dto import ArtifactView

MediaKind = Literal["image", "video", "audio", "3d"]
MediaDriver = Literal["flux3_native", "openai_images", "media_http"]


class MediaConfiguration(Command):
    expand_prompt: bool = True
    model_directory: str | None = Field(default=None, max_length=4096)
    timeout_seconds: int = Field(default=3600, ge=30, le=86400)
    memory_limit_gib: float = Field(default=10, ge=2, le=120, allow_inf_nan=False)
    headroom_gib: float = Field(default=9, ge=8.5, le=120, allow_inf_nan=False)
    allocator_limit_gib: float = Field(default=9, ge=1, le=120, allow_inf_nan=False)
    attention_backend: Literal["cudnn", "torch_sdpa"] = "cudnn"

    @model_validator(mode="after")
    def memory_budget(self):
        if self.allocator_limit_gib > self.memory_limit_gib - 1:
            raise ValueError("GPU allocator额度须为CUDA运行时及CPU临时空间至少保留1GiB")
        return self


class MediaConnectionSave(Command):
    name: str = Field(min_length=1, max_length=160)
    scope: Literal["user", "workspace"] = "user"
    capability: MediaKind = "image"
    driver: MediaDriver = "flux3_native"
    model_identifier: str = Field(min_length=1, max_length=256)
    endpoint: str | None = Field(default=None, max_length=2048)
    api_key: SecretStr | None = Field(default=None, max_length=8192)
    clear_api_key: bool = False
    allow_remote: bool = False
    enabled: bool = True
    is_default: bool = True
    configuration: MediaConfiguration = Field(default_factory=MediaConfiguration)
    expected_version: int = Field(default=0, ge=0)
    request_id: UUID


class MediaConnectionView(PublicResponse):
    id: str
    name: str
    scope: str
    workspace_id: str | None
    capability: MediaKind
    driver: MediaDriver
    model_identifier: str
    endpoint: str | None
    credential_configured: bool
    allow_remote: bool
    enabled: bool
    is_default: bool
    configuration: MediaConfiguration
    verification: str
    version: int


class MediaConnections(PublicResponse):
    connections: list[MediaConnectionView]
    native: dict
    adapters: list[dict]


class MediaReference(Command):
    artifact_id: UUID
    version: str = Field(pattern=r"^[a-f0-9]{64}$")
    consent_remote: bool = False


class GenerateImage(Command):
    prompt: str = Field(min_length=1, max_length=32768)
    connection_id: UUID | None = None
    references: list[MediaReference] = Field(
        default_factory=list,
        max_length=8,
        description="已有文件产物的真实artifact_id/version，不是生成任务run_id。",
    )
    width: int = Field(default=1024, ge=64, le=4096)
    height: int = Field(default=1024, ge=64, le=4096)
    seed: int | None = Field(default=None, ge=0, le=4294967295)
    steps: int | None = Field(default=None, ge=1, le=256)


class GenerateVideo(GenerateImage):
    duration_seconds: float = Field(default=5, gt=0, le=3600)


class GenerateAudio(Command):
    prompt: str = Field(min_length=1, max_length=32768)
    connection_id: UUID | None = None
    voice: str | None = Field(default=None, max_length=100)
    duration_seconds: float | None = Field(default=None, gt=0, le=3600)


class Generate3D(GenerateImage):
    format: Literal["glb", "gltf", "obj", "ply"] = "glb"


class MediaCreate(GenerateImage):
    expected_connection_version: int | None = Field(default=None, ge=1)
    capability: MediaKind = "image"
    duration_seconds: float | None = Field(default=None, gt=0, le=3600)
    voice: str | None = Field(default=None, max_length=100)
    format: str | None = Field(default=None, max_length=30)
    request_id: UUID


class MediaTarget(Command):
    run_id: UUID = Field(description="generate_*返回的run_id，用于查询或取消后台任务。")


class MediaGuide(Command):
    connection_id: UUID | None = None
    editing: bool = False


class MediaObservation(Command):
    artifact_id: UUID = Field(description="已完成任务artifacts列表中的真实文件id，不是任务run_id。")
    version: str = Field(pattern=r"^[a-f0-9]{64}$")


class MediaReconcile(Command):
    provider_job_id: str | None = Field(
        default=None, min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9_.-]+$"
    )
    confirmed_not_submitted: bool = False
    request_id: UUID


class MediaTaskView(PublicResponse):
    id: str
    run_id: str
    connection_id: str
    connection_name: str
    capability: MediaKind
    status: str
    phase: str
    progress: float | None
    provider_job_id: str | None
    dispatch_state: str
    resources_closed: bool
    deletion_requested: bool
    artifacts: list[ArtifactView]
    error: dict | None
    metadata: dict
    created_at: datetime


class MediaTaskList(PublicResponse):
    tasks: list[MediaTaskView]
    next_before: str | None
    total: int


class MediaTaskDeletion(Command):
    run_ids: list[UUID] = Field(min_length=1, max_length=200)
    stop_active: bool = False
    request_id: UUID


class MediaDeletionResult(PublicResponse):
    deleted: list[str]
    stopping: list[str]
