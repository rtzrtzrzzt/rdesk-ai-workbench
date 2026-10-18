from uuid import UUID, uuid5

from sqlalchemy import select

from workbench.contracts.errors import AppError
from workbench.modules import conversation_service as conversations
from workbench.modules.conversations import Thread
from workbench.modules.runs import service as runs
from workbench.modules.runs.domain import TERMINAL_STATUSES, Run


def rename(db, user_id, thread_id, values):
    def execute():
        thread = conversations.get_thread(db, user_id, thread_id, lock=True)
        if thread.version != values["version"] or thread.deleting:
            raise AppError("thread_stale", "会话已经改变，请刷新后操作。", 409)
        title = values["title"].strip()
        if not title:
            raise AppError("invalid_title", "请填写会话名称。", 422)
        thread.title, thread.version = title, thread.version + 1
        return conversations.thread_view(thread, db)

    return runs.idempotent(
        db, user_id, values["request_id"], f"thread:{thread_id}:rename", values, execute
    )


def lifecycle(db, user_id, thread_id, values):
    def execute():
        thread = conversations.get_thread(db, user_id, thread_id, lock=True)
        if thread.version != values["version"] or thread.deleting:
            raise AppError("thread_stale", "会话已经改变，请刷新后操作。", 409)
        archive = values["action"] == "archive"
        active = list(
            db.scalars(
                select(Run)
                .where(Run.thread_id == thread_id, Run.status.not_in(TERMINAL_STATUSES))
                .order_by(Run.id)
                .with_for_update()
            )
        )
        if archive and active and not values.get("stop_active"):
            raise AppError("thread_active", "归档将停止本会话任务及子任务，请确认后操作。", 409)
        if archive:
            thread.queue_paused = True
            for run in active:
                runs.cancel_descendants(db, run)
                runs.request_stop(db, run)
        thread.archived, thread.version = archive, thread.version + 1
        return conversations.thread_view(thread, db)

    return runs.idempotent(
        db, user_id, values["request_id"], f"thread:{thread_id}:lifecycle", values, execute
    )


def edit(db, user_id, thread_id, values, *, inherit_attachments):
    def execute():
        source = conversations.get_thread(db, user_id, thread_id, lock=True)
        if source.deleting:
            raise AppError("thread_deleting", "会话正在删除，不能编辑消息。", 409)
        history = conversations.history_rows(db, source)
        anchor = next((row for row in history if row.id == values["message_id"]), None)
        if anchor is None or anchor.role != "user" or anchor.status != "completed":
            raise AppError("edit_anchor_invalid", "只能编辑已经发送的用户消息。", 409)
        row = Thread(
            id=str(uuid5(UUID(values["request_id"]), "edited-thread")),
            user_id=user_id,
            workspace_id=source.workspace_id,
            title=source.title[:190] + " · 编辑分支",
            parent_thread_id=source.id,
            fork_message_id=anchor.id,
            fork_include_anchor=False,
        )
        db.add(row)
        db.flush()
        payload = {key: val for key, val in values.items() if key != "message_id"}
        payload["request_id"] = str(uuid5(UUID(values["request_id"]), "edited-run"))
        if inherit_attachments:
            selected = values.get("expected_model")
            consent_target = (
                f"{selected['connection_id']}:{selected['version']}" if selected else None
            )
            payload["attachments"] = [
                {
                    key: item[key]
                    for key in (
                        "artifact_id",
                        "version",
                        "mode",
                        "source_artifact_id",
                        "source_version",
                    )
                    if item.get(key) is not None
                }
                | {
                    "consent_remote": bool(
                        item.get("consent_remote")
                        and consent_target
                        and item.get("consent_target") == consent_target
                    )
                }
                for item in anchor.attachments or []
            ]
        result = runs.create_run(db, user_id, row.id, payload)
        return {"thread": conversations.thread_view(row, db), "run": result}

    return runs.idempotent(
        db, user_id, values["request_id"], f"thread:{thread_id}:edit", values, execute
    )
