"""Owned local vault broker. CLI arguments contain paths/policy only, never credentials."""

import argparse
import hmac
import os
import secrets
import signal
import socketserver
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from workbench.infrastructure.vault.engine import VaultEngine
from workbench.infrastructure.vault.errors import VaultError
from workbench.infrastructure.vault.ipc import (
    peer_identity,
    receive_frame,
    send_frame,
    unix_socket_address,
)
from workbench.infrastructure.vault.private import (
    check_private,
    private_directory,
    read_json,
    write_json,
    write_private,
)


def process_start(pid: int) -> str | None:
    if os.name != "nt" and Path(f"/proc/{pid}/stat").exists():
        try:
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        except OSError:
            return None
    return None


@contextmanager
def broker_lock(directory: Path):
    path = directory / "broker.lock"
    if path.exists():
        check_private(path)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError:
        raise VaultError("BROKER_ALREADY_RUNNING", "该目录已有vault broker持有锁。") from None
    finally:
        os.close(descriptor)


class _ThreadedServer(socketserver.ThreadingMixIn):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        super().process_request(request, address)

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, request, address):
        # Never let socketserver print a request/password-bearing traceback.
        return


if hasattr(socketserver, "UnixStreamServer"):

    class _UnixServer(_ThreadedServer, socketserver.UnixStreamServer):
        pass
else:
    _UnixServer = None


class _TCPServer(_ThreadedServer, socketserver.TCPServer):
    pass


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        broker = self.server.broker
        deadline = time.monotonic() + broker.request_timeout_seconds
        self.request.settimeout(broker.request_timeout_seconds)
        try:
            peer_identity(self.request)
            request = receive_frame(self.request, deadline=deadline)
            result = broker.dispatch(request)
            response = {"ok": True, "result": result, "instance_id": broker.instance_id}
        except VaultError as error:
            response = {
                "ok": False,
                "error": {"code": error.code, "message": error.message},
                "instance_id": broker.instance_id,
            }
        except TimeoutError:
            response = {
                "ok": False,
                "error": {"code": "BROKER_TIMEOUT", "message": "vault IPC操作超时。"},
                "instance_id": broker.instance_id,
            }
        except Exception:
            response = {
                "ok": False,
                "error": {"code": "BROKER_FAILED", "message": "vault操作未完成。"},
                "instance_id": broker.instance_id,
            }
        try:
            self.request.settimeout(1)
            send_frame(self.request, response)
        except Exception:
            pass


class VaultBroker:
    def __init__(
        self, data_dir: Path, *, ttl_seconds: float = 900, request_timeout_seconds: float = 10
    ):
        if not 0.1 <= request_timeout_seconds <= 60:
            raise ValueError("request timeout must be between 0.1 and 60")
        self.data_dir = Path(os.path.abspath(data_dir))
        self.ttl_seconds = ttl_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.instance_id = uuid.uuid4().hex
        self._admin_token = secrets.token_urlsafe(32)
        self._transport_token = secrets.token_urlsafe(32) if os.name == "nt" else None
        self.stopping = threading.Event()
        self.engine = None

    def dispatch(self, request: dict):
        if set(request) - {
            "operation",
            "arguments",
            "instance_id",
            "admin_token",
            "transport_token",
        }:
            raise VaultError("INVALID_REQUEST", "vault请求包含未支持字段。")
        if request.get("instance_id") != self.instance_id:
            raise VaultError("INSTANCE_CHANGED", "vault broker实例已变化。")
        if os.name == "nt" and not hmac.compare_digest(
            str(request.get("transport_token", "")), self._transport_token
        ):
            raise VaultError("PEER_REJECTED", "vault IPC认证失败。")
        operation, arguments = request.get("operation"), request.get("arguments", {})
        if not isinstance(arguments, dict):
            raise VaultError("INVALID_REQUEST", "vault参数必须为对象。")
        fields = {
            "status": set(),
            "encrypt": {"value"},
            "decrypt": {"ciphertext"},
            "initialize": {"mode", "passphrase"},
            "unlock": {"passphrase"},
            "lock": set(),
            "stop": set(),
        }
        if operation not in fields or set(arguments) != fields[operation]:
            raise VaultError("INVALID_REQUEST", "vault操作或参数无效。")
        if operation in {"initialize", "unlock", "lock", "stop"}:
            if not hmac.compare_digest(str(request.get("admin_token", "")), self._admin_token):
                raise VaultError("ADMIN_REQUIRED", "该vault操作只允许已认证的owner管理入口调用。")
        if operation == "status":
            return self.engine.status()
        if operation == "encrypt":
            return {"ciphertext": self.engine.encrypt(arguments["value"])}
        if operation == "decrypt":
            return {"value": self.engine.decrypt(arguments["ciphertext"])}
        if operation == "initialize":
            return self.engine.initialize(arguments["mode"], arguments["passphrase"])
        if operation == "unlock":
            return self.engine.unlock(arguments["passphrase"])
        if operation == "lock":
            return self.engine.lock()
        self.stopping.set()
        return {"stopping": True}

    def serve(self, *, cleanup_stale: bool = False):
        private_directory(self.data_dir, create=True)
        directory = private_directory(self.data_dir / "vault", create=True)
        with broker_lock(directory):
            descriptor_path = directory / "broker.json"
            socket_path = directory / "broker.sock"
            if descriptor_path.exists() or socket_path.exists():
                if not cleanup_stale:
                    raise VaultError(
                        "STALE_BROKER_STATE", "broker状态已存在；需显式核查清理，拒绝覆盖。"
                    )
                self._cleanup_stale(directory)
            self.engine = VaultEngine(self.data_dir, ttl_seconds=self.ttl_seconds)
            server = None
            created = []
            try:
                if os.name == "nt":
                    server = _TCPServer(("127.0.0.1", 0), _Handler)
                    address = {
                        "transport": "loopback",
                        "host": "127.0.0.1",
                        "port": server.server_address[1],
                    }
                    write_private(directory / "transport.token", self._transport_token.encode())
                    created.append(directory / "transport.token")
                else:
                    with unix_socket_address(socket_path) as address:
                        server = _UnixServer(address, _Handler)
                    os.chmod(socket_path, 0o600)
                    created.append(socket_path)
                    address = {"transport": "uds", "socket": str(socket_path)}
                server.broker, server.slots = self, threading.BoundedSemaphore(8)
                server.timeout = 0.25
                write_private(directory / "admin.token", self._admin_token.encode())
                created.append(directory / "admin.token")
                descriptor = {
                    **address,
                    "instance_id": self.instance_id,
                    "pid": os.getpid(),
                    "process_start": process_start(os.getpid()),
                }
                write_json(descriptor_path, descriptor)
                created.append(descriptor_path)
                print('{"broker_ready":true}', flush=True)
                while not self.stopping.is_set():
                    server.handle_request()
                    self.engine.poll_expiry()
            finally:
                self.engine.shutdown()
                if server:
                    server.server_close()
                for path in reversed(created):
                    if path.exists():
                        check_private(path)
                        path.unlink()
                self._admin_token = self._transport_token = None

    @staticmethod
    def _cleanup_stale(directory: Path):
        descriptor_path = directory / "broker.json"
        if not descriptor_path.exists():
            raise VaultError("STALE_IDENTITY_MISSING", "缺少原broker身份，不能自动删除socket。")
        descriptor = read_json(descriptor_path)
        pid = descriptor.get("pid")
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        except (TypeError, PermissionError):
            raise VaultError("STALE_IDENTITY_UNKNOWN", "无法确认旧broker已退出。") from None
        else:
            start = process_start(pid)
            if start is None or start == descriptor.get("process_start"):
                raise VaultError("BROKER_ALREADY_RUNNING", "原broker进程仍存在，拒绝清理。")
        for name in ("broker.json", "broker.sock", "admin.token", "transport.token"):
            path = directory / name
            if path.exists():
                check_private(path)
                mode = path.lstat().st_mode
                if not (stat.S_ISREG(mode) or name == "broker.sock" and stat.S_ISSOCK(mode)):
                    raise VaultError("INVALID_PRIVATE_FILE", "旧broker文件类型异常，拒绝清理。")
                path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--ttl-seconds", type=float, default=900)
    parser.add_argument("--request-timeout-seconds", type=float, default=10)
    parser.add_argument("--cleanup-stale", action="store_true")
    args = parser.parse_args()
    broker = VaultBroker(
        args.data_dir,
        ttl_seconds=args.ttl_seconds,
        request_timeout_seconds=args.request_timeout_seconds,
    )
    signal.signal(signal.SIGTERM, lambda *_: broker.stopping.set())
    signal.signal(signal.SIGINT, lambda *_: broker.stopping.set())
    try:
        broker.serve(cleanup_stale=args.cleanup_stale)
    except VaultError as error:
        print(error.code, flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
