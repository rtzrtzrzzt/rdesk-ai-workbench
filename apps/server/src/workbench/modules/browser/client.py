"""Async trusted-caller client. No automatic retry, endpoint override or cookie sharing."""

import asyncio
from pathlib import Path

import httpx
from pydantic import ValidationError

from workbench.contracts.errors import AppError
from workbench.modules.browser.dto import (
    BrowserAction,
    BrowserActionResult,
    BrowserCapability,
    BrowserCreate,
    BrowserDownload,
    BrowserDownloaded,
    BrowserObservation,
    BrowserObserve,
    BrowserOwner,
    BrowserSession,
    BrowserStop,
)


class BrowserClient:
    """Private identity-checked endpoint transport, with no remote URL override.

    Linux/macOS use UDS; Windows loopback transport needs actual device validation.
    The backend injects owner/granted origins/output_dir; these are not model
    authority. Operation/approval persistence remains the main executor's job.
    """

    def __init__(self, data_dir: Path, *, timeout_seconds=70, endpoint_name="browser"):
        self.data_dir = Path(data_dir).absolute()
        self.endpoint_name = endpoint_name
        self.socket_path = Path(data_dir).absolute() / "browser" / "companion.sock"
        self.timeout = min(max(timeout_seconds, 5), 90)

    async def _request(self, method, path, payload, response_type, *, effect=False):
        from workbench.modules.automation.transport import client_endpoint

        try:
            endpoint = client_endpoint(self.data_dir, self.endpoint_name)
            async with httpx.AsyncClient(
                transport=endpoint.transport,
                base_url=endpoint.base_url,
                headers=endpoint.headers,
                timeout=self.timeout,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                async with client.stream(method, path, json=payload) as response:
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > 256 * 1024:
                            raise AppError(
                                "browser_protocol_budget", "浏览器响应超过预算。", 502, "dependency"
                            )
                        content.extend(chunk)
                    import json

                    data = json.loads(content)
                    if response.status_code >= 400:
                        failure = data.get("error", {})
                        raise AppError(
                            failure.get("code", "browser_companion_error"),
                            failure.get("message", "浏览器 companion 拒绝请求。"),
                            response.status_code,
                            failure.get("kind", "dependency"),
                            False,
                        )
                    return response_type.model_validate(data)
        except asyncio.CancelledError:
            raise
        except AppError:
            raise
        except (httpx.HTTPError, ValueError, ValidationError):
            raise AppError(
                "browser_transport_unknown" if effect else "browser_companion_unavailable",
                "浏览器传输中断；动作结果不明，必须核对且不能自动重放。"
                if effect
                else "浏览器 companion 连接或协议失败。",
                502,
                "outcome_unknown" if effect else "dependency",
                False,
                {"outcome_unknown": effect},
            ) from None

    async def capability(self) -> BrowserCapability:
        return await self._request("GET", "/capability", None, BrowserCapability)

    async def create(self, owner: BrowserOwner, command: BrowserCreate) -> BrowserSession:
        return await self._request(
            "POST",
            "/sessions",
            {
                "owner": owner.model_dump(mode="json"),
                "command": command.model_dump(mode="json"),
            },
            BrowserSession,
        )

    async def observe(
        self, owner: BrowserOwner, session_id: str, command=None
    ) -> BrowserObservation:
        command = command or BrowserObserve()
        return await self._request(
            "POST",
            f"/sessions/{session_id}/observe",
            {
                "owner": owner.model_dump(mode="json"),
                "command": command.model_dump(mode="json"),
            },
            BrowserObservation,
        )

    async def action(self, owner: BrowserOwner, command: BrowserAction) -> BrowserActionResult:
        try:
            return await self._request(
                "POST",
                "/actions",
                {
                    "owner": owner.model_dump(mode="json"),
                    "command": command.model_dump(mode="json"),
                },
                BrowserActionResult,
                effect=True,
            )
        except asyncio.CancelledError:
            # Cancel only this owned session, never another user's Chrome. Closing
            # the page cannot undo submissions that already reached a website.
            try:
                await asyncio.shield(
                    asyncio.wait_for(self.stop(owner, str(command.session_id)), 12)
                )
            except Exception:
                pass
            raise

    async def download(
        self, owner: BrowserOwner, session_id: str, command: BrowserDownload
    ) -> BrowserDownloaded:
        try:
            return await self._request(
                "POST",
                f"/sessions/{session_id}/download",
                {
                    "owner": owner.model_dump(mode="json"),
                    "command": command.model_dump(mode="json"),
                },
                BrowserDownloaded,
                effect=True,
            )
        except asyncio.CancelledError:
            try:
                await asyncio.shield(asyncio.wait_for(self.stop(owner, session_id), 12))
            except Exception:
                pass
            raise

    async def stop(self, owner: BrowserOwner, session_id: str) -> BrowserStop:
        return await self._request(
            "POST", f"/sessions/{session_id}/stop", owner.model_dump(mode="json"), BrowserStop
        )
