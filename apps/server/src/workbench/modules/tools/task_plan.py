"""A run's editable plan, persisted separately from execution/checkpoint facts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine
from workbench.modules.runs.domain import TERMINAL_STATUSES
from workbench.modules.runs.service import emit


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)
    step: str = Field(min_length=1, max_length=500)
    status: Literal["pending", "in_progress", "completed", "skipped"] = "pending"


class UpdatePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    explanation: str = Field(default="", max_length=2000)
    steps: list[PlanStep] = Field(max_length=50)

    @model_validator(mode="after")
    def consistent(self):
        if len({item.id for item in self.steps}) != len(self.steps):
            raise ValueError("计划步骤ID不能重复。")
        if sum(item.status == "in_progress" for item in self.steps) > 1:
            raise ValueError("同时最多一个计划步骤进行中。")
        return self


def execute(run_id, operation_id, name, values):
    from workbench.modules.collaboration.service import lock_root

    with Session(engine()) as db, db.begin():
        _, run = lock_root(db, run_id)
        current = run.config_snapshot.get("task_plan") or {
            "version": 0,
            "steps": [],
            "explanation": "",
        }
        if name == "read_plan":
            return current
        if current.get("operation_id") == operation_id:
            return current
        if run.cancel_requested_at or run.status in TERMINAL_STATUSES:
            raise AppError("run_finished", "任务已停止，不能修改计划。", 409)
        if current["version"] != values["expected_version"]:
            raise AppError("plan_conflict", "计划已更新，请先read_plan再修改。", 409)
        updated = {
            "version": current["version"] + 1,
            "steps": values["steps"],
            "explanation": values["explanation"],
            "operation_id": operation_id,
        }
        run.config_snapshot = {**run.config_snapshot, "task_plan": updated}
        emit(db, run_id, "plan.updated", {"plan": updated})
        return updated
