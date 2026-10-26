from uuid import UUID

from fastapi import APIRouter
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import DB, USER
from workbench.modules.conversation_history import settings as history_settings
from workbench.modules.conversation_history.dto import (
    ConversationHistorySave,
    ConversationHistoryView,
)
from workbench.modules.preferences import service
from workbench.modules.preferences.configuration import (
    ExperienceSave,
    ExperienceView,
    experience,
    save_experience,
)

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)


@router.get("/settings/conversation-history", response_model=ConversationHistoryView)
def conversation_history(db: DB, user: USER):
    return history_settings.view(db, user.id)


@router.post("/settings/conversation-history", response_model=ConversationHistoryView)
def save_conversation_history(command: ConversationHistorySave, db: DB, user: USER):
    result = history_settings.save(db, user.id, command)
    db.commit()
    return result


@router.get("/settings/experience", response_model=ExperienceView)
def experience_preferences(db: DB, user: USER):
    return experience(db, user.id)


@router.post("/settings/experience", response_model=ExperienceView)
def update_experience(command: ExperienceSave, db: DB, user: USER):
    result = save_experience(db, user.id, command)
    db.commit()
    return result


@router.get("/settings/runtime", response_model=service.DefaultsView)
def defaults(db: DB, user: USER):
    return service.get(db, user.id)


@router.post("/settings/runtime", response_model=service.DefaultsView)
def save(command: service.DefaultsSave, db: DB, user: USER):
    result = service.save(db, user.id, command)
    db.commit()
    return result


@router.get(
    "/workspaces/{workspace_id}/settings/runtime", response_model=service.WorkspaceDefaultsView
)
def workspace_defaults(workspace_id: UUID, db: DB, user: USER):
    return service.get_workspace(db, user.id, str(workspace_id))


@router.post(
    "/workspaces/{workspace_id}/settings/runtime", response_model=service.WorkspaceDefaultsView
)
def save_workspace(workspace_id: UUID, command: service.WorkspaceDefaultsSave, db: DB, user: USER):
    result = service.save_workspace(db, user.id, str(workspace_id), command)
    db.commit()
    return result
