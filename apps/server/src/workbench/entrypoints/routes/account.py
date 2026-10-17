"""Authenticated account controls; no HTTP owner-recovery endpoint exists."""

from fastapi import APIRouter, Request, Response
from workbench.contracts.account import (
    AccountActivity,
    AccountSecurity,
    PasswordChange,
    PasswordChangeReceipt,
)
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import COOKIE, DB, USER
from workbench.modules import identity_service

router = APIRouter(prefix="/api/v1/auth", responses=ERROR_RESPONSES)


@router.get("/security", response_model=AccountSecurity)
def security(request: Request, db: DB, user: USER):
    return identity_service.security_status(db, request.cookies.get(COOKIE))


@router.post("/activity", status_code=204)
def activity(command: AccountActivity, request: Request, db: DB, user: USER):
    identity_service.record_activity(db, request.cookies.get(COOKIE))
    db.commit()
    return Response(status_code=204)


@router.post("/password", response_model=PasswordChangeReceipt)
def password(command: PasswordChange, request: Request, db: DB, user: USER):
    result = identity_service.change_password(db, request.cookies.get(COOKIE), command)
    db.commit()
    return result
