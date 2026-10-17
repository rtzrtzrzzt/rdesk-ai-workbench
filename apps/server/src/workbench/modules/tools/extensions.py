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


def discover(run_id: str, names: list[str]) -> dict:
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
        if any(TOOLS[name].module == "search" for name in names) and not web_enabled:
            raise AppError(
                "search_disabled",
                "先由用户在工具设置中配置并启用搜索，模型不能自行启用。",
                409,
                "policy",
            )
        if names:
            enabled = [name for name in CORE_TOOL_NAMES if name in permitted] + sorted(
                set(names) - set(CORE_TOOL_NAMES)
            )
            run.config_snapshot = {**run.config_snapshot, "tool_names": enabled}
            emit(db, run_id, "tools.loaded", {"names": enabled})
        else:
            enabled = run.config_snapshot.get("tool_names", CORE_TOOL_NAMES)
        result = {
            "loaded": enabled,
            "note": (
                "工具已在下一次模型请求的tools中提供，可直接调用；填写names会替换额外工具集合。"
            ),
        }
        if not names:
            result["catalog"] = [
                {
                    "name": name,
                    "description": spec.description,
                    "available": web_enabled if spec.module == "search" else True,
                    "requires_approval": spec.requires_approval,
                }
                for name, spec in sorted(TOOLS.items())
                if name not in CORE_TOOL_NAMES and name in permitted
            ]
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
    from workbench.modules.files import service as files
    from workbench.modules.files.exports import create_export
    from workbench.modules.files.exports_dto import ExportCreate
    from workbench.modules.files.inputs import read_artifact_text
    from workbench.modules.runs.service import get

    user_id, workspace_id, _ = scope_for(run_id)
    with Session(engine()) as db:
        if name == "list_artifacts":
            rows = files.list_artifacts(db, user_id, workspace_id)
            return {"artifacts": rows[:30], "truncated": len(rows) > 30}
        if name == "read_artifact":
            return read_artifact_text(
                db,
                user_id,
                workspace_id,
                values["artifact_id"],
                values["start_line"],
                values["limit"],
            )
        if name == "export_file":
            result = create_export(
                db, user_id, workspace_id, ExportCreate(**values, request_id=operation_id)
            ).model_dump(mode="json")
            parent = db.get(Run, run_id)
            child = db.get(Run, result["id"])
            child.parent_run_id, child.root_run_id = parent.id, parent.root_run_id or parent.id
            db.commit()
            return result
        task = get(db, user_id, values["run_id"])
        if task.workspace_id != workspace_id or task.kind != "file":
            raise AppError("file_task_not_found", "此文件任务不属于当前工作区。", 404)
        return files.task_view(db, task)


def context_tool(run_id, name, values):
    from workbench.modules.context.compiler import filter_memory
    from workbench.modules.context.memory_refs import dependencies
    from workbench.modules.context.transfers import filter_artifact_transfer
    from workbench.modules.conversations import Message

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
        rows = list(
            db.scalars(
                select(Message)
                .where(Message.thread_id == target.thread_id)
                .order_by(Message.created_at, Message.id)
                .offset(values["start_message"] - 1)
                .limit(values["limit"] + 1)
            )
        )
        results = []
        references = set()
        for row in rows[: values["limit"]]:
            filtered = filter_artifact_transfer(
                db,
                run,
                filter_memory(
                    db, run, {"role": row.role, "content": row.content, "_message_id": row.id}
                ),
            )
            content = filtered["content"]
            if row.role == "tool":
                references.update((item["id"], item["version"]) for item in dependencies(content))
                if len(references) > 2048:
                    raise AppError(
                        "history_reference_limit", "历史片段的记忆来源过多，请缩小读取范围。", 422
                    )
            results.append(
                {
                    "message_id": row.id,
                    "run_id": row.run_id,
                    "role": row.role,
                    "status": row.status,
                    "content": content[: values["max_chars"]],
                    "truncated": len(content) > values["max_chars"],
                    "attachments": row.attachments,
                }
            )
        return {
            "messages": results,
            "memory_dependencies": [
                {"id": identifier, "version": version} for identifier, version in sorted(references)
            ],
            "more": len(rows) > values["limit"],
            "next_message": values["start_message"] + len(results),
        }


async def execute(run_id: str, operation_id: str, name: str, values: dict) -> dict:
    if TOOLS[name].module == "processes":
        from workbench.modules.processes import service as processes

        if name == "start_command":
            return (await processes.start(run_id, operation_id, values)).model_dump(mode="json")
        user_id, workspace_id, _ = scope_for(run_id)
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

        if name == "spawn_agent":
            return await asyncio.to_thread(agents.spawn, run_id, operation_id, values)
        if name == "release_agent":
            return await asyncio.to_thread(agents.release, run_id, values["agent_id"])
        if name == "cancel_agent":
            return await asyncio.to_thread(
                agents.cancel_agent, run_id, operation_id, values["agent_id"]
            )
        if name == "send_agent_message":
            return await asyncio.to_thread(
                agents.send_message, run_id, operation_id, values["agent_id"], values["message"]
            )
        user_id, _, _ = await asyncio.to_thread(scope_for, run_id)
        with Session(engine()) as db:
            return agents.tree(db, user_id, run_id)
    if name == "discover_tools":
        return await asyncio.to_thread(discover, run_id, values["names"])
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
