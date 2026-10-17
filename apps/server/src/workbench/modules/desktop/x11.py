"""X11/XTEST adapter for an actual X11 session, never a Wayland fallback."""

import ctypes as C
import ctypes.util
import hashlib
import os
import re
import subprocess
import threading
from contextlib import contextmanager

from PIL import ImageGrab, features

from workbench.modules.desktop.dto import DesktopBounds, DesktopDisplay
from workbench.modules.desktop.native import NativeState, bind, failure

P = C.c_void_p
U = C.c_ulong
INT = C.c_int
LOCK = threading.RLock()
ERRORS = threading.local()


class XError(C.Structure):
    _fields_ = [
        ("type", INT),
        ("display", P),
        ("resourceid", U),
        ("serial", U),
        ("error_code", C.c_ubyte),
        ("request_code", C.c_ubyte),
        ("minor_code", C.c_ubyte),
    ]


@C.CFUNCTYPE(INT, P, C.POINTER(XError))
def _error_handler(display, event):
    ERRORS.code = event.contents.error_code
    return 0


def lock_state():
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return None
    try:
        result = subprocess.run(
            [
                "/usr/bin/gdbus",
                "call",
                "--session",
                "--dest",
                "org.gnome.ScreenSaver",
                "--object-path",
                "/org/gnome/ScreenSaver",
                "--method",
                "org.gnome.ScreenSaver.GetActive",
            ],
            capture_output=True,
            timeout=1,
            check=False,
        )
        if result.returncode == 0:
            value = result.stdout.strip()
            return True if value == b"(true,)" else False if value == b"(false,)" else None
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


class X11Driver:
    name = "x11-xcb-xtest"
    platform_verified = False
    input_supported = False
    inputs_released = True

    def __init__(self):
        self.display_name = os.environ.get("DISPLAY", "")
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or os.environ.get(
            "WAYLAND_DISPLAY"
        ):
            raise failure(
                "desktop_portal_required",
                "Wayland 需要 Portal ScreenCast/RemoteDesktop 及用户授权；"
                "不会退到 XWayland 系统输入。",
                "permission",
            )
        if not self.display_name or not features.check_feature("xcb"):
            raise failure("desktop_x11_unavailable", "没有可用的 X11 会话或 Pillow XCB 捕获。")
        path = ctypes.util.find_library("X11")
        if not path:
            raise failure("desktop_x11_unavailable", "系统 libX11 不可用。")
        self.x = C.CDLL(path)
        self.pending_keys = set()
        self.pending_buttons = set()
        if not bind(self.x, "XInitThreads", INT)():
            raise failure("desktop_x11_threads_unavailable", "Xlib线程支持初始化失败。")
        bind(self.x, "XSetErrorHandler", P, P)(C.cast(_error_handler, P))
        for name, result, arguments in [
            ("XOpenDisplay", P, [C.c_char_p]),
            ("XCloseDisplay", INT, [P]),
            ("XDefaultRootWindow", U, [P]),
            ("XDefaultScreen", INT, [P]),
            ("XDisplayWidth", INT, [P, INT]),
            ("XDisplayHeight", INT, [P, INT]),
            ("XGetInputFocus", INT, [P, C.POINTER(U), C.POINTER(INT)]),
            ("XInternAtom", U, [P, C.c_char_p, INT]),
            (
                "XGetWindowProperty",
                INT,
                [
                    P,
                    U,
                    U,
                    C.c_long,
                    C.c_long,
                    INT,
                    U,
                    C.POINTER(U),
                    C.POINTER(INT),
                    C.POINTER(U),
                    C.POINTER(U),
                    C.POINTER(P),
                ],
            ),
            (
                "XGetGeometry",
                INT,
                [
                    P,
                    U,
                    C.POINTER(U),
                    C.POINTER(INT),
                    C.POINTER(INT),
                    C.POINTER(C.c_uint),
                    C.POINTER(C.c_uint),
                    C.POINTER(C.c_uint),
                    C.POINTER(C.c_uint),
                ],
            ),
            (
                "XTranslateCoordinates",
                INT,
                [P, U, U, INT, INT, C.POINTER(INT), C.POINTER(INT), C.POINTER(U)],
            ),
            (
                "XQueryPointer",
                INT,
                [
                    P,
                    U,
                    C.POINTER(U),
                    C.POINTER(U),
                    C.POINTER(INT),
                    C.POINTER(INT),
                    C.POINTER(INT),
                    C.POINTER(INT),
                    C.POINTER(C.c_uint),
                ],
            ),
            ("XQueryKeymap", INT, [P, P]),
            ("XFree", INT, [P]),
            ("XSync", INT, [P, INT]),
            ("XKeysymToKeycode", C.c_ubyte, [P, U]),
            ("XKeycodeToKeysym", U, [P, C.c_ubyte, INT]),
        ]:
            bind(self.x, name, result, *arguments)
        xtst = ctypes.util.find_library("Xtst")
        self.t = C.CDLL(xtst) if xtst else None
        if self.t:
            bind(
                self.t,
                "XTestQueryExtension",
                INT,
                P,
                C.POINTER(INT),
                C.POINTER(INT),
                C.POINTER(INT),
                C.POINTER(INT),
            )
            bind(self.t, "XTestFakeKeyEvent", INT, P, C.c_uint, INT, U)
            bind(self.t, "XTestFakeButtonEvent", INT, P, C.c_uint, INT, U)
            bind(self.t, "XTestFakeMotionEvent", INT, P, INT, INT, INT, U)
        with self.connection() as display:
            values = [INT() for _ in range(4)]
            self.input_supported = bool(
                self.t and self.t.XTestQueryExtension(display, *(C.byref(v) for v in values))
            )

    @contextmanager
    def connection(self):
        with LOCK:
            ERRORS.code = 0
            display = self.x.XOpenDisplay(self.display_name.encode())
            if not display:
                raise failure(
                    "desktop_x11_connection", "不能连接当前用户的 X11 会话。", "permission"
                )
            try:
                yield display
                self.x.XSync(display, 0)
                if getattr(ERRORS, "code", 0):
                    raise failure(
                        "desktop_x11_state_changed", "X11 对象或权限发生变化；需要重新观察。"
                    )
            finally:
                self.x.XCloseDisplay(display)

    def cardinal(self, display, window, name):
        atom = self.x.XInternAtom(display, name.encode(), 1)
        if not atom:
            return None
        actual, count, remaining, value, fmt = U(), U(), U(), P(), INT()
        result = self.x.XGetWindowProperty(
            display,
            window,
            atom,
            0,
            1,
            0,
            0,
            C.byref(actual),
            C.byref(fmt),
            C.byref(count),
            C.byref(remaining),
            C.byref(value),
        )
        try:
            if result == 0 and fmt.value == 32 and count.value == 1 and value:
                return C.cast(value, C.POINTER(U))[0]
            return None
        finally:
            if value:
                self.x.XFree(value)

    def snapshot(self):
        with self.connection() as d:
            root = self.x.XDefaultRootWindow(d)
            screen = self.x.XDefaultScreen(d)
            width, height = self.x.XDisplayWidth(d, screen), self.x.XDisplayHeight(d, screen)
            window = self.cardinal(d, root, "_NET_ACTIVE_WINDOW")
            if not window:
                focus, revert = U(), INT()
                self.x.XGetInputFocus(d, C.byref(focus), C.byref(revert))
                window = focus.value if focus.value not in {0, 1, root} else None
            bounds = DesktopBounds(x=0, y=0, width=width, height=height)
            pid = None
            if window:
                parent, x, y, w, h, border, depth = (
                    U(),
                    INT(),
                    INT(),
                    C.c_uint(),
                    C.c_uint(),
                    C.c_uint(),
                    C.c_uint(),
                )
                if not self.x.XGetGeometry(
                    d,
                    window,
                    C.byref(parent),
                    C.byref(x),
                    C.byref(y),
                    C.byref(w),
                    C.byref(h),
                    C.byref(border),
                    C.byref(depth),
                ):
                    raise failure("desktop_window_changed", "前台窗口几何已失效。")
                child = U()
                if not self.x.XTranslateCoordinates(
                    d, window, root, 0, 0, C.byref(x), C.byref(y), C.byref(child)
                ):
                    raise failure("desktop_window_changed", "前台窗口坐标映射不可用。")
                left, top, right, bottom = (
                    max(0, x.value),
                    max(0, y.value),
                    min(width, x.value + w.value),
                    min(height, y.value + h.value),
                )
                if right <= left or bottom <= top:
                    raise failure("desktop_window_hidden", "前台窗口不在当前可见屏幕内。")
                bounds = DesktopBounds(x=left, y=top, width=right - left, height=bottom - top)
                pid = self.cardinal(d, window, "_NET_WM_PID")
            returned_root, child, px, py, wx, wy, mask = (
                U(),
                U(),
                INT(),
                INT(),
                INT(),
                INT(),
                C.c_uint(),
            )
            if not self.x.XQueryPointer(
                d,
                root,
                C.byref(returned_root),
                C.byref(child),
                C.byref(px),
                C.byref(py),
                C.byref(wx),
                C.byref(wy),
                C.byref(mask),
            ):
                raise failure("desktop_pointer_unavailable", "不能读取当前桌面指针。")
            keys = C.create_string_buffer(32)
            if not self.x.XQueryKeymap(d, keys):
                raise failure("desktop_keys_unavailable", "无法读取当前持有键状态。")
            held = (
                "clear"
                if not any(keys.raw) and mask.value == 0
                else hashlib.sha256(keys.raw + str(mask.value).encode()).hexdigest()
            )
        displays = []
        try:
            monitors = subprocess.run(
                ["/usr/bin/xrandr", "--listmonitors"], capture_output=True, timeout=1, check=False
            )
            if monitors.returncode == 0:
                for line in monitors.stdout.decode("utf-8", "replace").splitlines()[1:33]:
                    match = re.search(r"\s(\d+)/\d+x(\d+)/\d+([+-]\d+)([+-]\d+)\s+(\S+)", line)
                    if match:
                        w, h, x, y, name = match.groups()
                        displays.append(
                            DesktopDisplay(
                                display_id=name[:80],
                                bounds=DesktopBounds(
                                    x=int(x), y=int(y), width=int(w), height=int(h)
                                ),
                                coordinate_space="root_pixels",
                                scale_x=1,
                                scale_y=1,
                            )
                        )
        except (OSError, subprocess.TimeoutExpired):
            pass
        if not displays:
            displays = [
                DesktopDisplay(
                    display_id="x11-root",
                    bounds=DesktopBounds(x=0, y=0, width=width, height=height),
                    coordinate_space="root_pixels",
                    scale_x=1,
                    scale_y=1,
                )
            ]
        return NativeState(
            tuple(displays),
            bounds,
            hex(window) if window else None,
            pid,
            (px.value, py.value),
            held,
            lock_state(),
            self.display_name,
        )

    def capture(self, state):
        b = state.bounds
        return ImageGrab.grab(
            bbox=(b.x, b.y, b.x + b.width, b.y + b.height), xdisplay=self.display_name
        )

    def perform(self, action, state, cancelled, guard):
        if not self.input_supported:
            raise failure("desktop_xtest_unavailable", "当前 X11 服务器不提供 XTEST。")
        if action.kind == "type" and (
            not action.text or any(ord(c) < 32 or ord(c) > 126 for c in action.text)
        ):
            raise failure(
                "desktop_text_unsupported",
                "X11 普通文字输入目前只支持可打印 ASCII；不会改剪贴板或键盘映射。",
                "validation",
            )
        keys = {
            "Enter": 0xFF0D,
            "Tab": 0xFF09,
            "Escape": 0xFF1B,
            "Backspace": 0xFF08,
            "Delete": 0xFFFF,
            "ArrowUp": 0xFF52,
            "ArrowDown": 0xFF54,
            "ArrowLeft": 0xFF51,
            "ArrowRight": 0xFF53,
            "Home": 0xFF50,
            "End": 0xFF57,
            "PageUp": 0xFF55,
            "PageDown": 0xFF56,
            "Space": 0x20,
        }

        def check():
            if cancelled.is_set():
                raise failure("desktop_action_cancelled", "桌面输入已请求停止。", "cancelled")
            guard(effect=True)

        def pair(d, fn, code):
            check()
            pressed = False
            try:
                if not fn(d, code, 1, 0):
                    raise failure("desktop_native_input_failed", "原生输入请求被拒绝。")
                pressed = True
                self.pending_buttons.add(code)
                self.inputs_released = False
            finally:
                if pressed:
                    if not fn(d, code, 0, 0):
                        raise failure(
                            "desktop_native_release_unknown",
                            "原生输入释放未确认，需本机人工核对。",
                            "outcome_unknown",
                        )
                    self.pending_buttons.discard(code)
                    self.inputs_released = not (self.pending_keys or self.pending_buttons)
            self.x.XSync(d, 0)

        with self.connection() as d:
            if action.kind in {"click", "scroll"}:
                check()
                if not self.t.XTestFakeMotionEvent(
                    d, -1, state.bounds.x + action.x, state.bounds.y + action.y, 0
                ):
                    raise failure("desktop_native_input_failed", "原生指针定位被拒绝。")
                self.x.XSync(d, 0)
                # Pointer movement is ours; service guard accepts this new expected
                # pointer while still rejecting focus/geometry/physical key changes.
                guard((state.bounds.x + action.x, state.bounds.y + action.y))
                if action.kind == "click":
                    pair(d, self.t.XTestFakeButtonEvent, 1)
                else:
                    for _ in range(abs(action.scroll_steps)):
                        pair(d, self.t.XTestFakeButtonEvent, 4 if action.scroll_steps > 0 else 5)
                return
            symbols = [keys[action.key]] if action.kind == "key" else [ord(c) for c in action.text]
            # Preflight the whole text before sending anything; no partial output
            # because a character has no mapping in the current keymap.
            mapped = []
            for symbol in symbols:
                code = self.x.XKeysymToKeycode(d, symbol)
                if not code:
                    raise failure(
                        "desktop_key_unsupported", "当前键盘布局没有该键位。", "validation"
                    )
                level = next(
                    (i for i in range(2) if self.x.XKeycodeToKeysym(d, code, i) == symbol), None
                )
                if level is None:
                    raise failure(
                        "desktop_key_unsupported", "该字符需要未开放的键盘布局操作。", "validation"
                    )
                mapped.append((code, level))
            shift = self.x.XKeysymToKeycode(d, 0xFFE1)
            for code, level in mapped:
                check()
                shifted = False
                try:
                    if level:
                        if not shift or not self.t.XTestFakeKeyEvent(d, shift, 1, 0):
                            raise failure("desktop_native_input_failed", "原生修饰键请求被拒绝。")
                        shifted = True
                        self.pending_keys.add(shift)
                        self.inputs_released = False
                    # Do not re-run physical-held-key guard between our shift down
                    # and paired character; release is unconditional and immediate.
                    if not self.t.XTestFakeKeyEvent(d, code, 1, 0):
                        raise failure("desktop_native_input_failed", "原生按键请求被拒绝。")
                    self.pending_keys.add(code)
                    self.inputs_released = False
                    if not self.t.XTestFakeKeyEvent(d, code, 0, 0):
                        raise failure(
                            "desktop_native_release_unknown", "按键释放未确认。", "outcome_unknown"
                        )
                    self.pending_keys.discard(code)
                finally:
                    if shifted and self.t.XTestFakeKeyEvent(d, shift, 0, 0):
                        self.pending_keys.discard(shift)
                    self.x.XSync(d, 0)
                    self.inputs_released = not (self.pending_keys or self.pending_buttons)

    def release_owned(self):
        # Only releases positively tracked input from this driver. No all-keys-up
        # or release of the human's modifiers/buttons.
        if self.inputs_released:
            return True
        with self.connection() as d:
            for key in tuple(self.pending_keys):
                if self.t.XTestFakeKeyEvent(d, key, 0, 0):
                    self.pending_keys.discard(key)
            for button in tuple(self.pending_buttons):
                if self.t.XTestFakeButtonEvent(d, button, 0, 0):
                    self.pending_buttons.discard(button)
            self.x.XSync(d, 0)
        self.inputs_released = not (self.pending_keys or self.pending_buttons)
        return self.inputs_released
