from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from workbench.contracts.base import PublicResponse
from workbench.contracts.requests import Command


class PresentFile(Command):
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    artifact_id: UUID | None = None
    version: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def one_source(self):
        if (self.path is None) == (self.artifact_id is None):
            raise ValueError("path 和 artifact_id 必须恰好提供一项。")
        return self


class UploadCreate(Command):
    filename: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    total_bytes: int = Field(ge=0, le=1_073_741_824, strict=True)
    mime_type: str | None = Field(default=None, max_length=200)
    request_id: UUID


class UploadStatus(PublicResponse):
    id: str
    filename: str
    total_bytes: int
    received_bytes: int
    chunk_size: int
    status: str
    artifact_id: str | None
    expires_at: datetime


class ArtifactView(PublicResponse):
    id: str
    workspace_id: str
    parent_id: str | None
    run_id: str | None
    filename: str
    mime_type: str
    declared_mime: str | None
    byte_size: int
    version: str
    kind: str
    status: str
    metadata: dict
    created_at: datetime


class OCRSelection(Command):
    languages: list[Literal["eng", "chi_sim", "chi_tra"]] = Field(
        default_factory=lambda: ["eng"], min_length=1, max_length=3
    )
    pages: list[Annotated[int, Field(ge=1, le=1_000_000)]] = Field(
        default_factory=list, max_length=8
    )
    dpi: int = Field(default=150, ge=150, le=200)


class VideoSelection(Command):
    start_seconds: float = Field(ge=0, le=86400, allow_inf_nan=False)
    end_seconds: float = Field(gt=0, le=86400, allow_inf_nan=False)
    max_frames: int = Field(default=6, ge=1, le=12)

    @model_validator(mode="after")
    def range_budget(self):
        if not 0 < self.end_seconds - self.start_seconds <= 120:
            raise ValueError("请选择最多120秒的明确视频范围")
        return self


class ASRSelection(Command):
    language: str = Field(default="auto", min_length=2, max_length=4, pattern=r"^[a-z]+$")
    timestamps: Literal[True] = True


class ProcessFile(Command):
    request_id: UUID
    operation: Literal["extract", "preview", "ocr", "convert_pdf", "video_frames", "transcribe"] = (
        "extract"
    )
    start: int = Field(default=1, ge=1, le=1_000_000)
    limit: int = Field(default=50, ge=1, le=200)
    ocr: OCRSelection | None = None
    video: VideoSelection | None = None
    asr: ASRSelection | None = None

    @model_validator(mode="after")
    def explicit_selection(self):
        if (self.ocr is not None) != (self.operation == "ocr"):
            raise ValueError("OCR需要单独选择语言和PDF页码")
        if (self.video is not None) != (self.operation == "video_frames"):
            raise ValueError("视频帧提取需要明确时间范围")
        if (self.asr is not None) != (self.operation == "transcribe"):
            raise ValueError("语音转录需要明确语言及时间戳选项")
        return self


class FileTask(PublicResponse):
    id: str
    artifact_id: str
    status: str
    operation: str
    error: dict | None
    results: list[ArtifactView]


class FilePreview(PublicResponse):
    artifact_id: str
    text: str | None
    metadata: dict
    truncated: bool


class ProcessorCapability(PublicResponse):
    id: str
    name: str
    formats: list[str]
    operations: list[str]
    status: str
    version: str | None
    limitation: str


class ExportCapability(PublicResponse):
    formats: list[str]
    status: str
    version: str | None
    limitation: str
