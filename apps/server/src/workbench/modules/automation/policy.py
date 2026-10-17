"""Checks at both host dispatch and native primitive boundaries."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, now
from workbench.modules.automation.domain import (
    AutomationPolicy,
    AutomationSession,
    DesktopWriterLease,
)
from workbench.modules.automation.dto import AutomationConfiguration
from workbench.modules.browser.policy import NetworkPolicy
from workbench.modules.files.inputs import is_remote
from workbench.modules.runs.domain import TERMINAL_STATUSES, Run

CLOSED = frozenset({"stopped", "taken_over", "expired"})
LIVE = frozenset({"creating", "active", "stopping", "outcome_unknown"})


def denied(message, code="automation_permission_changed"):
    return AppError(code, message, 403, "policy")


def configuration(values):
    parsed = AutomationConfiguration.model_validate(values)
    if parsed.browser_enabled:
        NetworkPolicy(
            parsed.browser_origins,
            parsed.browser_local_origins,
            set(parsed.browser_local_origins),
        )
    elif parsed.browser_origins or parsed.browser_local_origins:
        # Validate saved scopes even while disabled; enabling later changes no semantics.
        NetworkPolicy(
            parsed.browser_origins,
            parsed.browser_local_origins,
            set(parsed.browser_local_origins),
        )
    if any(
        not re.fullmatch(r"[a-f0-9-]{36}:[1-9][0-9]*", target)
        for target in parsed.remote_model_revisions
    ):
        raise denied("外发授权必须绑定确切模型连接及版本。")
    return parsed


def current(db, user_id, workspace_id):
    return db.scalar(
        select(AutomationPolicy).where(
            AutomationPolicy.user_id == str(user_id),
            AutomationPolicy.workspace_id == str(workspace_id),
        )
    )


def assert_run(db, run_id, user_id, workspace_id):
    if run_id is None:
        return None
    run = db.get(Run, str(run_id))
    if (
        run is None
        or run.user_id != str(user_id)
        or run.workspace_id != str(workspace_id)
        or run.status in TERMINAL_STATUSES
        or run.cancel_requested_at
        or run.deadline_at
        and run.deadline_at <= now()
    ):
        raise denied("所属任务已停止或身份不匹配，禁止继续操作。")
    return run


def assert_policy(db, row, *, transfer=False):
    policy = current(db, row.user_id, row.workspace_id)
    if policy is None or policy.id != row.policy_id or policy.version != row.policy_version:
        raise denied("电脑操作授权已改变；旧会话不会沿用新授权。")
    config = configuration(policy.configuration)
    run = assert_run(db, row.run_id, row.user_id, row.workspace_id)
    if row.expires_at <= now() or row.status not in {"creating", "active"}:
        raise denied("电脑操作会话已到期或停止。")
    if row.kind == "browser" and not config.browser_enabled:
        raise denied("用户未启用浏览器。")
    if row.kind == "desktop":
        if (
            not config.desktop_capture
            or policy.grant_expires_at is None
            or policy.grant_expires_at <= now()
        ):
            raise denied("屏幕捕获授权已撤销或到期。")
        if row.mode == "control" and not config.desktop_control:
            raise denied("桌面输入授权已撤销。")
    if transfer and run:
        from workbench.modules.models.domain import ModelConnection, ModelRevision

        revision = db.get(ModelRevision, run.config_snapshot.get("model_revision_id"))
        current_model = db.get(ModelConnection, run.model_connection_id)
        if revision is None or current_model is None:
            raise denied("任务的冻结模型版本不存在，不能发送电脑内容。")
        frozen = revision.configuration
        if is_remote(frozen["endpoint"]) and not current_model.allow_remote:
            raise denied("用户已撤销此外部模型的远程访问权限。")
        if is_remote(frozen["endpoint"]) and (
            f"{frozen['id']}:{frozen['version']}" not in config.remote_model_revisions
        ):
            raise denied(
                "向此外部模型发送网页或桌面内容需要用户按连接版本授权。",
                "automation_remote_consent_required",
            )
    return policy, config


def native_session(owner, native_id=None, *, command_id=None):
    with Session(engine()) as db:
        row = (
            db.get(AutomationSession, str(command_id))
            if command_id
            else db.scalar(
                select(AutomationSession).where(AutomationSession.native_id == str(native_id))
            )
        )
        if row is None or (
            row.user_id != str(owner.user_id)
            or row.workspace_id != str(owner.workspace_id)
            or row.run_id != (str(owner.run_id) if owner.run_id else None)
        ):
            raise denied("原生会话不属于当前持久身份。")
        policy, config = assert_policy(db, row)
        return row, policy, config


def assert_desktop_lease(owner, native_view, lease):
    with Session(engine()) as db:
        row = db.scalar(
            select(AutomationSession).where(
                AutomationSession.native_id == native_view.session_id,
                AutomationSession.user_id == str(owner.user_id),
                AutomationSession.workspace_id == str(owner.workspace_id),
            )
        )
        if row is None or row.run_id != (str(owner.run_id) if owner.run_id else None):
            raise denied("持久桌面归属不匹配。")
        policy, config = assert_policy(db, row)
        holder = db.get(DesktopWriterLease, "local-desktop")
        if (
            row.mode != "control"
            or not config.desktop_control
            or holder is None
            or holder.revoked
            or holder.session_id != row.id
            or holder.lease_id != str(lease.lease_id)
            or holder.epoch != lease.epoch
            or holder.expires_at <= now()
            or holder.expires_at != lease.expires_at
            or str(lease.grant_id) != policy.id
        ):
            raise denied("桌面单写租约已过期、撤销或被接管。", "desktop_lease_lost")
