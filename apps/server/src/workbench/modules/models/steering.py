"""Interrupt only unfinished generation; committed tools keep their own lifecycle."""

import asyncio

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
    async def watch():
        while True:
            await asyncio.sleep(0.25)
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
                raise AppError(reason, "已收到新要求，正在调整当前回复。", kind="steering")
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
