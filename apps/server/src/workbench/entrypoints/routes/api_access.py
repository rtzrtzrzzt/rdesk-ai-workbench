from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select
from workbench.contracts.requests import Command
from workbench.entrypoints.dependencies import DB, USER
from workbench.infrastructure.database import now
from workbench.modules.access import dto, service
from workbench.modules.access.domain import ApiClient, ApiKey, Grant

router = APIRouter(prefix="/api/v1/api-clients")


class RevokeKey(Command):
    request_id: UUID


@router.get("", response_model=list[dto.ClientView])
def clients(db: DB, user: USER):
    return [
        service.client_view(row)
        for row in db.scalars(
            select(ApiClient)
            .where(ApiClient.owner_user_id == user.id)
            .order_by(ApiClient.created_at)
        )
    ]


@router.post("", response_model=dto.ClientView)
def create(command: dto.ClientCreate, db: DB, user: USER):
    result = service.create_client(db, user.id, command)
    db.commit()
    return result


@router.post("/{client_id}", response_model=dto.ClientView)
def save(client_id: UUID, command: dto.ClientSave, db: DB, user: USER):
    from workbench.contracts.errors import AppError

    row = service.owned_client(db, user.id, str(client_id), lock=True)
    if row.version != command.expected_version:
        raise AppError("version_conflict", "调用方已更新，请读取最新版本。", 409)
    row.enabled = command.enabled
    row.requests_per_minute, row.max_active_runs = (
        command.requests_per_minute,
        command.max_active_runs,
    )
    row.version += 1
    result = service.client_view(row)
    db.commit()
    return result


@router.get("/{client_id}/keys", response_model=list[dto.KeyView])
def keys(client_id: UUID, db: DB, user: USER):
    service.owned_client(db, user.id, str(client_id))
    return [
        service.key_view(row)
        for row in db.scalars(
            select(ApiKey)
            .where(ApiKey.client_id == str(client_id))
            .order_by(ApiKey.created_at.desc())
        )
    ]


@router.post("/{client_id}/keys", response_model=dto.IssuedKey)
def issue(client_id: UUID, command: dto.KeyIssue, db: DB, user: USER):
    result = service.issue_key(db, user.id, str(client_id), command)
    db.commit()
    return result


@router.post("/{client_id}/keys/{key_id}/revoke", response_model=dto.KeyView)
def revoke(client_id: UUID, key_id: UUID, command: RevokeKey, db: DB, user: USER):
    from workbench.contracts.errors import AppError

    service.owned_client(db, user.id, str(client_id), lock=True)
    row = db.get(ApiKey, str(key_id))
    if row is None or row.client_id != str(client_id):
        raise AppError("api_key_not_found", "密钥不存在。", 404)
    row.revoked_at = row.revoked_at or now()
    result = service.key_view(row)
    db.commit()
    return result


@router.get("/{client_id}/grants", response_model=list[dto.GrantView])
def grants(client_id: UUID, db: DB, user: USER):
    service.owned_client(db, user.id, str(client_id))
    return [
        service.grant_view(row)
        for row in db.scalars(
            select(Grant).where(Grant.client_id == str(client_id)).order_by(Grant.created_at)
        )
    ]


@router.post("/{client_id}/grants/{service_id}", response_model=dto.GrantView)
def grant(client_id: UUID, service_id: UUID, command: dto.GrantSave, db: DB, user: USER):
    result = service.save_grant(db, user.id, str(client_id), str(service_id), command)
    db.commit()
    return result
