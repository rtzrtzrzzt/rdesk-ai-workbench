"""Thread-locked admission for persistent follow-up messages."""

from uuid import UUID, uuid5

from sqlalchemy import func, select

from workbench.contracts.errors import AppError
from workbench.modules.conversation_service import get_thread
from workbench.modules.conversations import QueuedInput, Thread
from workbench.modules.preferences.service import resolve
from workbench.modules.runs.domain import TERMINAL_STATUSES, ClientRequest, Run
from workbench.modules.runs.service import create_run, idempotent


def item_view(row):
    return {
        "id": row.id,
        "thread_id": row.thread_id,
        "input": row.payload["input"],
        "position": row.position,
        "version": row.version,
        "status": row.status,
        "run_id": row.run_id,
        "error": row.error,
        "created_at": row.created_at,
        "has_attachments": bool(row.payload.get("attachments")),
    }


def view(db, user_id, thread_id):
    thread = get_thread(db, user_id, thread_id)
    rows = db.scalars(
        select(QueuedInput)
        .where(QueuedInput.thread_id == thread_id)
        .order_by(QueuedInput.position, QueuedInput.created_at)
    )
    return {
        "thread_id": thread.id,
        "paused": thread.queue_paused,
        "items": [item_view(row) for row in rows if row.status != "dispatched"],
    }


def ensure_writable(thread):
    if thread.archived or thread.deleting:
        raise AppError("thread_unavailable", "会话已归档或正在删除，请先恢复或选择其他会话。", 409)


def add(db, user_id, thread_id, values):
    def execute():
        thread = get_thread(db, user_id, thread_id, lock=True)
        ensure_writable(thread)
        if (
            db.scalar(
                select(func.count())
                .select_from(QueuedInput)
                .where(QueuedInput.thread_id == thread.id, QueuedInput.status != "dispatched")
            )
            >= 100
        ):
            raise AppError("queue_full", "待发送消息已达100条，请先处理队列。", 409)
        from workbench.modules.access.service import binding_for_thread, defaults_for_binding

        binding = binding_for_thread(db, thread)
        options = defaults_for_binding(binding) if binding else resolve(
            db, user_id, thread.workspace_id, task_options=values.get("options"),
            task_model=values.get("model_connection_id"),
        )
        from workbench.modules.files.inputs import lock_attachments, validate_attachments
        from workbench.modules.models.service import get as get_model

        if not options.model_connection_id:
            raise AppError("model_not_selected", "请先选择模型连接。", 422)
        model = get_model(db, user_id, options.model_connection_id)
        expected = values.get("expected_model")
        if expected and (
            str(expected["connection_id"]) != model.id or expected["version"] != model.version
        ):
            raise AppError("model_selection_changed", "模型版本已变化，请重新确认后加入队列。", 409)
        if not expected and any(a.get("consent_remote") for a in values.get("attachments", [])):
            raise AppError(
                "attachment_consent_target_required", "外部附件许可必须绑定模型版本。", 422
            )
        from workbench.modules.models.engine_adapters import reasoning_overrides

        reasoning_overrides(
            {
                "provider_type": model.provider_type,
                "endpoint": model.endpoint,
                "model_identifier": model.model_identifier,
                "generation": model.generation or {},
            },
            values.get("reasoning_effort"),
        )
        lock_attachments(db, user_id, thread.workspace_id, values.get("attachments", []))
        validate_attachments(db, user_id, thread.workspace_id, model, values.get("attachments", []))
        payload = {
            **values,
            "model_connection_id": model.id,
            "options": options.options.model_dump(mode="json"),
            "expected_model": {"connection_id": model.id, "version": model.version},
        }
        if binding:
            payload["_execution_binding_id"] = binding.id
            payload["model_connection_id"] = binding.snapshot["model_connection_id"]
            payload["options"] = binding.snapshot["runtime_options"]
        payload["wait_for_run_id"] = db.scalar(
            select(Run.id)
            .where(Run.thread_id == thread_id, Run.status.not_in(TERMINAL_STATUSES))
            .limit(1)
        )
        position = db.scalar(
            select(func.max(QueuedInput.position)).where(QueuedInput.thread_id == thread_id)
        )
        row = QueuedInput(
            user_id=user_id,
            thread_id=thread_id,
            payload=payload,
            status="paused" if thread.queue_paused else "queued",
            position=(position or 0) + 1,
        )
        db.add(row)
        db.flush()
        dispatch(db, thread)
        return item_view(row)

    return idempotent(
        db, user_id, values["request_id"], f"thread:{thread_id}:queue", values, execute
    )


def dispatch(db, thread):
    # Callers always hold this Thread lock. create_run takes it again within the
    # same transaction, preserving the single active Run invariant.
    if thread.queue_paused or thread.archived or thread.deleting:
        return
    if db.scalar(
        select(Run.id)
        .where(Run.thread_id == thread.id, Run.status.not_in(TERMINAL_STATUSES))
        .limit(1)
    ):
        return
    row = db.scalar(
        select(QueuedInput)
        .where(QueuedInput.thread_id == thread.id, QueuedInput.status.in_(["queued", "paused"]))
        .order_by(QueuedInput.position, QueuedInput.created_at)
        .limit(1)
        .with_for_update()
    )
    if row is None:
        return
    predecessor_id = row.payload.get("wait_for_run_id")
    predecessor = db.get(Run, predecessor_id) if predecessor_id else None
    if (
        predecessor
        and predecessor.status in {"failed", "cancelled"}
        and thread.queue_resume_run_id != predecessor.id
    ):
        thread.queue_paused, row.status = True, "paused"
        return
    payload = {key: value for key, value in row.payload.items() if key != "wait_for_run_id"}
    payload["request_id"] = str(uuid5(UUID(row.id), "dispatch"))
    binding_id = payload.pop("_execution_binding_id", None)
    try:
        with db.begin_nested():
            result = create_run(
                db, thread.user_id, thread.id, payload, execution_binding_id=binding_id
            )
    except AppError as error:
        row.error = {"code": error.code, "message": error.message}
        row.status = "paused"
        thread.queue_paused = True
        row.version += 1
        return
    row.run_id, row.status, row.error = result["id"], "dispatched", None
    row.version += 1
    for waiting in db.scalars(
        select(QueuedInput).where(
            QueuedInput.thread_id == thread.id, QueuedInput.status.in_(["queued", "paused"])
        )
    ):
        waiting.payload = {**waiting.payload, "wait_for_run_id": row.run_id}


def update(db, user_id, thread_id, item_id, values):
    def execute():
        thread = get_thread(db, user_id, thread_id, lock=True)
        ensure_writable(thread)
        row = db.scalar(
            select(QueuedInput)
            .where(QueuedInput.id == item_id, QueuedInput.thread_id == thread_id)
            .with_for_update()
        )
        if row is None or row.status == "dispatched":
            raise AppError("queue_item_dispatched", "消息已发送或已移除，请刷新队列。", 409)
        if row.version != values["version"]:
            raise AppError("queue_item_stale", "消息已改变，请重新查看后操作。", 409)
        if values.get("remove"):
            import json

            for request in db.scalars(
                select(ClientRequest).where(ClientRequest.user_id == user_id)
            ):
                if request.resource_id == row.id or (
                    request.response and row.id in json.dumps(request.response)
                ):
                    request.response, request.expired = None, True
            db.delete(row)
        else:
            if values.get("input") is not None:
                row.payload = {**row.payload, "input": values["input"]}
            if values.get("position") is not None:
                items = list(
                    db.scalars(
                        select(QueuedInput)
                        .where(
                            QueuedInput.thread_id == thread_id, QueuedInput.status != "dispatched"
                        )
                        .order_by(QueuedInput.position, QueuedInput.id)
                    )
                )
                items.remove(row)
                items.insert(min(values["position"], len(items)), row)
                for index, item in enumerate(items):
                    item.position = index
                    if item.id != row.id:
                        item.version += 1
            row.version += 1
        db.flush()
        return view(db, user_id, thread_id)

    return idempotent(db, user_id, values["request_id"], f"queue:{item_id}:update", values, execute)


def resume(db, user_id, thread_id, request_id):
    def execute():
        thread = get_thread(db, user_id, thread_id, lock=True)
        ensure_writable(thread)
        thread.queue_paused = False
        latest = db.scalar(
            select(Run.id)
            .where(Run.thread_id == thread_id)
            .order_by(Run.created_at.desc(), Run.id.desc())
            .limit(1)
        )
        thread.queue_resume_run_id = latest
        for row in db.scalars(
            select(QueuedInput).where(
                QueuedInput.thread_id == thread_id, QueuedInput.status == "paused"
            )
        ):
            row.status = "queued"
            row.error = None
            row.version += 1
        dispatch(db, thread)
        return view(db, user_id, thread_id)

    return idempotent(db, user_id, request_id, f"thread:{thread_id}:queue:resume", {}, execute)


def steer_queued(db, user_id, thread_id, item_id, values):
    def execute():
        from workbench.modules.collaboration.service import steer

        # Use the same Thread -> item -> Run order as queue dispatch. Receiving
        # the input and consuming the queue item must commit together.
        thread = get_thread(db, user_id, thread_id, lock=True)
        ensure_writable(thread)
        row = db.scalar(
            select(QueuedInput)
            .where(QueuedInput.id == item_id, QueuedInput.thread_id == thread.id)
            .with_for_update()
        )
        if row is None or row.status == "dispatched":
            raise AppError("queue_item_dispatched", "消息已发送或已移除，请刷新队列。", 409)
        if row.version != values["version"]:
            raise AppError("queue_item_stale", "消息已改变，请重新查看后操作。", 409)
        if row.payload.get("attachments"):
            raise AppError(
                "steering_attachments_unsupported",
                "带附件的消息请保留在队列中；当前引导仅支持文字，附件未被移除。",
                422,
            )
        run_id = db.scalar(
            select(Run.id).where(
                Run.id == values["run_id"],
                Run.thread_id == thread.id,
                Run.user_id == user_id,
                Run.parent_run_id.is_(None),
                Run.kind == "agent",
            )
        )
        if run_id is None:
            raise AppError("run_not_found", "当前任务已变化，请刷新后重试；消息仍在队列中。", 409)
        received = steer(
            db,
            user_id,
            run_id,
            {
                "input": row.payload["input"],
                "request_id": str(uuid5(UUID(values["request_id"]), "queue-steering")),
            },
        )
        row.status, row.run_id, row.error = "dispatched", run_id, None
        row.version += 1
        db.flush()
        return {"queue": view(db, user_id, thread_id), "run_id": run_id, "input_id": received["id"]}

    return idempotent(
        db, user_id, values["request_id"], f"queue:{item_id}:steer", values, execute
    )


def maintain(db):
    # Runs finish while holding their own locks; dispatch in a separate short
    # transaction avoids reversing Thread -> Run admission lock order.
    threads = list(
        db.scalars(
            select(Thread)
            .where(Thread.archived.is_(False), Thread.deleting.is_(False))
            .with_for_update(skip_locked=True)
        )
    )
    for thread in threads:
        if db.scalar(
            select(Run.id)
            .where(Run.thread_id == thread.id, Run.status.not_in(TERMINAL_STATUSES))
            .limit(1)
        ):
            continue
        latest = db.scalar(
            select(Run)
            .where(Run.thread_id == thread.id)
            .order_by(Run.created_at.desc(), Run.id.desc())
            .limit(1)
        )
        pending = db.scalar(
            select(QueuedInput.id)
            .where(QueuedInput.thread_id == thread.id, QueuedInput.status != "dispatched")
            .limit(1)
        )
        if (
            pending
            and latest
            and latest.status != "succeeded"
            and thread.queue_resume_run_id != latest.id
        ):
            # An explicit resume is needed after failure/Stop, including recovery.
            from sqlalchemy import update as sql_update

            thread.queue_paused = True
            db.execute(
                sql_update(QueuedInput)
                .where(QueuedInput.thread_id == thread.id, QueuedInput.status == "queued")
                .values(status="paused", version=QueuedInput.version + 1)
            )
        dispatch(db, thread)
