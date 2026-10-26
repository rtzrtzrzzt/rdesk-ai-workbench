from uuid import UUID

from fastapi import APIRouter
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import DB, USER
from workbench.modules.extensions import dto, service
from workbench.modules.mcp.host_dto import MCPView

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/extensions", responses=ERROR_RESPONSES)


@router.get("", response_model=dto.ExtensionList)
def listing(workspace_id: UUID, db: DB, user: USER):
    return service.listing(db, user.id, str(workspace_id))


@router.post("/inspect", response_model=dto.ExtensionInspection)
def inspect(workspace_id: UUID, command: dto.ExtensionSource, db: DB, user: USER):
    from workbench.modules import workspace_service

    workspace_service.get(db, user.id, str(workspace_id))
    db.rollback()
    return service.inspect(command)


@router.post("/install", response_model=dto.ExtensionView)
def install(workspace_id: UUID, command: dto.ExtensionInstall, db: DB, user: USER):
    from workbench.modules import workspace_service

    user_id = user.id
    workspace_service.get(db, user_id, str(workspace_id))
    db.rollback()
    return service.install(user_id, str(workspace_id), command)


@router.post("/{installation_id}/upgrade", response_model=dto.ExtensionView)
def upgrade(
    workspace_id: UUID, installation_id: UUID, command: dto.ExtensionUpgrade, db: DB, user: USER
):
    user_id = user.id
    service.installation(db, user_id, str(workspace_id), str(installation_id))
    db.rollback()
    return service.upgrade(user_id, str(workspace_id), str(installation_id), command)


@router.post("/{installation_id}/enabled", response_model=dto.ExtensionView)
def enabled(
    workspace_id: UUID, installation_id: UUID, command: dto.ExtensionChange, db: DB, user: USER
):
    result = service.change(db, user.id, str(workspace_id), str(installation_id), command)
    db.commit()
    return result


@router.post("/{installation_id}/remove", response_model=dto.ExtensionView)
def remove(
    workspace_id: UUID, installation_id: UUID, command: dto.ExtensionRemove, db: DB, user: USER
):
    result = service.remove(db, user.id, str(workspace_id), str(installation_id), command)
    db.commit()
    return result


@router.post("/{installation_id}/mcp", response_model=MCPView)
def mcp(
    workspace_id: UUID, installation_id: UUID, command: dto.ExtensionMCPImport, db: DB, user: USER
):
    result = service.import_mcp(db, user.id, str(workspace_id), str(installation_id), command)
    db.commit()
    return result
