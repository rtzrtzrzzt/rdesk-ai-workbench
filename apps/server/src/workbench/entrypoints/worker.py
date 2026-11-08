import asyncio
import logging
import os

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from sqlalchemy.exc import SQLAlchemyError
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
        if payload["kind"] == "continuation":
            return True, True
        if payload["kind"] == "safety":
            from workbench.modules.runs.domain import Run

            guard = db.get(Run, payload["run_id"]).config_snapshot.get("safety", {})
            return guard.get("accepted_wait") == payload["id"], True
        if payload["kind"] in {"agents", "agent_message"}:
            from workbench.modules.collaboration.service import wait_ready

            return wait_ready(
                db, payload["run_id"], messages_only=payload["kind"] == "agent_message"
            ), True
        if payload["kind"] == "question":
            question = db.get(Question, payload["question_id"])
            # The tool reads the durable answer itself. A non-null acknowledgement
            # also resumes a superseded question; Command(resume=None) does not.
            return question.status in {"answered", "superseded"}, True
        if payload["kind"] == "questions":
            return all(
                db.get(Question, question_id).status in {"answered", "superseded"}
                for question_id in payload["question_ids"]
            ), True
        approval = db.get(Approval, payload["approval_id"])
        return approval.status in {"approved", "rejected", "superseded"}, approval.approved


async def advance(graph, run_id: str) -> dict:
    from workbench.modules.runs.domain import Run

    def job_kind():
        with Session(engine()) as db:
            return db.get(Run, run_id).kind

    kind = await asyncio.to_thread(job_kind)
    if kind == "conversation_naming":
        from workbench.modules.conversation_naming import process

        return await process(run_id)
    if kind == "context_compaction":
        from workbench.modules.context.service import process_idle

        return await process_idle(run_id)

    def stage_size():
        with Session(engine()) as db:
            return (
                db.get(Run, run_id)
                .config_snapshot.get("runtime_options", {})
                .get("checkpoint_rounds", 32)
            )

    # One model response can contain up to 128 serial tool nodes. Recursion is
    # an implementation bound for a checkpoint segment, not a user turn limit.
    config = {
        "configurable": {"thread_id": run_id},
        "recursion_limit": (await asyncio.to_thread(stage_size)) * 260 + 16,
    }
    await asyncio.to_thread(scheduler.recover_operations, run_id)
    snapshot = await graph.aget_state(config)
    if snapshot.values:
        if snapshot.interrupts:
            payload = snapshot.interrupts[0].value
            if payload.get("kind") == "approval":
                from workbench.modules.tools.review import review

                await review(run_id, payload["approval_id"])
            resolved, resume = await asyncio.to_thread(resolved_interaction, payload)
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

        async def maintain():
            from workbench.modules import conversation_deletion, conversation_queue

            def queues():
                with Session(engine()) as db, db.begin():
                    from workbench.modules.access.service import maintain as maintain_access

                    maintain_access(db)
                    conversation_queue.maintain(db)
                    from workbench.modules.tools.review import maintain as maintain_reviews

                    maintain_reviews(db)
                    from workbench.modules.context.service import maintain

                    maintain(db)
                    from workbench.modules.conversation_naming import maintain as maintain_naming

                    maintain_naming(db)

            try:
                await asyncio.to_thread(queues)
                await conversation_deletion.maintain()
            except (SQLAlchemyError, psycopg.Error):
                logging.getLogger("workbench.worker").warning(
                    "Conversation maintenance deferred: database unavailable or busy."
                )

        try:
            await serve(
                "agent",
                82139702,
                handle,
                maintenance=maintain,
                maintenance_interval=1,
                maintenance_when_active=True,
            )
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
