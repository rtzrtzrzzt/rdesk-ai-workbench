"""Narrow native driver protocol; no arbitrary commands, scripts or clipboard."""

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Protocol

from workbench.contracts.errors import AppError
from workbench.modules.desktop.dto import DesktopAction, DesktopBounds, DesktopDisplay


def failure(code, message, kind="dependency"):
    status = {
        "permission": 403,
        "validation": 422,
        "budget": 429,
        "dependency": 503,
        "outcome_unknown": 502,
    }.get(kind, 409)
    if code.endswith("_not_found"):
        status = 404
    return AppError(code, message, status, kind, False)


def bind(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = list(arguments)
    return function


@dataclass(frozen=True)
class NativeState:
    displays: tuple[DesktopDisplay, ...]
    bounds: DesktopBounds
    window_id: str | None
    window_pid: int | None
    pointer: tuple[int, int]
    held_input: str
    locked: bool | None
    native_session: str

    def geometry(self):
        value = {
            "displays": [item.model_dump() for item in self.displays],
            "bounds": self.bounds.model_dump(),
            "window_id": self.window_id,
            "window_pid": self.window_pid,
            "native_session": self.native_session,
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def input_signature(self):
        return self.pointer, self.held_input


class NativeDriver(Protocol):
    name: str
    platform_verified: bool
    input_supported: bool

    def snapshot(self) -> NativeState: ...
    def capture(self, state: NativeState): ...
    def perform(
        self, action: DesktopAction, state: NativeState, cancelled: threading.Event, guard
    ): ...


def choose_driver():
    import platform

    match platform.system():
        case "Linux":
            from workbench.modules.desktop.x11 import X11Driver

            return X11Driver()
        case "Windows":
            from workbench.modules.desktop.windows import WindowsDriver

            return WindowsDriver()
        case "Darwin":
            from workbench.modules.desktop.macos import MacOSDriver

            return MacOSDriver()
        case _:
            raise failure("desktop_platform_unsupported", "此平台没有原生桌面适配器。")
