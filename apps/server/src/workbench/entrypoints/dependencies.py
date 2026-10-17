"""HTTP session and identity dependencies shared by feature routers."""

import hmac
from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine
from workbench.infrastructure.secrets import csrf_token
from workbench.modules import identity_service
from workbench.modules.identity import User

COOKIE = "workbench_session"
WRITES = {"POST", "PUT", "PATCH", "DELETE"}


def _session(*, snapshot: bool = False) -> Generator[Session, None, None]:
    bind = engine().execution_options(isolation_level="REPEATABLE READ") if snapshot else engine()
    with Session(bind, expire_on_commit=False) as db:
        try:
            yield db
        except Exception:
            db.rollback()
            raise


def get_db():
    yield from _session()


def snapshot_db():
    # Authorization and all returned pieces share one MVCC snapshot.
    yield from _session(snapshot=True)


DB = Annotated[Session, Depends(get_db)]
SNAPSHOT = Annotated[Session, Depends(snapshot_db)]


def authenticate(request: Request, db: Session) -> User:
    token = request.cookies.get(COOKIE)
    user, _ = identity_service.authenticate(db, token)
    if request.method in WRITES:
        supplied = request.headers.get("x-csrf-token", "")
        expected = csrf_token(token)
        if not supplied.isascii() or not hmac.compare_digest(supplied, expected):
            raise AppError("csrf_failed", "页面校验已失效，请刷新后重试。", 403, "policy")
    return user


def current_user(request: Request, db: DB) -> User:
    return authenticate(request, db)


USER = Annotated[User, Depends(current_user)]
