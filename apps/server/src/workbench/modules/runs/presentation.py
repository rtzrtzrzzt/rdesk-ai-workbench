"""Derive chat presentation from the same durable facts used by the public API."""

from sqlalchemy import select

from workbench.contracts.base import PublicResponse
from workbench.modules.conversations import Message
from workbench.modules.runs.domain import Attempt, Run, Step


class PresentedRun(PublicResponse):
    run_id: str
    final_message_id: str | None
    finalized_event_seq: int | None
    status: str
    tool_count: int
    elapsed_ms: int | None
    changes_revision: str
    changes_coverage: str
    changed_files: int | None
    added_lines: int | None
    removed_lines: int | None
    completion_summary: str | None = None


class ConversationPresentation(PublicResponse):
    groups: list[PresentedRun]


def reply_candidate(db, run_id):
    candidates = db.execute(
        select(Message, Attempt)
        .join(Attempt, Attempt.message_id == Message.id)
        .join(Step, Step.id == Attempt.step_id)
        .where(
            Message.run_id == run_id,
            Message.role == "assistant",
            Message.status == "completed",
            Attempt.status == "succeeded",
            Step.kind == "model",
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    ).first()
    if candidates and not (candidates[0].provider_message or {}).get("tool_calls"):
        return candidates
    return None


def seal_reply(db, run):
    """Called only inside finish's Run lock after the pending-input check."""
    from workbench.modules.runs.service import emit

    if run.kind != "agent" or run.final_message_id:
        return
    candidate = reply_candidate(db, run.id)
    if candidate:
        message, attempt = candidate
        run.final_message_id, run.final_attempt_id = message.id, attempt.id
        run.finalized_event_seq = run.last_event_seq + 1
        emit(
            db,
            run.id,
            "run.reply_finalized",
            {
                "message_id": message.id,
                "attempt_id": attempt.id,
                "finalized_event_seq": run.finalized_event_seq,
            },
        )


def view(db, user_id, thread_id):
    from workbench.modules.changes.service import view as changes
    from workbench.modules.conversation_service import get_thread, history_rows
    from workbench.modules.runs.activity import visible_operations
    from workbench.modules.runs.service import timing

    thread = get_thread(db, user_id, thread_id)
    messages = history_rows(db, thread)
    visible_messages = {message.id for message in messages}
    operations = list(db.scalars(visible_operations(db, user_id, thread_id)))
    identifiers = {message.run_id for message in messages if message.run_id}
    identifiers.update(operation.run_id for operation in operations)
    result = []
    if not identifiers:
        return {"groups": result}
    for run in db.scalars(
        select(Run)
        .where(Run.id.in_(identifiers), Run.user_id == user_id, Run.kind == "agent")
        .order_by(Run.created_at, Run.id)
    ):
        final_id = run.final_message_id
        if not final_id and run.status == "succeeded":
            # Read-only compatibility for successful historical tasks. No guessed
            # statistics or changes are written back into their original ledger.
            candidate = reply_candidate(db, run.id)
            final_id = candidate[0].id if candidate else None
        final_id = final_id if final_id in visible_messages else None
        delta = changes(db, user_id, thread_id, run.id)
        clock = timing(db, run)
        completion_summary = None
        if run.status in {"failed", "cancelled"} and not final_id:
            own_operations = [operation for operation in operations if operation.run_id == run.id]
            succeeded = sum(operation.status == "succeeded" for operation in own_operations)
            failed = sum(operation.status == "failed" for operation in own_operations)
            unknown = sum(
                operation.status == "outcome_unknown"
                or operation.effect_status == "needs_reconciliation"
                for operation in own_operations
            )
            reason = (run.error or {}).get("message") or "执行已停止。"
            completion_summary = (
                f"本次任务未完成最终交付。{reason}"
                f"已记录工具操作：{succeeded} 次成功、{failed} 次失败、{unknown} 次结果待核对。"
                "已保存的过程和文件变更可继续查看；工具操作成功不等于任务验证通过。"
            )
        result.append(
            {
                "run_id": run.id,
                "final_message_id": final_id,
                "finalized_event_seq": run.finalized_event_seq if final_id else None,
                "status": run.status,
                "tool_count": sum(operation.run_id == run.id for operation in operations),
                "elapsed_ms": clock["elapsed_ms"] if clock else None,
                "changes_revision": delta.revision,
                "changes_coverage": delta.coverage,
                "changed_files": delta.totals.files,
                "added_lines": delta.totals.added_lines,
                "removed_lines": delta.totals.removed_lines,
                "completion_summary": completion_summary,
            }
        )
    return {"groups": result}
