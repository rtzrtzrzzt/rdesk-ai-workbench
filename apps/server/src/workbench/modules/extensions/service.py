"""Package lifecycle and scoped read ports; installation never executes a plugin."""

import hashlib
import json
import shutil
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine
from workbench.modules import workspace_service
from workbench.modules.extensions import dto, formats, sources, storage
from workbench.modules.extensions.domain import (
    ExtensionInstallation,
    ExtensionMCPBinding,
    ExtensionOverride,
    ExtensionRevision,
)
from workbench.modules.files.storage import storage_path
from workbench.modules.runs.domain import Run
from workbench.modules.runs.ownership import assert_owned
from workbench.modules.runs.service import idempotent
from workbench.modules.tools.filesystem import authorized_path


def installation(db, user_id, workspace_id, identifier, *, lock=False):
    workspace_service.get(db, user_id, workspace_id)
    query = select(ExtensionInstallation).where(
        ExtensionInstallation.id == identifier,
        ExtensionInstallation.user_id == user_id,
        or_(
            ExtensionInstallation.workspace_id.is_(None),
            ExtensionInstallation.workspace_id == workspace_id,
        ),
    )
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise AppError("extension_not_found", "扩展不存在或不属于当前范围。", 404)
    return row


def effective(db, row, workspace_id) -> tuple[bool, bool | None]:
    override = db.scalar(
        select(ExtensionOverride).where(
            ExtensionOverride.installation_id == row.id,
            ExtensionOverride.workspace_id == workspace_id,
        )
    )
    selected = override.enabled if override else None
    enabled = row.enabled and (selected is not False)
    return row.status == "installed" and enabled, selected


def view(db, row, workspace_id):
    revision = db.get(ExtensionRevision, row.current_revision_id)
    enabled, override = effective(db, row, workspace_id)
    manifest = {key: value for key, value in revision.manifest.items() if key != "files"}
    manifest["file_count"] = len(revision.manifest["files"])
    manifest["total_bytes"] = sum(item["bytes"] for item in revision.manifest["files"].values())
    return {
        "id": row.id,
        "name": row.name,
        "version": row.version,
        "scope": "workspace" if row.workspace_id else "user",
        "workspace_id": row.workspace_id,
        "enabled": row.enabled,
        "effective_enabled": enabled,
        "workspace_override": override,
        "status": row.status,
        "revision_id": revision.id,
        "source": row.source,
        "source_revision": revision.source_revision,
        "manifest": manifest,
    }


def listing(db, user_id, workspace_id):
    workspace_service.get(db, user_id, workspace_id)
    rows = db.scalars(
        select(ExtensionInstallation)
        .where(
            ExtensionInstallation.user_id == user_id,
            or_(
                ExtensionInstallation.workspace_id.is_(None),
                ExtensionInstallation.workspace_id == workspace_id,
            ),
            ExtensionInstallation.status == "installed",
        )
        .order_by(ExtensionInstallation.created_at)
    )
    return {"installations": [view(db, row, workspace_id) for row in rows]}


def inspect(source: dto.ExtensionSource):
    with sources.prepare(source) as (path, revision):
        manifest = formats.inspect_package(path)
        digest = content_digest(manifest)
        files = manifest.pop("files")
        return {
            "manifest": manifest,
            "source_revision": revision,
            "file_count": len(files),
            "content_digest": digest,
            "total_bytes": sum(item["bytes"] for item in files.values()),
        }


def content_digest(manifest):
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _publish_revision(db, row, path, manifest, source_revision):
    digest = content_digest(manifest)
    identifier = str(uuid5(UUID(row.id), digest))
    revision = db.get(ExtensionRevision, identifier)
    if revision is None:
        relative = storage.publish(path, row.user_id, row.id, digest)
        revision = ExtensionRevision(
            id=identifier,
            installation_id=row.id,
            content_digest=digest,
            source_revision=source_revision,
            manifest=manifest,
            storage_path=relative,
        )
        db.add(revision)
    row.current_revision_id, row.name = identifier, manifest["name"]
    db.flush()


def install(user_id, workspace_id, command: dto.ExtensionInstall, *, before_publish=None):
    values = command.model_dump(mode="json")
    with Session(engine()) as db, db.begin():
        workspace_service.get(db, user_id, workspace_id)

        def execute():
            with sources.prepare(command.source) as (path, source_revision):
                manifest = formats.inspect_package(path)
                if (
                    command.expected_content_digest
                    and command.expected_content_digest != content_digest(manifest)
                ):
                    raise AppError(
                        "extension_inspection_changed",
                        "来源在检查后发生变化，请重新检查后安装。",
                        409,
                    )
                identifier = str(uuid5(UUID(user_id), str(command.request_id)))
                if before_publish:
                    before_publish(db)
                row = ExtensionInstallation(
                    id=identifier,
                    user_id=user_id,
                    workspace_id=workspace_id if command.scope == "workspace" else None,
                    name=manifest["name"],
                    source=command.source.model_dump(mode="json"),
                    current_revision_id="pending",
                    enabled=command.enabled,
                )
                db.add(row)
                db.flush()
                _publish_revision(db, row, path, manifest, source_revision)
                return view(db, row, workspace_id)

        return idempotent(
            db, user_id, str(command.request_id), "extensions:install", values, execute
        )


def upgrade(
    user_id, workspace_id, identifier, command: dto.ExtensionUpgrade, *, before_publish=None
):
    with Session(engine()) as db, db.begin():
        installation(db, user_id, workspace_id, identifier)

        def execute():
            if before_publish:
                before_publish(db)
            row = installation(db, user_id, workspace_id, identifier, lock=True)
            if row.version != command.expected_version or row.status != "installed":
                raise AppError("extension_version_conflict", "扩展已变化，请重新读取。", 409)
            source = command.source or dto.ExtensionSource.model_validate(row.source)
            with sources.prepare(source) as (path, source_revision):
                manifest = formats.inspect_package(path)
                if (
                    command.expected_content_digest
                    and command.expected_content_digest != content_digest(manifest)
                ):
                    raise AppError(
                        "extension_inspection_changed",
                        "来源在检查后发生变化，请重新检查后更新。",
                        409,
                    )
                if before_publish:
                    before_publish(db)
                _publish_revision(db, row, path, manifest, source_revision)
                row.source, row.version = source.model_dump(mode="json"), row.version + 1
                return view(db, row, workspace_id)

        return idempotent(
            db,
            user_id,
            str(command.request_id),
            f"extensions:{identifier}:upgrade",
            command.model_dump(mode="json"),
            execute,
        )


def change(db, user_id, workspace_id, identifier, command: dto.ExtensionChange):
    def execute():
        row = installation(db, user_id, workspace_id, identifier, lock=True)
        if row.version != command.expected_version or row.status != "installed":
            raise AppError("extension_version_conflict", "扩展已变化，请重新读取。", 409)
        if command.scope == "workspace" and row.workspace_id is None:
            override = db.scalar(
                select(ExtensionOverride)
                .where(
                    ExtensionOverride.installation_id == row.id,
                    ExtensionOverride.workspace_id == workspace_id,
                )
                .with_for_update()
            )
            if override is None:
                override = ExtensionOverride(
                    installation_id=row.id, workspace_id=workspace_id, enabled=command.enabled
                )
                db.add(override)
            else:
                override.enabled = command.enabled
        else:
            row.enabled = command.enabled
        row.version += 1
        db.flush()
        return view(db, row, workspace_id)

    return idempotent(
        db,
        user_id,
        str(command.request_id),
        f"extensions:{identifier}:change",
        command.model_dump(mode="json"),
        execute,
    )


def remove(db, user_id, workspace_id, identifier, command: dto.ExtensionRemove):
    def execute():
        row = installation(db, user_id, workspace_id, identifier, lock=True)
        if row.version != command.expected_version:
            raise AppError("extension_version_conflict", "扩展已变化，请重新读取。", 409)
        row.enabled, row.status, row.version = False, "removed", row.version + 1
        # Immutable revisions remain available to history; current revocation
        # prevents future tool use even when a Run still references their IDs.
        return view(db, row, workspace_id)

    return idempotent(
        db,
        user_id,
        str(command.request_id),
        f"extensions:{identifier}:remove",
        command.model_dump(mode="json"),
        execute,
    )


def freeze(db, user_id, workspace_id):
    return [
        {"installation_id": item["id"], "revision_id": item["revision_id"]}
        for item in listing(db, user_id, workspace_id)["installations"]
        if item["effective_enabled"]
    ]


def _revisions(db, run):
    for item in run.config_snapshot.get("extensions", []):
        row = installation(db, run.user_id, run.workspace_id, item["installation_id"])
        enabled, _ = effective(db, row, run.workspace_id)
        if not enabled:
            continue
        revision = db.get(ExtensionRevision, item["revision_id"])
        if revision is not None and revision.installation_id == row.id:
            yield row, revision


def skill_catalog(db, run, query=""):
    result = []
    for row, revision in _revisions(db, run):
        for item in revision.manifest["skills"]:
            if (
                query
                and query.casefold() not in (item["name"] + " " + item["description"]).casefold()
            ):
                continue
            result.append(
                {
                    "skill_id": f"{row.id}:{item['name']}",
                    "name": item["name"],
                    "description": item["description"],
                    "package": row.name,
                    "revision_id": revision.id,
                    "implicit_invocation": item.get("implicit_invocation", True),
                }
            )
    return result


def _skill(db, run, identifier):
    for row, revision in _revisions(db, run):
        for item in revision.manifest["skills"]:
            if identifier == f"{row.id}:{item['name']}":
                return row, revision, item
    raise AppError(
        "skill_not_available", "Skill未授权给此任务或已停用，请查询当前目录。", 403, "policy"
    )


def _resource(revision, skill, relative):
    prefix = Path(skill["directory"])
    relative = storage.relative_path(relative)
    package_path = (prefix / relative).as_posix()
    record = revision.manifest["files"].get(package_path)
    if record is None:
        raise AppError("skill_resource_missing", "此资源不属于固定版本的Skill。", 404)
    path = storage.contained(storage_path(revision.storage_path), package_path)
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != record["digest"]:
        raise AppError("skill_revision_changed", "受管Skill版本已被外部修改，请重新安装。", 409)
    return package_path, data


def read_resource(db, run, identifier, relative="SKILL.md", start_line=1, limit=200):
    row, revision, skill = _skill(db, run, identifier)
    path, data = _resource(revision, skill, relative)
    try:
        if b"\0" in data:
            raise UnicodeError
        lines = data.decode("utf-8").splitlines()
    except UnicodeError:
        return {
            "skill_id": identifier,
            "path": relative,
            "bytes": len(data),
            "binary": True,
            "note": "二进制资源可用prepare_skill导出到工作区，再使用相应文件工具。",
        }
    selected = "\n".join(lines[start_line - 1 : start_line - 1 + limit])
    if len(selected) > 32000:
        selected = selected[:32000]
    resources = [
        str(Path(name).relative_to(Path(skill["directory"])))
        for name in revision.manifest["files"]
        if Path(name).is_relative_to(Path(skill["directory"]))
    ]
    return {
        "skill_id": identifier,
        "path": relative,
        "body": selected,
        "start_line": start_line,
        "total_lines": len(lines),
        "truncated": start_line - 1 + limit < len(lines) or len(selected) >= 32000,
        "resources": resources[:200] if relative == "SKILL.md" else [],
        "skill_ref": {"skill_id": identifier, "revision_id": revision.id},
        "note": "Skill不扩大权限；脚本先prepare_skill导出，再走原命令审批。",
    }


def prepare_skill(db, run, identifier):
    row, revision, skill = _skill(db, run, identifier)
    workspace = workspace_service.get(db, run.user_id, run.workspace_id)
    root = Path(workspace.root_path).resolve()
    relative = f".workbench-skills/{row.id}/{revision.id}/{skill['name']}"
    if run.parent_run_id:
        from pathlib import PurePosixPath

        allowed = run.config_snapshot.get("allowed_paths", [])
        if not allowed or not any(PurePosixPath(relative).is_relative_to(item) for item in allowed):
            raise AppError(
                "agent_path_denied",
                "Skill导出目录超出子任务范围，请主任务先导出并授权目录。",
                403,
                "policy",
            )
    target = authorized_path(root, relative)
    prefix = Path(skill["directory"])
    count = 0
    for name, record in revision.manifest["files"].items():
        if not Path(name).is_relative_to(prefix):
            continue
        resource = str(Path(name).relative_to(prefix))
        _, data = _resource(revision, skill, resource)
        destination = authorized_path(root, str(target / resource))
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != record["digest"]:
                raise AppError(
                    "skill_export_conflict", "导出目录已有修改，未覆盖。请保留或移动后重试。", 409
                )
        else:
            with destination.open("xb") as stream:
                stream.write(data)
        count += 1
    return {
        "skill_id": identifier,
        "directory": relative,
        "files": count,
        "note": "仅导出固定版本文件，尚未执行。通过现有命令工具运行相应解释器或程序。",
    }


def tool(run_id, name, values):
    with Session(engine()) as db:
        run = db.get(Run, run_id)
        assert_owned(db, run_id)
        if name == "list_skills":
            items = skill_catalog(db, run, values.get("query", ""))
            return {"skills": items, "count": len(items)}
        if name == "prepare_skill":
            return prepare_skill(db, run, values["skill_id"])
        return read_resource(
            db,
            run,
            values["skill_id"],
            values.get("path", "SKILL.md"),
            values.get("start_line", 1),
            values.get("limit", 200),
        )


def reference_allowed(db, run, reference):
    try:
        _, revision, _ = _skill(db, run, reference.get("skill_id"))
        return revision.id == reference.get("revision_id")
    except AppError:
        return False


def binding_allowed(db, user_id, workspace_id, connection_id):
    binding = db.scalar(
        select(ExtensionMCPBinding).where(ExtensionMCPBinding.connection_id == connection_id)
    )
    if binding is None:
        return True
    try:
        row = installation(db, user_id, workspace_id, binding.installation_id)
        return binding.workspace_id == workspace_id and effective(db, row, workspace_id)[0]
    except AppError:
        return False


def import_mcp(db, user_id, workspace_id, identifier, command: dto.ExtensionMCPImport):
    from workbench.modules.mcp.dto import ConnectionConfig
    from workbench.modules.mcp.host import save
    from workbench.modules.mcp.host_domain import MCPConnection
    from workbench.modules.mcp.host_dto import MCPSave

    row = installation(db, user_id, workspace_id, identifier, lock=True)
    if row.version != command.expected_version or not effective(db, row, workspace_id)[0]:
        raise AppError("extension_version_conflict", "扩展已变化或停用，请重新读取。", 409)
    revision = db.get(ExtensionRevision, row.current_revision_id)
    server = next(
        (item for item in revision.manifest["mcp_servers"] if item["name"] == command.server_name),
        None,
    )
    if server is None or not server["importable"]:
        raise AppError(
            "plugin_mcp_unsupported", "此MCP声明需要手动配置；未丢弃不支持字段后启用。", 422
        )
    root = storage_path(revision.storage_path)
    data = storage_path(f"extensions/data/{row.id}/{workspace_id}")
    data.mkdir(mode=0o700, parents=True, exist_ok=True)

    def expand(value):
        if not isinstance(value, str):
            raise AppError("plugin_mcp_invalid", "MCP字段必须是字符串。", 422)
        result = value.replace("${PLUGIN_ROOT}", str(root)).replace("${PLUGIN_DATA}", str(data))
        if "${" in result:
            raise AppError("plugin_mcp_placeholder", "MCP含未支持占位符，请手动配置。", 422)
        return result

    identifier = str(uuid5(UUID(revision.id), workspace_id + ":" + server["name"]))
    existing = db.get(MCPConnection, identifier)
    if existing:
        from workbench.modules.mcp.host import view as mcp_view

        return mcp_view(existing)
    raw = server["configuration"]
    config = {
        "connection_id": identifier,
        "version": 1,
        "enabled": False,
        "name": (row.name + " · " + server["name"])[:100],
        "allowed_tools": [],
    }
    if server["transport"] == "stdio":
        command_path = expand(raw.get("command", ""))
        if command_path.startswith("./"):
            command_path = str(storage.contained(root, command_path))
        elif not Path(command_path).is_absolute():
            command_path = shutil.which(command_path) or ""
        cwd = expand(raw.get("cwd", str(data)))
        if cwd.startswith("./"):
            cwd = str(storage.contained(root, cwd))
        config.update(
            transport="stdio",
            command=command_path,
            cwd=cwd,
            args=[expand(item) for item in raw.get("args", [])],
        )
    else:
        config.update(transport="streamable_http", endpoint=expand(raw.get("url", "")))
    try:
        validated = ConnectionConfig.model_validate(config)
    except ValueError:
        raise AppError(
            "plugin_mcp_configuration", "MCP程序、路径或地址未配置完整，请在工具设置中配置。", 422
        ) from None
    result = save(
        db,
        user_id,
        workspace_id,
        identifier,
        MCPSave(
            configuration=validated,
            expected_version=0,
            execution_authorized=False,
            request_id=command.request_id,
        ),
    )
    db.add(
        ExtensionMCPBinding(
            installation_id=row.id,
            revision_id=revision.id,
            workspace_id=workspace_id,
            connection_id=identifier,
        )
    )
    db.flush()
    return result
