"""Interrupt only unfinished generation; committed tools keep their own lifecycle."""

import asyncio
import time

from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine
from workbench.modules.collaboration.inbox import has_new_directive, has_new_user_input
from workbench.modules.models.providers import abort_generation, stream_model
from workbench.modules.runs.domain import Run


def input_arrived(run_id, included):
    with Session(engine()) as db:
        if has_new_directive(db, run_id, included):
            return "generation_steered"
        run = db.get(Run, run_id)
        parent = db.get(Run, run.parent_run_id) if run.parent_run_id else None
        if (
            parent
            and parent.status in {"queued", "running", "waiting_resource", "waiting_children"}
            and has_new_user_input(db, parent.id)
        ):
            return "generation_yielded"
        return None


async def events(config, messages, tools, run_id, included):
    from workbench.modules.access.service import authorize_run_model

    def authorize():
        with Session(engine()) as db:
            authorize_run_model(db, db.get(Run, run_id))

    await asyncio.to_thread(authorize)
    budget = getattr(config, "generation_budget", None)
    finish_after = budget.working_seconds if budget else None
    started = time.monotonic()

    async def watch():
        while True:
            await asyncio.sleep(0.25)
            if finish_after is not None and time.monotonic() - started >= finish_after:
                return "generation_finishing"
            reason = await asyncio.to_thread(input_arrived, run_id, included)
            if reason:
                return reason

    stream = stream_model(config, messages, tools)
    watcher = asyncio.create_task(watch())
    pending = None
    try:
        while True:
            pending = asyncio.create_task(anext(stream))
            ready, _ = await asyncio.wait((pending, watcher), return_when=asyncio.FIRST_COMPLETED)
            if watcher in ready:
                reason = watcher.result()
                # A scoped SGLang abort stops GPU decoding promptly; closing the
                # HTTP stream is the portable fallback for other providers.
                await abort_generation(config)
                message = (
                    "已进入截止前收尾阶段，正在保留现有结果。"
                    if reason == "generation_finishing"
                    else "已收到新要求，正在调整当前回复。"
                )
                raise AppError(reason, message, kind="steering")
            try:
                yield pending.result()
            except StopAsyncIteration:
                return
    finally:
        watcher.cancel()
        if pending and not pending.done():
            pending.cancel()
        await asyncio.gather(watcher, *([pending] if pending else []), return_exceptions=True)
        await stream.aclose()
