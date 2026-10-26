from uuid import UUID

from fastapi import APIRouter, Query
from workbench.contracts.requests import RunCreate
from workbench.contracts.responses import ERROR_RESPONSES, Run
from workbench.entrypoints.dependencies import DB, SNAPSHOT, USER
from workbench.modules import conversation_deletion as deletion
from workbench.modules import conversation_dto as dto
from workbench.modules import conversation_lifecycle as lifecycle
from workbench.modules import conversation_queue as queue
from workbench.modules.context import dto as context_dto
from workbench.modules.context import service as context_service
from workbench.modules.runs import activity, safety

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)


@router.get("/threads/{thread_id}/context", response_model=context_dto.ContextView)
def context_view(thread_id: UUID, db: DB, user: USER, model_connection_id: UUID | None = None):
    return context_service.view(
        db, user.id, str(thread_id), str(model_connection_id) if model_connection_id else None
    )


@router.post("/threads/{thread_id}/context/compact", response_model=context_dto.ContextView)
def compact_context(thread_id: UUID, command: context_dto.CompactContext, db: DB, user: USER):
    result = context_service.request_compaction(
        db, user.id, str(thread_id), command.model_dump(mode="json")
    )
    db.commit()
    return result


@router.get("/threads/{thread_id}/activity", response_model=activity.ActivityPage)
def activity_page(
    thread_id: UUID,
    db: SNAPSHOT,
    user: USER,
    before: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=100),
):
    return activity.page(db, user.id, str(thread_id), str(before) if before else None, limit)


@router.get("/threads/{thread_id}/activity/{operation_id}", response_model=activity.ActivityDetails)
def activity_details(thread_id: UUID, operation_id: UUID, db: SNAPSHOT, user: USER):
    return activity.details(db, user.id, str(thread_id), str(operation_id))


@router.get("/threads/{thread_id}/queue", response_model=dto.QueueView)
def queue_view(thread_id: UUID, db: DB, user: USER):
    return queue.view(db, user.id, str(thread_id))


@router.post("/threads/{thread_id}/queue", response_model=dto.QueueItem)
def enqueue(thread_id: UUID, command: RunCreate, db: DB, user: USER):
    result = queue.add(db, user.id, str(thread_id), command.model_dump(mode="json"))
    db.commit()
    return result


@router.post("/threads/{thread_id}/queue/resume", response_model=dto.QueueView)
def queue_resume(thread_id: UUID, command: dto.RequestCommand, db: DB, user: USER):
    result = queue.resume(db, user.id, str(thread_id), str(command.request_id))
    db.commit()
    return result


@router.post("/threads/{thread_id}/queue/{item_id}", response_model=dto.QueueView)
def queue_update(thread_id: UUID, item_id: UUID, command: dto.QueueUpdate, db: DB, user: USER):
    result = queue.update(
        db, user.id, str(thread_id), str(item_id), command.model_dump(mode="json")
    )
    db.commit()
    return result


@router.post("/threads/{thread_id}/queue/{item_id}/steer", response_model=dto.QueueSteered)
def queue_steer(thread_id: UUID, item_id: UUID, command: dto.QueueSteer, db: DB, user: USER):
    result = queue.steer_queued(
        db, user.id, str(thread_id), str(item_id), command.model_dump(mode="json")
    )
    db.commit()
    return result


@router.post("/threads/{thread_id}/configuration", response_model=dto.Thread)
def rename(thread_id: UUID, command: dto.ThreadRename, db: DB, user: USER):
    result = lifecycle.rename(db, user.id, str(thread_id), command.model_dump(mode="json"))
    db.commit()
    return result


@router.post("/threads/{thread_id}/lifecycle", response_model=dto.Thread)
def archive(thread_id: UUID, command: dto.ThreadLifecycle, db: DB, user: USER):
    result = lifecycle.lifecycle(db, user.id, str(thread_id), command.model_dump(mode="json"))
    db.commit()
    return result


@router.post("/threads/{thread_id}/edit", response_model=dto.EditedThread)
def edit(thread_id: UUID, command: dto.ThreadEdit, db: DB, user: USER):
    result = lifecycle.edit(
        db,
        user.id,
        str(thread_id),
        command.model_dump(mode="json"),
        inherit_attachments="attachments" not in command.model_fields_set,
    )
    db.commit()
    return result


@router.get("/threads/{thread_id}/deletion-preview", response_model=dto.DeletionPreview)
def preview_delete(thread_id: UUID, db: DB, user: USER):
    return deletion.preview(db, user.id, str(thread_id))


@router.get("/threads/{thread_id}/deletion", response_model=dto.DeletionView | None)
def deletion_status(thread_id: UUID, db: DB, user: USER):
    return deletion.status(db, user.id, str(thread_id))


@router.post("/threads/{thread_id}/deletion", response_model=dto.DeletionView)
def delete(thread_id: UUID, command: dto.DeleteThread, db: DB, user: USER):
    result = deletion.begin(db, user.id, str(thread_id), command.model_dump(mode="json"))
    db.commit()
    return result


@router.post("/runs/{run_id}/resume", response_model=Run)
def resume(run_id: UUID, command: dto.ResumeRun, db: DB, user: USER):
    result = safety.resume(db, user.id, str(run_id), command.model_dump(mode="json"))
    db.commit()
    return result
