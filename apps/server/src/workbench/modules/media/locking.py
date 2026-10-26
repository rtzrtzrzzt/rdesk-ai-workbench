"""Match the scheduler's Run → Job → module-state lock order."""

from sqlalchemy import select

from workbench.modules.runs.domain import Job, Run
from workbench.modules.runs.ownership import assert_owned


def lock_run(db, run_id, *, owned=True):
    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if owned:
        assert_owned(db, run_id)
    else:
        db.scalar(select(Job).where(Job.run_id == run_id).with_for_update())
    return run
