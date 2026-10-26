import asyncio
import hashlib
import json
from contextlib import ExitStack
from pathlib import Path

from fastapi.encoders import jsonable_encoder
from langgraph.types import interrupt
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.command_limits import DEFAULT_COMMAND_TIMEOUT_SECONDS
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine
from workbench.modules.interactions import Approval, Question
from workbench.modules.runs.domain import Run
from workbench.modules.runs.service import approval_view, emit, question_view, set_status
from workbench.modules.tools import filesystem
from workbench.modules.tools.domain import Operation
from workbench.modules.tools.registry import CORE_TOOL_NAMES, TOOLS
from workbench.modules.tools.validation import command_timeout, parameter_error
from workbench.modules.workspaces import Workspace


def argument_digest(values: dict) -> str:
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def ensure_operation(run_id: str, call: dict) -> tuple[str, dict | None, dict]:
    name = call["name"]
    if name not in TOOLS:
        raise AppError("tool_not_registered", "模型请求了未注册工具。", 400, "policy")
    try:
        values = TOOLS[name].schema.model_validate(call["arguments"]).model_dump(mode="json")
    except ValidationError as error:
        raise parameter_error(error, TOOLS[name].schema) from None
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if name not in run.config_snapshot.get("tool_names", CORE_TOOL_NAMES):
            raise AppError(
                "tool_not_loaded", "请先用discover_tools为本任务加载这个工具。", 409, "capability"
            )
        if run.cancel_requested_at:
            raise asyncio.CancelledError
        enforce_agent_scope(db, run, name, values)
        operation = db.scalar(
            select(Operation).where(
                Operation.run_id == run_id,
                Operation.tool_call_id == call["id"],
            )
        )
        if name in {"exec_command", "start_command"}:
            values = command_timeout(
                values,
                run.config_snapshot.get("runtime_options", {}),
                operation.arguments if operation else None,
            )
        if operation:
            recorded_arguments = dict(operation.arguments)
            if name == "spawn_agent":
                recorded_arguments.setdefault("reasoning_effort", None)
                for key in ("max_output_tokens", "max_model_rounds"):
                    recorded_arguments.pop(key, None)
                if "allowed_tools" not in call["arguments"]:
                    values["allowed_tools"] = recorded_arguments.get("allowed_tools")
            # Preserve original durable arguments and their approval digest when
            # replaying calls made before optional query/PTY fields existed.
            for key in list(values.keys() - recorded_arguments.keys()):
                field = TOOLS[name].schema.model_fields.get(key)
                if key not in call["arguments"] and field is not None and not field.is_required():
                    values.pop(key)
            if operation.tool_name != name or recorded_arguments != values:
                raise AppError("tool_call_conflict", "调用标识对应的工具或参数已改变。", 409)
            if operation.status in {"succeeded", "failed"} and operation.result is not None:
                return operation.id, operation.result, values
            if operation.status in {"running", "outcome_unknown"}:
                raise AppError(
                    "tool_outcome_unknown",
                    "此前工具可能已经执行，需先核对结果。",
                    409,
                    "outcome_unknown",
                )
        else:
            operation = Operation(
                run_id=run_id, tool_call_id=call["id"], tool_name=name, arguments=values
            )
            db.add(operation)
            db.flush()
        return operation.id, None, values


def enforce_agent_scope(db, run, name, values):
    if not run.parent_run_id:
        return
    if name not in run.config_snapshot.get("allowed_tools", []):
        raise AppError("agent_tool_scope", "工具不在此子任务的授权范围。", 403, "policy")
    from workbench.modules.collaboration.policy import child_tool_allowed

    if not child_tool_allowed(name, TOOLS[name].module):
        raise AppError(
            "agent_tool_scope", "子任务只能协作通信，不能派生或控制其它Agent。", 403, "policy"
        )
    if TOOLS[name].requires_approval and run.config_snapshot.get("agent_mode") != "serial_write":
        raise AppError("agent_read_only", "此子任务为只读模式。", 403, "policy")
    paths = run.config_snapshot.get("allowed_paths", [])
    if paths and TOOLS[name].module == "filesystem":
        root = Path(db.get(Workspace, run.workspace_id).root_path).resolve()
        if name == "code_intelligence" and not any(
            filesystem.authorized_path(root, path) == root for path in paths
        ):
            raise AppError(
                "agent_code_scope",
                "代码分析器需要完整工作区的只读授权，请由主任务执行。",
                403,
                "policy",
            )
        targets = (
            [values["path"]]
            if "path" in values
            else [item["path"] for item in values.get("files", [])]
        )
        for value in targets:
            target = filesystem.authorized_path(root, value)
            if not any(
                target.is_relative_to(filesystem.authorized_path(root, path)) for path in paths
            ):
                raise AppError(
                    "agent_path_scope", "此路径不在子任务允许的文件范围。", 403, "policy"
                )


def prepare_question(run_id: str, operation_id: str, values: dict) -> dict:
    if values["kind"] != "text":
        identifiers = [option["id"] for option in values["options"]]
        if len(identifiers) < 2 or len(set(identifiers)) != len(identifiers):
            raise AppError("invalid_question_options", "选择题需要至少两个不同的有效选项。")
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        check_steering(db, run_id, operation_id)
        question = db.scalar(select(Question).where(Question.interaction_key == operation_id))
        if question is None:
            question = Question(run_id=run_id, interaction_key=operation_id, **values)
            db.add(question)
            db.flush()
            emit(db, run_id, "question.created", {"question": question_view(question)})
        if question.status == "pending":
            set_status(db, run, "waiting_user")
        return question_view(question)


def prepare_approval(
    run_id: str,
    operation_id: str,
    name: str,
    values: dict,
    *,
    provided_arguments: dict | None = None,
) -> dict:
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        check_steering(db, run_id, operation_id)
        approval = db.scalar(select(Approval).where(Approval.operation_id == operation_id))
        if approval is None:
            summary = (
                "允许在主机运行此命令？当前模式仅限制工作目录，未提供系统沙箱。"
                if name in {"exec_command", "start_command"}
                else f"允许 {name} 修改这个文件？提交前会再次核对版本。"
            )
            if TOOLS[name].module == "media":
                summary = (
                    "允许使用已接入的生成模型按本次参数提交一次任务？提交结果未知时不会自动重提。"
                )
            if name in {"exec_command", "start_command"}:
                profile = run.config_snapshot.get("runtime_options", {}).get(
                    "sandbox_profile", "native_scoped"
                )
                summary = (
                    "允许在所选工作区的 Linux bubblewrap 沙箱运行此命令？网络禁用；不可用时停止。"
                    if profile == "linux_bwrap"
                    else "允许在所选工作目录运行此主机命令？文件和网络访问不隔离。"
                )
            if name == "mcp_call":
                from workbench.modules.mcp.host import get_connection

                connection = get_connection(
                    db, run.user_id, run.workspace_id, values["connection_id"]
                )
                config = connection.configuration
                target = config.get("endpoint") or " ".join(
                    [config.get("command", ""), *config.get("args", [])]
                )
                summary = f"允许MCP工具 {values['tool_name']} 执行本次参数？目标：{target}"
            if name in {"mcp_list_resources", "mcp_list_resource_templates", "mcp_read_resource"}:
                from workbench.modules.mcp.resources import approval_summary

                summary = approval_summary(db, run, name, values)
            if name == "configure_settings":
                summary = (
                    "允许按本次参数修改工作台设置？按范围、版本和模块校验保存；"
                    "凭据正文、服务启停和数据维护使用专用边界。"
                )
            if name == "write_command_input":
                summary = (
                    "允许向本任务已有交互终端输入本次字符或EOF？"
                    "输入可能产生业务效果，未知结果不重发。"
                )
            if name == "code_intelligence":
                summary = (
                    "允许按受信代码智能配置运行短命本地语言服务器读取项目并返回导航/诊断？"
                    "不执行项目代码，结束或取消后回收进程。"
                )
            if name == "browser_download":
                from workbench.modules.automation.service import get_session

                session = get_session(
                    db, run.user_id, run.workspace_id, values["session_id"], run_id=run_id
                )
                observation = session.observation or {}
                target = next(
                    (
                        item.get("href")
                        for item in observation.get("elements", [])
                        if item.get("ref") == values["target_ref"]
                    ),
                    None,
                )
                if observation.get("observation_id") != values["last_observation"] or not target:
                    raise AppError("download_observation_stale", "请先重新观察可下载链接。", 409)
                summary = f"允许下载此观测链接的原件？最多16MiB、不继承登录Cookie。目标：{target}"
            if name == "read_artifact":
                from workbench.modules.models.domain import ModelRevision

                revision = db.get(ModelRevision, run.config_snapshot["model_revision_id"])
                summary = (
                    "允许读取此文件片段并发送至当前外部模型？"
                    f"接收端：{revision.configuration['endpoint']}；仅适用当前连接版本。"
                )
            approval = Approval(
                run_id=run_id,
                operation_id=operation_id,
                summary=summary,
                tool_name=name,
                arguments=values,
                parameters_digest=argument_digest(values),
            )
            if run.config_snapshot.get("runtime_options", {}).get("approval_mode") == "smart":
                from workbench.modules.tools.review import queued_review

                sources = None
                if name in {"exec_command", "start_command"} and provided_arguments is not None:
                    inherited = provided_arguments.get("timeout_seconds") is None
                    budget = run.config_snapshot.get("runtime_options", {}).get(
                        "command_timeout_seconds", DEFAULT_COMMAND_TIMEOUT_SECONDS
                    )
                    sources = {
                        "timeout_seconds": (
                            "task_default"
                            if inherited and values["timeout_seconds"] == budget
                            else "recorded_default"
                            if inherited
                            else "explicit"
                        )
                    }
                approval.review = queued_review(approval, argument_sources=sources)
            db.add(approval)
            db.flush()
            emit(db, run_id, "approval.requested", {"approval": approval_view(approval)})
        if approval.status == "pending":
            set_status(db, run, "waiting_approval")
        return approval_view(approval)


def record_result(
    run_id: str, operation_id: str, result: dict, *, effect_status: str = "confirmed"
) -> None:
    with Session(engine()) as db, db.begin():
        db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        operation = db.get(Operation, operation_id)
        operation.status = "failed" if result.get("error") is not None else "succeeded"
        operation.effect_status = effect_status
        operation.result = result
        observation = (operation.tool_name, operation.arguments)
        emit(
            db,
            run_id,
            "tool.result",
            {
                "operation_id": operation_id,
                "tool_call_id": operation.tool_call_id,
                "tool_name": operation.tool_name,
                "result": result,
            },
        )

    from workbench.modules.runs.safety import observe

    observe(run_id, operation_id, *observation, result)


def rule_notice(run_id: str, path: str) -> dict | None:
    from workbench.modules.context.compiler import path_rule_notice

    with Session(engine()) as db, db.begin():
        return path_rule_notice(db, run_id, path)


def answer_result(question_id: str) -> dict:
    with Session(engine()) as db:
        answer = db.get(Question, question_id)
        if answer.status != "answered":
            raise AppError("answer_not_committed", "问题答案尚未持久化，暂不能继续。", 409)
        return {"answer": answer.answer["value"]}


def approved_result(approval_id: str, values: dict) -> bool:
    with Session(engine()) as db:
        decision = db.get(Approval, approval_id)
        if decision.status not in {"approved", "rejected"}:
            raise AppError("approval_not_committed", "授权决定尚未持久化。", 409)
        if decision.parameters_digest != argument_digest(values):
            raise AppError(
                "approval_parameters_changed", "操作参数已变，原授权不再适用。", 409, "policy"
            )
        return decision.approved


def needs_approval(run_id: str, name: str, values: dict) -> bool:
    if TOOLS[name].requires_approval:
        return True
    if name != "read_artifact":
        return False
    from workbench.modules.files.inputs import is_remote, local_only_source
    from workbench.modules.files.service import get_artifact
    from workbench.modules.models.domain import ModelRevision

    with Session(engine()) as db:
        run = db.get(Run, run_id)
        revision = db.get(ModelRevision, run.config_snapshot["model_revision_id"])
        if not is_remote(revision.configuration["endpoint"]):
            return False
        if local_only_source(
            db, get_artifact(db, run.user_id, run.workspace_id, values["artifact_id"])
        ):
            raise AppError(
                "capture_remote_disabled", "截图及其派生内容当前仅供本地模型。", 403, "policy"
            )
        from workbench.modules.conversations import Message

        target = f"{revision.connection_id}:{revision.connection_version}"
        return not any(
            values["artifact_id"] in {item["artifact_id"], item.get("source_artifact_id")}
            and item.get("consent_remote")
            and item.get("consent_target") == target
            for refs in db.scalars(select(Message.attachments).where(Message.run_id == run_id))
            for item in refs or []
        )


def execution_root(run_id: str, operation_id: str, name: str, call_id: str) -> Path:
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run.cancel_requested_at:
            raise asyncio.CancelledError
        check_steering(db, run_id, operation_id)
        set_status(db, run, "running")
        workspace = db.get(Workspace, run.workspace_id)
        operation = db.get(Operation, operation_id)
        operation.status = "running"
        operation.effect_status = (
            "possibly_submitted" if TOOLS[name].requires_approval else "not_started"
        )
        emit(
            db,
            run_id,
            "tool.progress",
            {
                "tool_name": name,
                "tool_call_id": call_id,
                "operation_id": operation_id,
                "phase": "executing",
            },
        )
        return Path(workspace.root_path)


def check_steering(db, run_id, operation_id):
    from workbench.modules.collaboration.inbox import has_new_directive

    operation = db.get(Operation, operation_id)
    if operation and (
        operation.status == "waiting_input" or operation.effect_status != "not_started"
    ):
        # An MCP continuation or an already launched command must reach its
        # real completion/reconciliation boundary; steering is not rollback.
        return
    if has_new_directive(db, run_id):
        raise AppError(
            "tool_skipped_for_steering", "收到新要求，此操作尚未执行，将重新规划。", kind="steering"
        )


def before_tool(run_id, operation_id):
    with Session(engine()) as db:
        check_steering(db, run_id, operation_id)


def file_change(run_id: str, root: Path, name: str, values: dict) -> dict:
    from workbench.infrastructure.locks import advisory_connection
    from workbench.modules.runs.ownership import assert_owned

    targets = (
        [item["path"] for item in values["files"]] if name == "edit_files" else [values["path"]]
    )
    with ExitStack() as locks:
        connection = None
        for target in sorted({str(filesystem.authorized_path(root, path)) for path in targets}):
            connection = locks.enter_context(advisory_connection(f"workspace-file:{target}"))
        with Session(bind=connection) as db, db.begin():
            run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            assert_owned(db, run_id)
            if run.cancel_requested_at:
                raise asyncio.CancelledError
            if name == "edit_files":
                from workbench.modules.tools.file_queries import prepare_edits

                prepared = prepare_edits(root, values)
                results = []
                for item in prepared:
                    if run.cancel_requested_at:
                        raise asyncio.CancelledError
                    try:
                        results.append(filesystem.change_file(root, item, patch=False))
                    except AppError as error:
                        return {
                            "files": results,
                            "complete": False,
                            "error": {"code": error.code, "message": error.message},
                            "remaining": [row["path"] for row in prepared[len(results) :]],
                        }
                return {"files": results, "complete": True}
            if name == "edit_notebook":
                from workbench.modules.tools.notebooks import edit_notebook

                return edit_notebook(root, values)
            return filesystem.change_file(root, values, patch=name == "apply_patch")


def record_unknown(run_id: str, operation_id: str, name: str, result: dict) -> None:
    with Session(engine()) as db, db.begin():
        db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        operation = db.get(Operation, operation_id)
        combined = {**(operation.result or {}), **result}
        if operation.status == "outcome_unknown" and operation.result == combined:
            return
        operation.status = "outcome_unknown"
        operation.effect_status = "needs_reconciliation"
        operation.result = combined
        emit(
            db,
            run_id,
            "tool.outcome_unknown",
            {
                "operation_id": operation_id,
                "tool_name": name,
                "message": (combined.get("error") or {}).get("message")
                or "工具实际执行结果尚未确认，请先核对原记录，不要重复执行。",
            },
        )


async def execute_one(run_id: str, call: dict) -> dict:
    try:
        operation_id, existing, values = await asyncio.to_thread(ensure_operation, run_id, call)
        if existing is not None:
            return existing
        await asyncio.to_thread(before_tool, run_id, operation_id)
        name = call["name"]
        if TOOLS[name].module == "filesystem":
            paths = (
                [values["path"]]
                if "path" in values
                else [item["path"] for item in values.get("files", [])]
            )
            for path in paths:
                notice = await asyncio.to_thread(rule_notice, run_id, path)
                if notice:
                    await asyncio.to_thread(
                        record_result, run_id, operation_id, notice, effect_status="not_started"
                    )
                    return notice
        if name == "ask_user":
            question = await asyncio.to_thread(prepare_question, run_id, operation_id, values)
            interrupt({"kind": "question", "question_id": question["id"]})
            await asyncio.to_thread(before_tool, run_id, operation_id)
            result = await asyncio.to_thread(answer_result, question["id"])
            await asyncio.to_thread(record_result, run_id, operation_id, result)
            return result
        if name == "wait_agent_message":
            from workbench.modules.collaboration.service import (
                finish_message_wait,
                prepare_message_wait,
            )

            if await asyncio.to_thread(prepare_message_wait, run_id, values["timeout_seconds"]):
                interrupt({"kind": "agent_message", "run_id": run_id})
            result = jsonable_encoder(await asyncio.to_thread(finish_message_wait, run_id))
            await asyncio.to_thread(record_result, run_id, operation_id, result)
            return result
        if name == "wait_agents":
            from workbench.modules.collaboration.service import finish_wait, prepare_wait

            if await asyncio.to_thread(prepare_wait, run_id, values["timeout_seconds"]):
                interrupt({"kind": "agents", "run_id": run_id})
            result = jsonable_encoder(await asyncio.to_thread(finish_wait, run_id))
            await asyncio.to_thread(record_result, run_id, operation_id, result)
            return result
        if await asyncio.to_thread(needs_approval, run_id, name, values):
            approval = await asyncio.to_thread(
                prepare_approval,
                run_id,
                operation_id,
                name,
                values,
                provided_arguments=call["arguments"],
            )
            if approval["status"] == "pending":
                from workbench.modules.tools.review import review

                await review(run_id, approval["id"])
                approval = await asyncio.to_thread(
                    prepare_approval,
                    run_id,
                    operation_id,
                    name,
                    values,
                    provided_arguments=call["arguments"],
                )
            if approval["status"] == "pending":
                interrupt({"kind": "approval", "approval_id": approval["id"]})
            await asyncio.to_thread(before_tool, run_id, operation_id)
            approved = await asyncio.to_thread(approved_result, approval["id"], values)
            if not approved:
                assessment = approval.get("review") or {}
                reason = assessment.get("reason") if assessment.get("decision") == "deny" else None
                message = f"自动审核已拒绝：{reason}" if reason else "本次操作未获批准。"
                result = {"error": {"code": "permission_denied", "message": message}}
                await asyncio.to_thread(
                    record_result, run_id, operation_id, result, effect_status="not_started"
                )
                return result
        root = await asyncio.to_thread(execution_root, run_id, operation_id, name, call["id"])
        from workbench.modules.tools.watchdog import bounded

        result = await bounded(
            run_id, operation_id, name, values, invoke(run_id, operation_id, root, name, values)
        )
        if name == "exec_command" and result.get("timed_out"):
            await asyncio.to_thread(record_unknown, run_id, operation_id, name, result)
            raise AppError(
                "command_timed_out", "命令超时，已停止进程，结果需要核对。", 409, "outcome_unknown"
            )
        if name == "exec_command" and result["exit_code"] != 0:
            result["error"] = {"code": "command_failed", "message": "命令已结束，退出码非零。"}
        result = jsonable_encoder(result)
        await asyncio.to_thread(record_result, run_id, operation_id, result)
        from workbench.modules.runs.safety import observe

        await asyncio.to_thread(observe, run_id, operation_id, name, values, result)
        return result
    except AppError as error:
        if error.kind == "outcome_unknown":
            if "operation_id" in locals():
                await asyncio.to_thread(
                    record_unknown,
                    run_id,
                    operation_id,
                    call["name"],
                    {"error": {"code": error.code, "message": error.message}},
                )
            raise
        if error.kind == "ownership":
            raise
        result = {
            "error": {"code": error.code, "message": error.message, "retryable": error.retryable}
        }
        if error.code in {"invalid_tool_arguments", "command_capacity"} and error.details:
            result["error"]["details"] = error.details
        if "operation_id" in locals():
            await asyncio.to_thread(
                record_result, run_id, operation_id, result, effect_status="not_started"
            )
            from workbench.modules.runs.safety import observe

            await asyncio.to_thread(observe, run_id, operation_id, call["name"], values, result)
        return result
    except OSError:
        if "operation_id" in locals() and TOOLS[call["name"]].requires_approval:
            raise AppError(
                "tool_outcome_unknown",
                "系统操作中断，部分修改可能已发生，请核对目标。",
                409,
                "outcome_unknown",
            ) from None
        result = {
            "error": {
                "code": "tool_io_error",
                "message": "系统未能完成文件或进程操作，请检查权限与目标。",
            }
        }
        if "operation_id" in locals():
            await asyncio.to_thread(
                record_result, run_id, operation_id, result, effect_status="not_started"
            )
        return result


async def invoke(run_id, operation_id, root, name, values):
    if TOOLS[name].module in {
        "memory",
        "search",
        "discovery",
        "artifacts",
        "collaboration",
        "context",
        "conversations",
        "guide",
        "mcp",
        "generated",
        "automation",
        "processes",
        "skills",
        "rules",
        "media",
        "plan",
        "configuration",
    }:
        from workbench.modules.tools.extensions import execute

        return await execute(run_id, operation_id, name, values)
    if name in {"write_file", "apply_patch", "edit_files", "edit_notebook"}:
        return await asyncio.to_thread(file_change, run_id, root, name, values)
    if name == "list_files":
        return await asyncio.to_thread(
            filesystem.list_files,
            root,
            values["path"],
            cursor=values.get("cursor"),
            limit=values.get("limit", 500),
        )
    if name in {"find_files", "read_files", "search_files"}:
        from workbench.modules.tools import file_queries

        if name == "search_files":
            return await file_queries.search_files(root, values)
        return await asyncio.to_thread(getattr(file_queries, name), root, values)
    if name == "read_file":
        return await asyncio.to_thread(filesystem.read_file, root, values)
    if name == "read_notebook":
        from workbench.modules.tools.notebooks import read_notebook

        return await asyncio.to_thread(read_notebook, root, values)
    if name == "code_intelligence":
        from workbench.modules.preferences.configuration import get_state
        from workbench.modules.tools.code_intelligence import execute

        with Session(engine()) as db:
            run = db.get(Run, run_id)
            row = get_state(db, run.user_id, "developer_tools", run.workspace_id)
            row = row or get_state(db, run.user_id, "developer_tools")
            servers = row.values.get("language_servers") if row else None
        return await execute(root, values, servers=servers)
    if name == "exec_command":
        from workbench.modules.processes.service import execute_bounded

        return await execute_bounded(run_id, operation_id, values)
    return await filesystem.execute(root, name, values)
