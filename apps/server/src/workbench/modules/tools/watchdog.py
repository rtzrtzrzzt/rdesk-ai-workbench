"""Bound tool execution without treating cancellation as proof of rollback."""

import asyncio

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine
from workbench.modules.runs.domain import Run
from workbench.modules.runs.service import emit
from workbench.modules.tools.domain import Operation
from workbench.modules.tools.registry import TOOLS


def deadline(run_id, name, values):
    limit = max(TOOLS[name].timeout_seconds, values.get("timeout_seconds", 0) + 15)
    if name == "mcp_call":
        from workbench.modules.mcp.host import get_connection

        with Session(engine()) as db:
            run = db.get(Run, run_id)
            connection = get_connection(db, run.user_id, run.workspace_id, values["connection_id"])
            limit = max(
                limit,
                connection.configuration.get("call_timeout_seconds", 60)
                + connection.configuration.get("connect_timeout_seconds", 10)
                + 15,
            )
    return limit


def timed_out(run_id, operation_id, name):
    with Session(engine()) as db, db.begin():
        db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        operation = db.get(Operation, operation_id)
        operation.status = "outcome_unknown"
        operation.effect_status = "needs_reconciliation"
        emit(
            db,
            run_id,
            "tool.outcome_unknown",
            {
                "operation_id": operation_id,
                "tool_name": name,
                "message": "工具超过截止时间，实际执行结果需核对，不会自动重发。",
            },
        )


async def bounded(run_id, operation_id, name, values, coroutine):
    task = asyncio.create_task(coroutine)
    try:
        limit = await asyncio.to_thread(deadline, run_id, name, values)
        done, _ = await asyncio.wait({task}, timeout=limit)
        if done:
            return await task
        task.cancel()
        await asyncio.wait({task}, timeout=5)
        if task.done():
            await asyncio.gather(task, return_exceptions=True)
        else:
            task.add_done_callback(lambda item: item.exception() if not item.cancelled() else None)
        await asyncio.to_thread(timed_out, run_id, operation_id, name)
        raise AppError(
            "tool_deadline",
            "工具超时，已请求停止并保留断点；实际结果需核对。",
            409,
            "outcome_unknown",
        )
    except asyncio.CancelledError:
        task.cancel()
        # Native adapters own their receipts. A cancelled to_thread call may
        # still be running; do not publish a success or replay its write.
        await asyncio.wait({task}, timeout=5)
        if task.done():
            await asyncio.gather(task, return_exceptions=True)
        else:
            task.add_done_callback(lambda item: item.exception() if not item.cancelled() else None)
        raise
