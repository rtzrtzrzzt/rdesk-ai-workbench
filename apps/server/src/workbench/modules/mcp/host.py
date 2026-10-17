"""Authenticated connection configuration and protocol ports for the host."""

import asyncio
import hashlib
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.config import get_settings
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine
from workbench.infrastructure.secrets import decrypt, encrypt
from workbench.infrastructure.vault import VaultClient, VaultError
from workbench.modules import workspace_service
from workbench.modules.mcp.dto import ConnectionConfig, ToolSnapshot
from workbench.modules.mcp.host_domain import MCPConnection, MCPSnapshot
from workbench.modules.mcp.service import MCPService
from workbench.modules.mcp.transport import SessionManager
from workbench.modules.runs.domain import ClientRequest
from workbench.modules.runs.service import idempotent


def identity(row):
    return f"{row.user_id}:{row.id}:{row.version}"


def get_connection(db, user_id, workspace_id, connection_id, *, lock=False):
    workspace_service.get(db, user_id, workspace_id)
    query = select(MCPConnection).where(
        MCPConnection.id == connection_id,
        MCPConnection.user_id == user_id,
        MCPConnection.workspace_id == workspace_id,
    )
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise AppError("mcp_connection_missing", "MCP连接不存在或不可访问。", 404)
    return row


def view(row):
    return {
        "id": row.id,
        "version": row.version,
        "configuration": row.configuration,
        "execution_authorized": row.execution_authorized,
        "secret_names": sorted(row.encrypted_secrets),
    }


def listing(db, user_id, workspace_id):
    workspace_service.get(db, user_id, workspace_id)
    rows = list(
        db.scalars(
            select(MCPConnection)
            .where(MCPConnection.user_id == user_id, MCPConnection.workspace_id == workspace_id)
            .order_by(MCPConnection.created_at)
        )
    )
    snapshots = []
    for row in rows:
        snapshot = db.scalar(
            select(MCPSnapshot)
            .where(
                MCPSnapshot.connection_id == row.id,
                MCPSnapshot.connection_version == row.version,
            )
            .order_by(MCPSnapshot.created_at.desc())
            .limit(1)
        )
        if snapshot:
            snapshots.append({"id": snapshot.id, "snapshot": snapshot.snapshot})
    return {"connections": [view(row) for row in rows], "snapshots": snapshots}


def save(db, user_id, workspace_id, connection_id, command):
    values = command.model_dump(mode="json", exclude={"secrets"})
    values["secret_digests"] = {
        name: hashlib.sha256(secret.get_secret_value().encode()).hexdigest()
        for name, secret in command.secrets.items()
    }

    def execute():
        workspace_service.get(db, user_id, workspace_id)
        row = db.scalar(
            select(MCPConnection).where(MCPConnection.id == connection_id).with_for_update()
        )
        if row and (row.user_id != user_id or row.workspace_id != workspace_id):
            raise AppError("mcp_connection_missing", "MCP连接不存在或不可访问。", 404)
        if (row.version if row else 0) != command.expected_version:
            raise AppError("mcp_version_conflict", "MCP配置已改变，请重新读取再保存。", 409)
        version = row.version + 1 if row else 1
        config = ConnectionConfig.model_validate(
            {
                **command.configuration.model_dump(mode="json"),
                "connection_id": connection_id,
                "version": version,
            }
        )
        if config.enabled and not command.execution_authorized:
            raise AppError("mcp_grant_required", "启用前请确认此精确命令或网络目标。", 422)
        if config.transport == "stdio":
            if not Path(config.command).is_file() or not Path(config.cwd).is_dir():
                raise AppError("mcp_executable_missing", "MCP程序或工作目录不存在。", 422)
        secrets = dict(row.encrypted_secrets) if row else {}
        for name in command.clear_secrets:
            secrets.pop(name, None)
        for name, secret in command.secrets.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name):
                raise AppError("mcp_secret_name", "凭据名称格式无效。", 422)
            secrets[name] = encrypt(secret.get_secret_value())
        refs = list(config.env_secret_refs.values())
        if config.bearer_secret_ref:
            refs.append(config.bearer_secret_ref)
        if any(ref not in secrets for ref in refs):
            raise AppError("mcp_secret_missing", "配置引用的凭据尚未保存。", 422)
        if row is None:
            row = MCPConnection(id=connection_id, user_id=user_id, workspace_id=workspace_id)
            db.add(row)
        row.configuration = config.model_dump(mode="json")
        row.version = version
        row.execution_authorized = command.execution_authorized
        row.encrypted_secrets = secrets
        db.flush()
        return view(row)

    return idempotent(
        db, user_id, str(command.request_id), f"mcp:{connection_id}:save", values, execute
    )


def vault_unlocked():
    """No plaintext lookup; a locked/expired/unavailable vault grants no new send."""
    try:
        return VaultClient(get_settings().data_dir).status().get("state") == "unlocked"
    except VaultError:
        return False


class HostGate:
    async def check(self, config, identity_ref):
        await asyncio.to_thread(self._check, config, identity_ref)

    @staticmethod
    def _check(config, identity_ref):
        if (config.env_secret_refs or config.bearer_secret_ref) and not vault_unlocked():
            raise AppError(
                "mcp_vault_locked", "vault未解锁或不可用，此MCP凭据交换已停止。", 409, "policy"
            )
        with Session(engine()) as db:
            row = db.get(MCPConnection, config.connection_id)
            if (
                row is None
                or identity(row) != identity_ref
                or row.version != config.version
                or not row.execution_authorized
                or not row.configuration.get("enabled")
                or row.configuration != config.model_dump(mode="json")
            ):
                raise AppError("mcp_grant_revoked", "MCP授权或连接配置已经失效。", 403, "policy")


class HostSecrets:
    def __init__(self, config):
        self.config = config

    async def resolve(self, reference, identity_ref):
        def resolve():
            with Session(engine()) as db:
                row = db.get(MCPConnection, self.config.connection_id)
                if row is None or identity(row) != identity_ref:
                    raise AppError("mcp_identity_changed", "MCP凭据身份已改变。", 403, "policy")
                if reference not in row.encrypted_secrets:
                    raise AppError("mcp_secret_missing", "MCP凭据不可用。", 409)
                return decrypt(row.encrypted_secrets[reference])

        return await asyncio.to_thread(resolve)


def configuration(user_id, workspace_id, connection_id, expected_version=None):
    with Session(engine()) as db:
        row = get_connection(db, user_id, workspace_id, connection_id)
        if expected_version is not None and row.version != expected_version:
            raise AppError("mcp_version_conflict", "MCP配置已改变，请重新读取。", 409)
        return ConnectionConfig.model_validate(row.configuration), identity(row)


async def discover(user_id, workspace_id, connection_id, command):
    def reserve():
        fresh = False

        def begin():
            nonlocal fresh
            fresh = True
            return {"id": None, "status": "in_progress", "message": "MCP目录发现正在执行。"}

        with Session(engine()) as db, db.begin():
            workspace_service.get(db, user_id, workspace_id)
            result = idempotent(
                db,
                user_id,
                str(command.request_id),
                f"mcp:{connection_id}:discover",
                command.model_dump(mode="json"),
                begin,
            )
            return fresh, result

    fresh, existing = await asyncio.to_thread(reserve)
    if not fresh:
        if "snapshot" in existing:
            return existing
        raise AppError(
            "mcp_discovery_unconfirmed",
            existing.get("message") or "请刷新目录核对发现结果。",
            409,
            "outcome_unknown",
        )

    def receipt(response):
        with Session(engine()) as db, db.begin():
            row = db.scalar(
                select(ClientRequest)
                .where(
                    ClientRequest.user_id == user_id,
                    ClientRequest.request_id == str(command.request_id),
                )
                .with_for_update()
            )
            if row.response and "snapshot" in row.response:
                # Cancellation may race a to_thread publication that already
                # committed. Never replace its confirmed receipt with failure.
                return
            row.response = response
            row.resource_id = response.get("id")

    manager = None
    completed = False

    def publish(snapshot, config, identity_ref):
        # Snapshot and the idempotent receipt are one committed fact. A crash
        # cannot publish a directory but leave its request looking unperformed.
        result = {"id": snapshot.snapshot_id, "snapshot": snapshot.model_dump(mode="json")}
        with Session(engine()) as db, db.begin():
            row = get_connection(db, user_id, workspace_id, connection_id, lock=True)
            if identity(row) != identity_ref:
                raise AppError("mcp_version_conflict", "发现期间配置已改变，请重新发现。", 409)
            db.add(
                MCPSnapshot(
                    id=snapshot.snapshot_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    connection_id=connection_id,
                    connection_version=config.version,
                    snapshot=snapshot.model_dump(mode="json"),
                )
            )
            request = db.scalar(
                select(ClientRequest)
                .where(
                    ClientRequest.user_id == user_id,
                    ClientRequest.request_id == str(command.request_id),
                )
                .with_for_update()
            )
            request.response, request.resource_id = result, snapshot.snapshot_id
        return result

    async def save_failure(response):
        try:
            await asyncio.shield(asyncio.to_thread(receipt, response))
        except Exception:
            # The durable reserve remains in_progress/unconfirmed. Never replay
            # that request ID merely because publishing its receipt failed.
            return False
        return True

    try:
        config, identity_ref = await asyncio.to_thread(
            configuration, user_id, workspace_id, connection_id, command.expected_version
        )
        manager = SessionManager(HostSecrets(config), HostGate())
        service = MCPService(manager, None)  # Discovery performs no business exchange.
        snapshot = await service.discover(config, identity_ref)
        completed = True
        return await asyncio.to_thread(publish, snapshot, config, identity_ref)
    except asyncio.CancelledError:
        await save_failure(
            {
                "id": None,
                "status": "outcome_unknown",
                "message": "MCP目录发现被取消；请按原请求记录核对，不自动重新启动程序。",
            }
        )
        raise
    except AppError as error:
        await save_failure({"id": None, "status": "failed", "message": error.message})
        raise
    except Exception as error:
        await save_failure(
            {
                "id": None,
                "status": "outcome_unknown" if completed else "failed",
                "message": f"MCP发现回执未确认（{type(error).__name__}）；未自动重新启动程序。",
            }
        )
        raise AppError(
            "mcp_discovery_unconfirmed" if completed else "mcp_discovery_failed",
            "MCP发现结果未确认，请按原请求记录核对。"
            if completed
            else f"MCP发现失败（{type(error).__name__}），请检查程序和连接。",
            502,
            "outcome_unknown" if completed else "dependency",
        ) from None
    finally:
        if manager is not None:
            # SDK ownership scopes must exit even when the HTTP caller cancelled.
            # Cancellation is recorded as unknown, never proof of remote rollback.
            await asyncio.shield(manager.aclose())


def load_snapshot(db, user_id, workspace_id, snapshot_id):
    row = db.scalar(
        select(MCPSnapshot).where(
            MCPSnapshot.id == snapshot_id,
            MCPSnapshot.user_id == user_id,
            MCPSnapshot.workspace_id == workspace_id,
        )
    )
    if row is None:
        raise AppError("mcp_snapshot_missing", "此工具目录不存在或不可访问。", 404)
    return ToolSnapshot.model_validate(row.snapshot)
