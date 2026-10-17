"""Native services re-read persisted authority; IPC never grants new authority."""

import asyncio
import os
import shutil
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.infrastructure.database import engine, now
from workbench.modules.automation.domain import AutomationSession, DesktopWriterLease
from workbench.modules.automation.policy import CLOSED, assert_policy, native_session
from workbench.modules.browser.dto import BrowserOwner
from workbench.modules.browser.service import BrowserService
from workbench.modules.desktop.dto import DesktopOwner
from workbench.modules.desktop.service import DesktopService


def browser_executable():
    names = (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "chrome",
        "msedge",
    )
    for name in names:
        found = shutil.which(name)
        if found and Path(found).is_file():
            # Ubuntu snap's shell stub is not an actual browser binary.
            with Path(found).open("rb") as executable:
                if executable.read(2) != b"#!":
                    return found
    candidates = [
        Path("/opt/google/chrome/chrome"),
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ]
    if sys.platform == "win32":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if os.environ.get(variable):
                candidates.append(
                    Path(os.environ[variable]) / "Google/Chrome/Application/chrome.exe"
                )
    return str(next((path for path in candidates if path.is_file()), Path("browser-not-installed")))


def publish_native(identifier, native_view):
    with Session(engine()) as db, db.begin():
        row = db.scalar(
            select(AutomationSession)
            .where(AutomationSession.id == str(identifier))
            .with_for_update()
        )
        if row is None:
            raise RuntimeError("native session reservation missing")
        row.native_id = native_view.session_id
        row.native_view = native_view.model_dump(mode="json")
        if row.status == "creating":
            try:
                assert_policy(db, row)
                row.status = "active"
            except Exception:
                row.status = "stopping"


class GovernedBrowser(BrowserService):
    async def create(self, owner, command):
        row, _, policy = await asyncio.to_thread(
            native_session, owner, command_id=command.request_id
        )
        if (
            row.kind != "browser"
            or command.allowed_origins != policy.browser_origins
            or command.local_origins != policy.browser_local_origins
        ):
            from workbench.modules.automation.policy import denied

            raise denied("浏览器网络范围与持久授权不一致。")
        # The only values entering this set were validated by a real owner API
        # update. It is not supplied by model tool arguments.
        self.trusted_local.update(policy.browser_local_origins)
        view = await super().create(owner, command)
        await asyncio.to_thread(publish_native, row.id, view)
        return view

    def _session(self, owner, session_id):
        native_session(owner, session_id)
        return super()._session(owner, session_id)


class GovernedDesktop(DesktopService):
    async def create(self, owner, command, grant):
        row, policy, config = await asyncio.to_thread(
            native_session, owner, command_id=command.request_id
        )
        if (
            row.kind != "desktop"
            or row.mode != command.mode
            or str(grant.grant_id) != policy.id
            or grant.capture != config.desktop_capture
            or grant.control != config.desktop_control
            or grant.target_window_id != config.target_window_id
            or grant.target_window_pid != config.target_window_pid
            or grant.expires_at != policy.grant_expires_at
            or command.manual_takeover_ack != config.manual_takeover_ack
        ):
            from workbench.modules.automation.policy import denied

            raise denied("桌面捕获、控制、窗口或接管范围与持久授权不一致。")
        view = await super().create(owner, command, grant)
        await asyncio.to_thread(publish_native, row.id, view)
        return view

    def _get(self, owner, sid):
        native_session(owner, sid)
        return super()._get(owner, sid)


def lifecycle_rows(nonce):
    with Session(engine()) as db:
        result = []
        for row in db.scalars(
            select(AutomationSession).where(
                AutomationSession.runtime_nonce == nonce, AutomationSession.status.not_in(CLOSED)
            )
        ):
            try:
                assert_policy(db, row)
                reason = None
            except Exception:
                reason = "authority_expired_or_stopped"
            result.append(
                (row.id, row.native_id, row.kind, row.user_id, row.workspace_id, row.run_id, reason)
            )
        return result


def confirm_closed(identifier, result):
    closed = bool(
        getattr(result, "owned_resources_closed", False)
        or getattr(result, "owned_inputs_released", False)
    )
    if getattr(result, "status", None) == "stopping":
        closed = False
    with Session(engine()) as db, db.begin():
        row = db.scalar(
            select(AutomationSession).where(AutomationSession.id == identifier).with_for_update()
        )
        row.status = result.status if closed else "stopping"
        row.native_view = {**row.native_view, **result.model_dump(mode="json")}
        lease = db.get(DesktopWriterLease, "local-desktop", with_for_update=True)
        if closed and lease and lease.session_id == row.id:
            lease.session_id, lease.revoked, lease.expires_at = None, True, now()


async def reap(browser, desktop, nonce, stop):
    while not stop.is_set():
        for identifier, native_id, kind, user, workspace, run, reason in await asyncio.to_thread(
            lifecycle_rows, nonce
        ):
            service = browser if kind == "browser" else desktop
            if not native_id or native_id not in service.sessions:
                continue  # Absence of an in-memory object is never proof of closure.
            native = service.sessions[native_id]
            status = native.view.status
            if reason or status in {"stopping", "expired", "taken_over", "stopped"}:
                owner_type = BrowserOwner if kind == "browser" else DesktopOwner
                owner = owner_type(user_id=user, workspace_id=workspace, run_id=run)
                try:
                    result = await service.stop(owner, native_id)
                    await asyncio.to_thread(confirm_closed, identifier, result)
                except Exception:
                    pass
        try:
            await asyncio.wait_for(stop.wait(), 0.5)
        except TimeoutError:
            pass
