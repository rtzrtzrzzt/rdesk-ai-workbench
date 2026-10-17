import hashlib
import json
from collections.abc import Callable
from datetime import timedelta

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import new_id, now
from workbench.modules import conversation_service
from workbench.modules.conversations import Message
from workbench.modules.interactions import Approval, Question
from workbench.modules.models import service as model_service
from workbench.modules.runs.domain import TERMINAL_STATUSES, ClientRequest, Job, Run, RunEvent


def question_view(row: Question) -> dict:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "version": row.version,
        "prompt": row.prompt,
        "kind": row.kind,
        "options": row.options,
        "form_schema": row.form_schema,
        "status": row.status,
        "answer": row.answer.get("value") if row.answer else None,
    }


def approval_view(row: Approval) -> dict:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "version": row.version,
        "summary": row.summary,
        "tool_name": row.tool_name,
        "arguments": row.arguments,
        "status": row.status,
    }


def view(db: Session, run: Run) -> dict:
    questions = db.scalars(select(Question).where(Question.run_id == run.id))
    approvals = db.scalars(select(Approval).where(Approval.run_id == run.id))
    return {
        "id": run.id,
        "kind": run.kind,
        "thread_id": run.thread_id,
        "status": run.status,
        "model_connection_id": run.model_connection_id,
        "root_run_id": run.root_run_id,
        "parent_run_id": run.parent_run_id,
        "state_artifact_id": run.config_snapshot.get("context_compaction", {}).get(
            "state_artifact_id"
        ),
        "created_at": run.created_at,
        "last_event_seq": run.last_event_seq,
        "error": run.error,
        "questions": [question_view(q) for q in questions],
        "approvals": [approval_view(a) for a in approvals],
    }


def get(db: Session, user_id: str, run_id: str, *, lock: bool = False) -> Run:
    query = select(Run).where(Run.id == run_id, Run.user_id == user_id)
    if lock:
        query = query.with_for_update()
    run = db.scalar(query)
    if run is None:
        raise AppError("run_not_found", "任务不存在或不可访问。", 404)
    return run


def emit(db: Session, run_id: str, event_type: str, payload: dict) -> RunEvent:
    run = db.scalar(
        select(Run)
        .where(Run.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    from workbench.modules.runs.ownership import assert_owned

    assert_owned(db, run_id)
    run.last_event_seq += 1
    event = RunEvent(
        run_id=run_id, seq=run.last_event_seq, type=event_type, payload=jsonable_encoder(payload)
    )
    db.add(event)
    db.flush()
    return event


def event_view(row: RunEvent) -> dict:
    return {
        "schema_version": "1",
        "event_id": row.id,
        "run_id": row.run_id,
        "seq": row.seq,
        "type": row.type,
        "occurred_at": row.created_at,
        "payload": row.payload,
    }


def set_status(db: Session, run: Run, status: str) -> None:
    if run.status == status:
        return
    if run.status in TERMINAL_STATUSES:
        raise AppError("run_finished", "任务已结束，不能继续修改其执行状态。", 409)
    run.status = status
    db.flush()
    emit(db, run.id, "run.state_changed", {"status": status})


def request_lock_key(user_id: str, request_id: str) -> int:
    return int.from_bytes(
        hashlib.sha256(f"{user_id}:{request_id}".encode()).digest()[:8], signed=True
    )


def close_request(db: Session, user_id: str, request_id: str) -> dict:
    # A 404 alone cannot prove a delayed request will never arrive. Serialize
    # with admission and retain a tombstone that rejects any later original POST.
    if not db.scalar(
        text("SELECT pg_try_advisory_xact_lock(:key)"),
        {"key": request_lock_key(user_id, request_id)},
    ):
        raise AppError("request_in_progress", "请求仍在处理，请稍后读取结果。", 409)
    record = db.scalar(
        select(ClientRequest).where(
            ClientRequest.user_id == user_id,
            ClientRequest.request_id == request_id,
        )
    )
    from workbench.modules.automation.domain import AutomationSession

    native_session = db.get(AutomationSession, request_id)
    if record is not None:
        return {"status": "sealed" if record.expired else "recorded"}
    if native_session is not None and native_session.user_id == user_id:
        return {"status": "recorded"}
    db.add(
        ClientRequest(
            user_id=user_id,
            request_id=request_id,
            request_digest=hashlib.sha256(b"sealed").hexdigest(),
            resource_id=None,
            response=None,
            expired=True,
        )
    )
    return {"status": "sealed"}


def idempotent(
    db: Session, user_id: str, request_id: str, action: str, body: dict, execute: Callable[[], dict]
) -> dict:
    serialized = json.dumps({"action": action, "body": body}, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    lock_key = request_lock_key(user_id, request_id)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    record = db.scalar(
        select(ClientRequest).where(
            ClientRequest.user_id == user_id,
            ClientRequest.request_id == request_id,
        )
    )
    if record:
        if record.expired:
            raise AppError("request_expired", "请求记录已过期，请明确创建新的请求。", 410)
        if record.request_digest != digest:
            raise AppError("idempotency_conflict", "相同请求标识不能提交不同内容。", 409)
        return record.response
    response = jsonable_encoder(execute())
    db.add(
        ClientRequest(
            user_id=user_id,
            request_id=request_id,
            request_digest=digest,
            resource_id=response.get("id"),
            response=response,
        )
    )
    db.flush()
    return response


def create_run(db: Session, user_id: str, thread_id: str, values: dict) -> dict:
    def execute() -> dict:
        thread = conversation_service.get_thread(db, user_id, thread_id, lock=True)
        active = db.scalar(
            select(Run.id)
            .where(
                Run.thread_id == thread_id,
                Run.status.not_in(TERMINAL_STATUSES),
            )
            .limit(1)
        )
        if active:
            raise AppError("thread_busy", "这个会话还有任务在进行，请补充输入或先停止任务。", 409)
        from workbench.modules.preferences.service import resolve as runtime_defaults

        defaults = runtime_defaults(
            db,
            user_id,
            thread.workspace_id,
            task_options=values.get("options"),
            task_model=values.get("model_connection_id"),
        )
        if not defaults.model_connection_id:
            raise AppError("model_not_selected", "请为本次任务或默认设置选择模型连接。", 422)
        model = model_service.get(db, user_id, defaults.model_connection_id, lock=True)
        expected = values.get("expected_model")
        if expected and (
            str(expected["connection_id"]) != model.id or expected["version"] != model.version
        ):
            raise AppError(
                "model_selection_changed",
                "默认模型或连接版本已变化，请核对当前模型及附件许可后重新发送。",
                409,
            )
        if not expected and any(
            item.get("consent_remote") for item in values.get("attachments", [])
        ):
            raise AppError(
                "attachment_consent_target_required",
                "附件的外部传输许可必须绑定所确认的模型连接和版本，请重新确认。",
                422,
            )
        model_service.validate_endpoint(model.endpoint, model.allow_remote)
        from workbench.modules.files.inputs import validate_attachments

        attachments = validate_attachments(
            db, user_id, thread.workspace_id, model, values.get("attachments", [])
        )
        revision = model_service.freeze(db, user_id, model.id)
        options = defaults.options.model_dump(mode="json")
        run = Run(
            id=new_id(),
            user_id=user_id,
            workspace_id=thread.workspace_id,
            thread_id=thread_id,
            model_connection_id=model.id,
            input=values["input"],
            deadline_at=(
                now() + timedelta(seconds=values["deadline_seconds"])
                if values.get("deadline_seconds")
                else None
            ),
            config_snapshot={
                "model_version": model.version,
                "model_revision_id": revision.id,
                "max_output_tokens": revision.configuration["generation"]["max_output_tokens"],
                "runtime_options": options,
                "runtime_options_sources": defaults.sources,
                "runtime_options_source": "task" if values.get("options") else "inherited",
                "defaults_versions": {
                    "user": defaults.user_version,
                    "workspace": defaults.workspace_version,
                },
                "model_selection_source": defaults.model_source,
            },
        )
        run.root_run_id = run.id
        db.add(run)
        db.flush()
        db.add(
            Message(
                thread_id=thread_id,
                run_id=run.id,
                role="user",
                content=values["input"],
                status="completed",
                attachments=attachments,
            )
        )
        db.add(Job(run_id=run.id, available_at=now()))
        if thread.title == "新对话":
            thread.title = values["input"].strip()[:40]
            thread.version += 1
        emit(db, run.id, "run.accepted", {"status": "queued"})
        return view(db, run)

    return idempotent(db, user_id, values["request_id"], f"thread:{thread_id}:run", values, execute)


def invalidate_waits(db: Session, run_id: str) -> None:
    for row in db.scalars(
        select(Question).where(Question.run_id == run_id, Question.status == "pending")
    ):
        row.status = "invalidated"
        emit(db, run_id, "question.invalidated", {"question_id": row.id})
    for row in db.scalars(
        select(Approval).where(Approval.run_id == run_id, Approval.status == "pending")
    ):
        row.status = "expired"
        emit(db, run_id, "approval.expired", {"approval_id": row.id})


def cancel(db: Session, user_id: str, run_id: str, request_id: str) -> dict:
    def execute() -> dict:
        target = get(db, user_id, run_id)
        get(db, user_id, target.root_run_id or target.id, lock=True)
        run = get(db, user_id, run_id, lock=True)
        if run.status not in TERMINAL_STATUSES:
            cancel_descendants(db, run)
            request_stop(db, run)
        return view(db, run)

    return idempotent(db, user_id, request_id, f"run:{run_id}:cancel", {}, execute)


def cancel_descendants(db, run):
    from workbench.modules.runs.ownership import current_owner

    condition = Run.root_run_id == run.id if not run.parent_run_id else Run.parent_run_id == run.id
    descendants = list(
        db.scalars(
            select(Run)
            .where(
                condition,
                Run.id != run.id,
                Run.user_id == run.user_id,
                Run.status.not_in(TERMINAL_STATUSES),
            )
            # Agent children precede their file children, matching child finish
            # (agent -> file). Reversing this order deadlocks root cancellation.
            .order_by(Run.kind.asc(), Run.id)
            .with_for_update()
        )
    )
    token = current_owner.set(None)
    try:
        for child in descendants:
            request_stop(db, child)
    finally:
        current_owner.reset(token)


def has_children_running(db, run_id):
    return (
        db.scalar(
            select(Run.id)
            .where(Run.parent_run_id == run_id, Run.status.not_in(TERMINAL_STATUSES))
            .limit(1)
        )
        is not None
    )


def request_stop(db, run):
    from workbench.modules.mcp.store import request_transport_cleanup
    from workbench.modules.runs.resources import request_native_cleanup as request_cleanup

    retained = request_transport_cleanup(db, run.id, "user_cancelled")
    retained += request_cleanup(db, run.id)
    if run.cancel_requested_at is None:
        run.cancel_requested_at = now()
        emit(db, run.id, "run.cancellation_requested", {})
    job = db.scalar(select(Job).where(Job.run_id == run.id).with_for_update())
    if job:
        job.completion_status = "cancelled"
    if job is not None and job.status != "running":
        if retained or has_children_running(db, run.id):
            set_status(db, run, "cancelling")
            job.status = "cleanup"
        else:
            set_status(db, run, "cancelled")
            job.status = "cancelled"
        invalidate_waits(db, run.id)
    else:
        set_status(db, run, "cancelling")


def answer_question(db: Session, user_id: str, question_id: str, values: dict) -> dict:
    def execute() -> dict:
        question = db.get(Question, question_id)
        if question is None:
            raise AppError("question_not_found", "问题不存在或不可访问。", 404)
        run = get(db, user_id, question.run_id, lock=True)
        question = db.scalar(
            select(Question)
            .where(Question.id == question_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if run.status in TERMINAL_STATUSES or question.status != "pending":
            raise AppError("question_resolved", "问题已回答或失效，请刷新查看当前状态。", 409)
        if question.version != values["version"]:
            raise AppError("question_stale", "问题已经更新，请重新查看。", 409)
        answer = values["answer"]
        allowed = {option["id"] for option in question.options}
        if question.kind == "single_choice" and (
            not isinstance(answer, str) or answer not in allowed
        ):
            raise AppError("invalid_answer", "请选择当前问题中的一个选项。")
        if question.kind == "multiple_choice" and (
            not isinstance(answer, list) or not answer or not set(answer).issubset(allowed)
        ):
            raise AppError("invalid_answer", "请选择当前问题中的有效选项。")
        if question.kind == "text" and (not isinstance(answer, str) or not answer.strip()):
            raise AppError("invalid_answer", "请填写回答。")
        if question.kind == "form":
            from workbench.modules.generated.forms import validate_answer

            validate_answer(question.form_schema or {}, answer)
        question.answer = {"value": answer}
        question.status = "answered"
        emit(db, run.id, "question.answered", {"question_id": question.id})
        from workbench.modules.mcp.store import mcp_inputs_ready

        # One public interrupt represents the whole MCP round. A partially
        # answered form group must not advance the persisted graph.
        if mcp_inputs_ready(db, run.id):
            enqueue_resume(db, run)
        return question_view(question)

    return idempotent(db, user_id, values["request_id"], f"question:{question_id}", values, execute)


def decide_approval(db: Session, user_id: str, approval_id: str, values: dict) -> dict:
    def execute() -> dict:
        approval = db.get(Approval, approval_id)
        if approval is None:
            raise AppError("approval_not_found", "授权请求不存在或不可访问。", 404)
        run = get(db, user_id, approval.run_id, lock=True)
        approval = db.scalar(
            select(Approval)
            .where(Approval.id == approval_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if run.status in TERMINAL_STATUSES or approval.status != "pending":
            raise AppError("approval_resolved", "授权请求已处理或失效。", 409)
        if approval.version != values["version"]:
            raise AppError("approval_stale", "操作已经更新，请重新核对。", 409)
        approval.approved = values["approved"]
        approval.status = "approved" if approval.approved else "rejected"
        emit(
            db,
            run.id,
            "approval.resolved",
            {"approval_id": approval.id, "approved": approval.approved},
        )
        enqueue_resume(db, run)
        return approval_view(approval)

    return idempotent(db, user_id, values["request_id"], f"approval:{approval_id}", values, execute)


def enqueue_resume(db: Session, run: Run) -> None:
    job = db.scalar(select(Job).where(Job.run_id == run.id).with_for_update())
    if job and job.status != "running":
        job.status = "queued"
        job.available_at = now()
        set_status(db, run, "queued")
