import json
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select

from workbench.modules.runs.domain import Attempt, Step
from workbench.modules.runs.service import get
from workbench.modules.tools.domain import Operation


class TraceAttempt(BaseModel):
    id: str
    number: int
    status: str
    created_at: datetime
    finished_at: datetime | None
    error: dict | None


class TraceStep(BaseModel):
    id: str
    logical_key: str
    kind: str
    status: str
    attempts: list[TraceAttempt]


class TraceOperation(BaseModel):
    id: str
    tool_name: str
    status: str
    effect_status: str
    arguments: dict
    result: dict | None
    truncated: bool


class RunTrace(BaseModel):
    steps: list[TraceStep]
    operations: list[TraceOperation]
    truncated: bool


def trace(db, user_id, run_id):
    get(db, user_id, run_id)
    steps = list(
        db.scalars(select(Step).where(Step.run_id == run_id).order_by(Step.created_at).limit(201))
    )
    operations = list(
        db.scalars(
            select(Operation)
            .where(Operation.run_id == run_id)
            .order_by(Operation.created_at)
            .limit(101)
        )
    )
    result = []
    for operation in operations[:100]:
        serialized = json.dumps(operation.result, ensure_ascii=False) if operation.result else ""
        result.append(
            {
                "id": operation.id,
                "tool_name": operation.tool_name,
                "status": operation.status,
                "effect_status": operation.effect_status,
                "arguments": operation.arguments,
                "result": {"preview": serialized[:8000]}
                if len(serialized) > 8000
                else operation.result,
                "truncated": len(serialized) > 8000,
            }
        )
    return {
        "steps": [
            {
                "id": step.id,
                "logical_key": step.logical_key,
                "kind": step.kind,
                "status": step.status,
                "attempts": [
                    {
                        "id": attempt.id,
                        "number": attempt.number,
                        "status": attempt.status,
                        "created_at": attempt.created_at,
                        "finished_at": attempt.finished_at,
                        "error": attempt.error,
                    }
                    for attempt in db.scalars(
                        select(Attempt).where(Attempt.step_id == step.id).order_by(Attempt.number)
                    )
                ],
            }
            for step in steps[:200]
        ],
        "operations": result,
        "truncated": len(steps) > 200 or len(operations) > 100,
    }
