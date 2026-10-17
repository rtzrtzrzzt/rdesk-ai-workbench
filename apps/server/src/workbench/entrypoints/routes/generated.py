from uuid import UUID

from fastapi import APIRouter
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import SNAPSHOT, USER
from workbench.modules.generated import dto, service

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)


@router.get("/runs/{run_id}/surfaces", response_model=dto.SurfaceList)
def surfaces(run_id: UUID, db: SNAPSHOT, user: USER):
    return service.listing(db, user.id, str(run_id))


@router.get("/ui/protocol", response_model=dto.ProtocolStatus)
def protocol(user: USER):
    return dto.ProtocolStatus()
