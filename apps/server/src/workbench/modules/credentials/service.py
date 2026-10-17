"""Owner-only vault management; no secret values in receipts or diagnostics."""

import hashlib

from sqlalchemy import select
from workbench.config import get_settings
from workbench.contracts.errors import AppError
from workbench.infrastructure.vault.client import VaultAdminClient, VaultClient
from workbench.infrastructure.vault.errors import VaultError
from workbench.modules.mcp.host_domain import MCPConnection
from workbench.modules.models.domain import ModelConnection, ModelRevision
from workbench.modules.runs.service import idempotent


def legacy_count(db):
    values = list(db.scalars(select(ModelConnection.encrypted_api_key)))
    values.extend(db.scalars(select(ModelRevision.encrypted_api_key)))
    for secrets in db.scalars(select(MCPConnection.encrypted_secrets)):
        values.extend(secrets.values())
    return sum(bool(value and not value.startswith("vault:v1:")) for value in values)


def status(db):
    try:
        result = VaultClient(get_settings().data_dir).status()
    except VaultError as error:
        result = {
            "state": "unavailable",
            "initialized": False,
            "backend": None,
            "keyring_available": False,
            "keyring_backend": None,
            "keyring_reason": None,
            "initialization_complete": False,
            "ttl_seconds": 900,
            "remaining_seconds": None,
            "last_lock_reason": None,
            "reason": error.message,
        }
    return {**result, "legacy_credentials": legacy_count(db)}


def manage(db, user_id, action, command):
    values = command.model_dump(mode="json", exclude={"passphrase"})
    passphrase = getattr(command, "passphrase", None)
    if passphrase is not None:
        values["passphrase_digest"] = hashlib.sha256(
            passphrase.get_secret_value().encode()
        ).hexdigest()

    def execute():
        client = VaultAdminClient(get_settings().data_dir)
        try:
            if action == "initialize":
                client.initialize(
                    command.mode,
                    passphrase=passphrase.get_secret_value() if passphrase is not None else None,
                )
            elif action == "unlock":
                client.unlock(
                    passphrase=passphrase.get_secret_value() if passphrase is not None else None
                )
            elif action == "lock":
                client.lock()
            elif action == "migrate-legacy":
                migrate_legacy(db, user_id, client)
            else:
                raise AppError("vault_action_invalid", "密钥管理操作不受支持。", 422)
        except VaultError as error:
            raise AppError(
                f"vault_{error.code.lower()}", error.message, 409, "dependency"
            ) from None
        return status(db)

    return idempotent(db, user_id, str(command.request_id), f"vault:{action}", values, execute)


def migrate_legacy(db, user_id, client):
    from workbench.infrastructure.vault.migration import legacy_reencrypt

    if client.status()["state"] != "unlocked":
        raise AppError("vault_locked", "先解锁密钥保险库再迁移。", 409)
    path = get_settings().data_dir / "vault.key"
    for table in (ModelConnection, ModelRevision):
        if table is ModelConnection:
            rows = db.scalars(select(table).where(table.user_id == user_id).with_for_update())
        else:
            rows = db.scalars(
                select(table)
                .join(ModelConnection)
                .where(ModelConnection.user_id == user_id)
                .with_for_update(of=table)
            )
        for row in rows:
            if row.encrypted_api_key and not row.encrypted_api_key.startswith("vault:v1:"):
                row.encrypted_api_key = legacy_reencrypt(row.encrypted_api_key, path, client)
    for row in db.scalars(
        select(MCPConnection).where(MCPConnection.user_id == user_id).with_for_update()
    ):
        row.encrypted_secrets = {
            name: value if value.startswith("vault:v1:") else legacy_reencrypt(value, path, client)
            for name, value in row.encrypted_secrets.items()
        }
    db.flush()
