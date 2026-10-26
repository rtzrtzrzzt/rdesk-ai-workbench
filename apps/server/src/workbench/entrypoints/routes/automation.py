import asyncio
from uuid import UUID

from fastapi import APIRouter
from workbench.contracts.errors import AppError
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import DB, USER
from workbench.modules.automation import dto, runtime, service, settings
from workbench.modules.automation.domain import AutomationAction
from workbench.modules.workspace_service import get as workspace

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/automation", responses=ERROR_RESPONSES)
settings_router = APIRouter(prefix="/api/v1/automation", responses=ERROR_RESPONSES)


@settings_router.get("/settings", response_model=dto.ComputerSettingsView)
def computer_settings(db: DB, user: USER):
    return settings.view(db, user.id)


@settings_router.post("/settings", response_model=dto.ComputerSettingsView)
def save_computer_settings(command: dto.ComputerSettingsSave, db: DB, user: USER):
    result = settings.save(db, user.id, command)
    db.commit()
    return result


@router.get("/policy", response_model=dto.PolicyView)
def policy(workspace_id: UUID, db: DB, user: USER):
    return service.get_policy(db, user.id, str(workspace_id))


@router.post("/policy", response_model=dto.PolicyView)
def save_policy(workspace_id: UUID, command: dto.AutomationSave, db: DB, user: USER):
    result = service.save_policy(db, user.id, str(workspace_id), command)
    db.commit()
    return result


@router.get("/capabilities", response_model=dto.AutomationCapabilities)
async def capabilities(workspace_id: UUID, user: USER):
    return await service.capabilities(user.id, str(workspace_id))


@router.post("/runtime/start", response_model=dto.RuntimeView)
async def start(workspace_id: UUID, command: dto.RuntimeCommand, db: DB, user: USER):
    workspace(db, user.id, str(workspace_id))
    db.rollback()
    return await asyncio.to_thread(runtime.start)


@router.post("/runtime/stop", response_model=dto.RuntimeView)
async def stop(workspace_id: UUID, command: dto.RuntimeCommand, db: DB, user: USER):
    workspace(db, user.id, str(workspace_id))
    db.rollback()
    return await asyncio.to_thread(runtime.stop)


@router.get("/sessions", response_model=list[dto.SessionView])
def sessions(workspace_id: UUID, db: DB, user: USER):
    return service.listing(db, user.id, str(workspace_id))


@router.post("/sessions", response_model=dto.SessionView, status_code=201)
async def create(workspace_id: UUID, command: dto.SessionCreate, user: USER):
    return await service.create_session(user.id, str(workspace_id), command)


@router.post("/sessions/{session_id}/observe", response_model=dto.ActionView)
async def observe(workspace_id: UUID, session_id: UUID, command: dto.Observe, user: USER):
    return await service.observe(user.id, str(workspace_id), str(session_id), command)


@router.post("/sessions/{session_id}/download", response_model=dto.ActionView)
async def download(workspace_id: UUID, session_id: UUID, command: dto.BrowserDownload, user: USER):
    return await service.download(user.id, str(workspace_id), str(session_id), command)


@router.post("/sessions/{session_id}/browser-action", response_model=dto.ActionView)
async def browser_action(
    workspace_id: UUID, session_id: UUID, command: dto.BrowserCommand, user: USER
):
    return await service.act(
        user.id, str(workspace_id), str(session_id), command.action, kind="browser"
    )


@router.post("/sessions/{session_id}/desktop-action", response_model=dto.ActionView)
async def desktop_action(
    workspace_id: UUID, session_id: UUID, command: dto.DesktopCommand, user: USER
):
    return await service.act(
        user.id, str(workspace_id), str(session_id), command.action, kind="desktop"
    )


@router.post("/sessions/{session_id}/stop", response_model=dto.SessionView)
async def stop_session(
    workspace_id: UUID, session_id: UUID, command: dto.RuntimeCommand, user: USER
):
    return await service.stop_session(user.id, str(workspace_id), str(session_id))


@router.post("/sessions/{session_id}/takeover", response_model=dto.SessionView)
async def takeover(workspace_id: UUID, session_id: UUID, command: dto.RuntimeCommand, user: USER):
    return await service.stop_session(user.id, str(workspace_id), str(session_id), takeover=True)


@router.get("/actions/{action_id}", response_model=dto.ActionView)
def action(workspace_id: UUID, action_id: UUID, db: DB, user: USER):
    workspace(db, user.id, str(workspace_id))
    row = db.get(AutomationAction, str(action_id))
    if row is None or row.user_id != user.id:
        raise AppError("automation_action_missing", "此动作不存在或不可访问。", 404)
    service.get_session(db, user.id, str(workspace_id), row.session_id)
    return service.action_view(row)
