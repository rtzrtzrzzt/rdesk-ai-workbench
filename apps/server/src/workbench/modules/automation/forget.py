"""Forget only an already-closed owned session; never stop a native runtime."""

import asyncio
import shutil
import stat
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.config import get_settings
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine
from workbench.modules.automation.domain import AutomationSession, DesktopWriterLease
from workbench.modules.automation.policy import CLOSED


class SessionForgotten(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    status: Literal["forgotten"] = "forgotten"


def _denied():
    return AppError(
        "automation_forget_not_ready", "仅能清除归属已确认、资源已关闭的原生会话。", 409, "policy"
    )


def assert_closed(row):
    if row.status not in CLOSED or not row.run_id or not row.native_id:
        raise _denied()
    view = row.native_view or {}
    if view.get("session_id") != row.native_id:
        raise _denied()
    if row.kind == "browser":
        ready = view.get("owned_resources_closed") is True
    elif row.kind == "desktop":
        ready = (
            view.get("owned_inputs_released") is True and view.get("pending_native_call") is False
        )
    else:
        ready = False
    if not ready:
        raise _denied()


def assert_native_forget(owner, native_id, kind):
    """Companion-side durable proof, separate from authority to perform actions."""
    from workbench.modules.identity import User
    from workbench.modules.runs.domain import Run
    from workbench.modules.workspaces import Workspace

    with Session(engine()) as db:
        row = db.scalar(
            select(AutomationSession)
            .join(User, User.id == AutomationSession.user_id)
            .join(Workspace, Workspace.id == AutomationSession.workspace_id)
            .join(Run, Run.id == AutomationSession.run_id)
            .where(
                AutomationSession.native_id == str(native_id),
                AutomationSession.kind == kind,
                AutomationSession.user_id == str(owner.user_id),
                AutomationSession.workspace_id == str(owner.workspace_id),
                AutomationSession.run_id == str(owner.run_id),
                User.owner_slot == 1,
                Workspace.user_id == User.id,
                Run.user_id == User.id,
                Run.workspace_id == Workspace.id,
            )
        )
        if row is None:
            raise _denied()
        assert_closed(row)
        if (
            db.scalar(
                select(DesktopWriterLease.node_id).where(DesktopWriterLease.session_id == row.id)
            )
            is not None
        ):
            raise _denied()


def remove_private_directory(path: Path):
    """Unlink a known session directory without opening or decoding its contents."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or (getattr(info, "st_file_attributes", 0) & 0x400)
    ):
        raise _denied()
    try:
        shutil.rmtree(path)
    except OSError:
        raise AppError(
            "automation_forget_files_pending",
            "原生会话私有文件尚未清理完成，请核对后重试。",
            409,
            "dependency",
        ) from None


def remove_session_files(root, owner, native_id, kind):
    # UUIDs make each path a single, exact owner/session namespace component.
    from workbench.modules.browser.service import private_path

    if kind not in {"desktop", "browser"}:
        raise _denied()
    root = Path(root).absolute()
    for component in [root, *root.parents]:
        if component.is_symlink() or (
            component.exists() and getattr(component.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise _denied()
    sid = str(UUID(str(native_id)))
    user = str(UUID(str(owner.user_id)))
    relative = (
        f"desktop/observations/{user}/{sid}" if kind == "desktop" else f"browser/profiles/{sid}"
    )
    remove_private_directory(private_path(Path(root).absolute(), relative))


def _runtime_absent():
    import psutil

    from workbench.infrastructure.native_process import matches
    from workbench.modules.automation.runtime import state

    record = state()  # Read only: runtime.status() may signal and is forbidden here.
    if record is None:
        return True
    if matches(record):
        return False
    pid = record.get("pid")
    if type(pid) is not int or pid <= 1:
        return False
    try:
        psutil.Process(pid)
    except psutil.NoSuchProcess:
        return True
    except psutil.Error:
        pass
    return False


async def forget_closed_session(row: AutomationSession) -> SessionForgotten:
    """Call before deleting the durable row, while its closed proof still exists."""
    from workbench.modules.automation.transport import inspect_endpoint
    from workbench.modules.browser.client import BrowserClient
    from workbench.modules.browser.dto import BrowserOwner
    from workbench.modules.desktop.client import DesktopClient
    from workbench.modules.desktop.dto import DesktopOwner

    assert_closed(row)
    owner_type = BrowserOwner if row.kind == "browser" else DesktopOwner
    owner = owner_type(user_id=row.user_id, workspace_id=row.workspace_id, run_id=row.run_id)
    await asyncio.to_thread(assert_native_forget, owner, row.native_id, row.kind)
    root = get_settings().data_dir
    endpoint = await asyncio.to_thread(inspect_endpoint, root, row.kind)
    if endpoint["status"] == "unavailable" or (
        endpoint["status"] == "missing" and await asyncio.to_thread(_runtime_absent)
    ):
        # Closed resource receipts are mandatory even if the old process has
        # exited. Missing metadata alone does not establish process absence.
        await asyncio.to_thread(remove_session_files, root, owner, row.native_id, row.kind)
        return SessionForgotten(session_id=row.native_id)
    if endpoint["status"] != "bound":
        raise AppError(
            "automation_forget_unavailable",
            "原生端点状态无法确认，保留会话待核对清理。",
            409,
            "dependency",
        )
    client = BrowserClient(root) if row.kind == "browser" else DesktopClient(root)
    receipt = await client.forget(owner, row.native_id)
    if receipt.session_id != row.native_id:
        raise AppError(
            "automation_forget_receipt_mismatch",
            "原生会话清理回执不匹配，请核对后重试。",
            409,
            "dependency",
        )
    return receipt
