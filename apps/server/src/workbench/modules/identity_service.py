"""Single-owner authentication; only login owns an independent durable transaction."""

import hashlib
import hmac
import json
import math
import secrets
from datetime import timedelta
from uuid import uuid4

from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from workbench.config import get_settings
from workbench.contracts.account import (
    AccountRecoveryReceipt,
    AccountSecurity,
    PasswordChange,
    PasswordChangeReceipt,
)
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import now
from workbench.infrastructure.secrets import vault_key
from workbench.modules.identity import AccountCommandReceipt, AuthSession, LoginThrottle, User

passwords = PasswordHash.recommended()
# Unknown names still perform the same configured Argon2 verification work.
_DUMMY_HASH = passwords.hash(secrets.token_urlsafe(48))


def _positive_setting(name: str, default: int, maximum: int) -> int:
    value = getattr(get_settings(), name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise AppError("invalid_account_policy", "账号安全时限配置无效。", 503, "configuration")
    return value


def idle_timeout_seconds() -> int:
    return _positive_setting("session_idle_seconds", 1800, 86400)


def _login_policy() -> tuple[int, int]:
    return (
        _positive_setting("login_max_failures", 10, 1000),
        _positive_setting("login_window_seconds", 900, 86400),
    )


def has_owner(db: Session) -> bool:
    return db.scalar(select(User.id).limit(1)) is not None


def _new_password(value: str) -> None:
    if not isinstance(value, str) or not 12 <= len(value) <= 1024:
        raise AppError("invalid_password", "新密码需要 12 至 1024 个字符。", 422)


def create_owner(db: Session, username: str, password: str) -> tuple[User, str]:
    _new_password(password)
    db.execute(text("SELECT pg_advisory_xact_lock(82139701)"))
    if has_owner(db):
        raise AppError("already_configured", "工作台已经初始化，请登录。", 409)
    user = User(username=username, password_hash=passwords.hash(password))
    db.add(user)
    db.flush()
    # A downloaded deployment is a real connection candidate, not a verified capability.
    from workbench.modules.models.domain import ModelConnection
    from workbench.modules.models.managed import default_connection

    if deployment := default_connection():
        db.add(ModelConnection(user_id=user.id, **deployment))
    return user, new_session(db, user)


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


def _verify(password: str, encoded: str) -> tuple[bool, str | None]:
    try:
        return passwords.verify_and_update(password, encoded)
    except UnknownHashError:
        # A damaged/unusable stored hash is not an alternate account oracle.
        passwords.verify(password, _DUMMY_HASH)
        return False, None


def _login_key(username: str, peer: str | None) -> str:
    # Only the actual socket peer supplied by trusted HTTP code; never X-Forwarded-For.
    raw = json.dumps([peer if peer is not None else "unknown", username], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _prune_throttles(bind, cutoff) -> None:
    # Separate from the credential transaction: do not introduce inverse row-lock
    # ordering between concurrent usernames. Never wait on an in-flight attempt.
    try:
        with Session(bind) as cleanup, cleanup.begin():
            cleanup.execute(text("SET LOCAL lock_timeout = '1s'"))
            expired = list(
                cleanup.scalars(
                    select(LoginThrottle.key_digest)
                    .where(LoginThrottle.window_started_at < cutoff)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            )
            if expired:
                cleanup.execute(delete(LoginThrottle).where(LoginThrottle.key_digest.in_(expired)))
    except SQLAlchemyError:
        pass  # Opportunistic bounded cleanup cannot undo the committed attempt.


def login(
    db: Session, username: str, password: str, *, peer: str | None = None
) -> tuple[User, str]:
    """Commit throttle/session atomically before returning or raising a 401/429.

    This entry point cannot join another write unit of work. Its caller's pure
    read transaction is released before it obtains its own connection/row locks.
    """
    if db.new or db.dirty or db.deleted:
        raise AppError("account_transaction_conflict", "请先完成当前写入再登录。", 409, "conflict")
    bind = db.get_bind()
    bind = getattr(bind, "engine", bind)
    db.rollback()
    maximum, seconds = _login_policy()
    key = _login_key(username, peer)
    failure, user_id, token = None, None, None
    with Session(bind, expire_on_commit=False) as owned, owned.begin():
        owned.execute(text("SET LOCAL lock_timeout = '5s'"))
        moment = now()
        owned.execute(
            insert(LoginThrottle)
            .values(
                key_digest=key,
                window_started_at=moment,
                failed_attempts=0,
                last_attempt_at=moment,
            )
            .on_conflict_do_nothing(index_elements=[LoginThrottle.key_digest])
        )
        throttle = owned.scalar(
            select(LoginThrottle).where(LoginThrottle.key_digest == key).with_for_update()
        )
        # Compute the clock after waiting for the lock, not before a queued attempt.
        moment = now()
        elapsed = (moment - throttle.window_started_at).total_seconds()
        if elapsed >= seconds or elapsed < 0:
            throttle.window_started_at, throttle.failed_attempts = moment, 0
            elapsed = 0
        throttle.last_attempt_at = moment
        if throttle.failed_attempts >= maximum:
            failure = AppError(
                "login_rate_limited",
                "登录尝试过于频繁，请稍后再试。",
                429,
                "authentication",
                retryable=True,
                details={"retry_after_seconds": max(1, math.ceil(seconds - elapsed))},
            )
        else:
            # Serialize login against password changes/recovery, so a verified
            # old password cannot create a session after its revocation barrier.
            user = owned.scalar(select(User).where(User.username == username).with_for_update())
            valid, upgraded = _verify(password, user.password_hash if user else _DUMMY_HASH)
            if user is None or not valid:
                throttle.failed_attempts += 1
                failure = AppError(
                    "invalid_credentials", "用户名或密码不正确。", 401, "authentication"
                )
            else:
                if upgraded:
                    user.password_hash = upgraded
                    user.version += 1
                throttle.failed_attempts = 0
                throttle.window_started_at = moment
                user_id, token = user.id, new_session(owned, user)
    _prune_throttles(bind, now() - timedelta(seconds=seconds))
    if failure:
        raise failure
    # Return the caller's identity view, never an expired/detached foreign-session ORM object.
    user = db.get(User, user_id, populate_existing=True)
    if user is None:
        raise AppError("login_required", "请重新登录工作台。", 401, "authentication")
    return user, token


def _session_conditions(token: str | None, moment):
    if not token:
        raise AppError("login_required", "请先登录工作台。", 401, "authentication")
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
        raise AppError("session_expired", "登录已失效，请重新登录。", 401, "authentication")
    user = db.get(User, session.user_id)
    if user is None:
        raise AppError("login_required", "请先登录工作台。", 401, "authentication")
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
        raise AppError("session_expired", "登录已失效，请重新登录。", 401, "authentication")


def _change_digest(auth: AuthSession, command: PasswordChange) -> str:
    # Keyed, domain-separated comparison only. Neither password nor an unkeyed
    # password fingerprint is persisted in request data, receipts or logs.
    message = json.dumps(
        [
            "workbench.password-change.v1",
            auth.id,
            str(command.request_id),
            command.current_password.get_secret_value(),
            command.new_password.get_secret_value(),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return hmac.new(vault_key(), message, hashlib.sha256).hexdigest()


def change_password(
    db: Session, token: str | None, command: PasswordChange
) -> PasswordChangeReceipt:
    db.execute(text("SET LOCAL lock_timeout = '5s'"))
    user, auth = authenticate(db, token)
    user = db.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    auth = db.scalar(
        _valid_session_query(token, now())
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if auth is None:
        raise AppError("session_expired", "登录已失效，请重新登录。", 401, "authentication")
    digest = _change_digest(auth, command)
    existing = db.scalar(
        select(AccountCommandReceipt).where(
            AccountCommandReceipt.user_id == user.id,
            AccountCommandReceipt.request_id == str(command.request_id),
        )
    )
    if existing:
        if (
            existing.operation != "password_change"
            or existing.session_id != auth.id
            or not existing.request_digest
            or not hmac.compare_digest(existing.request_digest, digest)
        ):
            raise AppError(
                "idempotency_conflict", "相同请求标识不能提交不同的密码修改。", 409, "conflict"
            )
        return PasswordChangeReceipt.model_validate(existing.result)
    valid, _ = _verify(command.current_password.get_secret_value(), user.password_hash)
    if not valid:
        raise AppError("current_password_invalid", "当前密码不正确。", 409, "authentication")
    moment = now()
    user.password_hash = passwords.hash(command.new_password.get_secret_value())
    user.version += 1
    count = db.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == user.id,
            AuthSession.id != auth.id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=moment)
        .execution_options(synchronize_session=False)
    ).rowcount
    receipt = PasswordChangeReceipt(
        request_id=command.request_id,
        password_changed=True,
        current_session_retained=True,
        other_sessions_revoked=count,
        changed_at=moment,
    )
    db.add(
        AccountCommandReceipt(
            user_id=user.id,
            session_id=auth.id,
            request_id=str(command.request_id),
            operation="password_change",
            request_digest=digest,
            result=receipt.model_dump(mode="json"),
        )
    )
    db.flush()
    return receipt


def recover_owner_password(
    db: Session, new_password: str, *, services_stopped: bool
) -> AccountRecoveryReceipt:
    """Trusted native CLI only; its caller proves actual owned services are stopped.

    A bool is an explicit caller contract, not evidence of process shutdown. This
    function exposes no HTTP reset and never unlocks or changes the secret vault.
    """
    if services_stopped is not True:
        raise AppError(
            "account_recovery_requires_stopped", "请确认本实例服务已停止再恢复账号。", 409, "policy"
        )
    _new_password(new_password)
    db.execute(text("SET LOCAL lock_timeout = '5s'"))
    owners = list(db.scalars(select(User).with_for_update().limit(2)))
    if not owners:
        raise AppError("owner_not_configured", "本机所有者尚未初始化。", 409)
    if len(owners) != 1 or owners[0].owner_slot != 1:
        raise AppError("owner_integrity_conflict", "所有者记录不符合单账号恢复条件。", 409)
    owner, moment = owners[0], now()
    owner.password_hash = passwords.hash(new_password)
    owner.version += 1
    count = db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == owner.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=moment)
        .execution_options(synchronize_session=False)
    ).rowcount
    # This is the single-owner, explicitly stopped recovery boundary. Clear
    # anonymous hashed failures too, so the recovered owner can actually sign in.
    db.execute(delete(LoginThrottle))
    receipt = AccountRecoveryReceipt(
        request_id=uuid4(),
        user_id=owner.id,
        password_changed=True,
        sessions_revoked=count,
        recovered_at=moment,
    )
    db.add(
        AccountCommandReceipt(
            user_id=owner.id,
            session_id=None,
            request_id=str(receipt.request_id),
            operation="offline_password_recovery",
            request_digest=None,
            result=receipt.model_dump(mode="json"),
        )
    )
    db.flush()
    return receipt
