"""Generation permits are independent from task count and human/tool waiting."""

import asyncio
from contextlib import asynccontextmanager

import psycopg
from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.config import get_settings
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine
from workbench.modules.runs.domain import Run
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


@asynccontextmanager
async def model_permit(run_id):
    # Capacity is an installation resource, not a connection name. Two aliases
    # (localhost / 127.0.0.1, or two model IDs) can address the same GPU engine.
    key = 82139706
    dsn = get_settings().database_dsn().replace("postgresql+psycopg://", "postgresql://", 1)
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as connection:
        waiting = False
        while True:
            cursor = await connection.execute("SELECT pg_try_advisory_lock(%s)", (key,))
            if (await cursor.fetchone())[0]:
                break
            if not waiting:
                await asyncio.to_thread(status, run_id, True)
                waiting = True
            await asyncio.sleep(0.25)
        if waiting:
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
