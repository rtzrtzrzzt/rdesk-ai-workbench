"""Explicit OS backend allow-list, bypassing keyring configuration and plugins."""

import sys

from workbench.infrastructure.vault.errors import VaultError

SERVICE = "AIWorkbench.ProviderVault.v1"
ALLOWLIST = {
    "linux": "keyring.backends.SecretService.Keyring",
    "darwin": "keyring.backends.macOS.Keyring",
    "win32": "keyring.backends.Windows.WinVaultKeyring",
}


class NativeKeyring:
    def __init__(self):
        self.backend = None
        self.backend_name = ALLOWLIST.get(sys.platform)
        self.available = False
        self.reason = "KEYRING_UNAVAILABLE"

    def probe(self) -> dict:
        try:
            if sys.platform == "linux":
                from keyring.backends.SecretService import Keyring
            elif sys.platform == "darwin":
                from keyring.backends.macOS import Keyring
            elif sys.platform == "win32":
                from keyring.backends.Windows import WinVaultKeyring as Keyring
            else:
                raise RuntimeError("unsupported OS")
            candidate = Keyring()
            actual = type(candidate).__module__ + "." + type(candidate).__name__
            if actual != self.backend_name or candidate.priority <= 0:
                raise RuntimeError("native backend unavailable")
            self.backend, self.available, self.reason = candidate, True, None
        except Exception:
            self.backend, self.available, self.reason = None, False, "KEYRING_UNAVAILABLE"
        return {
            "available": self.available,
            "backend": self.backend_name,
            "reason": self.reason,
        }

    def _require(self):
        if not self.available or self.backend is None:
            raise VaultError("KEYRING_UNAVAILABLE", "原生系统凭据库不可用；不会降级为明文存储。")
        return self.backend

    def get(self, vault_id: str) -> str | None:
        try:
            return self._require().get_password(SERVICE, vault_id)
        except VaultError:
            raise
        except Exception:
            raise VaultError(
                "KEYRING_ACCESS_FAILED", "无法从原生系统凭据库取得vault主密钥。"
            ) from None

    def create(self, vault_id: str, master: str) -> None:
        try:
            backend = self._require()
            if backend.get_password(SERVICE, vault_id) is not None:
                raise VaultError("KEYRING_ENTRY_EXISTS", "该vault条目已存在，拒绝覆盖主密钥。")
            backend.set_password(SERVICE, vault_id, master)
        except VaultError:
            raise
        except Exception:
            raise VaultError(
                "KEYRING_ACCESS_FAILED", "无法向原生系统凭据库保存vault主密钥。"
            ) from None

    def delete_owned_test_entry(self, vault_id: str) -> None:
        """Explicit mechanism-test cleanup only; production never deletes an old master."""
        self._require().delete_password(SERVICE, vault_id)
