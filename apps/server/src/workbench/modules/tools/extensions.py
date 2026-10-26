"""Small adapters between the tool executor and independently maintained services."""

import asyncio

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine
from workbench.modules.memory import dto as memory_dto
from workbench.modules.memory import service as memory
from workbench.modules.runs.domain import Run
from workbench.modules.runs.service import emit
from workbench.modules.search import dto as search_dto
from workbench.modules.search import service as search
from workbench.modules.tools.registry import CORE_TOOL_NAMES, TOOLS


def scope_for(run_id: str) -> tuple[str, str, str]:
    with Session(engine()) as db:
        run = db.get(Run, run_id)
        return run.user_id, run.workspace_id, "subagent" if run.parent_run_id else "agent"


def discover(
    run_id: str, names: list[str], action: str = "add", *, query="", module=None, offset=0, limit=20
) -> dict:
    if action not in {"add", "remove", "replace"}:
        raise AppError("invalid_discovery_action", "工具加载操作必须为add、remove或replace。", 422)
    if any(name not in TOOLS for name in names):
        raise AppError("tool_not_registered", "请求加载的工具没有注册，请先查询目录。", 422)
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        from workbench.modules.runs.ownership import assert_owned

        assert_owned(db, run_id)
        permitted = set(run.config_snapshot.get("allowed_tools", list(TOOLS)))
        if not set(names) <= permitted:
            raise AppError("tool_scope_denied", "子任务不能加载父级未授予的工具。", 403, "policy")
        connection = search.get_connection(db, run.user_id, run.workspace_id)
        web_enabled = bool(connection and connection.enabled)
        if (
            action != "remove"
            and any(TOOLS[name].module == "search" for name in names)
            and not web_enabled
        ):
            raise AppError(
                "search_disabled",
                "先由用户在工具设置中配置并启用搜索，模型不能自行启用。",
                409,
                "policy",
            )
        current = (
            set(run.config_snapshot.get("tool_names", CORE_TOOL_NAMES)) & permitted & set(TOOLS)
        )
        catalog_rows, matches = None, []
        auto_names = []
        if not names and action == "add":
            from workbench.modules.tools.catalog import availability

            terms = query.casefold().split()
            matches = [
                (name, spec)
                for name, spec in sorted(TOOLS.items())
                if name in permitted
                and (module is None or spec.module == module)
                and all(
                    term in f"{name} {spec.module} {spec.description}".casefold() for term in terms
                )
            ]
            catalog_rows = [
                {
                    "name": name,
                    "description": spec.description,
                    "module": spec.module,
                    **availability(db, run, name, spec.module),
                    "requires_approval": spec.requires_approval,
                }
                for name, spec in matches[offset : offset + limit]
            ]
            # A targeted search should make its tools usable on the next model
            # request. The unfiltered catalog remains a read-only listing.
            if terms or module is not None:
                auto_names = [
                    item["name"]
                    for item in catalog_rows
                    if item["name"] not in current and item["available"] is not False
                ][:6]
        if names or auto_names or action != "add":
            requested = set(names or auto_names)
            if action == "add":
                selected = current | requested
            elif action == "remove":
                selected = current - requested
            else:
                selected = requested
            enabled = [name for name in CORE_TOOL_NAMES if name in permitted] + sorted(
                selected - set(CORE_TOOL_NAMES)
            )
            run.config_snapshot = {**run.config_snapshot, "tool_names": enabled}
            emit(db, run_id, "tools.loaded", {"names": enabled, "action": action})
        else:
            enabled = [name for name in CORE_TOOL_NAMES if name in current] + sorted(
                current - set(CORE_TOOL_NAMES)
            )
        result = {
            "loaded": enabled,
            "documentation": {"tool": "read_workbench_guide", "path": "docs/ai-guide.md"},
            "note": (
                "loaded是下一轮模型请求的工具集合；新工具schema届时才提供，"
                "本轮不能同时调用尚未加载的工具。默认增量保留已有工具，卸载或替换须显式指定action。"
            ),
        }
        if catalog_rows is not None:
            result["catalog"] = catalog_rows
            result["next_offset"] = offset + limit if offset + limit < len(matches) else None
            result["total"] = len(matches)
            result["modules"] = sorted({TOOLS[name].module for name in permitted if name in TOOLS})
            result["auto_loaded"] = auto_names
            remaining = [
                item["name"]
                for item in catalog_rows
                if item["name"] not in enabled and item["available"] is not False
            ][:6]
            result["next_step"] = (
                "目标在loaded中时，下一轮直接调用该工具，不要重复查询。"
                "如目标尚未加载，使用load_request中的names明确加载；为空时缩短查询或查看modules。"
            )
            if remaining:
                result["load_request"] = {"names": remaining, "action": "add"}
        return result


def memory_search(run_id: str, values: dict) -> dict:
    user_id, workspace_id, _ = scope_for(run_id)
    scopes = ["user", "workspace"] if values["scope"] == "all" else [values["scope"]]
    results = []
    with Session(engine()) as db:
        for scope in scopes:
            if values["query"].strip():
                result = memory.search(
                    db, user_id, workspace_id, values["query"], scope
                ).model_dump(mode="json")
                truncated = len(result["hits"]) > 8 or any(
                    len(hit["text"]) > 1200 for hit in result["hits"][:8]
                )
                result["hits"] = result["hits"][:8]
                for hit in result["hits"]:
                    hit["text"] = hit["text"][:1200]
                result["truncated"] = result["truncated"] or truncated
            else:
                listing = memory.list_documents(db, user_id, workspace_id, scope)
                result = {
                    "scope": scope,
                    "scope_generation": listing.scope_generation,
                    "documents": [
                        item.model_dump(mode="json")
                        for item in listing.documents
                        if item.status == "active"
                    ][:50],
                    "pending_writes": [
                        item.model_dump(mode="json") for item in listing.pending_writes
                    ],
                }
            results.append(result)
    return {"memory_results": results}


def memory_read(run_id: str, values: dict) -> dict:
    user_id, workspace_id, _ = scope_for(run_id)
    with Session(engine()) as db:
        document = memory.read_document(db, user_id, workspace_id, values["document_id"])
        if document.status != "active" or document.body is None:
            raise AppError(
                "memory_not_active", "此记忆已忘记、缺失或待核对，不会作为有效记忆使用。", 409
            )
        lines = document.body.splitlines()
        start = values["start_line"]
        end = min(len(lines), start - 1 + values["limit"])
        selected = "\n".join(lines[start - 1 : end])
        return {
            "memory_ref": {
                "id": document.id,
                "version": document.version,
                "scope_generation": document.scope_generation,
            },
            "path": document.path,
            "title": document.title,
            "body": selected[:12_000],
            "body_start_line": start,
            "body_end_line": end if end >= start else None,
            "total_lines": len(lines),
            "truncated": len(selected) > 12_000 or end < len(lines),
            "source_refs": document.source_refs,
        }


def memory_propose(run_id: str, operation_id: str, values: dict) -> dict:
    user_id, workspace_id, actor = scope_for(run_id)
    command = memory_dto.ProposalCreate(**values, request_id=operation_id)
    with Session(engine()) as db:
        proposal = memory.create_proposal(db, user_id, workspace_id, command, source_actor=actor)
    return {
        "proposal_id": proposal.id,
        "status": proposal.status,
        "scope": proposal.scope,
        "note": "候选已提交，用户可在记忆与规则页面审阅；尚未成为长期记忆。",
    }


def artifact_tool(run_id: str, operation_id: str, name: str, values: dict) -> dict:
    if name == "present_file":
        from workbench.modules.files.links import present_file

        return present_file(run_id, operation_id, values)
    from workbench.modules.files import service as files
    from workbench.modules.files.exports import create_export
    from workbench.modules.files.exports_dto import ExportCreate
    from workbench.modules.files.inputs import read_artifact_text
    from workbench.modules.runs.service import get

    user_id, workspace_id, _ = scope_for(run_id)
    with Session(engine()) as db:
        if name == "inspect_file_processors":
            from workbench.modules.files.processors import capabilities

            return {"processors": capabilities()}
        if name == "process_file":
            from workbench.modules.collaboration.service import lock_root
            from workbench.modules.files.dto import ProcessFile

            _, parent = lock_root(db, run_id)
            if parent.cancel_requested_at:
                raise AppError("run_finished", "任务已停止，不能发起文件处理。", 409)
            artifact = files.get_artifact(db, user_id, workspace_id, values["artifact_id"])
            if artifact.content_hash != values["expected_version"]:
                raise AppError("artifact_version_changed", "文件版本已变化，请重新读取。", 409)
            command = ProcessFile(
                request_id=operation_id,
                **{
                    key: value
                    for key, value in values.items()
                    if key not in {"artifact_id", "expected_version"}
                },
            )
            result = files.create_processing(
                db, user_id, workspace_id, artifact.id, command, actor=parent
            )
            child = db.get(Run, result["id"])
            db.commit()
            return {
                **result,
                "run_id": child.id,
                "next_step": (
                    "本批工具结束后自动等待文件Worker；完成后只用results里的真实文件ID读取。"
                ),
            }
        if name == "cancel_file_task":
            from workbench.modules.collaboration.service import lock_root
            from workbench.modules.runs.ownership import current_owner
            from workbench.modules.runs.service import cancel

            _, parent = lock_root(db, run_id)
            child = get(db, user_id, values["run_id"])
            if child.parent_run_id != parent.id or child.kind != "file":
                raise AppError("file_task_scope", "只能停止本任务创建的文件处理。", 403)
            token = current_owner.set(None)
            try:
                cancel(db, user_id, child.id, operation_id)
                db.commit()
            finally:
                current_owner.reset(token)
            return files.task_view(db, child)
        if name == "list_artifacts":
            from sqlalchemy import or_

            from workbench.modules.files.domain import Artifact
            from workbench.modules.workspace_service import get as workspace

            workspace(db, user_id, workspace_id)
            query = select(Artifact).where(
                Artifact.user_id == user_id,
                Artifact.workspace_id == workspace_id,
                Artifact.status == "available",
            )
            if values.get("query"):
                query = query.where(
                    Artifact.display_name.icontains(values["query"], autoescape=True)
                )
            if values.get("before"):
                previous = files.get_artifact(db, user_id, workspace_id, values["before"])
                query = query.where(
                    or_(
                        Artifact.created_at < previous.created_at,
                        (Artifact.created_at == previous.created_at) & (Artifact.id < previous.id),
                    )
                )
            limit = values.get("limit", 30)
            rows = list(
                db.scalars(
                    query.order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(limit + 1)
                )
            )
            return {
                "artifacts": [files.artifact_view(row) for row in rows[:limit]],
                "truncated": len(rows) > limit,
                "next_before": rows[limit - 1].id if len(rows) > limit else None,
            }
        if name == "read_artifact":
            return read_artifact_text(
                db,
                user_id,
                workspace_id,
                values["artifact_id"],
                values["start_line"],
                values["limit"],
                **{
                    key: values[key]
                    for key in ("start_character", "max_chars", "expected_version")
                    if key in values
                },
            )
        if name == "export_file":
            result = create_export(
                db,
                user_id,
                workspace_id,
                ExportCreate(**values, request_id=operation_id),
                actor_run_id=run_id,
            ).model_dump(mode="json")
            return result
        task = get(db, user_id, values["run_id"])
        if task.workspace_id != workspace_id or task.kind != "file":
            raise AppError("file_task_not_found", "此文件任务不属于当前工作区。", 404)
        return files.task_view(db, task)


def context_tool(run_id, name, values):
    from workbench.modules.context.compiler import filter_memory
    from workbench.modules.context.memory_refs import context_dependencies, dependencies
    from workbench.modules.context.transfers import filter_artifact_transfer

    with Session(engine()) as db:
        run = db.get(Run, run_id)
        if name == "inspect_context":
            return {
                "run_id": run.id,
                "rules_and_tools": run.config_snapshot.get("context_manifest"),
                "compaction": run.config_snapshot.get("context_compaction"),
                "note": "前缀及预算是工作台编译记录，token估计不作为提供方用量。",
            }
        target = db.get(Run, values.get("run_id") or run_id)
        if (
            target is None
            or target.user_id != run.user_id
            or target.workspace_id != run.workspace_id
            or (target.root_run_id or target.id) != (run.root_run_id or run.id)
        ):
            raise AppError(
                "history_scope_denied", "只能读取当前任务树内的实际会话。", 403, "policy"
            )
        from workbench.modules.conversation_service import get_thread, history_rows

        history = history_rows(db, get_thread(db, run.user_id, target.thread_id))
        if values.get("message_id"):
            matches = [row for row in history if row.id == str(values["message_id"])]
            if not matches:
                raise AppError("history_message_not_found", "消息不属于此授权对话历史。", 404)
            rows = matches
        else:
            rows = history[
                values["start_message"] - 1 : values["start_message"] - 1 + values["limit"] + 1
            ]
        results = []
        references = set()
        context_references = {}
        for position, row in enumerate(rows[: values["limit"]]):
            filtered = filter_artifact_transfer(
                db,
                run,
                filter_memory(
                    db, run, {"role": row.role, "content": row.content, "_message_id": row.id}
                ),
            )
            content = filtered["content"]
            if row.role == "tool":
                for item in context_dependencies(content):
                    context_references[(item.get("workspace_id"), item.get("fingerprint"))] = item
                references.update((item["id"], item["version"]) for item in dependencies(content))
                if len(references) > 2048:
                    raise AppError(
                        "history_reference_limit", "历史片段的记忆来源过多，请缩小读取范围。", 422
                    )
            start = values.get("start_character", 0) if position == 0 else 0
            end = min(len(content), start + values["max_chars"])
            if start > len(content):
                raise AppError(
                    "history_cursor_invalid", "字符游标超过消息正文长度，请从原始位置重读。", 422
                )
            results.append(
                {
                    "message_id": row.id,
                    "run_id": row.run_id,
                    "role": row.role,
                    "status": row.status,
                    "content": content[start:end],
                    "start_character": start,
                    "next_character": end if end < len(content) else None,
                    "total_characters": len(content),
                    "truncated": end < len(content),
                    "attachments": row.attachments,
                }
            )
        return {
            "messages": results,
            "memory_dependencies": [
                {"id": identifier, "version": version} for identifier, version in sorted(references)
            ],
            "context_memory_dependencies": list(context_references.values()),
            "more": len(rows) > values["limit"],
            "next_message": values["start_message"] + len(results),
        }


async def execute(run_id: str, operation_id: str, name: str, values: dict) -> dict:
    if TOOLS[name].module == "guide":
        from workbench.modules.tools.guide import read

        return await asyncio.to_thread(read, values)
    if TOOLS[name].module == "conversations":
        from workbench.modules.conversation_history.service import execute as history_execute

        return await asyncio.to_thread(history_execute, run_id, name, values)
    if TOOLS[name].module == "configuration":
        from workbench.modules.configuration.service import tool as settings_tool

        return await settings_tool(run_id, operation_id, name, values)
    if TOOLS[name].module == "plan":
        from workbench.modules.tools.task_plan import execute as plan_execute

        return await asyncio.to_thread(plan_execute, run_id, operation_id, name, values)
    if TOOLS[name].module == "media":
        from workbench.modules.media.tools import execute as media_execute

        return await media_execute(run_id, operation_id, name, values)
    if TOOLS[name].module == "skills":
        from workbench.modules.extensions.service import tool as skill_tool

        return await asyncio.to_thread(skill_tool, run_id, name, values)
    if TOOLS[name].module == "rules":

        def read_rules():
            with Session(engine()) as db:
                run = db.get(Run, run_id)
                paths = values["paths"]
                allowed = run.config_snapshot.get("allowed_paths", [])
                if allowed:
                    from pathlib import PurePosixPath

                    for path in paths:
                        if not any(PurePosixPath(path).is_relative_to(item) for item in allowed):
                            raise AppError(
                                "agent_path_denied", "规则路径超出子任务范围。", 403, "policy"
                            )
                snapshot = memory.load_rules(db, run.user_id, run.workspace_id, paths)
                return {
                    "rules": [item.model_dump(mode="json") for item in snapshot.rules],
                    "rules_ref": {"paths": paths, "version": snapshot.version_digest},
                }

        return await asyncio.to_thread(read_rules)
    if TOOLS[name].module == "processes":
        from workbench.modules.processes import service as processes

        if name == "start_command":
            return (await processes.start(run_id, operation_id, values)).model_dump(mode="json")
        if name == "write_command_input":
            return await processes.write_input(run_id, operation_id, values)
        user_id, workspace_id, _ = scope_for(run_id)
        if name == "list_commands":
            return await processes.active_commands(user_id, workspace_id, run_id)
        if name == "stop_command":
            result = await processes.stop(
                user_id, workspace_id, values["command_id"], run_id=run_id
            )
        else:
            result = await processes.read(
                user_id,
                workspace_id,
                values["command_id"],
                values["stream"],
                values["offset"],
                values["limit"],
                run_id=run_id,
            )
        return result.model_dump(mode="json")
    if TOOLS[name].module == "automation":
        from workbench.modules.automation.tools import execute as automation_execute

        return await automation_execute(run_id, operation_id, name, values)
    if TOOLS[name].module == "generated":
        from workbench.modules.generated import service as ui

        if name == "present_a2ui":
            from workbench.modules.generated.a2ui import A2UIInput, adapt

            adapted = await asyncio.to_thread(adapt, A2UIInput.model_validate(values))
            report = adapted.model_dump(mode="json", exclude={"draft"})
            if adapted.draft is not None:
                report["published"] = await asyncio.to_thread(
                    ui.create, run_id, operation_id, adapted.draft.model_dump(mode="json")
                )
            return report

        if name == "present_ui":
            return await asyncio.to_thread(ui.create, run_id, operation_id, values)
        return {
            "protocol": "workbench-ui/1",
            "a2ui_profile": (
                "v0.9.1有序消息快照子集，present_a2ui；"
                "静态Text/Column/Row/Card/List及显式自有catalog，无动作/脚本。"
            ),
            "catalog_version": "workbench-core/1",
            "components": {
                "Text": "props={text:string}",
                "Heading": "props={text:string,level:2|3}",
                "Card/Column/Row/Details": "props={title?:string}；children为组件ID列表",
                "List": "props={items:string[]}",
                "FileLink": "props={artifact_id,label}；ID必须在sources中",
                "Table": "props={columns:string[],rows:[]}；rows由实际JSON来源填入",
                "Chart": "props={kind:bar|line,x_label,y_label,unit,points:[]}；points由来源填入",
            },
            "structure": (
                "title/root_id/components:[{id,type,props,children}]/"
                "sources:[{artifact_id,expected_version}]"
            ),
            "bindings": {
                "Table": "bindings[组件ID]={artifact_id,pointer,fields:JSON_Pointer[]}",
                "Chart": "bindings[组件ID]={artifact_id,pointer,label_pointer,value_pointer}",
                "rule": (
                    "pointer指向实际JSON列表；fields/label_pointer/value_pointer相对于每行。"
                    "空路径表示自身。"
                ),
            },
            "limits": "64节点/深度8/单父树；JSON来源≤1MiB；表1000行、图2000点；无动作/HTML/脚本。",
        }
    if TOOLS[name].module == "mcp":
        if name in {"mcp_list_resources", "mcp_list_resource_templates", "mcp_read_resource"}:
            from workbench.modules.mcp.resources import execute_resource

            return await execute_resource(run_id, operation_id, name, values)
        from workbench.modules.mcp.execution import execute as mcp_execute
        from workbench.modules.mcp.host import listing

        if name == "mcp_call":
            return await mcp_execute(run_id, operation_id, values)

        def catalog():
            user_id, workspace_id, _ = scope_for(run_id)
            with Session(engine()) as db:
                items = listing(db, user_id, workspace_id)
                enabled = {
                    item["id"]
                    for item in items["connections"]
                    if item["execution_authorized"] and item["configuration"]["enabled"]
                }
                snapshots = [
                    row for row in items["snapshots"] if row["snapshot"]["connection_id"] in enabled
                ]
                if not values["connection_id"]:
                    return {
                        "connections": [
                            {
                                "connection_id": row["snapshot"]["connection_id"],
                                "snapshot_id": row["id"],
                                "tools": len(row["snapshot"]["tools"]),
                                "expires_at": row["snapshot"]["expires_at"],
                            }
                            for row in snapshots
                        ]
                    }
                selected = next(
                    (
                        row
                        for row in snapshots
                        if row["snapshot"]["connection_id"] == values["connection_id"]
                    ),
                    None,
                )
                if selected is None:
                    raise AppError(
                        "mcp_discovery_required", "请用户配置并重新发现这个MCP连接。", 409
                    )
                tools = selected["snapshot"]["tools"]
                if values["tool_name"]:
                    tools = [item for item in tools if item["name"] == values["tool_name"]]
                start, limit = values["offset"], values["limit"]
                return {
                    "snapshot_id": selected["id"],
                    "tools": [
                        {
                            "name": item["name"],
                            "description": (item["description"] or "")[:500],
                            **(
                                {"input_schema": item["input_schema"]}
                                if values["tool_name"]
                                else {}
                            ),
                        }
                        for item in tools[start : start + limit]
                    ],
                    "next_offset": start + limit if start + limit < len(tools) else None,
                }

        return await asyncio.to_thread(catalog)
    if TOOLS[name].module == "context":
        return await asyncio.to_thread(context_tool, run_id, name, values)
    if TOOLS[name].module == "collaboration":
        from workbench.modules.collaboration import service as agents

        if name == "inspect_agent_models":
            user_id, _, _ = await asyncio.to_thread(scope_for, run_id)

            def list_models():
                from workbench.modules.models.service import list_connections

                with Session(engine()) as db:
                    return {
                        "models": [
                            {
                                key: row[key]
                                for key in ("id", "name", "model_identifier", "reasoning_choices")
                            }
                            for row in list_connections(db, user_id)
                        ]
                    }

            return await asyncio.to_thread(list_models)
        if name == "spawn_agent":
            return await asyncio.to_thread(agents.spawn, run_id, operation_id, values)
        if name == "release_agent":
            return await asyncio.to_thread(agents.release, run_id, values["agent_id"])
        if name in {"cancel_agent", "interrupt_agent"}:
            return await asyncio.to_thread(
                agents.cancel_agent, run_id, operation_id, values["agent_id"]
            )
        if name == "send_agent_message":
            return await asyncio.to_thread(
                agents.send_message,
                run_id,
                operation_id,
                values["agent_id"],
                values["message"],
                values.get("kind", "note"),
                values.get("reply_to_id"),
            )
        if name == "followup_agent":
            return await asyncio.to_thread(
                agents.followup, run_id, operation_id, values["agent_id"], values["message"]
            )
        user_id, _, _ = await asyncio.to_thread(scope_for, run_id)
        with Session(engine()) as db:
            return agents.tree(db, user_id, run_id)
    if name == "discover_tools":
        return await asyncio.to_thread(
            discover,
            run_id,
            values["names"],
            values.get("action", "add"),
            **{key: values[key] for key in ("query", "module", "offset", "limit") if key in values},
        )
    if name == "memory_search":
        return await asyncio.to_thread(memory_search, run_id, values)
    if name == "memory_read":
        return await asyncio.to_thread(memory_read, run_id, values)
    if name == "memory_propose":
        return await asyncio.to_thread(memory_propose, run_id, operation_id, values)
    if TOOLS[name].module == "artifacts":
        return await asyncio.to_thread(artifact_tool, run_id, operation_id, name, values)
    if name in {"web_search", "read_webpage"}:
        user_id, workspace_id, _ = await asyncio.to_thread(scope_for, run_id)
        with Session(engine()) as db:
            if name == "web_search":
                result = await search.search(
                    db, user_id, workspace_id, search_dto.SearchQuery(**values)
                )
            else:
                result = await search.fetch_page(
                    db, user_id, workspace_id, search_dto.FetchPage(**values)
                )
            await asyncio.to_thread(db.commit)
            return result.model_dump(mode="json")
    raise AppError("tool_not_registered", "此扩展工具没有执行适配器。", 409, "capability")
