"""macOS native permission probe and capture. Input remains explicitly unverified.

No TCC prompt is opened automatically and no permission is bypassed. CGEventPost
cannot by itself prove application receipt or identify a secure foreground target.
"""

import ctypes as C
import platform

from PIL import ImageGrab

from workbench.modules.desktop.dto import DesktopBounds, DesktopDisplay
from workbench.modules.desktop.native import NativeState, bind, failure


class Point(C.Structure):
    _fields_ = [("x", C.c_double), ("y", C.c_double)]


class Size(C.Structure):
    _fields_ = [("width", C.c_double), ("height", C.c_double)]


class Rect(C.Structure):
    _fields_ = [("origin", Point), ("size", Size)]


class MacOSDriver:
    name = "macos-coregraphics-tcc"
    platform_verified = False
    input_supported = False

    def __init__(self):
        if platform.system() != "Darwin":
            raise failure("desktop_platform_unavailable", "macOS 驱动需要实际目标设备与TCC权限。")
        self.cg = C.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        self.ax = C.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        self.cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        # Keep the binding helper independent of importing a Linux shared library.
        bind(self.cg, "CGPreflightScreenCaptureAccess", C.c_bool)
        bind(self.ax, "AXIsProcessTrusted", C.c_bool)
        bind(
            self.cg,
            "CGGetActiveDisplayList",
            C.c_int,
            C.c_uint32,
            C.POINTER(C.c_uint32),
            C.POINTER(C.c_uint32),
        )
        bind(self.cg, "CGDisplayBounds", Rect, C.c_uint32)
        bind(self.cg, "CGEventCreate", C.c_void_p, C.c_void_p)
        bind(self.cg, "CGEventGetLocation", Point, C.c_void_p)
        bind(self.cf, "CFRelease", None, C.c_void_p)
        self.screen_permission = bool(self.cg.CGPreflightScreenCaptureAccess())
        self.accessibility_permission = bool(self.ax.AXIsProcessTrusted())

    def snapshot(self):
        self.screen_permission = bool(self.cg.CGPreflightScreenCaptureAccess())
        self.accessibility_permission = bool(self.ax.AXIsProcessTrusted())
        if not self.screen_permission:
            raise failure(
                "desktop_macos_screen_permission",
                "请在macOS系统设置中为实际companion进程授予屏幕录制权限。",
                "permission",
            )
        ids = (C.c_uint32 * 32)()
        count = C.c_uint32()
        if self.cg.CGGetActiveDisplayList(32, ids, C.byref(count)) != 0 or not count.value:
            raise failure("desktop_macos_display_unavailable", "CoreGraphics未返回活动显示器。")
        displays = []
        for display_id in ids[: count.value]:
            r = self.cg.CGDisplayBounds(display_id)
            b = DesktopBounds(
                x=int(r.origin.x),
                y=int(r.origin.y),
                width=int(r.size.width),
                height=int(r.size.height),
            )
            displays.append(
                DesktopDisplay(
                    display_id=str(display_id),
                    bounds=b,
                    coordinate_space="logical_points",
                    scale_x=None,
                    scale_y=None,
                )
            )
        left = min(d.bounds.x for d in displays)
        top = min(d.bounds.y for d in displays)
        right = max(d.bounds.x + d.bounds.width for d in displays)
        bottom = max(d.bounds.y + d.bounds.height for d in displays)
        event = self.cg.CGEventCreate(None)
        if not event:
            raise failure("desktop_macos_event_unavailable", "CoreGraphics会话不可访问。")
        try:
            p = self.cg.CGEventGetLocation(event)
        finally:
            self.cf.CFRelease(event)
        return NativeState(
            tuple(displays),
            DesktopBounds(x=left, y=top, width=right - left, height=bottom - top),
            None,
            None,
            (int(p.x), int(p.y)),
            "unknown",
            None,
            "macos-current-session",
        )

    def capture(self, state):
        b = state.bounds
        return ImageGrab.grab(bbox=(b.x, b.y, b.x + b.width, b.y + b.height))

    def perform(self, action, state, cancelled, guard):
        raise failure(
            "desktop_macos_control_unverified",
            "macOS控制尚缺实际安全前台身份/锁屏/输入验证；不会仅凭AX权限就注入。",
            "permission",
        )
