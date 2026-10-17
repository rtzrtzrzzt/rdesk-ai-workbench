import asyncio
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy.orm import Session
from workbench.contracts.requests import EmptyCommand
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import DB, USER
from workbench.infrastructure.database import engine
from workbench.infrastructure.mutations import quiescence, request_abort
from workbench.modules.maintenance import cleanup, dto, service

router = APIRouter(prefix="/api/v1/maintenance", responses=ERROR_RESPONSES)


@router.get("/cleanup", response_model=dto.CleanupPreview)
def cleanup_preview(db: DB, user: USER):
    return cleanup.preview(db, user.id)


@router.post("/cleanup", response_model=dto.CleanupResult)
async def cleanup_temporary(command: dto.CleanupCreate, db: DB, user: USER):
    user_id = user.id
    db.rollback()

    def execute():
        with quiescence() as boundary, Session(engine()) as owned:
            return cleanup.execute(owned, user_id, command, boundary)

    return await asyncio.to_thread(execute)


@router.get("/backups", response_model=list[dto.BackupView])
def backups(db: DB, user: USER):
    return service.list_backups(db, user.id)


@router.get("/backups/{backup_id}", response_model=dto.BackupManifest)
def inspect(backup_id: UUID, db: DB, user: USER):
    return service.inspect_backup(db, user.id, str(backup_id))


@router.post("/backups", response_model=dto.BackupView, status_code=201)
async def backup(command: dto.BackupCreate, db: DB, user: USER):
    user_id = user.id
    db.rollback()

    def execute():
        with quiescence() as boundary, Session(engine()) as owned:
            return service.create_backup(
                owned, user_id, command, boundary=boundary, abort_check=boundary.gate_check
            )

    return await asyncio.to_thread(execute)


@router.post("/restores", response_model=dto.RestoreView, status_code=201)
async def restore(command: dto.RestoreCreate, db: DB, user: USER):
    user_id = user.id
    db.rollback()

    def execute():
        with quiescence() as boundary, Session(engine()) as owned:
            return service.restore_backup(owned, user_id, command, abort_check=boundary.gate_check)

    return await asyncio.to_thread(execute)


@router.post("/abort", response_model=dict[str, str])
def abort(command: EmptyCommand, user: USER):
    request_abort()
    return {"status": "stop_requested"}
