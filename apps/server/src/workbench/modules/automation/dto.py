from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from workbench.contracts.base import PublicResponse
from workbench.contracts.requests import Command
from workbench.modules.browser.dto import BrowserAction
from workbench.modules.desktop.dto import DesktopAction


class ComputerSettingsView(PublicResponse):
    enabled: bool
    version: int


class ComputerSettingsSave(Command):
    enabled: bool = Field(strict=True)
    expected_version: int = Field(ge=0)
    request_id: UUID


class AutomationConfiguration(Command):
    browser_enabled: bool = False
    browser_origins: list[str] = Field(default_factory=list, max_length=24)
    browser_local_origins: list[str] = Field(default_factory=list, max_length=4)
    browser_public_network: bool = False
    desktop_capture: bool = False
    desktop_control: bool = False
    desktop_all_windows: bool = False
    target_window_id: str | None = Field(default=None, min_length=1, max_length=80)
    target_window_pid: int | None = Field(default=None, ge=1)
    manual_takeover_ack: bool = False
    remote_model_revisions: list[str] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def control_scope(self):
        if self.desktop_control and not (
            self.desktop_capture
            and (self.desktop_all_windows or self.target_window_id and self.target_window_pid)
            and self.manual_takeover_ack
        ):
            raise ValueError("桌面控制须授权捕获、精确窗口/PID与明确接管约定")
        return self


class AutomationSave(Command):
    expected_version: int = Field(ge=0)
    configuration: AutomationConfiguration
    grant_ttl_seconds: int = Field(default=300, ge=10, le=900)
    request_id: UUID


class PolicyView(PublicResponse):
    id: str | None
    version: int
    configuration: AutomationConfiguration
    grant_expires_at: datetime | None


class SessionCreate(Command):
    kind: Literal["browser", "desktop"]
    mode: Literal["observe", "control"] = "observe"
    ttl_seconds: int = Field(default=300, ge=30, le=900)
    request_id: UUID

    @model_validator(mode="after")
    def browser_mode(self):
        if self.kind == "browser" and self.mode != "observe":
            raise ValueError("浏览器使用observe会话，通过逐次授权的动作执行")
        return self


class Observe(Command):
    screenshot: bool = False
    request_id: UUID


class SessionView(PublicResponse):
    id: str
    kind: str
    mode: str
    run_id: str | None
    status: str
    policy_version: int
    expires_at: datetime
    native_view: dict
    observation: dict | None
    error: dict | None


class ActionView(PublicResponse):
    id: str
    session_id: str
    kind: str
    status: str
    result: dict | None


class BrowserCommand(Command):
    action: BrowserAction


class BrowserDownload(Command):
    request_id: UUID
    last_observation: UUID
    target_ref: str = Field(pattern=r"^e[0-9]{1,4}$")


class DesktopCommand(Command):
    action: DesktopAction


class RuntimeCommand(Command):
    request_id: UUID


class RuntimeView(PublicResponse):
    status: Literal["running", "stopped", "unknown", "unavailable"]
    reason: str
    instance_id: str | None = None


class AutomationCapabilities(PublicResponse):
    runtime: RuntimeView
    browser: dict | None
    desktop: dict | None
    foreground: dict | None
    remote_models: list[dict]
    limitations: list[str]
