"""Broker-owned master state. OS Keyring or explicitly password-wrapped storage only."""

import base64
import hmac
import os
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from workbench.infrastructure.vault.errors import VaultError
from workbench.infrastructure.vault.native import NativeKeyring
from workbench.infrastructure.vault.private import private_directory, read_json, write_json

PREFIX = "vault:v1:"
KDF = {"name": "scrypt", "n": 131072, "r": 8, "p": 1, "length": 32}


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _decode(value: str, length: int) -> bytes:
    try:
        result = base64.b64decode(value, altchars=b"-_", validate=True)
        if len(result) != length:
            raise ValueError
        return result
    except Exception:
        raise VaultError("INVALID_VAULT_FILE", "vault加密参数无效。") from None


def _password(value: str | None) -> bytes:
    if not isinstance(value, str) or not 12 <= len(value.encode("utf-8")) <= 1024:
        raise VaultError("INVALID_PASSPHRASE", "独立vault口令需为12至1024个UTF-8字节。")
    return value.encode("utf-8")


class VaultEngine:
    def __init__(self, data_dir: Path, *, ttl_seconds: float = 900):
        if not 1 <= ttl_seconds <= 86400:
            raise VaultError("INVALID_TTL", "vault解锁期限必须在1秒至24小时内。")
        base = private_directory(Path(os.path.abspath(data_dir)))
        self.directory = private_directory(base / "vault", create=True)
        self.document_path = self.directory / "envelope.json"
        self.native = NativeKeyring()
        self.native.probe()
        self.ttl_seconds = ttl_seconds
        self._mutex = threading.RLock()
        self._master: bytearray | None = None
        self._cipher: Fernet | None = None
        self._expires = 0.0
        self._last_lock_reason = "broker_start"
        self._closing = False
        self._document = self._read_document()

    def _read_document(self) -> dict | None:
        if not self.document_path.exists():
            return None
        value = read_json(self.document_path)
        if (
            value.get("format_version") != 1
            or value.get("backend") not in {"keyring", "passphrase"}
            or not isinstance(value.get("vault_id"), str)
            or len(value["vault_id"]) != 32
        ):
            raise VaultError("INVALID_VAULT_FILE", "现有vault格式不受支持；拒绝创建替代主密钥。")
        try:
            uuid.UUID(hex=value["vault_id"])
        except ValueError:
            raise VaultError("INVALID_VAULT_FILE", "vault标识无效。") from None
        return value

    def _expire(self) -> None:
        if self._cipher is not None and time.monotonic() >= self._expires:
            self.lock(reason="ttl_expired")

    def status(self) -> dict:
        with self._mutex:
            self._expire()
            return {
                "state": "uninitialized"
                if self._document is None
                else "unlocked"
                if self._cipher is not None
                else "locked",
                "initialized": self._document is not None,
                "backend": self._document["backend"] if self._document else None,
                "keyring_available": self.native.available,
                "keyring_backend": self.native.backend_name,
                "keyring_reason": self.native.reason,
                "initialization_complete": self._document.get("status", "ready") == "ready"
                if self._document
                else False,
                "ttl_seconds": self.ttl_seconds,
                "remaining_seconds": max(0.0, self._expires - time.monotonic())
                if self._cipher
                else None,
                "last_lock_reason": self._last_lock_reason,
            }

    def _adopt(self, master: bytes) -> None:
        if self._closing:
            raise VaultError("BROKER_STOPPING", "vault broker正在停止。")
        if len(master) != 32:
            raise VaultError("INVALID_MASTER", "系统凭据库中的vault主密钥无效。")
        self._master = bytearray(master)
        self._cipher = Fernet(_encode(master).encode("ascii"))
        self._expires = time.monotonic() + self.ttl_seconds

    def initialize(self, mode: str = "keyring", passphrase: str | None = None) -> dict:
        with self._mutex:
            if self.document_path.exists():
                raise VaultError("ALREADY_INITIALIZED", "现有vault不会被重新初始化或覆盖。")
            if mode not in {"keyring", "passphrase"}:
                raise VaultError("INVALID_BACKEND", "请显式选择原生Keyring或独立口令vault。")
            if mode == "keyring" and passphrase is not None:
                raise VaultError("UNEXPECTED_PASSPHRASE", "原生Keyring不接收应用口令。")
            master = os.urandom(32)
            value = {
                "format_version": 1,
                "vault_id": uuid.uuid4().hex,
                "backend": mode,
                "created_at": datetime.now(UTC).isoformat(),
            }
            if mode == "passphrase":
                password = _password(passphrase)
                salt, nonce = os.urandom(16), os.urandom(12)
                derived = Scrypt(salt=salt, n=KDF["n"], r=8, p=1, length=32).derive(password)
                aad = ("AIWorkbench.ProviderVault/master/v1/" + value["vault_id"]).encode()
                value.update(
                    kdf=KDF,
                    salt=_encode(salt),
                    wrap_nonce=_encode(nonce),
                    wrapped_master=_encode(AESGCM(derived).encrypt(nonce, master, aad)),
                )
                write_json(self.document_path, value)
            else:
                self.native.probe()
                self.native._require()
                value.update(keyring_backend=self.native.backend_name, status="pending")
                # Persist intent first. A failed/ambiguous OS write can be reconciled
                # by unlock; a later initialize never overwrites this account/master.
                write_json(self.document_path, value)
                self._document = value
                self.native.create(value["vault_id"], _encode(master))
                stored = self.native.get(value["vault_id"])
                if not stored or not hmac.compare_digest(stored, _encode(master)):
                    raise VaultError(
                        "KEYRING_WRITE_UNCONFIRMED", "Keyring写入未确认；保留原初始化记录。"
                    )
                value["status"] = "ready"
                write_json(self.document_path, value, exclusive=False)
            self._document = value
            self._adopt(master)
            return self.status()

    def unlock(self, passphrase: str | None = None) -> dict:
        with self._mutex:
            self._document = self._read_document()
            value = self._document
            if value is None:
                raise VaultError("VAULT_UNINITIALIZED", "请先显式初始化vault。")
            # A wrong unlock attempt cannot leave a previously unlocked key active.
            self.lock(reason="unlock_requested")
            if value["backend"] == "passphrase":
                if value.get("kdf") != KDF:
                    raise VaultError("INVALID_KDF_PARAMETERS", "不支持或异常的vault派生参数。")
                password = _password(passphrase)
                salt, nonce = (
                    _decode(value.get("salt", ""), 16),
                    _decode(value.get("wrap_nonce", ""), 12),
                )
                wrapped = _decode(value.get("wrapped_master", ""), 48)
                key = Scrypt(salt=salt, n=131072, r=8, p=1, length=32).derive(password)
                aad = ("AIWorkbench.ProviderVault/master/v1/" + value["vault_id"]).encode()
                try:
                    master = AESGCM(key).decrypt(nonce, wrapped, aad)
                except InvalidTag:
                    raise VaultError(
                        "UNLOCK_FAILED", "vault口令不正确或envelope已被修改。"
                    ) from None
            else:
                if passphrase is not None:
                    raise VaultError("UNEXPECTED_PASSPHRASE", "原生Keyring不接收应用口令。")
                if value.get("keyring_backend") != self.native.backend_name:
                    raise VaultError(
                        "KEYRING_BACKEND_MISMATCH", "原vault的系统Keyring后端与当前平台不匹配。"
                    )
                self.native.probe()
                stored = self.native.get(value["vault_id"])
                if stored is None:
                    raise VaultError(
                        "KEYRING_ENTRY_MISSING", "原vault条目不可用；不会生成替代密钥。"
                    )
                master = _decode(stored, 32)
                if value.get("status") == "pending":
                    value["status"] = "ready"
                    write_json(self.document_path, value, exclusive=False)
            self._adopt(master)
            return self.status()

    def lock(self, *, reason: str = "explicit_lock") -> dict:
        with self._mutex:
            if self._master is not None:
                self._master[:] = b"\0" * len(self._master)
            self._master, self._cipher, self._expires = None, None, 0.0
            self._last_lock_reason = reason
            return self.status()

    def encrypt(self, value: str) -> str:
        with self._mutex:
            self._expire()
            if self._cipher is None:
                raise VaultError("VAULT_LOCKED", "vault未解锁，远程凭据不可用。")
            if not isinstance(value, str) or len(value.encode()) > 65536:
                raise VaultError("VALUE_TOO_LARGE", "凭据必须是至多64KiB的文本。")
            return PREFIX + self._cipher.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        with self._mutex:
            self._expire()
            if self._cipher is None:
                raise VaultError("VAULT_LOCKED", "vault未解锁，远程凭据不可用。")
            if not isinstance(value, str) or not value.startswith(PREFIX) or len(value) > 100000:
                raise VaultError("INVALID_CIPHERTEXT", "凭据密文格式不受支持；旧密文需显式迁移。")
            try:
                return self._cipher.decrypt(value[len(PREFIX) :].encode()).decode()
            except (InvalidToken, UnicodeError):
                raise VaultError("INVALID_CIPHERTEXT", "凭据密文无效或属于其他vault。") from None

    def poll_expiry(self) -> None:
        if self._mutex.acquire(blocking=False):
            try:
                self._expire()
            finally:
                self._mutex.release()

    def shutdown(self) -> None:
        self._closing = True
        acquired = self._mutex.acquire(timeout=1)
        try:
            if self._master is not None:
                self._master[:] = b"\0" * len(self._master)
            self._master, self._cipher, self._expires = None, None, 0.0
        finally:
            if acquired:
                self._mutex.release()
