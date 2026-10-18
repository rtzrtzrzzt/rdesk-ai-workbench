from sqlalchemy import func, select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.modules import workspace_service
from workbench.modules.conversations import Message, Thread


def thread_view(row: Thread, db=None) -> dict:
    from workbench.modules.conversations import QueuedInput
    from workbench.modules.runs.domain import Run
    from workbench.modules.runs.service import activity

    latest = (
        db.scalar(
            select(Run)
            .where(Run.thread_id == row.id, Run.kind != "context_compaction")
            .order_by(Run.created_at.desc(), Run.id.desc())
            .limit(1)
        )
        if db
        else None
    )
    child = (
        db.scalar(select(Run).where(Run.thread_id == row.id, Run.parent_run_id.is_not(None)))
        if db
        else None
    )
    parent = db.get(Run, child.parent_run_id) if child else None
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "title": row.title,
        "parent_thread_id": row.parent_thread_id,
        "fork_message_id": row.fork_message_id,
        "created_at": row.created_at,
        "version": row.version,
        "archived": row.archived,
        "deleting": row.deleting,
        "is_subagent": child is not None,
        "agent_parent_thread_id": parent.thread_id if parent else None,
        "latest_run_id": latest.id if latest else None,
        "latest_run_status": latest.status if latest else None,
        "latest_run_phase": activity(db, latest) if latest else None,
        "queue_count": db.scalar(
            select(func.count())
            .select_from(QueuedInput)
            .where(QueuedInput.thread_id == row.id, QueuedInput.status != "dispatched")
        )
        if db
        else 0,
    }


def message_view(row: Message) -> dict:
    from workbench.modules.models.providers import visible_reasoning

    provider = row.provider_message or {}
    phase = provider.get("phase")
    if provider.get("tool_calls"):
        phase = "commentary"
    return {
        "id": row.id,
        "thread_id": row.thread_id,
        "run_id": row.run_id,
        "role": row.role,
        "content": row.content,
        "reasoning": row.reasoning or visible_reasoning(provider.get("provider_output")),
        "phase": phase if phase in {"commentary", "final_answer"} else None,
        "status": row.status,
        "attachments": row.attachments,
        "forkable": (
            row.status == "completed"
            and row.role in {"user", "assistant"}
            and not (row.provider_message or {}).get("tool_calls")
        ),
        "created_at": row.created_at,
    }


def get_thread(db: Session, user_id: str, thread_id: str, *, lock: bool = False) -> Thread:
    query = select(Thread).where(Thread.id == thread_id, Thread.user_id == user_id)
    if lock:
        query = query.with_for_update()
    row = db.scalar(query)
    if row is None:
        raise AppError("thread_not_found", "会话不存在或不可访问。", 404)
    workspace_service.get(db, user_id, row.workspace_id)
    return row


def list_threads(db: Session, user_id: str, workspace_id: str) -> list[dict]:
    workspace_service.get(db, user_id, workspace_id)
    return [
        thread_view(row, db)
        for row in db.scalars(
            select(Thread)
            .where(
                Thread.workspace_id == workspace_id,
                Thread.user_id == user_id,
            )
            .order_by(Thread.created_at.desc())
        )
    ]


def create_thread(db: Session, user_id: str, workspace_id: str, title: str) -> dict:
    workspace_service.get(db, user_id, workspace_id)
    row = Thread(user_id=user_id, workspace_id=workspace_id, title=title)
    db.add(row)
    db.flush()
    return thread_view(row, db)


def list_messages(db: Session, user_id: str, thread_id: str) -> list[dict]:
    thread = get_thread(db, user_id, thread_id)
    return [
        message_view(row) for row in history_rows(db, thread) if row.role in {"user", "assistant"}
    ]


def history_rows(db: Session, thread: Thread, *, completed_only: bool = False) -> list[Message]:
    """Resolve only the immutable prefixes explicitly selected when a branch was made.

    Original message IDs and provider metadata are retained so file consent,
    forgotten-memory filtering and tool-group validation keep their provenance.
    A branch never follows messages added to its parent after its selected anchor.
    """
    seen: set[str] = set()

    def collect(current: Thread) -> list[Message]:
        if current.id in seen or len(seen) >= 16:
            raise AppError("thread_branch_limit", "对话分支层数超过可读取范围。", 409)
        seen.add(current.id)
        prefix = []
        if current.parent_thread_id:
            parent = db.get(Thread, current.parent_thread_id)
            if (
                parent is None
                or parent.user_id != thread.user_id
                or parent.workspace_id != thread.workspace_id
                or not current.fork_message_id
            ):
                raise AppError("thread_branch_invalid", "分支来源不完整，请核对原对话。", 409)
            rows = collect(parent)
            anchor = next(
                (index for index, row in enumerate(rows) if row.id == current.fork_message_id),
                None,
            )
            if anchor is None:
                raise AppError("thread_branch_invalid", "分支来源消息已不可读取。", 409)
            prefix = rows[: anchor + int(current.fork_include_anchor)]
        own = list(
            db.scalars(
                select(Message)
                .where(Message.thread_id == current.id)
                .order_by(Message.created_at, Message.id)
            )
        )
        result = prefix + own
        if len(result) > 8192 or sum(len(row.content.encode()) for row in result) > 16 * 1024**2:
            raise AppError("thread_history_limit", "对话历史过大，请选择较短对话建立分支。", 409)
        return result

    rows = collect(thread)
    return [row for row in rows if row.status == "completed"] if completed_only else rows


def fork_thread(db: Session, user_id: str, thread_id: str, command) -> dict:
    from workbench.modules.runs.service import idempotent

    def create():
        source = get_thread(db, user_id, thread_id, lock=True)
        if source.deleting:
            raise AppError("thread_deleting", "会话正在删除，不能建立新分支。", 409)
        history = history_rows(db, source)
        anchor = next((row for row in history if row.id == str(command.source_message_id)), None)
        if (
            anchor is None
            or anchor.status != "completed"
            or anchor.role not in {"user", "assistant"}
            or (anchor.provider_message or {}).get("tool_calls")
        ):
            raise AppError("fork_anchor_invalid", "请选择已完成的用户消息或最终回复建立分支。", 409)
        row = Thread(
            user_id=user_id,
            workspace_id=source.workspace_id,
            title=command.title or (source.title[:194] + " · 分支"),
            parent_thread_id=source.id,
            fork_message_id=anchor.id,
        )
        db.add(row)
        db.flush()
        return thread_view(row)

    return idempotent(
        db,
        user_id,
        str(command.request_id),
        f"thread:{thread_id}:fork",
        command.model_dump(mode="json"),
        create,
    )
