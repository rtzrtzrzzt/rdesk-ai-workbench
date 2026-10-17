"""Private companion HTTP endpoints. No database, automation authority or process kill.

Bind only from the real companion process with its unique --instance-id argument.
Unix uses a prebound0600 UDS; Windows uses token-authenticated127.0.0.1 HTTP.
The security boundary is the current OS user, not a same-user malicious process.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import math
import os
import secrets
import socket
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import httpx
import psutil

from workbench.infrastructure.native_process import matches
from workbench.infrastructure.vault.errors import VaultError
from workbench.infrastructure.vault.private import (
    check_private,
    private_directory,
    read_private,
    write_json,
)

Name = Literal["browser", "desktop"]
TOKEN_HEADER = "X-Workbench-Companion-Token"
_MAX_RECORD = 16384


class EndpointError(httpx.TransportError):
    """Safe diagnostic only; never attach metadata/token or raw request details."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _error(
    code="automation_endpoint_unknown", message="私有端点归属或权限无法确认；保留记录供核对。"
):
    return EndpointError(code, message)


def _nonce(value):
    if (
        not isinstance(value, str)
        or not 16 <= len(value) <= 256
        or any(ord(c) < 33 or ord(c) > 126 for c in value)
    ):
        raise _error("automation_instance_invalid", "实例标识必须是明确的16至256字符ASCII参数。")
    return value


def _has_nonce(arguments, value):
    found = []
    for index, item in enumerate(arguments):
        if item == "--instance-id":
            found.append(arguments[index + 1] if index + 1 < len(arguments) else None)
        elif item.startswith("--instance-id="):
            found.append(item.split("=", 1)[1])
    return found == [value]


def _executable(value):
    if not isinstance(value, str) or not os.path.isabs(value):
        raise _error()
    return os.path.normcase(os.path.normpath(value))


def _self_record(instance_id):
    """capture() deliberately cannot capture self; use public native psutil APIs."""
    _nonce(instance_id)
    try:
        process = psutil.Process(os.getpid())
        if not _has_nonce(process.cmdline(), instance_id):
            raise _error(
                "automation_instance_missing", "当前进程必须包含唯一匹配的--instance-id参数。"
            )
        result = {
            "version": 1,
            "platform": sys.platform,
            "pid": os.getpid(),
            "create_time": process.create_time(),
            "executable": _executable(process.exe()),
            "nonce": instance_id,
            "nonce_flag": "--instance-id",
        }
        if not _self_matches(result):
            raise _error()
        return result
    except (psutil.Error, OSError):
        raise _error() from None


def _valid_process(record, instance_id):
    if not isinstance(record, dict) or set(record) != {
        "version",
        "platform",
        "pid",
        "create_time",
        "executable",
        "nonce",
        "nonce_flag",
    }:
        raise _error()
    pid, created = record["pid"], record["create_time"]
    if (
        record["version"] != 1
        or record["platform"] != sys.platform
        or type(pid) is not int
        or pid <= 1
        or isinstance(created, bool)
        or not isinstance(created, (int, float))
        or not math.isfinite(created)
        or created <= 0
        or record["nonce"] != instance_id
        or record["nonce_flag"] != "--instance-id"
    ):
        raise _error()
    _executable(record["executable"])
    _nonce(record["nonce"])


def _self_matches(record):
    try:
        _valid_process(record, record.get("nonce"))
        if record["pid"] != os.getpid():
            return False
        process = psutil.Process(os.getpid())
        return (
            process.create_time() == record["create_time"]
            and _executable(process.exe()) == _executable(record["executable"])
            and _has_nonce(process.cmdline(), record["nonce"])
            and process.is_running()
        )
    except (EndpointError, psutil.Error, OSError):
        return False


def _process_status(record):
    # matches() is the existing cross-process identity check. It intentionally
    # refuses self; the self branch still verifies exe/create_time/exact nonce.
    if record["pid"] == os.getpid():
        return "bound" if _self_matches(record) else "unknown"
    if matches(record):
        return "bound"
    try:
        psutil.Process(record["pid"])
    except psutil.NoSuchProcess:
        return "unavailable"
    except psutil.Error:
        pass
    # Reused PID, zombie, inaccessible/mismatched identity: not proof of death.
    return "unknown"


def _paths(data_dir, name, *, create=False):
    if (
        not isinstance(name, str)
        or name not in {"browser", "desktop"}
        or sys.platform not in {"linux", "darwin", "win32"}
    ):
        raise _error("automation_transport_unsupported", "当前平台或端点类型不受支持。")
    root = Path(data_dir).absolute()
    # No symlink/reparse-point components, including parent aliases. Existing
    # ancestors need not be private, but the data root/runtime directory must be.
    for part in [root, *root.parents]:
        if part.is_symlink() or (
            part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise _error()
    private_directory(root)
    runtime = root / name
    if not runtime.exists() and not create:
        return runtime, runtime / "endpoint.json", runtime / "companion.sock"
    private_directory(runtime, create=create)
    return runtime, runtime / "endpoint.json", runtime / "companion.sock"


def _identity(path):
    info = path.lstat()
    if not info.st_ino:
        raise _error("automation_file_identity_unavailable", "文件系统未提供可核对的端点文件标识。")
    return info.st_dev, info.st_ino


def _socket_check(path, expected):
    check_private(path)
    info = path.lstat()
    if (
        not stat.S_ISSOCK(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or tuple(expected) != (info.st_dev, info.st_ino)
    ):
        raise _error()


def _validate_record(record, name, path):
    if not isinstance(record, dict) or set(record) != {
        "schema_version",
        "name",
        "platform",
        "instance_id",
        "process",
        "transport",
        "host",
        "port",
        "socket_path",
        "socket_identity",
        "token",
    }:
        raise _error()
    if (
        record["schema_version"] != 1
        or record["name"] != name
        or record["platform"] != sys.platform
    ):
        raise _error()
    _nonce(record["instance_id"])
    _valid_process(record["process"], record["instance_id"])
    token = record["token"]
    if (
        not isinstance(token, str)
        or len(token) != 64
        or any(
            c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
            for c in token
        )
    ):
        raise _error()
    if sys.platform == "win32":
        if (
            record["transport"] != "loopback"
            or record["host"] != "127.0.0.1"
            or type(record["port"]) is not int
            or not 1 <= record["port"] <= 65535
            or record["socket_path"] is not None
            or record["socket_identity"] is not None
        ):
            raise _error()
    else:
        if (
            record["transport"] != "unix"
            or record["host"] != name + "-companion"
            or record["port"] is not None
            or record["socket_path"] != str(path)
            or not isinstance(record["socket_identity"], list)
            or len(record["socket_identity"]) != 2
            or any(type(x) is not int or x < 0 for x in record["socket_identity"])
        ):
            raise _error()


def _load(data_dir, name, *, check_socket=True):
    _, metadata, path = _paths(data_dir, name)
    raw = read_private(metadata, _MAX_RECORD)
    record = json.loads(raw)
    _validate_record(record, name, path)
    identity = _identity(metadata)
    if raw != read_private(metadata, _MAX_RECORD) or identity != _identity(metadata):
        raise _error()
    if check_socket and record["transport"] == "unix":
        _socket_check(path, record["socket_identity"])
    return record, metadata, identity, raw


def _headers(headers):
    # Mapping.items() preserves duplicate raw names on Starlette/httpx Headers.
    # Normalize first, then reject duplicates rather than pick a first/last token.
    if not isinstance(headers, Mapping):
        return None
    result = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return None
        key = key.lower()
        if key in result:
            return None
        result[key] = value
    return result


def _authorized(headers, token, host):
    values = _headers(headers)
    if values is None or "origin" in values or any(k.startswith("sec-fetch-") for k in values):
        return False
    if values.get("host") != host:
        return False
    supplied = values.get(TOKEN_HEADER.lower())
    if not isinstance(supplied, str) or len(supplied) > 256:
        return False
    try:
        return hmac.compare_digest(supplied.encode("ascii"), token.encode("ascii"))
    except UnicodeEncodeError:
        return False


def _host(record):
    return record["host"] + (":" + str(record["port"]) if record["port"] else "")


@dataclass
class OwnedEndpoint:
    listener: socket.socket = field(repr=False)
    record: dict = field(repr=False)
    _metadata: Path = field(repr=False)
    _file_identity: tuple = field(repr=False)
    _raw: bytes = field(repr=False)
    _closed: bool = field(default=False, init=False)

    def authorize(self, request_headers) -> bool:
        # Secrets in public .record are for private persistence only; use the
        # immutable original bytes for checks even if a trusted caller edits it.
        original = json.loads(self._raw)
        return not self._closed and _authorized(request_headers, original["token"], _host(original))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.listener.close()
        original = json.loads(self._raw)
        try:
            if (
                _identity(self._metadata) != self._file_identity
                or read_private(self._metadata) != self._raw
            ):
                return  # Replacement/changed metadata remains unknown; never unlink it.
            if original["transport"] == "unix":
                path = Path(original["socket_path"])
                _socket_check(path, original["socket_identity"])
                path.unlink()
            # Recheck immediately before unlinking; same-user adversarial races
            # are outside the stated OS-user boundary, not a sandbox guarantee.
            if (
                _identity(self._metadata) == self._file_identity
                and read_private(self._metadata) == self._raw
            ):
                self._metadata.unlink()
        except (OSError, ValueError, VaultError, EndpointError):
            return


def bind_endpoint(data_dir: Path, name: Name, instance_id: str) -> OwnedEndpoint:
    """Bind once; existing endpoint/metadata is never overwritten, even if dead."""
    listener = None
    socket_identity = None
    socket_path = None
    try:
        process = _self_record(instance_id)
        _, metadata, socket_path = _paths(data_dir, name, create=True)
        if (
            metadata.exists()
            or metadata.is_symlink()
            or socket_path.exists()
            or socket_path.is_symlink()
        ):
            raise _error(
                "automation_endpoint_exists", "私有端点已有记录或socket；必须先核对，不能覆盖。"
            )
        token = secrets.token_urlsafe(48)
        if sys.platform == "win32":
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", 0))
            host, port = listener.getsockname()
            transport = "loopback"
        else:
            if len(os.fsencode(socket_path)) > (103 if sys.platform == "darwin" else 107):
                raise _error(
                    "automation_socket_path_long", "UDS路径超过系统限制；请选择较短的私有data_dir。"
                )
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(socket_path))
            socket_identity = _identity(socket_path)
            os.chmod(socket_path, 0o600)
            _socket_check(socket_path, socket_identity)
            host, port, transport = name + "-companion", None, "unix"
        listener.listen(128)
        listener.setblocking(False)
        listener.set_inheritable(False)
        record = {
            "schema_version": 1,
            "name": name,
            "platform": sys.platform,
            "instance_id": instance_id,
            "process": process,
            "transport": transport,
            "host": host,
            "port": port,
            "socket_path": str(socket_path) if transport == "unix" else None,
            "socket_identity": list(socket_identity) if socket_identity else None,
            "token": token,
        }
        write_json(metadata, record, exclusive=True)
        # Windows write_private inherits the current-user-only directory DACL;
        # verify the actual resulting file, never claim chmod equals a Windows ACL.
        loaded, _, identity, raw = _load(data_dir, name)
        if loaded != record or not _self_matches(process):
            raise _error()
        return OwnedEndpoint(listener, record, metadata, identity, raw)
    except Exception as error:
        if listener:
            listener.close()
        if socket_identity and socket_path:
            try:
                _socket_check(socket_path, socket_identity)
                socket_path.unlink()
            except (OSError, VaultError, EndpointError):
                pass
        if isinstance(error, EndpointError):
            raise
        raise _error() from None


class _BoundTransport(httpx.AsyncHTTPTransport):
    """Revalidate private record each request; refuse absolute URL/token overrides."""

    def __init__(self, data_dir, name, record, identity, raw):
        super().__init__(
            uds=record["socket_path"],
            retries=0,
            trust_env=False,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=0),
        )
        self._data_dir, self._name = data_dir, name
        self._record, self._file_identity, self._raw = record, identity, raw

    def _verify(self):
        try:
            record, _, identity, raw = _load(self._data_dir, self._name)
            if identity != self._file_identity or raw != self._raw or record != self._record:
                raise _error()
            if _process_status(record["process"]) != "bound":
                raise _error(
                    "automation_endpoint_unavailable", "端点的当前进程身份未确认；不会发送请求。"
                )
        except (OSError, ValueError, VaultError):
            raise _error() from None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        expected_port = self._record["port"] or 80
        if (
            url.scheme != "http"
            or url.host != self._record["host"]
            or (url.port or 80) != expected_port
            or url.userinfo
            or not _authorized(request.headers, self._record["token"], _host(self._record))
        ):
            raise _error(
                "automation_endpoint_override_denied", "私有端点URL、Host或授权头不允许被替换。"
            )
        await asyncio.to_thread(self._verify)
        return await super().handle_async_request(request)


@dataclass(frozen=True)
class ClientEndpoint:
    transport: httpx.AsyncHTTPTransport = field(repr=False)
    base_url: str
    headers: dict = field(repr=False)


def client_endpoint(data_dir: Path, name: Name) -> ClientEndpoint:
    """Only reads a verified private endpoint; no model-provided URL or fallback."""
    try:
        record, _, identity, raw = _load(data_dir, name)
        if _process_status(record["process"]) != "bound":
            raise _error("automation_endpoint_unavailable", "私有端点当前进程身份未确认。")
        return ClientEndpoint(
            _BoundTransport(Path(data_dir).absolute(), name, record, identity, raw),
            "http://" + _host(record),
            {TOKEN_HEADER: record["token"]},
        )
    except EndpointError:
        raise
    except (OSError, ValueError, VaultError):
        raise _error() from None


def _absent_record_status(data_dir, name):
    try:
        (Path(data_dir).absolute() / name / "endpoint.json").lstat()
    except FileNotFoundError:
        return "missing"
    except OSError:
        pass
    return "unknown"


def inspect_endpoint(data_dir: Path, name: Name) -> dict:
    """Read-only diagnostics; bound is identity verified, not application readiness."""
    try:
        record, _, _, _ = _load(data_dir, name)
        return {
            "status": _process_status(record["process"]),
            "name": name,
            "instance_id": record["instance_id"],
            "transport": record["transport"],
            "process": dict(record["process"]),
            "reason": "仅端点进程身份检查；会话资源与动作效果仍须另行核对。",
        }
    except FileNotFoundError:
        return {
            "status": _absent_record_status(data_dir, name),
            "name": name,
            "reason": "端点不可用；不代表业务会话已关闭。",
        }
    except (OSError, ValueError, VaultError, EndpointError):
        return {
            "status": "unknown",
            "name": name,
            "reason": "端点记录、权限或归属未确认，原记录保留。",
        }


def release_dead_endpoint(data_dir: Path, name: Name, expected_instance_id: str) -> dict:
    """Explicit lifecycle-only cleanup. Never signals a process or closes DB sessions."""
    try:
        record, metadata, identity, raw = _load(data_dir, name, check_socket=False)
        if (
            record["instance_id"] != expected_instance_id
            or _process_status(record["process"]) != "unavailable"
        ):
            return {"status": "unknown", "reason": "未确认指定实例已退出；端点保留。"}
        if identity != _identity(metadata) or read_private(metadata) != raw:
            raise _error()
        if record["transport"] == "unix":
            path = Path(record["socket_path"])
            if path.exists() or path.is_symlink():
                _socket_check(path, record["socket_identity"])
                path.unlink()
        if identity != _identity(metadata) or read_private(metadata) != raw:
            raise _error()
        metadata.unlink()
        return {
            "status": "released",
            "reason": "仅释放已退出实例的传输文件；未确认业务会话资源关闭。",
        }
    except FileNotFoundError:
        return {
            "status": _absent_record_status(data_dir, name),
            "reason": "端点不可用；业务会话状态未改变。",
        }
    except (OSError, ValueError, VaultError, EndpointError):
        return {"status": "unknown", "reason": "归属或文件标识未确认；原记录保留。"}
