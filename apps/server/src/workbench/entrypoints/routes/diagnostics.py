"""Owner-only, structurally redacted diagnostics; never export raw runtime logs."""

import asyncio

from fastapi import APIRouter, Response
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import DB, USER
from workbench.infrastructure.database import engine
from workbench.modules.diagnostics import dto, service

router = APIRouter(prefix="/api/v1/maintenance/diagnostics", responses=ERROR_RESPONSES)


@router.get("", response_model=dto.DiagnosticsSnapshot)
async def snapshot(db: DB, user: USER):
    user_id = user.id
    db.rollback()
    return await asyncio.to_thread(service.snapshot, engine(), user_id)


@router.get("/export", response_class=Response)
async def export(db: DB, user: USER):
    user_id = user.id
    db.rollback()
    payload = await asyncio.to_thread(service.export, engine(), user_id)
    return Response(
        payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="workbench-diagnostics.zip"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
