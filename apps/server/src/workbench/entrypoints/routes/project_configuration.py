from uuid import UUID

from fastapi import APIRouter, Response
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import DB, USER
from workbench.modules import workspace_service
from workbench.modules.project_configuration import dto, service

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)


@router.get("/settings/data-directory", response_model=dto.DataDirectoryView)
def data_directory(db: DB, user: USER, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return service.data_directory(db, user.id)


@router.get(
    "/workspaces/{workspace_id}/project-configuration", response_model=dto.ProjectConfigurationView
)
def project_configuration(workspace_id: UUID, db: DB, user: USER, response: Response):
    from pathlib import Path

    response.headers["Cache-Control"] = "no-store"
    workspace = workspace_service.get(db, user.id, str(workspace_id))
    return service.load(Path(workspace.root_path))
