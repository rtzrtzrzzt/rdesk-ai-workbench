"""Explicit row-by-row legacy conversion helper. No database/file mutations."""

from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from workbench.infrastructure.vault.client import VaultClient
from workbench.infrastructure.vault.errors import VaultError
from workbench.infrastructure.vault.private import read_private


def legacy_reencrypt(ciphertext: str, legacy_key_path: Path, client: VaultClient) -> str:
    """Caller authorizes migration and commits returned ciphertext transactionally.

    Keep the old key and old ciphertext until the caller has verified and
    committed the replacement; this helper never deletes either one.
    """
    if client.status()["state"] != "unlocked":
        raise VaultError("VAULT_LOCKED", "请先显式解锁目标vault，再迁移旧密文。")
    try:
        old_key = read_private(Path(legacy_key_path), 128)
        cleartext = Fernet(old_key).decrypt(ciphertext.encode()).decode()
    except VaultError:
        raise
    except (InvalidToken, ValueError, UnicodeError):
        raise VaultError(
            "LEGACY_DECRYPT_FAILED", "旧密文或旧密钥无效；原始数据保持不变。"
        ) from None
    return client.encrypt(cleartext)
