from uuid import UUID

from fastapi import APIRouter, Query
from workbench.contracts.requests import EmptyCommand
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import DB, USER
from workbench.modules.processes import dto, native, service
from workbench.modules.workspace_service import get as workspace

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/commands", responses=ERROR_RESPONSES)


@router.get("/capability")
def capability(workspace_id: UUID, db: DB, user: USER):
    workspace(db, user.id, str(workspace_id))
    return native.capability()


@router.get("", response_model=list[dto.CommandView])
def listing(workspace_id: UUID, db: DB, user: USER):
    return service.listing(db, user.id, str(workspace_id))


def _reader(db, user):
    user_id = user.id
    db.rollback()
    return user_id


@router.get("/{command_id}", response_model=dto.CommandView)
async def inspect(workspace_id: UUID, command_id: UUID, db: DB, user: USER):
    return await service.inspect(_reader(db, user), str(workspace_id), str(command_id))


@router.get("/{command_id}/output", response_model=dto.CommandOutput)
async def output(
    workspace_id: UUID,
    command_id: UUID,
    db: DB,
    user: USER,
    stream: str = Query(default="stdout", pattern="^(stdout|stderr)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=8000, ge=1, le=16000),
):
    return await service.read(
        _reader(db, user), str(workspace_id), str(command_id), stream, offset, limit
    )


@router.post("/{command_id}/stop", response_model=dto.CommandView)
async def stop(workspace_id: UUID, command_id: UUID, command: EmptyCommand, db: DB, user: USER):
    return await service.stop(_reader(db, user), str(workspace_id), str(command_id))
