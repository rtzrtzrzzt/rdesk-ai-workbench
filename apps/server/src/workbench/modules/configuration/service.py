"""Configuration facade over module services, with safe discovery and durable receipts."""

import asyncio
import inspect
import json
from uuid import UUID, uuid4, uuid5

from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine
from workbench.modules.preferences import configuration as state
from workbench.modules.runs.domain import ClientRequest
from workbench.modules.runs.service import idempotent
from workbench.modules.workspace_service import get as workspace

from . import catalog, dto


def public(value):
    value = jsonable_encoder(value)
    if isinstance(value, dict):
        result = {
            key: public(item)
            for key, item in value.items()
            if key.casefold() not in catalog.SECRET_FIELDS
            and key != "args"
            and not (key == "command" and isinstance(item, list))
        }
        if "args" in value:
            result["arguments_configured"] = bool(value["args"])
        if isinstance(value.get("command"), list):
            result["command_executable"] = value["command"][0] if value["command"] else None
            result["arguments_configured"] = len(value["command"]) > 1
        return result
    if isinstance(value, list):
        return [public(item) for item in value]
    return value


def _no_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.casefold() in catalog.SECRET_FIELDS:
                raise AppError(
                    "settings_secret_input",
                    "此接口只接受凭据引用；密钥正文及清除凭据请使用专用用户入口。",
                    422,
                )
            _no_secrets(item)
    elif isinstance(value, list):
        for item in value:
            _no_secrets(item)


def _section(name):
    if name not in catalog.SECTIONS:
        raise AppError("settings_section_unknown", "未知设置分类，请先discover_settings。", 422)
    return catalog.SECTIONS[name]


def scoped(command):
    scopes = _section(command.section).scopes
    if command.scope is None:
        return command.model_copy(update={"scope": scopes[0]})
    if command.scope not in scopes:
        raise AppError(
            "settings_scope_mismatch",
            "此分类不支持该scope，请查看discover_settings返回的scopes。",
            422,
        )
    return command


def _read_values(db, user_id, workspace_id, command):
    name, resource_id = command.section, str(command.resource_id) if command.resource_id else None
    if name == "experience":
        return state.experience(db, user_id)
    if name == "conversation_history":
        from workbench.modules.conversation_history.settings import view

        return view(db, user_id)
    if name in {"runtime", "workspace_runtime"}:
        from workbench.modules.preferences import service

        return (
            service.get(db, user_id)
            if name == "runtime"
            else service.get_workspace(db, user_id, workspace_id)
        )
    if name == "models":
        from workbench.modules.models import service

        return (
            service.view(service.get(db, user_id, resource_id))
            if resource_id
            else service.list_connections(db, user_id)
        )
    if name == "search":
        from workbench.modules.search import service

        return service.get_connection(db, user_id, workspace_id, command.scope)
    if name == "mcp":
        from workbench.modules.mcp import host

        rows = host.listing(db, user_id, workspace_id)
        return (
            host.view(host.get_connection(db, user_id, workspace_id, resource_id))
            if resource_id
            else rows
        )
    if name in {"computer", "automation_policy"}:
        from workbench.modules.automation import service, settings

        return (
            settings.view(db, user_id)
            if name == "computer"
            else service.get_policy(db, user_id, workspace_id)
        )
    if name == "extensions":
        from workbench.modules.extensions import service

        return (
            service.view(
                db, service.installation(db, user_id, workspace_id, resource_id), workspace_id
            )
            if resource_id
            else service.listing(db, user_id, workspace_id)
        )
    if name == "media":
        from workbench.modules.media import service

        return (
            service.connection_view(service.connection(db, user_id, workspace_id, resource_id))
            if resource_id
            else service.listing(db, user_id, workspace_id)
        )
    if name == "global_rules":
        from workbench.modules.memory import service

        return service.get_global_rules(db, user_id, workspace_id)
    if name == "developer_tools":
        user = state.get_state(db, user_id, name)
        override = (
            state.get_state(db, user_id, name, workspace_id)
            if command.scope == "workspace"
            else None
        )
        row = override if command.scope == "workspace" else user
        effective = override or user
        return {
            "version": row.version if row else 0,
            "values": row.values if row else {},
            "effective": effective.values if effective else {"language_servers": None},
            "source": "workspace" if override else "user" if user else "automatic_discovery",
        }
    if name == "file_processing":
        from workbench.modules.files.dto import ProcessFile
        from workbench.modules.files.processors import capabilities

        return {"processors": capabilities(), "job_schema": catalog.public_schema(ProcessFile)}
    if name == "credentials":
        return {
            "secret_values_readable": False,
            "interface": "/api/v1/vault",
            "status_interface": "/api/v1/vault/status",
            "instructions": catalog.SECTIONS[name].instructions,
        }
    if name == "application":
        from workbench.config import get_settings

        settings = get_settings()
        safe = {
            key: getattr(settings, key)
            for key in (
                "host",
                "port",
                "data_dir",
                "frontend_dir",
                "session_hours",
                "secure_cookies",
                "lan_enabled",
                "public_origins",
                "session_idle_seconds",
                "worker_lease_seconds",
                "model_timeout_seconds",
                "command_max_active_per_run",
                "command_max_active_total",
            )
        }
        return {
            "effective": safe,
            "read_only": True,
            "source": "process_environment",
            "restart_required": True,
            "instructions": catalog.SECTIONS[name].instructions,
        }
    return {
        "source": catalog.SECTIONS[name].source,
        "effective_at": catalog.SECTIONS[name].effective_at,
        "instructions": catalog.SECTIONS[name].instructions,
        "read_only": True,
        "reason": "operator_or_dedicated_maintenance_boundary",
    }


def read(db, user_id, workspace_id, command):
    command = scoped(command)
    workspace(db, user_id, workspace_id)
    section = _section(command.section)
    result = {
        "section": command.section,
        "scope": command.scope,
        "effective_at": section.effective_at,
        "values": public(_read_values(db, user_id, workspace_id, command)),
        "instructions": section.instructions,
    }
    if command.include_schema:
        result["contract"] = catalog.describe(command.section)
    return result


def validate(command, request_id=None):
    command = scoped(command)
    section = _section(command.section)
    contract = catalog.actions().get(command.section, {}).get(command.action)
    if section.operator_only or contract is None:
        raise AppError(
            "settings_action_unavailable",
            "此分类须使用目录所列的专用配置或维护入口。",
            409,
            "policy",
        )
    if len(json.dumps(command.values, ensure_ascii=False)) > 262144:
        raise AppError("settings_budget", "单次配置内容超过256KiB，请缩小修改范围。", 422)
    _no_secrets(command.values)
    values = dict(command.values)
    if "request_id" in contract.model_fields:
        values["request_id"] = request_id or uuid4()
    if (
        command.section in {"search", "media"}
        or command.section == "extensions"
        and command.action == "install"
    ):
        if "scope" in values and values["scope"] != command.scope:
            raise AppError("settings_scope_mismatch", "scope必须与本次配置范围一致。", 422)
        values["scope"] = command.scope
    if (
        command.section in {"models", "extensions"}
        and command.action not in {"create", "install"}
        and not command.resource_id
    ):
        raise AppError(
            "settings_resource_required", "此操作需要read_settings返回的resource_id。", 422
        )
    if command.section == "mcp" and command.action == "discover" and not command.resource_id:
        raise AppError("settings_resource_required", "发现MCP前须提供已有连接ID。", 422)
    try:
        parsed = contract.model_validate(values)
        if command.section == "developer_tools":
            state.DeveloperTools.model_validate(parsed.model_dump(include={"language_servers"}))
    except ValidationError as error:
        from workbench.modules.tools.validation import parameter_error

        raise parameter_error(error, contract) from None
    return parsed


def validate_target(db, user_id, workspace_id, command, request_id=None):
    """Preserve hidden trusted command arguments when editing other fields."""
    command = scoped(command)
    values = dict(command.values)
    _no_secrets(values)
    if command.section == "mcp" and command.action == "save" and command.resource_id:
        from workbench.modules.mcp import host

        row = host.get_connection(db, user_id, workspace_id, str(command.resource_id))
        configuration = dict(values.get("configuration", {}))
        if "args" not in configuration:
            configuration["args"] = row.configuration.get("args", [])
        values["configuration"] = configuration
    if command.section == "developer_tools" and isinstance(values.get("language_servers"), list):
        row = state.get_state(
            db, user_id, "developer_tools", workspace_id if command.scope == "workspace" else None
        )
        previous = (
            {item["language"]: item for item in (row.values.get("language_servers") or [])}
            if row
            else {}
        )
        values["language_servers"] = [
            {
                **(
                    {"command": previous[item["language"]]["command"]}
                    if "command" not in item and item.get("language") in previous
                    else {}
                ),
                **item,
            }
            for item in values["language_servers"]
        ]
    # Existing hidden values are host-owned, not new model secret input. The DTO
    # validates them, while the public preview still hides all command arguments.
    contract = catalog.actions().get(command.section, {}).get(command.action)
    if values == command.values:
        return validate(command, request_id)
    validate(command, request_id) if command.section != "developer_tools" else None
    if contract is None:
        return validate(command, request_id)
    if "request_id" in contract.model_fields:
        values["request_id"] = request_id or uuid4()
    try:
        result = contract.model_validate(values)
        if command.section == "developer_tools":
            state.DeveloperTools.model_validate(result.model_dump(include={"language_servers"}))
        return result
    except ValidationError as error:
        from workbench.modules.tools.validation import parameter_error

        raise parameter_error(error, contract) from None


def preview(db, user_id, workspace_id, command):
    command = scoped(command)
    parsed = validate_target(db, user_id, workspace_id, command)
    before = read(db, user_id, workspace_id, command)
    current = before["values"]
    expected = command.values.get("expected_version")
    if (
        isinstance(current, dict)
        and expected is not None
        and "version" in current
        and current["version"] != expected
    ):
        raise AppError("settings_conflict", "预览使用的版本已过期，请重新读取。", 409)
    return {
        "section": command.section,
        "action": command.action,
        "scope": command.scope,
        "resource_id": command.resource_id,
        "before": current,
        "proposed": public(parsed.model_dump(mode="json", exclude={"request_id"})),
        "effective_at": _section(command.section).effective_at,
        "validated": "schema_and_current_version",
        "saved": False,
        "note": (
            "保存时仍执行模块的权限、版本和运行条件检查；预览不会连接提供方、安装组件或启动服务。"
        ),
    }


async def _write(db, user_id, workspace_id, command, parsed, guard=None):
    if guard:
        guard(db)
    name, action = command.section, command.action
    resource_id = str(command.resource_id) if command.resource_id else str(uuid4())
    if name == "experience":
        return state.save_experience(db, user_id, parsed)
    if name in {"runtime", "workspace_runtime"}:
        from workbench.modules.preferences import service

        return (
            service.save(db, user_id, parsed)
            if name == "runtime"
            else service.save_workspace(db, user_id, workspace_id, parsed)
        )
    if name == "models":
        from workbench.modules.models import service

        if action == "capabilities":
            return service.declare_capabilities(db, user_id, resource_id, parsed)
        return (
            service.create(db, user_id, parsed.model_dump(mode="json"))
            if action == "create"
            else service.update(db, user_id, resource_id, parsed.model_dump(mode="json"))
        )
    if name == "search":
        from workbench.modules.search import service

        return await service.save_connection(db, user_id, workspace_id, parsed, before_save=guard)
    if name == "mcp":
        from workbench.modules.mcp import host

        if action == "discover":
            db.rollback()
            return await host.discover(
                user_id, workspace_id, resource_id, parsed, before_publish=guard
            )
        return host.save(db, user_id, workspace_id, resource_id, parsed)
    if name in {"computer", "automation_policy"}:
        from workbench.modules.automation import service, settings

        return (
            settings.save(db, user_id, parsed)
            if name == "computer"
            else service.save_policy(db, user_id, workspace_id, parsed)
        )
    if name == "extensions":
        from workbench.modules.extensions import service

        if action in {"install", "upgrade"}:
            db.rollback()
            args = (
                (user_id, workspace_id, parsed)
                if action == "install"
                else (user_id, workspace_id, resource_id, parsed)
            )
            return await asyncio.to_thread(
                getattr(service, "install" if action == "install" else "upgrade"),
                *args,
                before_publish=guard,
            )
        method = {
            "save": service.change,
            "remove": service.remove,
            "import_mcp": service.import_mcp,
        }[action]
        return method(db, user_id, workspace_id, resource_id, parsed)
    if name == "media":
        from workbench.modules.media import service

        return service.save(db, user_id, workspace_id, resource_id, parsed)
    if name == "global_rules":
        from workbench.modules.memory import service

        db.rollback()
        return await asyncio.to_thread(
            service.update_global_rules, db, user_id, workspace_id, parsed, before_publish=guard
        )
    if name == "developer_tools":
        values = state.DeveloperTools.model_validate(
            {"language_servers": parsed.language_servers}
        ).model_dump(mode="json")
        row = state.save_state(
            db,
            user_id,
            name,
            values,
            parsed.expected_version,
            workspace_id if command.scope == "workspace" else None,
        )
        return {"version": row.version, "values": row.values}
    raise AppError("settings_action_unavailable", "此操作没有配置适配器。", 409)


def receipt(db, user_id, request_id):
    row = db.scalar(
        select(ClientRequest).where(
            ClientRequest.user_id == user_id, ClientRequest.request_id == str(request_id)
        )
    )
    if (
        row is None
        or not isinstance(row.response, dict)
        or row.response.get("protocol") != "workbench-settings/1"
    ):
        raise AppError("settings_receipt_missing", "没有此配置请求回执。", 404)
    return row.response


async def apply(user_id, workspace_id, command, *, actor_run_id=None):
    command = scoped(command)
    request_id = str(command.request_id)
    derived_id = uuid5(UUID(request_id), "configuration:module-action")
    created = False

    def reserve():
        nonlocal created
        created = True
        return {
            "protocol": "workbench-settings/1",
            "request_id": request_id,
            "status": "outcome_unknown",
            "section": command.section,
            "action": command.action,
            "resource_id": str(command.resource_id) if command.resource_id else None,
            "note": "提交意图已持久保存；若没有完成回执，请先核对实际配置，禁止新请求盲重放。",
        }

    with Session(engine()) as db, db.begin():
        workspace(db, user_id, workspace_id)
        result = idempotent(
            db,
            user_id,
            request_id,
            f"settings:{workspace_id}",
            command.model_dump(mode="json"),
            reserve,
        )
    if not created:
        return result

    def guard(db):
        if actor_run_id is None:
            return
        from workbench.modules.runs.domain import TERMINAL_STATUSES, Run
        from workbench.modules.runs.ownership import assert_owned

        actor = db.scalar(select(Run).where(Run.id == actor_run_id).with_for_update())
        assert_owned(db, actor_run_id)
        if actor.cancel_requested_at or actor.status in TERMINAL_STATUSES:
            raise AppError(
                "settings_actor_stopped", "原任务已停止，尚未提交的配置不再发布。", 409, "policy"
            )

    dispatched = False
    try:
        with Session(engine()) as db:
            workspace(db, user_id, workspace_id)
            parsed = validate_target(db, user_id, workspace_id, command, derived_id)
            dispatched = True
            result = _write(db, user_id, workspace_id, command, parsed, guard)
            if inspect.isawaitable(result):
                result = await result
            db.commit()
            result = {
                "protocol": "workbench-settings/1",
                "request_id": request_id,
                "status": "applied",
                "section": command.section,
                "action": command.action,
                "effective_at": _section(command.section).effective_at,
                "result": public(result),
            }
    except AppError as error:
        result = {
            "protocol": "workbench-settings/1",
            "request_id": request_id,
            "status": "outcome_unknown" if dispatched else "rejected",
            "section": command.section,
            "error": {"code": error.code, "message": error.message},
        }
    with Session(engine()) as db, db.begin():
        row = db.scalar(
            select(ClientRequest)
            .where(ClientRequest.user_id == user_id, ClientRequest.request_id == request_id)
            .with_for_update()
        )
        row.response = result
    return result


async def tool(run_id, operation_id, name, values):
    from workbench.modules.runs.domain import Run
    from workbench.modules.runs.ownership import assert_owned

    with Session(engine()) as db:
        run = db.get(Run, run_id)
        assert_owned(db, run_id)
        user_id, workspace_id = run.user_id, run.workspace_id
        if name == "discover_settings":
            return catalog.discover(**values)
        if name == "settings_receipt":
            return receipt(db, user_id, values["request_id"])
        if name == "read_settings":
            return read(db, user_id, workspace_id, dto.ReadSettings(**values))
        if name == "preview_settings":
            return preview(db, user_id, workspace_id, dto.SettingsChange(**values))
        if run.parent_run_id:
            raise AppError("settings_child_write_denied", "配置写入由主任务执行。", 403, "policy")
        if run.cancel_requested_at:
            raise AppError("run_finished", "任务已停止，不能保存配置。", 409)
    return await apply(
        user_id,
        workspace_id,
        dto.ApplySettings(**values, request_id=operation_id),
        actor_run_id=run_id,
    )
