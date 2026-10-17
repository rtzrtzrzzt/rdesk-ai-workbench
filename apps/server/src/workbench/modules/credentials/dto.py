from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr
from workbench.contracts.base import PublicResponse
from workbench.contracts.requests import Command


class VaultView(PublicResponse):
    state: Literal["uninitialized", "locked", "unlocked", "unavailable"]
    initialized: bool
    backend: Literal["keyring", "passphrase"] | None
    keyring_available: bool
    keyring_backend: str | None
    keyring_reason: str | None
    initialization_complete: bool
    ttl_seconds: float
    remaining_seconds: float | None
    last_lock_reason: str | None
    legacy_credentials: int
    reason: str | None = None


class VaultInitialize(Command):
    mode: Literal["keyring", "passphrase"] = "keyring"
    passphrase: SecretStr | None = Field(default=None, max_length=1024)
    request_id: UUID


class VaultUnlock(Command):
    passphrase: SecretStr | None = Field(default=None, max_length=1024)
    request_id: UUID


class VaultCommand(Command):
    request_id: UUID
