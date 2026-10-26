"""Remove visible history only after the real task and its resources have closed."""

from uuid import uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, now
from workbench.modules import workspace_service
from workbench.modules.media import resources
from workbench.modules.media.domain import MediaTask
from workbench.modules.media.locking import lock_run
from workbench.modules.runs.domain import TERMINAL_STATUSES, Run
from workbench.modules.runs.service import cancel, emit, idempotent


def remove_when_closed(db, run, row):
    if (
        run.status not in TERMINAL_STATUSES
        or not row.resources_closed
        or resources.cleanup_pending(db, run.id)
    ):
        return False
    if row.deleted_at is None:
        row.deleted_at = now()
        emit(db, run.id, "media.history_deleted", {})
    return True


def delete(db, user_id, workspace_id, command):
    workspace_service.get(db, user_id, workspace_id)
    identifiers = {str(identifier) for identifier in command.run_ids}

    def execute():
        targets = list(
            db.execute(
                select(MediaTask, Run)
                .join(Run, Run.id == MediaTask.run_id)
                .where(
                    Run.id.in_(identifiers),
                    Run.user_id == user_id,
                    Run.workspace_id == workspace_id,
                )
            )
        )
        if len(targets) != len(identifiers):
            raise AppError("media_task_missing", "部分生成记录不存在或不可访问。", 404)
        # Match ordinary cancellation, including media children of agent runs.
        # Lock every root in stable order before any descendant/job/module row.
        for root_id in sorted({run.root_run_id or run.id for _, run in targets}):
            db.scalar(select(Run).where(Run.id == root_id).with_for_update())
        result = {"deleted": [], "stopping": []}
        for row, target in sorted(targets, key=lambda pair: pair[1].id):
            run = db.scalar(select(Run).where(Run.id == target.id).with_for_update())
            db.refresh(row)
            if row.deleted_at is not None:
                result["deleted"].append(run.id)
                continue
            active = run.status not in TERMINAL_STATUSES or not row.resources_closed
            active = active or resources.cleanup_pending(db, run.id)
            if active and not command.stop_active:
                raise AppError(
                    "media_delete_requires_stop",
                    "所选记录包含尚未结束的任务，请选择先停止任务再删除记录。",
                    409,
                )
            if active:
                # Ordinary cancel also stops derived preview tasks. Its receipt
                # stays separate from the batch receipt and is safe to replay.
                cancel(
                    db,
                    user_id,
                    run.id,
                    str(uuid5(command.request_id, "media-history:" + run.id)),
                )
            # Cancellation must acquire child Run locks before the parent Job
            # and media row, as ordinary cancel does. Take module locks last.
            lock_run(db, run.id, owned=False)
            db.refresh(row, with_for_update=True)
            row.deletion_requested_at = row.deletion_requested_at or now()
            if active:
                row.metadata_json = {**row.metadata_json, "user_cancel_requested": True}
            if remove_when_closed(db, run, row):
                result["deleted"].append(run.id)
            else:
                result["stopping"].append(run.id)
        return result

    return idempotent(
        db,
        user_id,
        str(command.request_id),
        "media_history_delete:" + workspace_id,
        command.model_dump(mode="json"),
        execute,
    )


def finalize_pending():
    with Session(engine()) as db:
        identifiers = list(
            db.execute(
                select(Run.id, Run.root_run_id)
                .select_from(MediaTask)
                .join(Run, Run.id == MediaTask.run_id)
                .where(
                    MediaTask.deletion_requested_at.is_not(None),
                    MediaTask.deleted_at.is_(None),
                    MediaTask.resources_closed.is_(True),
                    Run.status.in_(TERMINAL_STATUSES),
                )
                .order_by(MediaTask.run_id)
                .limit(100)
            )
        )
    for run_id, root_run_id in identifiers:
        # One task per transaction prevents holding a descendant while waiting
        # for another task's root during concurrent batch cancellation.
        with Session(engine()) as db, db.begin():
            db.scalar(select(Run).where(Run.id == (root_run_id or run_id)).with_for_update())
            run = lock_run(db, run_id, owned=False)
            row = db.scalar(
                select(MediaTask).where(MediaTask.run_id == run_id).with_for_update()
            )
            remove_when_closed(db, run, row)
