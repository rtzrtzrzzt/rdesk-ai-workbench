from uuid import UUID

from fastapi import APIRouter
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import DB, SNAPSHOT, USER
from workbench.modules.collaboration import dto, service
from workbench.modules.runs.trace import RunTrace, trace

router = APIRouter(prefix="/api/v1/runs/{run_id}", responses=ERROR_RESPONSES)


@router.get("/agents", response_model=dto.AgentTree)
def agents(run_id: UUID, db: SNAPSHOT, user: USER):
    return service.tree(db, user.id, str(run_id))


@router.get("/trace", response_model=RunTrace)
def run_trace(run_id: UUID, db: SNAPSHOT, user: USER):
    return trace(db, user.id, str(run_id))


@router.post("/inputs", response_model=dto.InputReceived, status_code=201)
def steer(run_id: UUID, command: dto.SteerRun, db: DB, user: USER):
    result = service.steer(db, user.id, str(run_id), command.model_dump(mode="json"))
    db.commit()
    return result
