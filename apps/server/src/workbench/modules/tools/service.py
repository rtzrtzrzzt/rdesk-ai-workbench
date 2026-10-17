import asyncio
import hashlib
import json
from pathlib import Path

from fastapi.encoders import jsonable_encoder
from langgraph.types import interrupt
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine
from workbench.modules.interactions import Approval, Question
from workbench.modules.runs.domain import Run
from workbench.modules.runs.service import approval_view, emit, question_view, set_status
from workbench.modules.tools import filesystem
from workbench.modules.tools.domain import Operation
from workbench.modules.tools.registry import CORE_TOOL_NAMES, TOOLS
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
    except ValidationError:
        raise AppError("invalid_tool_arguments", "工具参数不符合格式，请修正后继续。") from None
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
        if operation:
            if operation.tool_name != name or operation.arguments != values:
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
    if TOOLS[name].requires_approval and run.config_snapshot.get("agent_mode") != "serial_write":
        raise AppError("agent_read_only", "此子任务为只读模式。", 403, "policy")
    paths = run.config_snapshot.get("allowed_paths", [])
    if paths and TOOLS[name].module == "filesystem" and "path" in values:
        root = Path(db.get(Workspace, run.workspace_id).root_path).resolve()
        target = filesystem.authorized_path(root, values["path"])
        if not any(target.is_relative_to(filesystem.authorized_path(root, path)) for path in paths):
            raise AppError("agent_path_scope", "此路径不在子任务允许的文件范围。", 403, "policy")


def prepare_question(run_id: str, operation_id: str, values: dict) -> dict:
    if values["kind"] != "text":
        identifiers = [option["id"] for option in values["options"]]
        if len(identifiers) < 2 or len(set(identifiers)) != len(identifiers):
            raise AppError("invalid_question_options", "选择题需要至少两个不同的有效选项。")
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        question = db.scalar(select(Question).where(Question.interaction_key == operation_id))
        if question is None:
            question = Question(run_id=run_id, interaction_key=operation_id, **values)
            db.add(question)
            db.flush()
            emit(db, run_id, "question.created", {"question": question_view(question)})
        if question.status == "pending":
            set_status(db, run, "waiting_user")
        return question_view(question)


def prepare_approval(run_id: str, operation_id: str, name: str, values: dict) -> dict:
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        approval = db.scalar(select(Approval).where(Approval.operation_id == operation_id))
        if approval is None:
            summary = (
                "允许在主机运行此命令？当前模式仅限制工作目录，未提供系统沙箱。"
                if name in {"exec_command", "start_command"}
                else f"允许 {name} 修改这个文件？提交前会再次核对版本。"
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
    from workbench.modules.files.inputs import is_remote
    from workbench.modules.models.domain import ModelRevision

    with Session(engine()) as db:
        run = db.get(Run, run_id)
        revision = db.get(ModelRevision, run.config_snapshot["model_revision_id"])
        if not is_remote(revision.configuration["endpoint"]):
            return False
        from workbench.modules.conversations import Message

        target = f"{revision.connection_id}:{revision.connection_version}"
        return not any(
            item["artifact_id"] == values["artifact_id"]
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


def file_change(run_id: str, root: Path, name: str, values: dict) -> dict:
    from workbench.infrastructure.locks import advisory_connection
    from workbench.modules.runs.ownership import assert_owned

    target = filesystem.authorized_path(root, values["path"])
    with advisory_connection(f"workspace-file:{target}") as connection:
        with Session(bind=connection) as db, db.begin():
            run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            assert_owned(db, run_id)
            if run.cancel_requested_at:
                raise asyncio.CancelledError
            return filesystem.change_file(root, values, patch=name == "apply_patch")


def record_unknown(run_id: str, operation_id: str, name: str, result: dict) -> None:
    with Session(engine()) as db, db.begin():
        db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        operation = db.get(Operation, operation_id)
        operation.status = "outcome_unknown"
        operation.effect_status = "needs_reconciliation"
        operation.result = result
        emit(
            db,
            run_id,
            "tool.outcome_unknown",
            {
                "operation_id": operation_id,
                "tool_name": name,
                "message": "命令已超时停止，部分修改可能已经发生，请先核对结果。",
            },
        )


async def execute_one(run_id: str, call: dict) -> dict:
    try:
        operation_id, existing, values = await asyncio.to_thread(ensure_operation, run_id, call)
        if existing is not None:
            return existing
        name = call["name"]
        if TOOLS[name].module == "filesystem" and "path" in values:
            notice = await asyncio.to_thread(rule_notice, run_id, values["path"])
            if notice:
                await asyncio.to_thread(
                    record_result, run_id, operation_id, notice, effect_status="not_started"
                )
                return notice
        if name == "ask_user":
            question = await asyncio.to_thread(prepare_question, run_id, operation_id, values)
            interrupt({"kind": "question", "question_id": question["id"]})
            result = await asyncio.to_thread(answer_result, question["id"])
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
            approval = await asyncio.to_thread(prepare_approval, run_id, operation_id, name, values)
            interrupt({"kind": "approval", "approval_id": approval["id"]})
            approved = await asyncio.to_thread(approved_result, approval["id"], values)
            if not approved:
                result = {"error": {"code": "permission_denied", "message": "用户拒绝了此操作。"}}
                await asyncio.to_thread(
                    record_result, run_id, operation_id, result, effect_status="not_started"
                )
                return result
        root = await asyncio.to_thread(execution_root, run_id, operation_id, name, call["id"])
        if TOOLS[name].module in {
            "memory",
            "search",
            "discovery",
            "artifacts",
            "collaboration",
            "context",
            "mcp",
            "generated",
            "automation",
            "processes",
        }:
            from workbench.modules.tools.extensions import execute

            result = await execute(run_id, operation_id, name, values)
        elif name in {"write_file", "apply_patch"}:
            result = await asyncio.to_thread(file_change, run_id, root, name, values)
        elif name == "list_files":
            result = await asyncio.to_thread(filesystem.list_files, root, values["path"])
        elif name == "read_file":
            result = await asyncio.to_thread(filesystem.read_file, root, values)
        elif name == "exec_command":
            from workbench.modules.processes.service import execute_bounded

            result = await execute_bounded(run_id, operation_id, values)
        else:
            result = await filesystem.execute(root, name, values)
        if name == "exec_command" and result.get("timed_out"):
            await asyncio.to_thread(record_unknown, run_id, operation_id, name, result)
            raise AppError(
                "command_timed_out", "命令超时，已停止进程，结果需要核对。", 409, "outcome_unknown"
            )
        if name == "exec_command" and result["exit_code"] != 0:
            result["error"] = {"code": "command_failed", "message": "命令已结束，退出码非零。"}
        result = jsonable_encoder(result)
        await asyncio.to_thread(record_result, run_id, operation_id, result)
        return result
    except AppError as error:
        if error.kind in {"outcome_unknown", "ownership"}:
            raise
        result = {
            "error": {"code": error.code, "message": error.message, "retryable": error.retryable}
        }
        if "operation_id" in locals():
            await asyncio.to_thread(
                record_result, run_id, operation_id, result, effect_status="not_started"
            )
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
