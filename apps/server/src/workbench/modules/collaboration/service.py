"""All delegation decisions are durable and serialized on their root Run row."""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, now
from workbench.modules.collaboration.domain import AgentMessage, AgentSlot, AgentTurn
from workbench.modules.collaboration.policy import CHILD_COLLABORATION_TOOLS, child_tool_allowed
from workbench.modules.conversations import Message, Thread
from workbench.modules.models import service as models
from workbench.modules.models.domain import ModelRevision
from workbench.modules.runs.domain import TERMINAL_STATUSES, Attempt, Job, Run, Step
from workbench.modules.runs.ownership import assert_owned, current_owner
from workbench.modules.runs.service import emit, get, idempotent, set_status, view
from workbench.modules.tools.domain import Operation
from workbench.modules.tools.filesystem import authorized_path, display_path
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
    turns = list(
        db.scalars(
            select(AgentTurn.run_id)
            .where(AgentTurn.agent_id == slot.id)
            .order_by(AgentTurn.created_at, AgentTurn.id)
        )
    )
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
        "state": (
            "closed"
            if slot.released_at
            else "stopping"
            if run.cancel_requested_at and run.status not in TERMINAL_STATUSES
            else "idle"
            if run.status in TERMINAL_STATUSES
            else "busy"
        ),
        "turn_count": len(turns),
        "turn_run_ids": turns,
    }


def tree(db, user_id, run_id):
    run = get(db, user_id, run_id)
    root = get(db, user_id, run.root_run_id or run.id)
    slots = list(
        db.scalars(
            select(AgentSlot).where(AgentSlot.root_run_id == root.id).order_by(AgentSlot.created_at)
        )
    )
    from workbench.modules.media.domain import MediaTask
    from workbench.modules.media.service import task_view

    media = db.scalars(
        select(MediaTask)
        .join(Run, Run.id == MediaTask.run_id)
        .where(
            Run.user_id == user_id,
            Run.workspace_id == root.workspace_id,
            Run.root_run_id == root.id,
        )
        .order_by(MediaTask.created_at)
        .limit(100)
    )
    return {
        "root": view(db, root),
        "agents": [slot_view(db, slot) for slot in slots],
        "media_tasks": [task_view(db, item) for item in media],
        "file_tasks": _file_tasks(db, root),
        "limit": root.config_snapshot.get("runtime_options", {}).get("child_agent_limit", 3),
        "occupied": sum(slot.released_at is None for slot in slots),
    }


def _file_tasks(db, root):
    from workbench.modules.files.service import task_view

    return [
        task_view(db, run)
        for run in db.scalars(
            select(Run)
            .where(
                Run.user_id == root.user_id,
                Run.workspace_id == root.workspace_id,
                Run.root_run_id == root.id,
                Run.kind == "file",
            )
            .order_by(Run.created_at)
            .limit(100)
        )
    ]


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
        parent_allowed = set(root.config_snapshot.get("allowed_tools", list(TOOLS)))
        requested = values.get("allowed_tools")
        allowed = (
            set(requested)
            if requested is not None
            else {
                name
                for name in parent_allowed
                if name in TOOLS
                and (TOOLS[name].replay_policy == "read" or name == "discover_tools")
                and not TOOLS[name].requires_approval
                and child_tool_allowed(name, TOOLS[name].module)
            }
        )
        if not allowed <= parent_allowed or any(name not in TOOLS for name in allowed):
            raise AppError(
                "agent_tools_invalid", "子任务工具必须是父权限允许的明确子集。", 422, "policy"
            )
        if any(not child_tool_allowed(name, TOOLS[name].module) for name in allowed):
            raise AppError(
                "agent_tools_escalation",
                "子任务不提供未隔离主机命令或继续派生、控制其它Agent的权限。",
                403,
                "policy",
            )
        # Communication is part of the child's runtime, including for an otherwise
        # pure-text task. It cannot grant file, network or agent-control authority.
        if requested is None:
            allowed |= CHILD_COLLABORATION_TOOLS & parent_allowed
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
            {display_path(base, authorized_path(base, item)) for item in values["paths"]}
        )
        model_id = values.get("model_connection_id") or root.model_connection_id
        from workbench.modules.access.service import scope_for_run

        public_scope = scope_for_run(db, root)
        if public_scope and model_id not in public_scope.allowed_model_ids:
            raise AppError("api_model_denied", "子任务模型未发布给此调用方。", 403, "policy")
        model = models.get(db, root.user_id, model_id)
        models.validate_endpoint(model.endpoint, model.allow_remote)
        revision = models.freeze(db, root.user_id, model_id)
        if model_id == root.model_connection_id:
            parent_revision = db.get(ModelRevision, root.config_snapshot.get("model_revision_id"))
            if parent_revision:
                revision = parent_revision
        from workbench.modules.models.engine_adapters import reasoning_overrides

        explicit_effort = values.get("reasoning_effort")
        if explicit_effort is not None:
            try:
                generation_overrides = reasoning_overrides(revision.configuration, explicit_effort)
            except AppError as error:
                from workbench.modules.models.engine_adapters import supported_reasoning_efforts

                allowed_efforts = supported_reasoning_efforts(revision.configuration)
                raise AppError(
                    "agent_reasoning_invalid",
                    f"此子模型可选思考档位：{', '.join(allowed_efforts) or '仅默认'}；"
                    "请使用列出的值，或省略reasoning_effort使用默认。",
                    422,
                ) from error
        elif model_id == root.model_connection_id:
            # Inherit the actual frozen choice, including disabled and custom
            # presets, rather than rebuilding it from a nullable effort alone.
            generation_overrides = dict(root.config_snapshot.get("generation_overrides", {}))
        else:
            generation_overrides = {}
        output_limits = [
            value
            for value in (
                root.config_snapshot.get("max_output_tokens"),
                revision.configuration["generation"].get("max_output_tokens"),
            )
            if value is not None
        ]
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
                "model_version": revision.connection_version,
                "model_revision_id": revision.id,
                "generation_overrides": generation_overrides,
                "extensions": list(root.config_snapshot.get("extensions", [])),
                "project_declarations": root.config_snapshot.get("project_declarations", ""),
                "max_output_tokens": min(output_limits) if output_limits else None,
                "runtime_options": {
                    **root.config_snapshot.get("runtime_options", {}),
                    "child_agent_limit": 0,
                },
                "allowed_tools": sorted(allowed),
                "tool_names": sorted(allowed),
                "allowed_paths": paths,
                "agent_mode": values["mode"],
                "deliverable": values["deliverable"],
            },
        )
        from workbench.modules.access.service import inherit_binding

        inherit_binding(root, child)
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
        db.flush()
        db.add(AgentTurn(agent_id=slot.id, run_id=child.id))
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
        from workbench.modules.collaboration.inbox import has_pending_inputs

        if not pending(db, actor.id) or has_pending_inputs(db, actor.id):
            return False
        if seconds and actor.wait_expires_at is None:
            actor.wait_expires_at = now() + timedelta(seconds=seconds)
        if actor.wait_expires_at and actor.wait_expires_at <= now():
            return False
        set_status(db, actor, "waiting_children")
        return True


def wait_ready(db, run_id, *, messages_only=False):
    from workbench.modules.collaboration.inbox import has_pending_inputs

    run = db.get(Run, run_id)
    return (
        has_pending_inputs(db, run_id)
        or (not messages_only and not pending(db, run_id))
        or bool(run.wait_expires_at and run.wait_expires_at <= now())
    )


def has_pending(run_id):
    with Session(engine()) as db:
        return pending(db, run_id)


def finish_wait(run_id):
    with Session(engine()) as db, db.begin():
        _, actor = lock_root(db, run_id)
        actor.wait_expires_at = None
        return tree(db, actor.user_id, actor.id)


def require_clean_turn(db, child):
    from workbench.modules.mcp.store import has_pending_transport_cleanup
    from workbench.modules.runs.resources import native_cleanup_pending
    from workbench.modules.runs.service import has_children_running

    job = db.scalar(select(Job).where(Job.run_id == child.id).with_for_update())
    unsafe = db.scalar(
        select(Operation.id)
        .where(
            Operation.run_id == child.id,
            Operation.status.in_(("running", "outcome_unknown")),
        )
        .limit(1)
    )
    if (
        child.status not in TERMINAL_STATUSES
        or (job and job.lease_owner)
        or unsafe
        or has_children_running(db, child.id)
        or has_pending_transport_cleanup(db, child.id)
        or native_cleanup_pending(db, child.id)
    ):
        raise AppError(
            "agent_release_pending",
            "子任务尚未完成资源清理或存在结果未知操作，名额仍保留，暂不能开启新轮或关闭。",
            409,
        )


def release(run_id, agent_id):
    with Session(engine()) as db, db.begin():
        root, actor = lock_root(db, run_id)
        slot = db.get(AgentSlot, agent_id)
        if actor.id != root.id or slot is None or slot.root_run_id != root.id:
            raise AppError("agent_not_found", "此子Agent不属于当前主任务。", 404)
        child = db.scalar(select(Run).where(Run.id == slot.run_id).with_for_update())
        require_clean_turn(db, child)
        if slot.released_at is None:
            slot.released_at = now()
            emit(db, root.id, "agent.released", {"agent_id": slot.id})
        return slot_view(db, slot)


def require_live_root(root):
    if (
        root.status in TERMINAL_STATUSES | {"paused", "held", "cancelling"}
        or root.cancel_requested_at
        or (root.deadline_at and root.deadline_at <= now())
    ):
        raise AppError("parent_finished", "主任务已结束、暂停或正在停止，不能追加协作工作。", 409)


def resolve_recipient(db, root, actor, agent_id, kind):
    if actor.parent_run_id:
        if agent_id != "parent" or kind == "instruction":
            raise AppError(
                "agent_message_scope",
                "子任务只能给主任务发送消息，不能控制或联系其它Agent。",
                403,
                "policy",
            )
        return root
    if agent_id == "parent":
        raise AppError("agent_not_found", "主任务没有父Agent，请使用已有子Agent标识。", 404)
    slot = db.get(AgentSlot, agent_id)
    if slot is None or slot.root_run_id != root.id or slot.released_at:
        raise AppError("agent_not_found", "子Agent不属于当前任务树或已关闭。", 404)
    return db.scalar(select(Run).where(Run.id == slot.run_id).with_for_update())


def queue_message(
    db, root, actor, operation_id, agent_id, content, kind="note", reply_to_id=None, source="agent"
):
    existing = db.scalar(select(AgentMessage).where(AgentMessage.operation_id == operation_id))
    if existing is not None:
        recipient_matches = (
            actor.parent_run_id and agent_id == "parent" and existing.to_run_id == root.id
        ) or (
            not actor.parent_run_id
            and db.scalar(
                select(AgentTurn.id).where(
                    AgentTurn.agent_id == agent_id, AgentTurn.run_id == existing.to_run_id
                )
            )
        )
        if (
            existing.from_run_id != actor.id
            or existing.content != content
            or existing.kind != kind
            or existing.reply_to_id != reply_to_id
            or not recipient_matches
        ):
            raise AppError("agent_message_conflict", "已有协作消息的参数或身份不同。", 409)
        return existing
    target = resolve_recipient(db, root, actor, agent_id, kind)
    require_live_root(root)
    job = db.scalar(select(Job).where(Job.run_id == target.id))
    if (
        target.status in TERMINAL_STATUSES | {"held", "cancelling"}
        or target.cancel_requested_at
        or (job and job.completion_status)
    ):
        raise AppError(
            "agent_finished", "目标执行轮已结束或正在收尾；继续已有子Agent请用followup_agent。", 409
        )
    if reply_to_id:
        original = db.get(AgentMessage, reply_to_id)
        from workbench.modules.collaboration.inbox import mailbox_runs

        if (
            original is None
            or original.root_run_id != root.id
            or original.to_run_id not in mailbox_runs(db, actor)
            or original.from_run_id not in mailbox_runs(db, target)
        ):
            raise AppError("agent_reply_scope", "只能回复此主子会话中收到的消息。", 403, "policy")
    if kind == "reply" and not reply_to_id:
        raise AppError(
            "agent_reply_required", "回复消息需要reply_to_id，使用收到消息的真实id。", 422
        )
    from workbench.modules.context.transfers import allowed_result

    if not allowed_result(db, target, actor.id):
        raise AppError(
            "agent_transfer_denied",
            "协作资料尚未授权传给目标模型版本，正文保留在本机。",
            403,
            "policy",
        )
    message = AgentMessage(
        id=str(uuid5(UUID(operation_id), "agent-message")),
        root_run_id=root.id,
        from_run_id=actor.id,
        to_run_id=target.id,
        operation_id=operation_id,
        source=source,
        kind=kind,
        reply_to_id=reply_to_id,
        content=content,
    )
    db.add(message)
    db.flush()
    if kind == "instruction":
        token = current_owner.set(None)
        try:
            wake_for_directive(db, target)
        finally:
            current_owner.reset(token)
    # Events belong to the executing actor. A child does not own its parent's lease.
    emit(
        db,
        actor.id,
        "agent.message.sent",
        {"message_id": message.id, "to_run_id": target.id, "kind": kind},
    )
    return message


def send_message(run_id, operation_id, agent_id, content, kind="note", reply_to_id=None):
    with Session(engine()) as db, db.begin():
        root, actor = lock_root(db, run_id)
        message = queue_message(db, root, actor, operation_id, agent_id, content, kind, reply_to_id)
        return {
            "agent_id": agent_id,
            "message_id": message.id,
            "kind": kind,
            "state": "queued_for_next_model_call",
            "starts_new_turn": False,
            "next_step": (
                "消息已持久接受，请勿重复发送相同内容。确实需要对方回复时才等待；"
                "交付已完成时正常最终答复，不必等待额外确认。"
            ),
        }


def followup(run_id, operation_id, agent_id, content):
    with Session(engine()) as db, db.begin():
        root, actor = lock_root(db, run_id)
        if actor.id != root.id:
            raise AppError("agent_control_scope", "只有主任务能追加子Agent工作。", 403, "policy")
        slot = db.get(AgentSlot, agent_id)
        if slot is None or slot.root_run_id != root.id:
            raise AppError("agent_not_found", "子Agent不属于当前任务树。", 404)
        accepted = db.scalar(select(AgentMessage).where(AgentMessage.operation_id == operation_id))
        if accepted is not None:
            if (
                accepted.from_run_id != actor.id
                or accepted.content != content
                or accepted.kind != "instruction"
                or not db.scalar(
                    select(AgentTurn.id).where(
                        AgentTurn.agent_id == slot.id, AgentTurn.run_id == accepted.to_run_id
                    )
                )
            ):
                raise AppError("agent_followup_conflict", "追加任务标识对应的参数或目标不同。", 409)
            return slot_view(db, slot)
        identifier = str(uuid5(UUID(operation_id), "agent-followup"))
        existing = db.get(Run, identifier)
        if existing:
            if existing.input != content or not db.scalar(
                select(AgentTurn.id).where(
                    AgentTurn.agent_id == slot.id, AgentTurn.run_id == identifier
                )
            ):
                raise AppError("agent_followup_conflict", "追加任务标识对应的参数不同。", 409)
            return slot_view(db, slot)
        require_live_root(root)
        if slot.released_at:
            raise AppError("agent_closed", "此Agent已关闭，请明确创建新Agent。", 409)
        previous = db.scalar(select(Run).where(Run.id == slot.run_id).with_for_update())
        # Archive/deletion admission owns Thread before Run. Never wait in the
        # opposite order: a concurrent lifecycle action wins and the caller retries.
        thread = db.scalar(
            select(Thread).where(Thread.id == previous.thread_id).with_for_update(skip_locked=True)
        )
        if thread is None or thread.archived or thread.deleting:
            raise AppError("thread_unavailable", "子会话正在变更、归档或删除，不能追加任务。", 409)
        if previous.status not in TERMINAL_STATUSES:
            if previous.status in {"paused", "held", "cancelling"} or previous.cancel_requested_at:
                raise AppError(
                    "agent_not_ready", "子任务已暂停或正在停止，不能越过当前边界追加工作。", 409
                )
            queue_message(db, root, actor, operation_id, agent_id, content, "instruction")
            return slot_view(db, slot)
        require_clean_turn(db, previous)
        config = deepcopy(previous.config_snapshot)
        for key in (
            "safety",
            "context_compaction",
            "task_plan",
            "model_continuation",
            "paused_reason",
        ):
            config.pop(key, None)
        child = Run(
            id=identifier,
            user_id=root.user_id,
            workspace_id=root.workspace_id,
            thread_id=previous.thread_id,
            model_connection_id=previous.model_connection_id,
            root_run_id=root.id,
            parent_run_id=root.id,
            input=content,
            deadline_at=root.deadline_at,
            config_snapshot=config,
        )
        from workbench.modules.access.service import inherit_binding

        inherit_binding(previous, child)
        db.add(child)
        db.flush()
        db.add(AgentTurn(agent_id=slot.id, run_id=child.id))
        message_id = str(uuid5(UUID(operation_id), "agent-message"))
        db.add(
            AgentMessage(
                id=message_id,
                root_run_id=root.id,
                from_run_id=root.id,
                to_run_id=child.id,
                operation_id=operation_id,
                kind="instruction",
                content=content,
            )
        )
        db.add(
            Message(
                id=message_id,
                thread_id=child.thread_id,
                run_id=child.id,
                role="user",
                content=content,
                provider_message={
                    "role": "user",
                    "content": content,
                    "_input_message_id": message_id,
                    "source_kind": "agent_message",
                },
            )
        )
        db.add(Job(run_id=child.id, available_at=now()))
        slot.run_id = child.id
        db.flush()
        emit(db, actor.id, "agent.followup", {"agent_id": slot.id, "run_id": child.id})
        return slot_view(db, slot)


def prepare_message_wait(run_id, seconds):
    from workbench.modules.collaboration.inbox import has_pending_inputs

    with Session(engine()) as db, db.begin():
        _, actor = lock_root(db, run_id)
        if has_pending_inputs(db, actor.id):
            return False
        if actor.wait_expires_at is None:
            actor.wait_expires_at = now() + timedelta(seconds=seconds)
        if actor.wait_expires_at <= now():
            return False
        set_status(db, actor, "waiting_messages")
        return True


def finish_message_wait(run_id):
    from workbench.modules.collaboration.inbox import has_pending_inputs

    with Session(engine()) as db, db.begin():
        _, actor = lock_root(db, run_id)
        available = has_pending_inputs(db, actor.id)
        timed_out = bool(not available and actor.wait_expires_at and actor.wait_expires_at <= now())
        actor.wait_expires_at = None
        return {
            "messages_available": available,
            "timed_out": timed_out,
            "next_step": "新协作消息会进入下一次模型上下文；超时不代表收到回复。",
        }


def communication_page(db, user_id, run_id, agent_id=None, before=None, limit=30):
    actor = get(db, user_id, run_id)
    root = get(db, user_id, actor.root_run_id or actor.id)
    slots = list(db.scalars(select(AgentSlot).where(AgentSlot.root_run_id == root.id)))
    names = {root.id: "主任务"}
    selected = None
    for slot in slots:
        ids = list(db.scalars(select(AgentTurn.run_id).where(AgentTurn.agent_id == slot.id)))
        names.update({identifier: slot.name for identifier in ids})
        if slot.id == agent_id:
            selected = ids
    if agent_id and selected is None:
        raise AppError("agent_not_found", "子Agent不属于当前任务树。", 404)
    query = select(AgentMessage).where(
        AgentMessage.root_run_id == root.id, AgentMessage.source != "user"
    )
    if selected is not None:
        query = query.where(
            or_(AgentMessage.from_run_id.in_(selected), AgentMessage.to_run_id.in_(selected))
        )
    if before:
        cursor = db.get(AgentMessage, before)
        if cursor is None or cursor.root_run_id != root.id:
            raise AppError("agent_message_cursor", "消息分页位置无效。", 422)
        query = query.where(
            or_(
                AgentMessage.created_at < cursor.created_at,
                (AgentMessage.created_at == cursor.created_at) & (AgentMessage.id < cursor.id),
            )
        )
    rows = list(
        db.scalars(
            query.order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc()).limit(limit + 1)
        )
    )
    processing = set()
    for attempt in db.scalars(
        select(Attempt).where(Attempt.run_id.in_(names), Attempt.status == "running")
    ):
        processing.update(attempt.input_message_ids)
    items = [
        {
            "id": row.id,
            "from_run_id": row.from_run_id,
            "to_run_id": row.to_run_id,
            "from_name": names.get(row.from_run_id, "任务"),
            "to_name": names.get(row.to_run_id, "任务"),
            "kind": row.kind,
            "content": row.content,
            "reply_to_id": row.reply_to_id,
            "status": "delivered"
            if row.delivered_attempt_id
            else "applying"
            if row.id in processing
            else "queued",
            "created_at": row.created_at,
        }
        for row in rows[:limit]
    ]
    return {"items": items, "next_cursor": rows[limit - 1].id if len(rows) > limit else None}


def notify_finished(db, run):
    """Commit the completion notice with the terminal fact, under root -> child locks."""
    if run.kind != "agent" or not run.parent_run_id:
        return
    turn = db.scalar(select(AgentTurn).where(AgentTurn.run_id == run.id))
    if turn is None:
        return
    identifier = str(uuid5(UUID(run.id), "agent-completion"))
    if db.get(AgentMessage, identifier):
        return
    summary = db.scalar(
        select(Message.content)
        .where(
            Message.run_id == run.id,
            Message.role == "assistant",
            Message.status == "completed",
            Message.content != "",
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    slot = db.get(AgentSlot, turn.agent_id)
    db.add(
        AgentMessage(
            id=identifier,
            root_run_id=run.root_run_id,
            from_run_id=run.id,
            to_run_id=run.parent_run_id,
            operation_id=identifier,
            kind="result",
            content=f"子Agent {slot.name} 的本轮已结束，实际状态：{run.status}。\n"
            + (
                (
                    summary[:8000]
                    + ("\n（完整结果请读取子任务历史。）" if len(summary) > 8000 else "")
                )
                if summary
                else "本轮没有完整正文，已有部分输出和工具记录保留。"
            ),
        )
    )


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


def wake_for_directive(db, run):
    """Supersede only unstarted questions/approvals; committed tools keep their lifecycle."""
    inflight = db.scalar(
        select(Operation.id)
        .where(
            Operation.run_id == run.id,
            Operation.status.in_(["running", "waiting_input", "outcome_unknown"]),
        )
        .limit(1)
    )
    if inflight:
        return
    from workbench.modules.interactions import Approval, Question

    for question in db.scalars(
        select(Question).where(Question.run_id == run.id, Question.status == "pending")
    ):
        question.status = "superseded"
    for approval in db.scalars(
        select(Approval).where(Approval.run_id == run.id, Approval.status == "pending")
    ):
        approval.status = "superseded"
    job = db.scalar(select(Job).where(Job.run_id == run.id).with_for_update())
    if run.status in {"waiting_user", "waiting_approval"} and job and job.status == "waiting":
        job.status, job.available_at = "queued", now()
        set_status(db, run, "queued")


def steer(db, user_id, run_id, values):
    def execute():
        run = get(db, user_id, run_id, lock=True)
        if run.thread_id:
            from workbench.modules.conversations import Thread

            thread = db.get(Thread, run.thread_id)
            if thread and (thread.deleting or thread.archived):
                raise AppError("thread_unavailable", "会话正在删除或已归档，不能补充任务。", 409)
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
            {"message_id": message.id, "effective": "steering_requested"},
        )
        wake_for_directive(db, run)
        return {"id": message.id, "status": "queued_for_next_model_call"}

    return idempotent(db, user_id, values["request_id"], f"run:{run_id}:input", values, execute)


def budget_check(db, run):
    root = db.get(Run, run.root_run_id or run.id)
    maximum = root.config_snapshot.get("runtime_options", {}).get("root_model_attempt_limit", 8192)
    attempts = db.scalar(
        select(func.count())
        .select_from(Attempt)
        .join(Run, Run.id == Attempt.run_id)
        .join(Step, Step.id == Attempt.step_id)
        .where(Run.root_run_id == root.id, Step.kind.in_(["model", "approval_review"]))
    )
    if attempts >= maximum:
        raise AppError(
            "root_model_budget",
            "主任务与子任务已达到共同模型调用预算，未发出更多请求。",
            409,
            "budget",
        )
