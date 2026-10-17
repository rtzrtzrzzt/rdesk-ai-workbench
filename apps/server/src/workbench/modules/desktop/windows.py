"""Windows User32/Pillow adapter. No elevation, secure-desktop switching or clipboard."""

import ctypes as C
import hashlib
import os
from contextlib import contextmanager
from ctypes import wintypes as W

from PIL import ImageGrab

from workbench.modules.desktop.dto import DesktopBounds, DesktopDisplay
from workbench.modules.desktop.native import NativeState, bind, failure


class WindowsDriver:
    name = "windows-user32-sendinput"
    platform_verified = False
    input_supported = True
    inputs_released = True

    def __init__(self):
        if os.name != "nt":
            raise failure(
                "desktop_platform_unavailable", "Windows 原生驱动仅可在目标 Windows 交互会话使用。"
            )
        self.u = C.WinDLL("user32", use_last_error=True)
        self.pending_releases = []
        bind(self.u, "GetForegroundWindow", W.HWND)
        bind(self.u, "GetWindowThreadProcessId", W.DWORD, W.HWND, C.POINTER(W.DWORD))
        bind(self.u, "GetWindowRect", W.BOOL, W.HWND, C.POINTER(W.RECT))
        bind(self.u, "GetCursorPos", W.BOOL, C.POINTER(W.POINT))
        bind(self.u, "SetCursorPos", W.BOOL, C.c_int, C.c_int)
        bind(self.u, "GetAsyncKeyState", C.c_short, C.c_int)
        bind(self.u, "OpenInputDesktop", W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
        bind(self.u, "CloseDesktop", W.BOOL, W.HANDLE)
        bind(
            self.u,
            "GetUserObjectInformationW",
            W.BOOL,
            W.HANDLE,
            C.c_int,
            P := C.c_void_p,
            W.DWORD,
            C.POINTER(W.DWORD),
        )
        bind(self.u, "SetThreadDpiAwarenessContext", W.HANDLE, W.HANDLE)
        self.pointer = C.c_size_t

        class Mouse(C.Structure):
            _fields_ = [
                ("dx", W.LONG),
                ("dy", W.LONG),
                ("mouseData", W.DWORD),
                ("dwFlags", W.DWORD),
                ("time", W.DWORD),
                ("extra", self.pointer),
            ]

        class Keyboard(C.Structure):
            _fields_ = [
                ("wVk", W.WORD),
                ("wScan", W.WORD),
                ("dwFlags", W.DWORD),
                ("time", W.DWORD),
                ("extra", self.pointer),
            ]

        class Data(C.Union):
            _fields_ = [("mi", Mouse), ("ki", Keyboard)]

        class Input(C.Structure):
            _fields_ = [("type", W.DWORD), ("data", Data)]

        self.Input, self.Keyboard, self.Mouse = Input, Keyboard, Mouse
        bind(self.u, "SendInput", W.UINT, W.UINT, C.POINTER(Input), C.c_int)
        self.P = P

    @contextmanager
    def dpi(self):
        old = self.u.SetThreadDpiAwarenessContext(C.c_void_p(-4))
        if not old:
            raise failure("desktop_dpi_unavailable", "不能建立物理像素DPI坐标上下文。")
        try:
            yield
        finally:
            self.u.SetThreadDpiAwarenessContext(old)

    def snapshot(self):
        with self.dpi():
            hwnd = self.u.GetForegroundWindow()
            if not hwnd:
                raise failure(
                    "desktop_window_unavailable",
                    "没有可访问的交互前台窗口，可能位于锁屏或安全桌面。",
                    "permission",
                )
            rect = W.RECT()
            pid = W.DWORD()
            point = W.POINT()
            if not self.u.GetWindowRect(hwnd, C.byref(rect)) or not self.u.GetCursorPos(
                C.byref(point)
            ):
                raise failure(
                    "desktop_geometry_unavailable", "窗口几何或指针读取失败。", "permission"
                )
            self.u.GetWindowThreadProcessId(hwnd, C.byref(pid))
            bounds = DesktopBounds(
                x=rect.left, y=rect.top, width=rect.right - rect.left, height=rect.bottom - rect.top
            )
            x, y, w, h = (self.u.GetSystemMetrics(i) for i in (76, 77, 78, 79))
            display = DesktopDisplay(
                display_id="windows-virtual-screen",
                bounds=DesktopBounds(x=x, y=y, width=w, height=h),
                coordinate_space="physical_pixels",
                scale_x=1,
                scale_y=1,
            )
            keys = bytes(1 if self.u.GetAsyncKeyState(i) & 0x8000 else 0 for i in range(256))
            held = "clear" if not any(keys) else hashlib.sha256(keys).hexdigest()
            desktop = self.u.OpenInputDesktop(0, False, 1)
            locked = None
            if desktop:
                try:
                    name = C.create_unicode_buffer(256)
                    needed = W.DWORD()
                    if self.u.GetUserObjectInformationW(
                        desktop, 2, name, C.sizeof(name), C.byref(needed)
                    ):
                        locked = False if name.value.lower() == "default" else True
                finally:
                    self.u.CloseDesktop(desktop)
            return NativeState(
                (display,),
                bounds,
                hex(hwnd),
                pid.value,
                (point.x, point.y),
                held,
                locked,
                "windows-current-interactive-desktop",
            )

    def capture(self, state):
        b = state.bounds
        with self.dpi():
            return ImageGrab.grab(bbox=(b.x, b.y, b.x + b.width, b.y + b.height), all_screens=True)

    def perform(self, action, state, cancelled, guard):
        def check():
            if cancelled.is_set():
                raise failure("desktop_action_cancelled", "原生输入已请求停止。", "cancelled")
            guard(effect=True)

        def send(inputs):
            data = (self.Input * len(inputs))(*inputs)
            if self.u.SendInput(len(data), data, C.sizeof(self.Input)) != len(data):
                raise failure(
                    "desktop_sendinput_unknown",
                    "SendInput 未确认全部输入；可能是UIPI/权限或会话变化，不会提权重试。",
                    "outcome_unknown",
                )

        def keypair(vk=0, scan=0, flags=0):
            check()
            down = self.Input(type=1)
            up = self.Input(type=1)
            down.data.ki = self.Keyboard(vk, scan, flags, 0, 0)
            up.data.ki = self.Keyboard(vk, scan, flags | 2, 0, 0)
            self.pending_releases = [up]
            self.inputs_released = False
            send([down, up])
            self.pending_releases = []
            self.inputs_released = True

        with self.dpi():
            if action.kind in {"click", "scroll"}:
                check()
                if not self.u.SetCursorPos(state.bounds.x + action.x, state.bounds.y + action.y):
                    raise failure("desktop_pointer_failed", "原生指针定位失败。")
                guard((state.bounds.x + action.x, state.bounds.y + action.y))
                if action.kind == "click":
                    down = self.Input(type=0)
                    up = self.Input(type=0)
                    down.data.mi = self.Mouse(0, 0, 0, 2, 0, 0)
                    up.data.mi = self.Mouse(0, 0, 0, 4, 0, 0)
                    self.pending_releases = [up]
                    self.inputs_released = False
                    send([down, up])
                    self.pending_releases = []
                    self.inputs_released = True
                else:
                    check()
                    wheel = self.Input(type=0)
                    wheel.data.mi = self.Mouse(0, 0, action.scroll_steps * 120, 0x800, 0, 0)
                    send([wheel])
            elif action.kind == "type":
                data = action.text.encode("utf-16-le")
                for i in range(0, len(data), 2):
                    keypair(scan=int.from_bytes(data[i : i + 2], "little"), flags=4)
            else:
                keys = {
                    "Enter": 13,
                    "Tab": 9,
                    "Escape": 27,
                    "Backspace": 8,
                    "Delete": 46,
                    "ArrowUp": 38,
                    "ArrowDown": 40,
                    "ArrowLeft": 37,
                    "ArrowRight": 39,
                    "Home": 36,
                    "End": 35,
                    "PageUp": 33,
                    "PageDown": 34,
                    "Space": 32,
                }
                keypair(vk=keys[action.key])

    def release_owned(self):
        if not self.pending_releases:
            return True
        data = (self.Input * len(self.pending_releases))(*self.pending_releases)
        if self.u.SendInput(len(data), data, C.sizeof(self.Input)) != len(data):
            return False
        self.pending_releases = []
        self.inputs_released = True
        return True
