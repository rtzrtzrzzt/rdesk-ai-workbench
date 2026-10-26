from uuid import UUID

from fastapi import APIRouter, Query
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


@router.get("/agents/messages", response_model=dto.AgentCommunications)
def agent_messages(
    run_id: UUID,
    db: SNAPSHOT,
    user: USER,
    agent_id: UUID | None = None,
    before: UUID | None = None,
    limit: int = Query(default=30, ge=1, le=50),
):
    return service.communication_page(
        db,
        user.id,
        str(run_id),
        str(agent_id) if agent_id else None,
        str(before) if before else None,
        limit,
    )


def control_root(db, user_id, run_id):
    from workbench.contracts.errors import AppError
    from workbench.modules.runs.service import get

    run = get(db, user_id, run_id)
    if run.parent_run_id or run.kind != "agent":
        raise AppError("agent_control_scope", "请从主任务管理子Agent。", 403, "policy")


@router.post("/agents/{agent_id}/followup", response_model=dto.AgentView)
def followup_agent(run_id: UUID, agent_id: UUID, command: dto.AgentFollowup, db: DB, user: USER):
    from workbench.modules.runs.service import idempotent

    control_root(db, user.id, str(run_id))
    result = idempotent(
        db,
        user.id,
        str(command.request_id),
        f"agent:{run_id}:{agent_id}:followup",
        {"input": command.input},
        lambda: service.followup(
            str(run_id), str(command.request_id), str(agent_id), command.input
        ),
    )
    db.commit()
    return result


@router.post("/agents/{agent_id}/interrupt", response_model=dto.Run)
def interrupt_agent(run_id: UUID, agent_id: UUID, command: dto.AgentControl, db: DB, user: USER):
    control_root(db, user.id, str(run_id))
    return service.cancel_agent(str(run_id), str(command.request_id), str(agent_id))


@router.post("/agents/{agent_id}/close", response_model=dto.AgentView)
def close_agent(run_id: UUID, agent_id: UUID, command: dto.AgentControl, db: DB, user: USER):
    from workbench.modules.runs.service import idempotent

    control_root(db, user.id, str(run_id))
    result = idempotent(
        db,
        user.id,
        str(command.request_id),
        f"agent:{run_id}:{agent_id}:close",
        {},
        lambda: service.release(str(run_id), str(agent_id)),
    )
    db.commit()
    return result


@router.get("/trace", response_model=RunTrace)
def run_trace(run_id: UUID, db: SNAPSHOT, user: USER):
    return trace(db, user.id, str(run_id))


@router.post("/inputs", response_model=dto.InputReceived, status_code=201)
def steer(run_id: UUID, command: dto.SteerRun, db: DB, user: USER):
    result = service.steer(db, user.id, str(run_id), command.model_dump(mode="json"))
    db.commit()
    return result
