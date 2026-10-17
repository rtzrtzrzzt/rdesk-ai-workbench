from uuid import UUID

from fastapi import APIRouter, Query
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import SNAPSHOT, USER
from workbench.modules.analytics import dto, service

router = APIRouter(prefix="/api/v1/analytics", responses=ERROR_RESPONSES)


@router.get("/summary", response_model=dto.AnalyticsSummary)
def summary(
    db: SNAPSHOT,
    user: USER,
    workspace_id: UUID | None = None,
    window: dto.Window = "24h",
    model_connection_id: UUID | None = None,
):
    return service.summary(
        db,
        user.id,
        str(workspace_id) if workspace_id else None,
        window,
        str(model_connection_id) if model_connection_id else None,
    )


@router.get("/calls", response_model=dto.AnalyticsCalls)
def calls(
    db: SNAPSHOT,
    user: USER,
    workspace_id: UUID | None = None,
    window: dto.Window = "24h",
    model_connection_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=4096),
):
    return service.calls(
        db,
        user.id,
        str(workspace_id) if workspace_id else None,
        window,
        str(model_connection_id) if model_connection_id else None,
        limit,
        cursor,
    )


@router.get("/hardware", response_model=dto.HardwareSnapshot)
def hardware(user: USER):
    return service.hardware()
