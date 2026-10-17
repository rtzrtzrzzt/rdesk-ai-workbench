from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import now
from workbench.modules.runs.domain import Job


@dataclass(frozen=True)
class ExecutionOwner:
    run_id: str
    worker_id: str
    epoch: int


current_owner: ContextVar[ExecutionOwner | None] = ContextVar("execution_owner", default=None)


def assert_owned(db: Session, run_id: str) -> None:
    owner = current_owner.get()
    if owner is None:
        return  # Authenticated API commands have their own authorization and idempotency.
    if owner.run_id != run_id:
        raise AppError("lease_lost", "执行归属不匹配，已停止提交。", 409, "ownership")
    job = db.scalar(select(Job).where(Job.run_id == run_id).with_for_update())
    if (
        job is None
        or job.lease_owner != owner.worker_id
        or job.lease_epoch != owner.epoch
        or job.status != "running"
        or not job.lease_until
        or job.lease_until <= now()
    ):
        raise AppError("lease_lost", "执行租约已失效，旧执行者不能继续提交。", 409, "ownership")
