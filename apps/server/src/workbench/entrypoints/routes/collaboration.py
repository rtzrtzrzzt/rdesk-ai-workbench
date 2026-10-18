from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import DB, SNAPSHOT, USER
from workbench.modules.collaboration import dto, service
from workbench.modules.runs.trace import RunTrace, trace

router = APIRouter(prefix="/api/v1/runs/{run_id}", responses=ERROR_RESPONSES)


@router.get("/inputs", response_model=list[dto.InputDelivery])
def inputs(run_id: UUID, db: DB, user: USER):
    from workbench.modules.collaboration.domain import AgentMessage
    from workbench.modules.runs.domain import Attempt
    from workbench.modules.runs.service import get

    get(db, user.id, str(run_id))
    running = list(
        db.scalars(
            select(Attempt).where(Attempt.run_id == str(run_id), Attempt.status == "running")
        )
    )
    processing = {
        identifier: attempt.id for attempt in running for identifier in attempt.input_message_ids
    }
    return [
        {
            "id": row.id,
            "input": row.content,
            "created_at": row.created_at,
            "delivered_attempt_id": row.delivered_attempt_id,
            "processing_attempt_id": processing.get(row.id),
            "status": "delivered"
            if row.delivered_attempt_id
            else "applying"
            if row.id in processing
            else "queued_for_next_model_call",
        }
        for row in db.scalars(
            select(AgentMessage)
            .where(AgentMessage.to_run_id == str(run_id), AgentMessage.source == "user")
            .order_by(AgentMessage.created_at, AgentMessage.id)
        )
    ]


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
