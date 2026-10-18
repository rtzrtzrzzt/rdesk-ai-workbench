"""Explicit Linux companion entry point; never imported into the main API runtime."""

import argparse
import os
import socket as sockets
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from workbench.config import get_settings
from workbench.contracts.errors import AppError
from workbench.modules.automation.forget import SessionForgotten
from workbench.modules.browser.dto import (
    ActionEnvelope,
    BrowserActionResult,
    BrowserCapability,
    BrowserDownloaded,
    BrowserObservation,
    BrowserOwner,
    BrowserSession,
    BrowserStop,
    CreateEnvelope,
    DownloadEnvelope,
    ObserveEnvelope,
)
from workbench.modules.browser.service import BrowserService, private_path


def create_app(service: BrowserService, *, on_shutdown=None, authorize=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        await service.start()
        try:
            yield
        finally:
            try:
                await service.close()
            finally:
                if on_shutdown:
                    on_shutdown()

    app = FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def bounded_private_requests(request: Request, call_next):
        if authorize is not None and not authorize(request.headers):
            return JSONResponse(
                {"error": {"code": "native_transport_denied", "message": "私有原生入口认证失败。"}},
                403,
            )
        # UDS mode has no public listener. Do not allow a browser-originated HTTP
        # request even if an operator accidentally adds a reverse proxy later.
        if request.headers.get("origin") or request.headers.get("sec-fetch-site"):
            return JSONResponse(
                {
                    "error": {
                        "code": "browser_private_transport",
                        "message": "此入口只供受信本机后端。",
                    }
                },
                403,
            )
        if request.headers.get("transfer-encoding"):
            return JSONResponse(
                {"error": {"code": "browser_body_budget", "message": "请求传输格式不支持。"}}, 413
            )
        try:
            size = int(request.headers.get("content-length", "0"))
        except ValueError:
            size = 100001
        if size > 100000 or size < 0:
            return JSONResponse(
                {"error": {"code": "browser_body_budget", "message": "请求超过预算。"}}, 413
            )
        return await call_next(request)

    @app.exception_handler(AppError)
    async def app_error(request, exc):
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message, "kind": exc.kind}}, exc.status
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse(
            {
                "error": {
                    "code": "browser_request_invalid",
                    "message": "浏览器请求字段不符合受限契约。",
                    "kind": "validation",
                }
            },
            422,
        )

    @app.get("/capability", response_model=BrowserCapability)
    async def capability():
        return service.capability()

    @app.post("/sessions", response_model=BrowserSession)
    async def create(body: CreateEnvelope):
        return await service.create(body.owner, body.command)

    @app.post("/sessions/{session_id}/observe", response_model=BrowserObservation)
    async def observe(session_id: UUID, body: ObserveEnvelope):
        return await service.observe(body.owner, str(session_id), body.command)

    @app.post("/actions", response_model=BrowserActionResult)
    async def action(body: ActionEnvelope):
        return await service.action(body.owner, body.command)

    @app.post("/sessions/{session_id}/download", response_model=BrowserDownloaded)
    async def download(session_id: UUID, body: DownloadEnvelope):
        return await service.download(body.owner, str(session_id), body.command)

    @app.post("/sessions/{session_id}/stop", response_model=BrowserStop)
    async def stop(session_id: UUID, owner: BrowserOwner):
        return await service.stop(owner, str(session_id))

    @app.post("/sessions/{sid}/forget", response_model=SessionForgotten)
    async def forget(sid: UUID, owner: BrowserOwner):
        return await service.forget(owner, str(sid))

    return app


def main():
    parser = argparse.ArgumentParser(description="Private opt-in managed browser companion")
    parser.add_argument("--enable", action="store_true", help="allow explicitly granted sessions")
    parser.add_argument("--data-dir", type=Path, default=get_settings().data_dir)
    parser.add_argument("--trusted-local-origin", action="append", default=[])
    args = parser.parse_args()
    if os.name != "posix":
        parser.error(
            "Windows named-pipe/token transport is waiting for implementation and verification."
        )
    root = args.data_dir.absolute()
    runtime = private_path(root, "browser", create=True)
    os.chmod(runtime, 0o700)
    socket = runtime / "companion.sock"
    if socket.exists() or socket.is_symlink():
        parser.error(
            "companion.sock already exists; do not overwrite another running/stale endpoint"
        )
    os.umask(0o077)
    service = BrowserService(
        root, enabled=args.enable, trusted_local_origins=args.trusted_local_origin
    )
    import uvicorn

    listener = sockets.socket(sockets.AF_UNIX, sockets.SOCK_STREAM)
    listener.bind(str(socket))
    os.chmod(socket, 0o600)
    identity = socket.stat().st_ino

    def remove_owned_socket():
        if socket.exists() and not socket.is_symlink() and socket.stat().st_ino == identity:
            socket.unlink()

    try:
        # Uvicorn's uds= convenience path explicitly chmods 0666. Passing an
        # already-bound fd preserves our private socket's 0600 permissions.
        # Lifespan cleanup precedes Uvicorn re-raising a captured SIGTERM.
        uvicorn.run(
            create_app(service, on_shutdown=remove_owned_socket),
            fd=listener.fileno(),
            access_log=False,
            log_level="warning",
        )
    finally:
        listener.close()
        remove_owned_socket()


if __name__ == "__main__":
    main()
