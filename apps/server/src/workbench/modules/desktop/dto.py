"""Native desktop contracts. Grant, owner, lease and staging are trusted inputs."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DesktopOwner(DTO):
    user_id: UUID
    workspace_id: UUID
    run_id: UUID | None = None


class DesktopGrant(DTO):
    grant_id: UUID
    capture: bool = False
    control: bool = False
    target_window_id: str | None = Field(default=None, max_length=80)
    target_window_pid: int | None = Field(default=None, ge=1)
    expires_at: datetime


class DesktopLease(DTO):
    lease_id: UUID
    grant_id: UUID
    epoch: int = Field(ge=1)
    expires_at: datetime


class DesktopCreate(DTO):
    request_id: UUID
    mode: Literal["observe", "control"] = "observe"
    target_window_id: str | None = Field(default=None, max_length=80)
    ttl_seconds: int = Field(default=300, ge=10, le=900)
    manual_takeover_ack: bool = False


class DesktopFeature(DTO):
    status: Literal[
        "available", "unavailable", "needs_permission", "unsupported", "waiting_verification"
    ]
    reason: str


class DesktopCapability(DTO):
    node_id: str
    platform: str
    architecture: str
    session_type: str
    driver: str
    capture: DesktopFeature
    input: DesktopFeature
    window_identity: DesktopFeature
    human_input_detection: DesktopFeature
    os_lock_state: bool | None
    explicit_takeover_required: Literal[True] = True
    clipboard_enabled: Literal[False] = False
    file_transfer_enabled: Literal[False] = False
    platform_verified: bool
    limitations: list[str]


class DesktopBounds(DTO):
    x: int
    y: int
    width: int
    height: int


class DesktopDisplay(DTO):
    display_id: str
    bounds: DesktopBounds
    coordinate_space: Literal["root_pixels", "physical_pixels", "logical_points"]
    scale_x: float | None
    scale_y: float | None


class DesktopSession(DTO):
    session_id: str
    node_id: str
    generation: int
    mode: Literal["observe", "control"]
    status: Literal["active", "taken_over", "stopping", "stopped", "expired"]
    target_window_id: str | None
    created_at: datetime
    expires_at: datetime


class DesktopObserve(DTO):
    output_dir: str | None = Field(default=None, min_length=1, max_length=4096)


class DesktopObservation(DTO):
    node_id: str
    session_id: str
    observation_id: str
    generation: int
    geometry_id: str
    captured_at: datetime
    displays: list[DesktopDisplay]
    window_id: str | None
    window_pid: int | None
    bounds: DesktopBounds
    screenshot_path: str
    content_hash: str
    byte_size: int
    image_width: int
    image_height: int
    logical_width: int
    logical_height: int
    pixel_ratio_x: float
    pixel_ratio_y: float
    transform: Literal["identity_crop_offset"] = "identity_crop_offset"
    accessibility_tree_available: Literal[False] = False
    locked: bool | None


class DesktopAction(DTO):
    request_id: UUID
    session_id: UUID
    last_observation: UUID
    generation: int = Field(ge=1)
    geometry_id: str = Field(min_length=64, max_length=64)
    target_window_id: str = Field(min_length=1, max_length=80)
    kind: Literal["click", "type", "key", "scroll"]
    x: int | None = Field(default=None, ge=0, le=32768)
    y: int | None = Field(default=None, ge=0, le=32768)
    text: str | None = Field(default=None, max_length=256)
    key: (
        Literal[
            "Enter",
            "Tab",
            "Escape",
            "Backspace",
            "Delete",
            "ArrowUp",
            "ArrowDown",
            "ArrowLeft",
            "ArrowRight",
            "Home",
            "End",
            "PageUp",
            "PageDown",
            "Space",
        ]
        | None
    ) = None
    scroll_steps: int | None = Field(default=None, ge=-10, le=10)

    @model_validator(mode="after")
    def arguments(self):
        expected = {
            "click": {"x", "y"},
            "scroll": {"x", "y", "scroll_steps"},
            "type": {"text"},
            "key": {"key"},
        }[self.kind]
        values = {
            "x": self.x,
            "y": self.y,
            "text": self.text,
            "key": self.key,
            "scroll_steps": self.scroll_steps,
        }
        if any((value is not None) != (name in expected) for name, value in values.items()):
            raise ValueError("only the selected native action arguments are allowed")
        return self


class DesktopActionResult(DTO):
    request_id: str
    session_id: str
    execution_status: Literal["executed", "not_executed", "outcome_unknown"]
    business_outcome: Literal["unverified"] = "unverified"
    started_at: datetime
    finished_at: datetime
    error_code: str | None
    message: str
    requires_observe: Literal[True] = True


class DesktopStop(DTO):
    session_id: str
    status: Literal["taken_over", "stopping", "stopped"]
    generation: int
    pending_native_call: bool
    owned_inputs_released: bool
    external_effects_reverted: Literal[False] = False
