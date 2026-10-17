import hashlib
import hmac
import os
import tempfile

from workbench.config import get_settings
from workbench.contracts.errors import AppError
from workbench.infrastructure.vault.client import VaultClient
from workbench.infrastructure.vault.errors import VaultError


def vault_key() -> bytes:
    # Retain the old signing value so existing browser sessions survive the
    # transition. It is never used to encrypt/decrypt provider credentials.
    directory = get_settings().ensure_data_dir()
    legacy = directory / "vault.key"
    path = legacy if legacy.is_file() else directory / "session-signing.key"
    if path.exists():
        return path.read_bytes()
    key = os.urandom(32)
    descriptor, temporary = tempfile.mkstemp(prefix=".vault-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(key)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # The destination becomes visible only after a complete key is durable.
            os.link(temporary, path)
        except FileExistsError:
            pass
        return path.read_bytes()
    finally:
        os.unlink(temporary)


def encrypt(value: str) -> str:
    try:
        return VaultClient(get_settings().data_dir).encrypt(value)
    except VaultError as error:
        raise AppError(f"vault_{error.code.lower()}", error.message, 409, "dependency") from None


def decrypt(value: str | None) -> str | None:
    if not value:
        return None
    if not value.startswith("vault:v1:"):
        raise AppError(
            "vault_legacy_migration_required", "请先解锁密钥保险库并显式迁移旧凭据。", 409
        )
    try:
        return VaultClient(get_settings().data_dir).decrypt(value)
    except VaultError as error:
        raise AppError(f"vault_{error.code.lower()}", error.message, 409, "dependency") from None


def csrf_token(session_token: str) -> str:
    return hmac.new(vault_key(), session_token.encode(), hashlib.sha256).hexdigest()
