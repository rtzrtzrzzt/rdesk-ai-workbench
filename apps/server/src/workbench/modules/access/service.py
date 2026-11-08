"""One durable authority for public requests and all work they create.

Keys authenticate a client; execution bindings preserve that client's identity
after disconnects, key rotation, background work and conversation deletion.
"""

import hashlib
import hmac
import secrets
from datetime import timedelta
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid5

from sqlalchemy import func, select

from workbench.config import get_settings
from workbench.contracts.errors import AppError
from workbench.contracts.execution import ExecutionScope
from workbench.infrastructure.database import new_id, now
from workbench.modules.access.domain import (
    ApiClient,
    ApiKey,
    ExecutionBinding,
    ExternalNamespace,
    Grant,
)

ACTIONS = frozenset({"invoke", "read", "write", "files", "approve"})
INTERNAL_ARTIFACT_KINDS = frozenset({"change_snapshot"})


def denied(message="调用方没有这项能力。", code="api_permission_denied"):
    return AppError(code, message, 403, "policy")


def external_request_id(client_id, request_id):
    return str(uuid5(UUID(str(client_id)), f"public-request:{request_id}"))


def client_view(row):
    return {
        "id": row.id,
        "name": row.name,
        "enabled": row.enabled,
        "version": row.version,
        "requests_per_minute": row.requests_per_minute,
        "max_active_runs": row.max_active_runs,
        "created_at": row.created_at,
    }


def owned_client(db, owner_user_id, client_id, *, lock=False):
    query = select(ApiClient).where(
        ApiClient.id == client_id, ApiClient.owner_user_id == owner_user_id
    )
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise AppError("api_client_not_found", "调用方不存在。", 404)
    return row


def require_client(db, client_id):
    row = db.get(ApiClient, str(client_id), populate_existing=True)
    if row is None or not row.enabled:
        raise denied("调用方已停用。", "api_client_disabled")
    return row


def create_client(db, owner_user_id, command):
    identifier = str(uuid5(UUID(owner_user_id), f"api-client:{command.request_id}"))
    row = db.get(ApiClient, identifier)
    if row:
        if (row.name, row.requests_per_minute, row.max_active_runs) != (
            command.name,
            command.requests_per_minute,
            command.max_active_runs,
        ):
            raise AppError("request_conflict", "相同请求标识的参数已改变。", 409)
        return client_view(row)
    row = ApiClient(
        id=identifier,
        owner_user_id=owner_user_id,
        name=command.name,
        requests_per_minute=command.requests_per_minute,
        max_active_runs=command.max_active_runs,
    )
    db.add(row)
    db.flush()
    return client_view(row)


def key_view(row):
    return {
        "id": row.id,
        "client_id": row.client_id,
        "prefix": row.prefix,
        "expires_at": row.expires_at,
        "revoked_at": row.revoked_at,
        "last_used_at": row.last_used_at,
        "created_at": row.created_at,
    }


def issue_key(db, owner_user_id, client_id, command):
    owned_client(db, owner_user_id, client_id, lock=True)
    identifier = str(uuid5(UUID(client_id), f"api-key:{command.request_id}"))
    previous = db.get(ApiKey, identifier)
    if previous:
        # Never put plaintext keys in durable request receipts just to replay them.
        return {**key_view(previous), "key": None, "shown_once": True}
    if command.expires_at and (command.expires_at.tzinfo is None or command.expires_at <= now()):
        raise AppError("key_expiry_invalid", "密钥到期时间必须为带时区的未来时间。", 422)
    raw = "rdesk_" + secrets.token_urlsafe(32)
    row = ApiKey(
        id=identifier,
        client_id=client_id,
        token_digest=hashlib.sha256(raw.encode()).hexdigest(),
        prefix=raw[:14],
        expires_at=command.expires_at,
    )
    db.add(row)
    db.flush()
    return {**key_view(row), "key": raw, "shown_once": True}


def authenticate(db, raw_key, *, count_request=True):
    if not isinstance(raw_key, str) or not 20 <= len(raw_key) <= 256:
        raise AppError("api_key_invalid", "API Key 无效或已到期。", 401, "policy")
    digest = hashlib.sha256(raw_key.encode()).hexdigest()
    key = db.scalar(select(ApiKey).where(ApiKey.token_digest == digest))
    if (
        key is None
        or not hmac.compare_digest(key.token_digest, digest)
        or key.revoked_at
        or (key.expires_at and key.expires_at <= now())
    ):
        raise AppError("api_key_invalid", "API Key 无效或已到期。", 401, "policy")
    query = select(ApiClient).where(ApiClient.id == key.client_id)
    client = db.scalar(query.with_for_update() if count_request else query)
    if client is None or not client.enabled:
        raise denied("调用方已停用。", "api_client_disabled")
    observed = now()
    if count_request:
        if client.request_window is None or observed - client.request_window >= timedelta(
            minutes=1
        ):
            client.request_window, client.request_count = observed, 0
        if client.request_count >= client.requests_per_minute:
            raise AppError(
                "api_rate_limit",
                "请求频率超过此调用方的上限。",
                429,
                "resource",
                True,
                details={"retry_after_seconds": 60},
            )
        client.request_count += 1
    if count_request:
        key.last_used_at = observed
        db.flush()
    return client


def grant_view(row):
    return {
        "id": row.id,
        "client_id": row.client_id,
        "service_id": row.service_id,
        "enabled": row.enabled,
        "actions": row.actions,
        "allowed_tools": row.allowed_tools,
        "version": row.version,
    }


def save_grant(db, owner_user_id, client_id, service_id, command):
    from workbench.modules.agent_services import service as publications

    owned_client(db, owner_user_id, client_id, lock=True)
    publications.get(db, owner_user_id, service_id)
    if set(command.actions) - ACTIONS:
        raise AppError("api_scope_invalid", "包含未注册的授权动作。", 422)
    row = db.scalar(
        select(Grant)
        .where(Grant.client_id == client_id, Grant.service_id == service_id)
        .with_for_update()
    )
    if command.expected_version != (row.version if row else 0):
        raise AppError("version_conflict", "授权已更新，请重新读取。", 409)
    if row is None:
        row = Grant(client_id=client_id, service_id=service_id)
        db.add(row)
    else:
        row.version += 1
    row.enabled, row.actions, row.allowed_tools = (
        command.enabled,
        list(dict.fromkeys(command.actions)),
        command.allowed_tools,
    )
    db.flush()
    return grant_view(row)


def authorize_service(db, client_id, service_id, action):
    from workbench.modules.agent_services.service import runtime_policy

    client = require_client(db, client_id)
    policy = runtime_policy(db, str(service_id))
    if policy["owner_user_id"] != client.owner_user_id:
        raise denied()
    row = db.scalar(
        select(Grant)
        .where(Grant.client_id == client.id, Grant.service_id == str(service_id))
        .execution_options(populate_existing=True)
    )
    if row is None or not row.enabled or action not in row.actions:
        raise denied()
    if not policy["enabled"] and action in {"invoke", "write", "approve"}:
        raise denied("Agent 服务已停用。", "agent_disabled")
    return row


def get_namespace(db, client_id, service_id):
    """A client/service workspace never points at the operator's private project."""
    from workbench.modules.workspaces import Workspace

    client = require_client(db, client_id)
    # Client lock serializes namespace creation without leaving two workspace registrations.
    db.scalar(select(ApiClient).where(ApiClient.id == client.id).with_for_update())
    authorize_service(db, client.id, str(service_id), "invoke")
    row = db.scalar(
        select(ExternalNamespace).where(
            ExternalNamespace.client_id == client.id,
            ExternalNamespace.service_id == str(service_id),
        )
    )
    if row:
        return row
    identifier = new_id()
    data = get_settings().data_dir.resolve()
    base = data.parent / (data.name + "-api-workspaces")
    if base.is_symlink():
        raise denied("API 工作空间不能是符号链接。")
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = base / identifier
    directory.mkdir(mode=0o700)
    workspace = Workspace(
        user_id=client.owner_user_id,
        name=f"API · {client.name}",
        root_path=str(directory.resolve()),
    )
    db.add(workspace)
    db.flush()
    row = ExternalNamespace(
        id=identifier,
        owner_user_id=client.owner_user_id,
        client_id=client.id,
        service_id=str(service_id),
        workspace_id=workspace.id,
    )
    db.add(row)
    db.flush()
    return row


def create_execution_binding(db, client, service_id, revision_id, namespace_id, snapshot):
    from workbench.modules.agent_services.service import runtime_policy
    from workbench.modules.runs.domain import TERMINAL_STATUSES, Run

    grant = authorize_service(db, client.id, str(service_id), "invoke")
    policy = runtime_policy(db, str(service_id), str(revision_id))
    namespace = db.get(ExternalNamespace, str(namespace_id))
    if namespace is None or (namespace.client_id, namespace.service_id) != (
        client.id,
        str(service_id),
    ):
        raise denied()
    active = db.scalar(
        select(func.count())
        .select_from(Run)
        .join(ExecutionBinding, ExecutionBinding.id == Run.execution_binding_id)
        .where(
            ExecutionBinding.client_id == client.id,
            Run.kind == "agent",
            Run.parent_run_id.is_(None),
            Run.status.not_in(TERMINAL_STATUSES),
        )
    )
    if active >= client.max_active_runs:
        raise AppError("api_capacity", "此调用方的排队和活动任务已达上限。", 429, "resource", True)
    definition = policy["revision_definition"]
    tools = set(definition["allowed_tools"])
    if grant.allowed_tools is not None:
        tools &= set(grant.allowed_tools)
    # Only the published definition is authoritative, never caller-supplied JSON.
    frozen = {
        **definition,
        "allowed_tools": sorted(tools),
        "model_connection_id": str(definition["model_connection_id"]),
    }
    from workbench.modules.agent_services.sources import materialize

    frozen.update(materialize(db, namespace, str(revision_id), definition))
    row = ExecutionBinding(
        owner_user_id=client.owner_user_id,
        origin="api",
        client_id=client.id,
        service_id=str(service_id),
        revision_id=str(revision_id),
        namespace_id=namespace.id,
        snapshot=frozen,
    )
    db.add(row)
    db.flush()
    return row


def binding_for_thread(db, thread, identifier=None):
    from workbench.modules.agent_services.domain import AgentSessionBinding

    namespace = db.scalar(
        select(ExternalNamespace).where(ExternalNamespace.workspace_id == thread.workspace_id)
    )
    if namespace is None:
        if identifier:
            raise denied("外部任务不能使用本机私人工作区。")
        return None
    session = db.scalar(
        select(AgentSessionBinding).where(AgentSessionBinding.thread_id == thread.id)
    )
    if session is None or session.namespace_id != namespace.id:
        raise denied("外部会话授权绑定缺失。", "execution_binding_invalid")
    if identifier:
        row = db.get(ExecutionBinding, str(identifier))
        if row is None or (row.namespace_id, row.revision_id, row.owner_user_id) != (
            namespace.id,
            session.revision_id,
            thread.user_id,
        ):
            raise denied("执行授权与会话不匹配。", "execution_binding_invalid")
        authorize_service(db, row.client_id, row.service_id, "invoke")
        return row
    client = require_client(db, namespace.client_id)
    return create_execution_binding(
        db, client, namespace.service_id, session.revision_id, namespace.id, {}
    )


def defaults_for_binding(binding):
    from workbench.contracts.requests import RunOptions
    from workbench.modules.preferences.service import ResolvedDefaults

    options = RunOptions.model_validate(
        {"model_output_recovery_tokens": 0, **binding.snapshot["runtime_options"]}
    )
    if options.approval_mode == "full_access":
        raise denied("对外执行不能获得主机完全开放权限。", "api_full_access_denied")
    return ResolvedDefaults(
        options=options,
        sources=dict.fromkeys(type(options).model_fields, "agent_service"),
        model_connection_id=str(binding.snapshot["model_connection_id"]),
        model_source="agent_service",
        user_version=0,
        workspace_version=0,
    )


def binding_for_run(db, run):
    origin = getattr(run, "execution_origin", "owner")
    identifier = getattr(run, "execution_binding_id", None)
    if origin == "owner" and identifier is None:
        return None
    row = db.get(ExecutionBinding, identifier) if identifier else None
    if row is None or origin != "api" or row.owner_user_id != run.user_id:
        raise denied("执行授权记录缺失或不匹配。", "execution_binding_invalid")
    namespace = db.get(ExternalNamespace, row.namespace_id)
    if namespace is None or (
        namespace.workspace_id,
        namespace.client_id,
        namespace.service_id,
        namespace.owner_user_id,
    ) != (run.workspace_id, row.client_id, row.service_id, row.owner_user_id):
        raise denied("执行空间与授权不匹配。", "execution_binding_invalid")
    return row


def scope_for_run(db, run):
    from workbench.modules.agent_services.service import runtime_policy

    binding = binding_for_run(db, run)
    if binding is None:
        return None
    grant = authorize_service(db, binding.client_id, binding.service_id, "invoke")
    client = require_client(db, binding.client_id)
    policy = runtime_policy(db, binding.service_id, binding.revision_id)
    tools = set(binding.snapshot.get("allowed_tools", []))
    tools &= set(policy["revision_definition"].get("allowed_tools", []))
    tools &= set(policy["definition"].get("allowed_tools", []))
    if grant.allowed_tools is not None:
        tools &= set(grant.allowed_tools)
    source_paths = []
    current = policy["definition"]
    if binding.snapshot.get("source_workspace_id") and binding.snapshot.get(
        "source_workspace_id"
    ) == current.get("source_workspace_id"):
        for original in binding.snapshot.get("source_paths", []):
            for allowed in current.get("source_paths", []):
                old, live = PurePosixPath(original), PurePosixPath(allowed)
                if old.is_relative_to(live):
                    source_paths.append(original)
                elif live.is_relative_to(old):
                    source_paths.append(allowed)
    return ExecutionScope(
        client_id=client.id,
        namespace_id=binding.namespace_id,
        service_id=binding.service_id,
        revision_id=binding.revision_id,
        workspace_id=run.workspace_id,
        allowed_tools=frozenset(tools),
        allowed_model_ids=frozenset({str(binding.snapshot["model_connection_id"])}),
        instructions=binding.snapshot.get("instructions", ""),
        policy_version=(client.version, grant.version, policy.get("version", 1)),
        source_revision=binding.snapshot.get("source_revision"),
        source_paths=tuple(sorted(set(source_paths))),
    )


def authorize_run_model(db, run):
    scope = scope_for_run(db, run)
    if scope and run.model_connection_id not in scope.allowed_model_ids:
        raise denied("模型不在此 Agent 的授权范围。", "api_model_denied")
    return scope


def authorize_tool(db, run, name, values):
    scope = scope_for_run(db, run)
    if scope and name not in scope.allowed_tools:
        raise denied("工具未发布给此调用方，或授权已撤销。", "api_tool_denied")
    if scope and name in {"write_file", "apply_patch", "edit_files", "edit_notebook"}:
        from workbench.modules.workspaces import Workspace

        root = Path(db.get(Workspace, run.workspace_id).root_path).resolve()
        targets = (
            [values["path"]]
            if "path" in values
            else [item["path"] for item in values.get("files", [])]
        )
        for target in targets:
            resolved = (root / target).resolve()
            if resolved.is_relative_to(root / "_sources"):
                raise denied("发布来源为只读快照，请将修改结果写入其他文件。", "source_read_only")
    return scope


def inherit_binding(source, target):
    target.execution_origin = getattr(source, "execution_origin", "owner")
    target.execution_binding_id = getattr(source, "execution_binding_id", None)


def require_run(db, client_id, run, action="read"):
    binding = binding_for_run(db, run)
    if binding is None or binding.client_id != str(client_id):
        raise AppError("run_not_found", "任务不存在或不可访问。", 404)
    authorize_service(db, client_id, binding.service_id, action)
    return binding


def internal_artifact(row):
    return row.kind in INTERNAL_ARTIFACT_KINDS or bool((row.metadata_json or {}).get("internal"))


def require_public_artifact(db, client_id, artifact, action="files"):
    namespace = db.scalar(
        select(ExternalNamespace).where(
            ExternalNamespace.workspace_id == artifact.workspace_id,
            ExternalNamespace.client_id == str(client_id),
        )
    )
    if namespace is None or internal_artifact(artifact):
        raise AppError("artifact_not_found", "文件不存在或不可访问。", 404)
    authorize_service(db, client_id, namespace.service_id, action)
    return namespace


def maintain(db):
    """Persist revocation as normal cancellation; resource cleanup still runs."""
    from workbench.modules.runs.domain import TERMINAL_STATUSES, Run
    from workbench.modules.runs.service import cancel_descendants, request_stop

    rows = db.scalars(
        select(Run)
        .where(
            Run.execution_origin == "api",
            Run.status.not_in(TERMINAL_STATUSES),
            Run.cancel_requested_at.is_(None),
            Run.parent_run_id.is_(None),
        )
        .limit(100)
    )
    for run in rows:
        try:
            scope_for_run(db, run)
        except AppError as error:
            if error.kind == "policy":
                target = db.scalar(select(Run).where(Run.id == run.id).with_for_update())
                cancel_descendants(db, target)
                request_stop(db, target)
