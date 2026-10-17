import asyncio
import logging
import os

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from sqlalchemy.orm import Session
from workbench.composition import tables  # noqa: F401
from workbench.config import get_settings
from workbench.infrastructure.checkpoints import FencedPostgresSaver
from workbench.infrastructure.database import engine
from workbench.modules.agent_runtime.graph import build_graph
from workbench.modules.interactions import Approval, Question
from workbench.modules.runs import scheduler
from workbench.modules.runs.executor import serve


def resolved_interaction(payload) -> tuple[bool, object]:
    with Session(engine()) as db:
        if payload["kind"] == "agents":
            from workbench.modules.collaboration.service import wait_ready

            return wait_ready(db, payload["run_id"]), True
        if payload["kind"] == "question":
            question = db.get(Question, payload["question_id"])
            return question.status == "answered", question.answer[
                "value"
            ] if question.answer else None
        if payload["kind"] == "questions":
            return all(
                db.get(Question, question_id).status == "answered"
                for question_id in payload["question_ids"]
            ), True
        approval = db.get(Approval, payload["approval_id"])
        return approval.status in {"approved", "rejected"}, approval.approved


async def advance(graph, run_id: str) -> dict:
    config = {"configurable": {"thread_id": run_id}, "recursion_limit": 160}
    await asyncio.to_thread(scheduler.recover_operations, run_id)
    snapshot = await graph.aget_state(config)
    if snapshot.values:
        if snapshot.interrupts:
            resolved, resume = await asyncio.to_thread(
                resolved_interaction, snapshot.interrupts[0].value
            )
            if not resolved:
                return {"__interrupt__": snapshot.interrupts}
            return await graph.ainvoke(Command(resume=resume), config, durability="sync")
        if not snapshot.next:
            from workbench.modules.collaboration.inbox import has_pending_inputs

            def pending_input():
                with Session(engine()) as db:
                    return has_pending_inputs(db, run_id)

            if await asyncio.to_thread(pending_input):
                # A new input can commit after the END checkpoint but before
                # scheduler.finish. Resume the retained round, never step zero.
                return await graph.ainvoke(snapshot.values, config, durability="sync")
            return snapshot.values
        return await graph.ainvoke(None, config, durability="sync")
    initial = await asyncio.to_thread(scheduler.initial_state, run_id)
    return await graph.ainvoke(initial, config, durability="sync")


async def main_async():
    dsn = get_settings().database_dsn()
    if not dsn:
        raise RuntimeError("Database is not configured")
    dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        graph = build_graph(FencedPostgresSaver(saver.conn, dsn=dsn))

        async def handle(run_id):
            return await advance(graph, run_id)

        from workbench.modules.mcp.execution import serve_cleanup
        from workbench.modules.processes.service import serve_cleanup as command_cleanup

        cleanup_stop = asyncio.Event()
        cleanup_task = asyncio.create_task(serve_cleanup(cleanup_stop))
        commands_task = asyncio.create_task(command_cleanup(cleanup_stop))
        try:
            await serve("agent", 82139702, handle)
        finally:
            cleanup_stop.set()
            await commands_task
            report = await cleanup_task
            if report["pending"]:
                logging.getLogger("workbench.worker").warning(
                    "MCP resource closure remains unconfirmed: %s", report["pending"]
                )


def main():
    logging.basicConfig(level=logging.INFO)
    os.environ["LANGSMITH_TRACING"] = "false"
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
