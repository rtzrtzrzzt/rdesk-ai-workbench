from uuid import UUID

from fastapi import APIRouter
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import DB, USER
from workbench.modules.mcp import host, host_dto

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/mcp", responses=ERROR_RESPONSES)


@router.get("/connections", response_model=host_dto.MCPList)
def connections(workspace_id: UUID, db: DB, user: USER):
    return host.listing(db, user.id, str(workspace_id))


@router.post("/connections/{connection_id}", response_model=host_dto.MCPView)
def save(workspace_id: UUID, connection_id: UUID, command: host_dto.MCPSave, db: DB, user: USER):
    result = host.save(db, user.id, str(workspace_id), str(connection_id), command)
    db.commit()
    return result


@router.post("/connections/{connection_id}/discover", response_model=host_dto.SnapshotView)
async def discover(
    workspace_id: UUID, connection_id: UUID, command: host_dto.MCPDiscover, db: DB, user: USER
):
    user_id = user.id
    db.rollback()
    return await host.discover(user_id, str(workspace_id), str(connection_id), command)
