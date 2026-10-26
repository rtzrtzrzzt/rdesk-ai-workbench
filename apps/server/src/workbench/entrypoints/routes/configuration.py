"""Authenticated, discoverable configuration surface for UI and external agents."""

from uuid import UUID

from fastapi import APIRouter, Query
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import DB, USER
from workbench.modules.configuration import catalog, dto, service

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)


@router.get("/settings/catalog", response_model=dict)
def settings_catalog(user: USER, q: str = Query(default="", max_length=200), schemas: bool = False):
    return catalog.discover(q, schemas)


@router.post("/workspaces/{workspace_id}/settings/read", response_model=dict)
def read_settings(workspace_id: UUID, command: dto.ReadSettings, db: DB, user: USER):
    return service.read(db, user.id, str(workspace_id), command)


@router.post("/workspaces/{workspace_id}/settings/preview", response_model=dict)
def preview_settings(workspace_id: UUID, command: dto.SettingsChange, db: DB, user: USER):
    return service.preview(db, user.id, str(workspace_id), command)


@router.post("/workspaces/{workspace_id}/settings/apply", response_model=dict)
async def configure_settings(workspace_id: UUID, command: dto.ApplySettings, user: USER):
    return await service.apply(user.id, str(workspace_id), command)


@router.get("/settings/requests/{request_id}", response_model=dict)
def settings_receipt(request_id: UUID, db: DB, user: USER):
    return service.receipt(db, user.id, str(request_id))
