import asyncio
from uuid import UUID

from fastapi import APIRouter
from workbench.contracts.requests import EmptyCommand
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import DB, USER
from workbench.modules.search import dto, service

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/search", responses=ERROR_RESPONSES)


@router.get("/connection", response_model=dto.SearchConnectionView | None)
def connection(workspace_id: UUID, db: DB, user: USER, scope: dto.Scope | None = None):
    return service.get_connection(db, user.id, str(workspace_id), scope)


@router.post("/connection", response_model=dto.SearchConnectionView)
async def configure(workspace_id: UUID, command: dto.SearchConnectionSave, db: DB, user: USER):
    user_id = user.id
    db.rollback()
    result = await service.save_connection(db, user_id, str(workspace_id), command)
    await asyncio.to_thread(db.commit)
    return result


@router.get("/engines", response_model=dto.SearchEngineCatalog)
async def engines(workspace_id: UUID, db: DB, user: USER, scope: dto.Scope | None = None):
    user_id = user.id
    db.rollback()
    return await service.get_engine_catalog(
        db, user_id, str(workspace_id), dto.SearchEngineCatalogQuery(scope=scope)
    )


@router.post("/engines", response_model=dto.SearchEngineCatalog)
async def draft_engines(
    workspace_id: UUID, command: dto.SearchEngineCatalogQuery, db: DB, user: USER
):
    user_id = user.id
    db.rollback()
    return await service.get_engine_catalog(db, user_id, str(workspace_id), command)


@router.get("/capability", response_model=dto.SearchCapability)
def capability(workspace_id: UUID, db: DB, user: USER):
    return service.get_capability(db, user.id, str(workspace_id))


@router.post("/check", response_model=dto.SearchCapability)
async def check(workspace_id: UUID, command: EmptyCommand, db: DB, user: USER):
    user_id = user.id
    db.rollback()
    result = await service.check_connection(db, user_id, str(workspace_id))
    await asyncio.to_thread(db.commit)
    return result


@router.post("/query", response_model=dto.SearchResult)
async def query(workspace_id: UUID, command: dto.SearchQuery, db: DB, user: USER):
    user_id = user.id
    db.rollback()
    result = await service.search(db, user_id, str(workspace_id), command)
    await asyncio.to_thread(db.commit)
    return result


@router.post("/read", response_model=dto.WebPage)
async def read(workspace_id: UUID, command: dto.FetchPage, db: DB, user: USER):
    user_id = user.id
    db.rollback()
    result = await service.fetch_page(db, user_id, str(workspace_id), command)
    await asyncio.to_thread(db.commit)
    return result
