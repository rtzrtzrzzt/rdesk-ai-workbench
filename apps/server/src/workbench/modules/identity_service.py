"""Durable single-owner local identity and random, revocable browser sessions."""

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from workbench.config import get_settings
from workbench.contracts.account import AccountSecurity
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import now
from workbench.modules.identity import AuthSession, User


def idle_timeout_seconds() -> int:
    value = get_settings().session_idle_seconds
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 86400:
        raise AppError("invalid_account_policy", "会话空闲时限配置无效。", 503, "configuration")
    return value


def has_owner(db: Session) -> bool:
    return db.scalar(select(User.id).limit(1)) is not None


def ensure_owner(db: Session) -> User:
    # Serialize first startup and concurrent bootstrap requests without replacing
    # an existing owner's ID, to preserve all ownership foreign keys.
    db.execute(text("SET LOCAL lock_timeout = '5s'"))
    db.execute(text("SELECT pg_advisory_xact_lock(82139701)"))
    owners = list(db.scalars(select(User).limit(2)))
    if owners:
        if len(owners) != 1 or owners[0].owner_slot != 1:
            raise AppError("owner_integrity_conflict", "本机所有者记录不符合单所有者约束。", 409)
        return owners[0]
    user = User(username="本机所有者")
    db.add(user)
    db.flush()
    from workbench.modules.models.domain import ModelConnection
    from workbench.modules.models.managed import default_connection

    if deployment := default_connection():
        db.add(ModelConnection(user_id=user.id, **deployment))
    return user


def bootstrap(db: Session, token: str | None) -> tuple[User, str]:
    # Bootstrap is reachable only after the HTTP boundary verifies the actual
    # loopback peer and browser origin. Reuse a valid session; never revive one.
    owner = ensure_owner(db)
    if token:
        try:
            user, _ = authenticate(db, token)
            if user.id == owner.id:
                return user, token
        except AppError as error:
            if error.status != 401:
                raise
    return owner, new_session(db, owner)


def new_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(48)
    moment = now()
    db.add(
        AuthSession(
            user_id=user.id,
            token_digest=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=moment + timedelta(hours=get_settings().session_hours),
            last_active_at=moment,
        )
    )
    db.flush()
    return token


def _session_conditions(token: str | None, moment):
    if not token:
        raise AppError("login_required", "请重新连接本机工作台。", 401, "authentication")
    return (
        AuthSession.token_digest == hashlib.sha256(token.encode()).hexdigest(),
        AuthSession.revoked_at.is_(None),
        AuthSession.expires_at > moment,
        func.coalesce(AuthSession.last_active_at, AuthSession.created_at)
        > moment - timedelta(seconds=idle_timeout_seconds()),
    )


def _valid_session_query(token: str | None, moment):
    return select(AuthSession).where(*_session_conditions(token, moment))


def authenticate(db: Session, token: str | None) -> tuple[User, AuthSession]:
    # Reads, polling and SSE batches do not renew inactivity or absolute expiry.
    session = db.scalar(
        _valid_session_query(token, now()).execution_options(populate_existing=True)
    )
    if session is None:
        raise AppError("session_expired", "本机会话已失效，请重新连接。", 401, "authentication")
    user = db.get(User, session.user_id)
    if user is None:
        raise AppError("login_required", "请重新连接本机工作台。", 401, "authentication")
    return user, session


def logout(db: Session, token: str | None) -> None:
    _, auth = authenticate(db, token)
    auth.revoked_at = now()


def security_status(db: Session, token: str | None) -> AccountSecurity:
    _, auth = authenticate(db, token)
    return AccountSecurity(
        idle_timeout_seconds=idle_timeout_seconds(),
        session_expires_at=auth.expires_at,
        last_active_at=auth.last_active_at or auth.created_at,
    )


def record_activity(db: Session, token: str | None) -> None:
    db.execute(text("SET LOCAL lock_timeout = '5s'"))
    # The client must send this explicitly for real user interaction. A single
    # conditional UPDATE cannot revive a concurrently expired/revoked session.
    moment = now()
    affected = db.execute(
        update(AuthSession)
        .where(*_session_conditions(token, moment))
        .values(last_active_at=moment)
        .returning(AuthSession.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if affected is None:
        raise AppError("session_expired", "本机会话已失效，请重新连接。", 401, "authentication")
