"""Shared lease, cancellation and ownership lifecycle for native workers."""

import asyncio
import logging
import os
import signal
import time
from uuid import uuid4

import psycopg

from workbench.config import get_settings
from workbench.contracts.errors import AppError
from workbench.modules.maintenance.boundary import GATE_KEY
from workbench.modules.runs import scheduler
from workbench.modules.runs.ownership import ExecutionOwner, current_owner

log = logging.getLogger("workbench.worker")


async def owns_connection(connection, backend_pid):
    cursor = await connection.execute("SELECT pg_backend_pid()")
    row = await cursor.fetchone()
    return row[0] == backend_pid


async def execute_owned(advance, worker_id, run_id, epoch, ownership, backend_pid, stop):
    token = current_owner.set(ExecutionOwner(run_id, worker_id, epoch))
    task = asyncio.create_task(advance(run_id))
    cancellation = None
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=0.5)
            try:
                same_process = await owns_connection(ownership, backend_pid)
            except Exception:
                same_process = False
            if not same_process:
                cancellation = "lease_lost"
                stop.set()
                task.cancel()
                break
            # Sync service transactions must not block the event loop: checkpoint
            # writes may hold the job row while awaiting their asynchronous commit.
            await asyncio.to_thread(scheduler.touch_worker, worker_id)
            reason = await asyncio.to_thread(scheduler.heartbeat, run_id, worker_id, epoch)
            if reason:
                cancellation = reason
                task.cancel()
                break
        result = await task
        await asyncio.to_thread(
            scheduler.finish, run_id, worker_id, epoch, waiting=bool(result.get("__interrupt__"))
        )
    except asyncio.CancelledError:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if cancellation is None:
            try:
                cancellation = await asyncio.to_thread(
                    scheduler.heartbeat, run_id, worker_id, epoch
                )
            except Exception:
                cancellation = "lease_lost"
        if cancellation is None:
            await asyncio.to_thread(scheduler.suspend, run_id, worker_id, epoch)
        elif cancellation != "lease_lost":
            await asyncio.to_thread(
                scheduler.finish, run_id, worker_id, epoch, cancellation=cancellation
            )
    except AppError as error:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if error.kind != "ownership":
            await asyncio.to_thread(scheduler.finish, run_id, worker_id, epoch, error=error)
    except Exception as error:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        log.error("Task %s failed with %s", run_id, type(error).__name__)
        await asyncio.to_thread(
            scheduler.finish,
            run_id,
            worker_id,
            epoch,
            error=AppError(
                "execution_failed",
                "执行未完成，已保留现有结果，请检查本机诊断日志。",
                500,
                "execution",
            ),
        )
    finally:
        current_owner.reset(token)


async def serve(
    queue: str,
    lock_id: int,
    advance,
    *,
    maintenance=None,
    maintenance_interval=60,
    maintenance_when_active=False,
):
    worker_id = os.environ.get("WORKBENCH_WORKER_ID") or str(uuid4())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    dsn = get_settings().database_dsn().replace("postgresql+psycopg://", "postgresql://", 1)
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as ownership:
        cursor = await ownership.execute("SELECT pg_try_advisory_lock(%s)", (lock_id,))
        if not (await cursor.fetchone())[0]:
            raise RuntimeError(f"A workbench worker already owns queue {queue}")
        cursor = await ownership.execute("SELECT pg_backend_pid()")
        backend_pid = (await cursor.fetchone())[0]
        next_maintenance = 0
        active = set()
        capacity = 4 if queue == "agent" else 1
        stopped = asyncio.create_task(stop.wait())

        async def handle(claimed):
            try:
                await execute_owned(advance, worker_id, *claimed, ownership, backend_pid, stop)
            finally:
                if not ownership.closed:
                    await ownership.execute("SELECT pg_advisory_unlock_shared(%s)", (GATE_KEY,))

        try:
            while not stop.is_set():
                completed = {task for task in active if task.done()}
                await asyncio.gather(*completed, return_exceptions=True)
                active -= completed
                if not await owns_connection(ownership, backend_pid):
                    raise RuntimeError("Worker ownership connection changed; refusing new jobs")
                if len(active) < capacity:
                    cursor = await ownership.execute(
                        "SELECT pg_try_advisory_lock_shared(%s)", (GATE_KEY,)
                    )
                    if (await cursor.fetchone())[0]:
                        claimed = None
                        try:
                            if (
                                maintenance
                                and (not active or maintenance_when_active)
                                and time.monotonic() >= next_maintenance
                            ):
                                await maintenance()
                                next_maintenance = time.monotonic() + maintenance_interval
                            await asyncio.to_thread(scheduler.touch_worker, worker_id, queue=queue)
                            claimed = await asyncio.to_thread(
                                scheduler.claim, worker_id, queue=queue
                            )
                            if claimed:
                                active.add(asyncio.create_task(handle(claimed)))
                        finally:
                            if not claimed:
                                await ownership.execute(
                                    "SELECT pg_advisory_unlock_shared(%s)", (GATE_KEY,)
                                )
                await asyncio.wait(
                    active | {stopped}, timeout=0.5, return_when=asyncio.FIRST_COMPLETED
                )
        finally:
            for task in active:
                task.cancel()
            await asyncio.gather(*active, return_exceptions=True)
            stopped.cancel()
            await asyncio.gather(stopped, return_exceptions=True)
