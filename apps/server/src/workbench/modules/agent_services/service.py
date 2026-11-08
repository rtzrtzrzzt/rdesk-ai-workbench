from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.modules.agent_services import dto, sources
from workbench.modules.agent_services.domain import AgentService, AgentServiceRevision
from workbench.modules.models import service as models


def get(db: Session, owner_user_id: str, service_id: str, *, lock: bool = False) -> AgentService:
    query = select(AgentService).where(
        AgentService.id == service_id, AgentService.owner_user_id == owner_user_id
    )
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise AppError("agent_not_found", "Agent 不存在或不可访问。", 404)
    return row


def revision(db: Session, row: AgentService) -> AgentServiceRevision:
    result = db.get(AgentServiceRevision, row.current_revision_id)
    if result is None or result.service_id != row.id:
        raise AppError("agent_unavailable", "Agent 当前不可调用。", 409)
    return result


def owner_view(db: Session, row: AgentService) -> dict:
    current = revision(db, row)
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "enabled": row.enabled,
        "version": row.version,
        "revision_id": current.id,
        "revision_number": current.number,
        "definition": current.definition,
    }


def list_owned(db: Session, owner_user_id: str) -> list[dict]:
    rows = db.scalars(
        select(AgentService)
        .where(AgentService.owner_user_id == owner_user_id)
        .order_by(AgentService.created_at.desc())
    )
    return [owner_view(db, row) for row in rows]


def _validate(db: Session, owner_user_id: str, definition: dto.Definition) -> dict:
    # Lookup checks ownership and current availability without returning provider configuration.
    models.get(db, owner_user_id, str(definition.model_connection_id), lock=True)
    from workbench.modules.tools.registry import TOOLS

    if set(definition.allowed_tools) - set(TOOLS):
        raise AppError("agent_tool_unavailable", "发布配置包含当前不可用的工具。", 422)
    return sources.validate(db, owner_user_id, definition)


def publish(db: Session, owner_user_id: str, command: dto.PublishAgent) -> dict:
    definition = _validate(db, owner_user_id, command.definition)
    row = AgentService(
        owner_user_id=owner_user_id,
        name=command.name,
        description=command.description,
        enabled=True,
    )
    db.add(row)
    db.flush()
    current = AgentServiceRevision(service_id=row.id, number=1, definition=definition)
    db.add(current)
    db.flush()
    sources.prepare_revision(db, owner_user_id, current.id, definition)
    row.current_revision_id = current.id
    db.flush()
    return owner_view(db, row)


def revise(db: Session, owner_user_id: str, service_id: str, command: dto.ReviseAgent) -> dict:
    row = get(db, owner_user_id, service_id, lock=True)
    _expected(row, command.expected_version)
    definition = _validate(db, owner_user_id, command.definition)
    current = AgentServiceRevision(
        service_id=row.id, number=revision(db, row).number + 1, definition=definition
    )
    db.add(current)
    db.flush()
    sources.prepare_revision(db, owner_user_id, current.id, definition)
    row.name, row.description = command.name, command.description
    row.current_revision_id = current.id
    row.version += 1
    db.flush()
    return owner_view(db, row)


def _expected(row: AgentService, expected_version: int) -> None:
    if row.version != expected_version:
        raise AppError("version_conflict", "Agent 已更新，请读取最新版本。", 409)


def set_enabled(db: Session, owner_user_id: str, service_id: str, command: dto.AgentState) -> dict:
    row = get(db, owner_user_id, service_id, lock=True)
    _expected(row, command.expected_version)
    row.enabled = command.enabled
    row.version += 1
    db.flush()
    return owner_view(db, row)


def public_view(db: Session, row: AgentService) -> dict:
    current = revision(db, row)
    definition = dto.Definition.model_validate(current.definition)
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "revision_id": current.id,
        "capabilities": dto.Capabilities(
            tools=definition.allowed_tools,
            child_agents="spawn_agent" in definition.allowed_tools
            and definition.runtime_options.child_agent_limit > 0,
            source_snapshot=bool(definition.source_workspace_id),
        ).model_dump(),
    }


def runtime_policy(db: Session, service_id: str, revision_id: str | None = None) -> dict:
    """Pure configuration lookup used by admission and workers; no access-module cycle."""
    row = db.get(AgentService, service_id)
    if row is None:
        raise AppError("agent_not_found", "Agent 不存在或不可访问。", 404)
    current = revision(db, row)
    selected = db.get(AgentServiceRevision, revision_id) if revision_id else current
    if selected is None or selected.service_id != row.id:
        raise AppError("agent_revision_not_found", "Agent 版本不存在。", 404)
    return {
        "enabled": row.enabled,
        "owner_user_id": row.owner_user_id,
        "definition": current.definition,
        "revision_definition": selected.definition,
    }


def publication_capabilities(db: Session, owner_user_id: str) -> dict:
    from workbench.modules.models.domain import ModelConnection
    from workbench.modules.tools.registry import TOOLS

    connections = db.scalars(
        select(ModelConnection).where(ModelConnection.user_id == owner_user_id)
    )
    return {
        "models": [{"id": row.id, "name": row.name} for row in connections],
        "tools": [
            {"name": name, "description": TOOLS[name].description}
            for name in sorted(dto.SAFE_TOOLS & TOOLS.keys())
        ],
        "unsupported": ["host_commands", "mcp", "media_generation"],
        "source_sharing": {
            "mode": "revision_snapshot",
            "read_only": True,
            "explicit_paths_required": True,
            "frozen_at": "publication",
            "max_file_bytes": sources.MAX_FILE_BYTES,
            "max_total_bytes": sources.MAX_TOTAL_BYTES,
            "max_files": sources.MAX_FILES,
            "max_scan_entries": sources.MAX_ENTRIES,
            "max_seconds": sources.MAX_SECONDS,
        },
        "runtime_options_schema": dto.public_runtime_options_schema(),
    }
