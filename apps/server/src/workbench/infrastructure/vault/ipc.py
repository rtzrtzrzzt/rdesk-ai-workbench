"""Local authentication and framing shared by broker and synchronous clients."""

import json
import os
import socket
import struct
import sys
import time

from workbench.infrastructure.vault.errors import VaultError

MAX_FRAME = 131072


def peer_identity(connection: socket.socket, *, expected_pid: int | None = None) -> None:
    if os.name == "nt":
        return  # Windows uses a per-instance token protected by native owner-only ACL.
    if sys.platform.startswith("linux"):
        pid, uid, _ = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        if uid != os.geteuid() or expected_pid is not None and pid != expected_pid:
            raise VaultError("PEER_REJECTED", "vault IPC对端不是预期的当前账户进程。")
    elif sys.platform == "darwin":
        import ctypes

        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.getpeereid(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
            raise VaultError("PEER_REJECTED", "无法核验vault IPC账户。")
        if uid.value != os.geteuid():
            raise VaultError("PEER_REJECTED", "vault IPC对端账户不匹配。")
    else:
        raise VaultError("IPC_UNSUPPORTED", "当前平台缺少已实现的本地IPC身份核验。")


def receive_frame(connection: socket.socket, *, deadline: float | None = None) -> dict:
    deadline = deadline or time.monotonic() + 10
    data = bytearray()
    while b"\n" not in data:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        connection.settimeout(remaining)
        chunk = connection.recv(min(4096, MAX_FRAME + 1 - len(data)))
        if not chunk:
            raise VaultError("INVALID_FRAME", "vault IPC消息未完成。")
        data.extend(chunk)
        if len(data) > MAX_FRAME:
            raise VaultError("FRAME_TOO_LARGE", "vault IPC请求超出大小上限。")
    first, trailing = bytes(data).split(b"\n", 1)
    if trailing:
        raise VaultError("INVALID_FRAME", "每次vault连接仅允许一条消息。")
    try:
        value = json.loads(first)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, UnicodeError):
        raise VaultError("INVALID_FRAME", "vault IPC消息格式无效。") from None


def send_frame(connection: socket.socket, value: dict) -> None:
    data = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
    if len(data) > MAX_FRAME:
        raise VaultError("FRAME_TOO_LARGE", "vault IPC响应超出大小上限。")
    connection.sendall(data)
