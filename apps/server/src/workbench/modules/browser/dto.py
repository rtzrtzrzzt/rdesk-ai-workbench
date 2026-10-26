"""Restricted browser contracts. Identity and output paths come from trusted callers."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BrowserOwner(DTO):
    user_id: UUID
    workspace_id: UUID
    run_id: UUID | None = None


class BrowserCreate(DTO):
    request_id: UUID
    enabled: bool = False
    allowed_origins: list[str] = Field(default_factory=list, max_length=24)
    local_origins: list[str] = Field(default_factory=list, max_length=4)
    allow_public_network: bool = False
    timeout_seconds: int = Field(default=20, ge=1, le=60)
    ttl_seconds: int = Field(default=900, ge=30, le=3600)


class BrowserSession(DTO):
    session_id: str
    generation: int
    status: Literal["active", "stopping", "stopped", "expired"]
    created_at: datetime
    expires_at: datetime
    allowed_origins: list[str]
    local_origins: list[str]
    allow_public_network: bool = False
    isolated_profile: Literal[True] = True
    downloads_enabled: Literal[False] = False
    explicit_download_supported: Literal[True] = True
    uploads_enabled: Literal[False] = False


class BrowserObserve(DTO):
    screenshot: bool = False
    output_dir: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def screenshot_target(self):
        if self.screenshot != (self.output_dir is not None):
            raise ValueError("screenshot requires a trusted worker staging output_dir")
        return self


class BrowserElement(DTO):
    ref: str
    tag: str
    role: str
    name: str
    input_type: str | None
    href: str | None


class BrowserObservation(DTO):
    session_id: str
    observation_id: str
    generation: int
    captured_at: datetime
    url: str
    title: str
    text: str
    truncated: bool
    elements: list[BrowserElement]
    screenshot_path: str | None
    screenshot_width: int | None
    screenshot_height: int | None
    blocked_requests: int
    scope: Literal["main_frame"] = "main_frame"


class BrowserAction(DTO):
    request_id: UUID
    session_id: UUID
    last_observation: UUID
    target_ref: str = Field(min_length=1, max_length=64)
    kind: Literal["navigate", "click", "type", "scroll", "key"]
    url: str | None = Field(default=None, max_length=4096)
    text: str | None = Field(default=None, max_length=16000)
    key: (
        Literal["Enter", "Tab", "Escape", "ArrowUp", "ArrowDown", "Home", "End", "Space"] | None
    ) = None
    delta_y: int | None = Field(default=None, ge=-3000, le=3000)

    @model_validator(mode="after")
    def arguments(self):
        fields = {"url": self.url, "text": self.text, "key": self.key, "delta_y": self.delta_y}
        wanted = {"navigate": "url", "type": "text", "key": "key", "scroll": "delta_y"}.get(
            self.kind
        )
        if any((value is not None) != (name == wanted) for name, value in fields.items()):
            raise ValueError("action must contain only its required argument")
        if self.kind == "navigate" and self.target_ref != "page":
            raise ValueError("navigate requires the observed page ref")
        return self


class BrowserActionResult(DTO):
    request_id: str
    session_id: str
    execution_status: Literal["executed", "outcome_unknown", "not_executed"]
    business_outcome: Literal["unverified"] = "unverified"
    started_at: datetime
    finished_at: datetime
    observation: BrowserObservation | None
    error_code: str | None
    message: str
    requires_observe: bool


class BrowserStop(DTO):
    session_id: str
    status: Literal["stopped", "stopping"]
    interrupted_action: bool
    owned_resources_closed: bool
    external_effects_reverted: Literal[False] = False


class BrowserCapability(DTO):
    status: Literal["unconfigured", "available", "unavailable"]
    reason: str
    playwright_version: str | None
    browser_version: str | None
    enabled: bool
    sessions: int
    network_isolation: Literal["application_proxy_not_os_sandbox"] = (
        "application_proxy_not_os_sandbox"
    )


class CreateEnvelope(DTO):
    owner: BrowserOwner
    command: BrowserCreate


class ObserveEnvelope(DTO):
    owner: BrowserOwner
    command: BrowserObserve


class ActionEnvelope(DTO):
    owner: BrowserOwner
    command: BrowserAction


class BrowserDownload(DTO):
    request_id: UUID
    last_observation: UUID
    target_ref: str = Field(pattern=r"^e[0-9]{1,4}$")
    output_dir: str = Field(min_length=1, max_length=4096)


class BrowserDownloaded(DTO):
    request_id: str
    session_id: str
    generation: int
    last_observation: str
    status: Literal["downloaded"]
    candidate_path: str
    sha256: str
    bytes: int
    mime: str
    mime_source: Literal["response_header", "fallback"]
    filename: str
    source_url: str
    final_url: str
    started_at: datetime
    finished_at: datetime
    cookies_inherited: Literal[False] = False
    business_outcome: Literal["unverified"] = "unverified"


class DownloadEnvelope(DTO):
    owner: BrowserOwner
    command: BrowserDownload
