"""All delegation decisions are durable and serialized on their root Run row."""

from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, now
from workbench.modules.collaboration.domain import AgentMessage, AgentSlot
from workbench.modules.conversations import Message, Thread
from workbench.modules.models import service as models
from workbench.modules.runs.domain import TERMINAL_STATUSES, Attempt, Job, Run, Step
from workbench.modules.runs.ownership import assert_owned, current_owner
from workbench.modules.runs.service import emit, get, idempotent, set_status, view
from workbench.modules.tools.domain import Operation
from workbench.modules.tools.filesystem import authorized_path
from workbench.modules.tools.registry import TOOLS
from workbench.modules.workspaces import Workspace


def lock_root(db: Session, actor_id: str) -> tuple[Run, Run]:
    actor = db.get(Run, actor_id)
    root = db.scalar(select(Run).where(Run.id == (actor.root_run_id or actor.id)).with_for_update())
    actor = (
        root
        if actor.id == root.id
        else db.scalar(select(Run).where(Run.id == actor_id).with_for_update())
    )
    assert_owned(db, actor_id)
    return root, actor


def slot_view(db, slot):
    run = db.get(Run, slot.run_id)
    result = db.scalar(
        select(Message.content)
        .where(
            Message.run_id == run.id,
            Message.role == "assistant",
            Message.status == "completed",
            Message.content != "",
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    from workbench.modules.context.transfers import allowed_result

    if result and not allowed_result(db, db.get(Run, slot.root_run_id), run.id):
        result = "子任务文件内容尚未授权给主任务模型；正文保留在本机。"
    return {
        "agent_id": slot.id,
        "name": slot.name,
        "deliverable": slot.deliverable,
        "root_run_id": slot.root_run_id,
        "released_at": slot.released_at,
        "allowed_tools": run.config_snapshot.get("allowed_tools", []),
        "paths": run.config_snapshot.get("allowed_paths", []),
        "mode": run.config_snapshot.get("agent_mode", "read_only"),
        "run": view(db, run),
        "result": result[:8000] if result else None,
    }


def tree(db, user_id, run_id):
    run = get(db, user_id, run_id)
    root = get(db, user_id, run.root_run_id or run.id)
    slots = list(
        db.scalars(
            select(AgentSlot).where(AgentSlot.root_run_id == root.id).order_by(AgentSlot.created_at)
        )
    )
    return {
        "root": view(db, root),
        "agents": [slot_view(db, slot) for slot in slots],
        "limit": root.config_snapshot.get("runtime_options", {}).get("child_agent_limit", 3),
        "occupied": sum(slot.released_at is None for slot in slots),
    }


def spawn(run_id: str, operation_id: str, values: dict) -> dict:
    with Session(engine()) as db, db.begin():
        root, actor = lock_root(db, run_id)
        if actor.parent_run_id:
            raise AppError("agent_depth_limit", "子Agent不能继续派生下一层。", 409, "policy")
        slot_id = str(uuid5(UUID(operation_id), "agent-slot"))
        existing = db.get(AgentSlot, slot_id)
        if existing:
            return slot_view(db, existing)
        if root.cancel_requested_at or root.status in TERMINAL_STATUSES:
            raise AppError("parent_finished", "父任务已停止，不能派生子任务。", 409)
        count = db.scalar(
            select(func.count())
            .select_from(AgentSlot)
            .where(AgentSlot.root_run_id == root.id, AgentSlot.released_at.is_(None))
        )
        limit = min(3, root.config_snapshot.get("runtime_options", {}).get("child_agent_limit", 3))
        if count >= limit:
            raise AppError(
                "agent_slot_limit",
                f"此根任务最多有{limit}个未释放子Agent；排队和等待也占名额。",
                409,
                "resource",
            )
        allowed = set(values["allowed_tools"])
        parent_allowed = set(root.config_snapshot.get("allowed_tools", list(TOOLS)))
        if (
            not allowed
            or not allowed <= parent_allowed
            or any(name not in TOOLS for name in allowed)
        ):
            raise AppError(
                "agent_tools_invalid", "子任务工具必须是父权限允许的明确子集。", 422, "policy"
            )
        if {"exec_command", "start_command"} & allowed or any(
            TOOLS[name].module == "collaboration" and name != "inspect_agents" for name in allowed
        ):
            raise AppError(
                "agent_tools_escalation",
                "子任务不提供未隔离主机命令或继续派生、控制其它Agent的权限。",
                403,
                "policy",
            )
        writes = allowed & {"write_file", "apply_patch"}
        if writes and (values["mode"] != "serial_write" or not values["paths"]):
            raise AppError(
                "agent_write_scope",
                "子任务写入必须选择串行条件写模式并指定文件范围。",
                422,
                "policy",
            )
        workspace = db.get(Workspace, root.workspace_id)
        base = Path(workspace.root_path).resolve()
        paths = sorted(
            {authorized_path(base, item).relative_to(base).as_posix() for item in values["paths"]}
        )
        model_id = values.get("model_connection_id") or root.model_connection_id
        model = models.get(db, root.user_id, model_id)
        models.validate_endpoint(model.endpoint, model.allow_remote)
        revision = models.freeze(db, root.user_id, model_id)
        child_thread = Thread(
            user_id=root.user_id, workspace_id=root.workspace_id, title=values["name"]
        )
        db.add(child_thread)
        db.flush()
        child = Run(
            id=str(uuid5(UUID(operation_id), "agent-run")),
            user_id=root.user_id,
            workspace_id=root.workspace_id,
            thread_id=child_thread.id,
            model_connection_id=model_id,
            root_run_id=root.id,
            parent_run_id=root.id,
            input=values["task"],
            deadline_at=root.deadline_at,
            config_snapshot={
                "model_version": model.version,
                "model_revision_id": revision.id,
                "max_output_tokens": min(
                    values["max_output_tokens"],
                    revision.configuration["generation"]["max_output_tokens"],
                ),
                "runtime_options": {
                    **root.config_snapshot.get("runtime_options", {}),
                    "max_model_rounds": values["max_model_rounds"],
                    "child_agent_limit": 0,
                },
                "allowed_tools": sorted(allowed),
                "tool_names": sorted(allowed),
                "allowed_paths": paths,
                "agent_mode": values["mode"],
                "deliverable": values["deliverable"],
            },
        )
        db.add(child)
        db.flush()
        db.add(
            Message(
                thread_id=child_thread.id,
                run_id=child.id,
                role="user",
                content=(
                    f"子任务目标：{values['task']}\n交付要求：{values['deliverable']}\n"
                    f"允许路径：{paths or ['整个授权工作区（只读）']}；模式：{values['mode']}。"
                    "只处理本子任务，结果由主Agent整合与验收；不能自行扩大权限。"
                ),
            )
        )
        slot = AgentSlot(
            id=slot_id,
            root_run_id=root.id,
            run_id=child.id,
            name=values["name"],
            deliverable=values["deliverable"],
        )
        db.add(slot)
        db.add(Job(run_id=child.id, available_at=now()))
        emit(
            db,
            root.id,
            "agent.spawned",
            {"agent_id": slot.id, "run_id": child.id, "name": slot.name, "status": "queued"},
        )
        return slot_view(db, slot)


def pending(db, root_id):
    return (
        db.scalar(
            select(Run.id)
            .where(Run.parent_run_id == root_id, Run.status.not_in(TERMINAL_STATUSES))
            .limit(1)
        )
        is not None
    )


def prepare_wait(run_id, seconds=None):
    with Session(engine()) as db, db.begin():
        root, actor = lock_root(db, run_id)
        if not pending(db, actor.id):
            return False
        if seconds and actor.wait_expires_at is None:
            actor.wait_expires_at = now() + timedelta(seconds=seconds)
        set_status(db, actor, "waiting_children")
        return True


def wait_ready(db, run_id):
    run = db.get(Run, run_id)
    return not pending(db, run_id) or bool(run.wait_expires_at and run.wait_expires_at <= now())


def has_pending(run_id):
    with Session(engine()) as db:
        return pending(db, run_id)


def finish_wait(run_id):
    with Session(engine()) as db, db.begin():
        _, actor = lock_root(db, run_id)
        actor.wait_expires_at = None
        return tree(db, actor.user_id, actor.id)


def release(run_id, agent_id):
    with Session(engine()) as db, db.begin():
        root, actor = lock_root(db, run_id)
        slot = db.get(AgentSlot, agent_id)
        if actor.id != root.id or slot is None or slot.root_run_id != root.id:
            raise AppError("agent_not_found", "此子Agent不属于当前主任务。", 404)
        child = db.scalar(select(Run).where(Run.id == slot.run_id).with_for_update())
        job = db.scalar(select(Job).where(Job.run_id == child.id).with_for_update())
        unsafe = db.scalar(
            select(Operation.id)
            .where(
                Operation.run_id == child.id, Operation.status.in_(("running", "outcome_unknown"))
            )
            .limit(1)
        )
        if child.status not in TERMINAL_STATUSES or (job and job.lease_owner) or unsafe:
            raise AppError(
                "agent_release_pending",
                "子任务尚未完成资源清理或存在结果未知操作，名额仍保留。",
                409,
            )
        if slot.released_at is None:
            slot.released_at = now()
            emit(db, root.id, "agent.released", {"agent_id": slot.id})
        return slot_view(db, slot)


def send_message(run_id, operation_id, agent_id, content):
    with Session(engine()) as db, db.begin():
        root, actor = lock_root(db, run_id)
        slot = db.get(AgentSlot, agent_id)
        if slot is None or slot.root_run_id != root.id:
            raise AppError("agent_not_found", "子Agent不属于当前任务树。", 404)
        existing = db.scalar(select(AgentMessage).where(AgentMessage.operation_id == operation_id))
        if existing is not None:
            if (
                existing.from_run_id != actor.id
                or existing.to_run_id != slot.run_id
                or existing.content != content
            ):
                raise AppError("agent_message_conflict", "已有协作消息的参数或身份不同。", 409)
            # Recovery returns the accepted fact even if the recipient finished
            # before the tool-result/checkpoint commit.
            return {"agent_id": slot.id, "state": "queued_for_next_model_call"}
        target = db.scalar(select(Run).where(Run.id == slot.run_id).with_for_update())
        job = db.scalar(select(Job).where(Job.run_id == target.id))
        if (
            target.status in TERMINAL_STATUSES | {"held", "cancelling"}
            or target.cancel_requested_at
            or (job and job.completion_status)
        ):
            raise AppError(
                "agent_finished", "目标子任务已结束；请保留其结果并明确创建新子任务。", 409
            )
        db.add(
            AgentMessage(
                root_run_id=root.id,
                from_run_id=actor.id,
                to_run_id=target.id,
                operation_id=operation_id,
                content=content,
            )
        )
        return {"agent_id": slot.id, "state": "queued_for_next_model_call"}


def cancel_agent(run_id, operation_id, agent_id):
    from workbench.modules.runs.service import cancel

    with Session(engine()) as db, db.begin():
        root, actor = lock_root(db, run_id)
        slot = db.get(AgentSlot, agent_id)
        if actor.id != root.id or slot is None or slot.root_run_id != root.id:
            raise AppError("agent_not_found", "此子Agent不属于当前主任务。", 404)
        # The authenticated command remains bound to the validated tree; the child's
        # cancellation is not a lease-owned execution by the parent worker.
        token = current_owner.set(None)
        try:
            return cancel(db, actor.user_id, slot.run_id, operation_id)
        finally:
            current_owner.reset(token)


def steer(db, user_id, run_id, values):
    def execute():
        run = get(db, user_id, run_id, lock=True)
        job = db.scalar(select(Job).where(Job.run_id == run.id))
        if (
            run.status in TERMINAL_STATUSES | {"held", "cancelling"}
            or run.cancel_requested_at
            or (job and job.completion_status)
        ):
            raise AppError(
                "run_finished", "任务已结束、暂停或正在收尾，请保留草稿并发送新消息。", 409
            )
        message = AgentMessage(
            root_run_id=run.root_run_id or run.id,
            from_run_id=run.id,
            to_run_id=run.id,
            source="user",
            operation_id=values["request_id"],
            content=values["input"],
        )
        db.add(message)
        db.flush()
        # Accepted user input is part of conversation history even if the run
        # later fails or is cancelled. Delivery is a separate durable fact.
        db.add(
            Message(
                id=message.id,
                thread_id=run.thread_id,
                run_id=run.id,
                role="user",
                content=values["input"],
                status="completed",
                provider_message={
                    "role": "user",
                    "content": values["input"],
                    "source_kind": "user_steering",
                    "_input_message_id": message.id,
                },
            )
        )
        emit(
            db,
            run.id,
            "run.input_received",
            {"message_id": message.id, "effective": "next_model_call"},
        )
        return {"id": message.id, "status": "queued_for_next_model_call"}

    return idempotent(db, user_id, values["request_id"], f"run:{run_id}:input", values, execute)


def budget_check(db, run):
    root = db.get(Run, run.root_run_id or run.id)
    maximum = root.config_snapshot.get("runtime_options", {}).get("root_model_attempt_limit", 60)
    attempts = db.scalar(
        select(func.count())
        .select_from(Attempt)
        .join(Run, Run.id == Attempt.run_id)
        .join(Step, Step.id == Attempt.step_id)
        .where(Run.root_run_id == root.id, Step.kind == "model")
    )
    if attempts >= maximum:
        raise AppError(
            "root_model_budget",
            "主任务与子任务已达到共同模型调用预算，未发出更多请求。",
            409,
            "budget",
        )
