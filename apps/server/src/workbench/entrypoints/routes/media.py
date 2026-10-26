from uuid import UUID

from fastapi import APIRouter, Query
from workbench.contracts.errors import AppError
from workbench.contracts.requests import CancelRun, EmptyCommand
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import DB, USER
from workbench.modules.media import dto, history, lifecycle, service

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/media", responses=ERROR_RESPONSES)


@router.get("/connections", response_model=dto.MediaConnections)
def connections(workspace_id: UUID, db: DB, user: USER):
    return service.listing(db, user.id, str(workspace_id))


@router.post("/connections/{connection_id}", response_model=dto.MediaConnectionView)
def save(
    workspace_id: UUID, connection_id: UUID, command: dto.MediaConnectionSave, db: DB, user: USER
):
    result = service.save(db, user.id, str(workspace_id), str(connection_id), command)
    db.commit()
    return result


@router.post("/connections/{connection_id}/check", response_model=dto.MediaConnectionView)
async def check(workspace_id: UUID, connection_id: UUID, command: EmptyCommand, db: DB, user: USER):
    row = service.connection(db, user.id, str(workspace_id), str(connection_id))
    snapshot, credential, version = service.connection_view(row), row.encrypted_api_key, row.version
    db.rollback()
    verification = await lifecycle.check(snapshot, credential)
    row = service.connection(db, user.id, str(workspace_id), str(connection_id), lock=True)
    if row.version != version:
        raise AppError("media_connection_changed", "检查期间连接发生变化，请重试。", 409)
    row.verification = verification
    db.commit()
    return service.connection_view(row)


@router.get("/tasks", response_model=dto.MediaTaskList)
def tasks(
    workspace_id: UUID,
    db: DB,
    user: USER,
    parent_run_id: UUID | None = None,
    before: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=100),
):
    return service.tasks(
        db,
        user.id,
        str(workspace_id),
        str(parent_run_id) if parent_run_id else None,
        before=str(before) if before else None,
        limit=limit,
    )


@router.post("/tasks", response_model=dto.MediaTaskView)
def create(workspace_id: UUID, command: dto.MediaCreate, db: DB, user: USER):
    result = service.create(db, user.id, str(workspace_id), command)
    db.commit()
    return result


@router.get("/tasks/{run_id}", response_model=dto.MediaTaskView)
def task(workspace_id: UUID, run_id: UUID, db: DB, user: USER):
    return service.task_view(db, service.task(db, user.id, str(workspace_id), str(run_id)))


@router.post("/tasks/deletion", response_model=dto.MediaDeletionResult)
def delete_tasks(workspace_id: UUID, command: dto.MediaTaskDeletion, db: DB, user: USER):
    result = history.delete(db, user.id, str(workspace_id), command)
    db.commit()
    return result


@router.post("/tasks/{run_id}/cancel", response_model=dto.MediaTaskView)
def cancel(workspace_id: UUID, run_id: UUID, command: CancelRun, db: DB, user: USER):
    result = service.cancel_task(db, user.id, str(workspace_id), str(run_id), command.request_id)
    db.commit()
    return result


@router.post("/tasks/{run_id}/reconcile", response_model=dto.MediaTaskView)
def reconcile(workspace_id: UUID, run_id: UUID, command: dto.MediaReconcile, db: DB, user: USER):
    result = lifecycle.reconcile(db, user.id, str(workspace_id), str(run_id), command)
    db.commit()
    return result
