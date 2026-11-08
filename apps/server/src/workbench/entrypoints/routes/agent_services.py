"""Owner-only publication; included solely by the loopback management application."""

from uuid import UUID

from fastapi import APIRouter
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import DB, USER
from workbench.modules.agent_services import dto, service

router = APIRouter(prefix="/api/v1/agent-services", responses=ERROR_RESPONSES)


@router.get("/capabilities", response_model=dict)
def capabilities(db: DB, user: USER):
    return service.publication_capabilities(db, user.id)


@router.get("", response_model=list[dto.OwnerAgent])
def agents(db: DB, user: USER):
    return service.list_owned(db, user.id)


@router.post("", response_model=dto.OwnerAgent, status_code=201)
def publish(command: dto.PublishAgent, db: DB, user: USER):
    result = service.publish(db, user.id, command)
    db.commit()
    return result


@router.get("/{agent_id}", response_model=dto.OwnerAgent)
def agent(agent_id: UUID, db: DB, user: USER):
    return service.owner_view(db, service.get(db, user.id, str(agent_id)))


@router.post("/{agent_id}/revisions", response_model=dto.OwnerAgent)
def revise(agent_id: UUID, command: dto.ReviseAgent, db: DB, user: USER):
    result = service.revise(db, user.id, str(agent_id), command)
    db.commit()
    return result


@router.post("/{agent_id}/state", response_model=dto.OwnerAgent)
def state(agent_id: UUID, command: dto.AgentState, db: DB, user: USER):
    result = service.set_enabled(db, user.id, str(agent_id), command)
    db.commit()
    return result
