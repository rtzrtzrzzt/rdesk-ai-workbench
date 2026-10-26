"""Generation permits are independent from task count and human/tool waiting."""

import asyncio
from contextlib import asynccontextmanager

import psycopg
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from workbench.config import get_settings
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, now
from workbench.modules.collaboration.domain import AgentMessage
from workbench.modules.runs.domain import Job, Run, RunEvent
from workbench.modules.runs.service import emit, set_status


def status(run_id, waiting):
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        set_status(db, run, "waiting_resource" if waiting else "running")
        emit(
            db,
            run_id,
            "resource.waiting" if waiting else "resource.acquired",
            {"resource": "model_generation", "capacity": 1},
        )


def queue_turn(run_id):
    """User updates take priority; normal generations follow persisted arrival order."""
    with Session(engine()) as db:
        from workbench.modules.media.resources import active_run_ids

        residents = active_run_ids(db)
        if residents and run_id not in residents:
            return False
        interactive = (
            select(AgentMessage.id)
            .where(
                AgentMessage.to_run_id == Run.id,
                AgentMessage.source == "user",
                AgentMessage.delivered_attempt_id.is_(None),
            )
            .correlate(Run)
            .exists()
        )
        directed = (
            select(AgentMessage.id)
            .where(
                AgentMessage.to_run_id == Run.id,
                AgentMessage.kind == "instruction",
                AgentMessage.delivered_attempt_id.is_(None),
            )
            .correlate(Run)
            .exists()
        )
        arrived = (
            select(func.max(RunEvent.created_at))
            .where(
                RunEvent.run_id == Run.id,
                RunEvent.type == "resource.waiting",
            )
            .correlate(Run)
            .scalar_subquery()
        )
        current = db.get(Run, run_id)
        if current and current.kind == "conversation_naming":
            foreground_ready = db.scalar(
                select(Job.id)
                .join(Run, Run.id == Job.run_id)
                .where(
                    Run.kind != "conversation_naming",
                    Run.status.not_in(["succeeded", "failed", "cancelled"]),
                    Run.cancel_requested_at.is_(None),
                    Job.status == "queued",
                    Job.available_at <= now(),
                )
                .limit(1)
            )
            if foreground_ready:
                return False
        first = db.scalar(
            select(Run.id)
            .join(Job, Job.run_id == Run.id)
            .where(
                Run.status == "waiting_resource",
                Job.status == "running",
                Job.lease_until > now(),
                Run.cancel_requested_at.is_(None),
                Run.id.in_(residents) if residents else True,
            )
            .order_by(
                (Run.kind == "conversation_naming").asc(),
                interactive.desc(),
                directed.desc(),
                arrived,
                Run.created_at,
                Run.id,
            )
            .limit(1)
        )
        return first == run_id


@asynccontextmanager
async def model_permit(run_id):
    # Capacity is an installation resource, not a connection name. Two aliases
    # (localhost / 127.0.0.1, or two model IDs) can address the same GPU engine.
    key = 82139706
    dsn = get_settings().database_dsn().replace("postgresql+psycopg://", "postgresql://", 1)
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as connection:
        await asyncio.to_thread(status, run_id, True)
        while True:
            if await asyncio.to_thread(queue_turn, run_id):
                cursor = await connection.execute("SELECT pg_try_advisory_lock(%s)", (key,))
                if (await cursor.fetchone())[0]:
                    break
            await asyncio.sleep(0.1)
        await asyncio.to_thread(status, run_id, False)
        cursor = await connection.execute("SELECT pg_backend_pid()")
        pid = (await cursor.fetchone())[0]
        parent = asyncio.current_task()
        lost = False

        async def monitor():
            nonlocal lost
            while True:
                await asyncio.sleep(1)
                try:
                    cursor = await connection.execute("SELECT pg_backend_pid()")
                    if (await cursor.fetchone())[0] == pid:
                        continue
                except Exception:
                    pass
                lost = True
                parent.cancel()
                return

        watcher = asyncio.create_task(monitor())
        try:
            yield
        except asyncio.CancelledError:
            if lost:
                raise AppError(
                    "model_resource_lease_lost",
                    "模型资源锁连接中断，已停止请求；本次用量可能未知。",
                    409,
                    "resource",
                ) from None
            raise
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            if not connection.closed:
                await connection.execute("SELECT pg_advisory_unlock(%s)", (key,))
