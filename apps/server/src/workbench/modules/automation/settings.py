"""User-wide live permission; never frozen into an individual task."""

from sqlalchemy import select

from workbench.contracts.errors import AppError
from workbench.modules.automation.domain import AutomationSettings
from workbench.modules.identity import User
from workbench.modules.runs.service import idempotent


def enabled(db, user_id):
    value = db.scalar(
        select(AutomationSettings.enabled).where(AutomationSettings.user_id == user_id)
    )
    return True if value is None else value


def view(db, user_id):
    row = db.scalar(select(AutomationSettings).where(AutomationSettings.user_id == user_id))
    return {"enabled": row.enabled if row else True, "version": row.version if row else 0}


def save(db, user_id, command):
    def execute():
        db.scalar(select(User).where(User.id == user_id).with_for_update())
        row = db.scalar(select(AutomationSettings).where(AutomationSettings.user_id == user_id))
        if (row.version if row else 0) != command.expected_version:
            raise AppError("computer_settings_stale", "电脑操作设置已更新，请重新读取。", 409)
        if row is None:
            row = AutomationSettings(user_id=user_id, enabled=command.enabled)
            db.add(row)
        else:
            row.enabled = command.enabled
            row.version += 1
        if not command.enabled:
            from workbench.modules.automation.service import stop_sessions

            stop_sessions(db, user_id)
        db.flush()
        return view(db, user_id)

    return idempotent(
        db,
        user_id,
        str(command.request_id),
        "automation:settings",
        command.model_dump(mode="json"),
        execute,
    )
