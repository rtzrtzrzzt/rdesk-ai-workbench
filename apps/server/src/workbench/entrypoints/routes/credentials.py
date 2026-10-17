from fastapi import APIRouter
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import DB, USER
from workbench.modules.credentials import dto, service

router = APIRouter(prefix="/api/v1/vault", responses=ERROR_RESPONSES)


@router.get("/status", response_model=dto.VaultView)
def status(db: DB, user: USER):
    return service.status(db)


@router.post("/initialize", response_model=dto.VaultView)
def initialize(command: dto.VaultInitialize, db: DB, user: USER):
    result = service.manage(db, user.id, "initialize", command)
    db.commit()
    return result


@router.post("/unlock", response_model=dto.VaultView)
def unlock(command: dto.VaultUnlock, db: DB, user: USER):
    result = service.manage(db, user.id, "unlock", command)
    db.commit()
    return result


@router.post("/lock", response_model=dto.VaultView)
def lock(command: dto.VaultCommand, db: DB, user: USER):
    result = service.manage(db, user.id, "lock", command)
    db.commit()
    return result


@router.post("/migrate-legacy", response_model=dto.VaultView)
def migrate_legacy(command: dto.VaultCommand, db: DB, user: USER):
    result = service.manage(db, user.id, "migrate-legacy", command)
    db.commit()
    return result
