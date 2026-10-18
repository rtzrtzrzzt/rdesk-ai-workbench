"""Synchronous API/Worker client. Construction and module import perform no I/O."""

import os
import socket
import time
from pathlib import Path

from workbench.infrastructure.vault.errors import VaultError
from workbench.infrastructure.vault.ipc import (
    peer_identity,
    receive_frame,
    send_frame,
    unix_socket_address,
)
from workbench.infrastructure.vault.private import private_directory, read_json, read_private


class VaultClient:
    def __init__(self, data_dir: Path, *, timeout_seconds: float = 10):
        if not 0.1 <= timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be between 0.1 and 60")
        self.directory = Path(os.path.abspath(data_dir)) / "vault"
        self.timeout_seconds = timeout_seconds

    def _call(self, operation: str, arguments: dict | None = None, *, admin: bool = False):
        deadline = time.monotonic() + self.timeout_seconds
        try:
            private_directory(self.directory.parent)
            private_directory(self.directory)
            descriptor_path = self.directory / "broker.json"
            if not descriptor_path.exists():
                raise VaultError("BROKER_UNAVAILABLE", "vault broker尚未就绪。")
            descriptor = read_json(descriptor_path)
            request = {
                "operation": operation,
                "arguments": arguments or {},
                "instance_id": descriptor["instance_id"],
            }
            if admin:
                request["admin_token"] = read_private(self.directory / "admin.token", 256).decode()
            if os.name == "nt":
                if (
                    descriptor.get("transport") != "loopback"
                    or descriptor.get("host") != "127.0.0.1"
                ):
                    raise VaultError("INVALID_BROKER", "vault broker地址无效。")
                request["transport_token"] = read_private(
                    self.directory / "transport.token", 256
                ).decode()
                address = ("127.0.0.1", descriptor["port"])
                connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            else:
                expected = str(self.directory / "broker.sock")
                if descriptor.get("transport") != "uds" or descriptor.get("socket") != expected:
                    raise VaultError("INVALID_BROKER", "vault UDS路径无效。")
                address = expected
                connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            with connection:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                connection.settimeout(remaining)
                if os.name == "nt":
                    connection.connect(address)
                else:
                    with unix_socket_address(Path(address)) as endpoint:
                        connection.connect(endpoint)
                peer_identity(connection, expected_pid=descriptor["pid"])
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                connection.settimeout(remaining)
                send_frame(connection, request)
                response = receive_frame(connection, deadline=deadline)
            if response.get("instance_id") != descriptor["instance_id"]:
                raise VaultError("INVALID_BROKER", "vault broker实例已变化。")
            if not response.get("ok"):
                error = response.get("error", {})
                raise VaultError(
                    error.get("code", "BROKER_FAILED"), error.get("message", "vault操作失败。")
                )
            return response["result"]
        except VaultError:
            raise
        except TimeoutError:
            raise VaultError(
                "BROKER_TIMEOUT", "vault操作超时；请检查状态，不要自动重放初始化。"
            ) from None
        except Exception:
            raise VaultError("BROKER_UNAVAILABLE", "vault broker不可用；凭据保持不可用。") from None

    def status(self) -> dict:
        return self._call("status")

    def encrypt(self, value: str) -> str:
        return self._call("encrypt", {"value": value})["ciphertext"]

    def decrypt(self, ciphertext: str) -> str:
        return self._call("decrypt", {"ciphertext": ciphertext})["value"]


class VaultAdminClient(VaultClient):
    """Only the host's authenticated owner-management routes should instantiate this API."""

    def initialize(self, mode: str = "keyring", *, passphrase: str | None = None) -> dict:
        return self._call("initialize", {"mode": mode, "passphrase": passphrase}, admin=True)

    def unlock(self, *, passphrase: str | None = None) -> dict:
        return self._call("unlock", {"passphrase": passphrase}, admin=True)

    def lock(self) -> dict:
        return self._call("lock", admin=True)

    def stop(self) -> dict:
        return self._call("stop", admin=True)
