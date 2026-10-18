"""Trusted async client: in-process native service or private local UDS transport."""

import asyncio
from pathlib import Path

from workbench.contracts.errors import AppError
from workbench.modules.browser.client import BrowserClient
from workbench.modules.desktop.dto import (
    DesktopActionResult,
    DesktopCapability,
    DesktopObservation,
    DesktopObserve,
    DesktopSession,
    DesktopStop,
)


class DesktopClient:
    def __init__(self, data_dir: Path | None = None, *, service=None):
        self.service = service
        self.wire = None
        if service is None:
            if data_dir is None:
                raise ValueError("data_dir or trusted local service required")
            # Reuse existing bounded private-UDS transport without cookies/retries.
            self.wire = BrowserClient(data_dir, timeout_seconds=12, endpoint_name="desktop")
            self.wire.socket_path = Path(data_dir).absolute() / "desktop" / "companion.sock"

    async def _request(self, *args, **kwargs):
        try:
            return await self.wire._request(*args, **kwargs)
        except AppError as exc:
            if not exc.code.startswith("browser_"):
                raise
            raise AppError(
                exc.code.replace("browser_", "desktop_", 1),
                exc.message.replace("浏览器", "桌面"),
                exc.status,
                exc.kind,
                False,
                exc.details,
            ) from None

    async def probe(self):
        if self.service:
            return await self.service.probe()
        return await self._request("GET", "/capability", None, DesktopCapability)

    async def create(self, owner, command, grant):
        if self.service:
            return await self.service.create(owner, command, grant)
        return await self._request(
            "POST",
            "/sessions",
            {
                "owner": owner.model_dump(mode="json"),
                "command": command.model_dump(mode="json"),
                "grant": grant.model_dump(mode="json"),
            },
            DesktopSession,
        )

    async def observe(self, owner, sid, command=None):
        command = command or DesktopObserve()
        if self.service:
            return await self.service.observe(owner, sid, command)
        return await self._request(
            "POST",
            f"/sessions/{sid}/observe",
            {"owner": owner.model_dump(mode="json"), "command": command.model_dump(mode="json")},
            DesktopObservation,
        )

    async def action(self, owner, command, lease):
        if self.service:
            return await self.service.action(owner, command, lease)
        try:
            return await self._request(
                "POST",
                "/actions",
                {
                    "owner": owner.model_dump(mode="json"),
                    "command": command.model_dump(mode="json"),
                    "lease": lease.model_dump(mode="json"),
                },
                DesktopActionResult,
                effect=True,
            )
        except asyncio.CancelledError:
            try:
                await asyncio.shield(asyncio.wait_for(self.stop(owner, str(command.session_id)), 3))
            except Exception:
                pass
            raise

    async def stop(self, owner, sid, *, takeover=False):
        if self.service:
            return await self.service.stop(owner, sid, takeover=takeover)
        return await self._request(
            "POST",
            f"/sessions/{sid}/{'takeover' if takeover else 'stop'}",
            owner.model_dump(mode="json"),
            DesktopStop,
        )

    async def forget(self, owner, session_id):
        from workbench.modules.automation.forget import SessionForgotten

        if self.service:
            return await self.service.forget(owner, session_id)
        return await self._request(
            "POST",
            f"/sessions/{session_id}/forget",
            owner.model_dump(mode="json"),
            SessionForgotten,
        )
