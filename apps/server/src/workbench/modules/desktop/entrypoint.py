"""Local read-only desktop companion. Control requires an injected lease verifier."""

import argparse
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from workbench.config import get_settings
from workbench.contracts.errors import AppError
from workbench.modules.browser.service import private_path
from workbench.modules.desktop.dto import (
    DTO,
    DesktopAction,
    DesktopActionResult,
    DesktopCapability,
    DesktopCreate,
    DesktopGrant,
    DesktopLease,
    DesktopObservation,
    DesktopObserve,
    DesktopOwner,
    DesktopSession,
    DesktopStop,
)
from workbench.modules.desktop.service import DesktopService


class CreateEnvelope(DTO):
    owner: DesktopOwner
    command: DesktopCreate
    grant: DesktopGrant


class ObserveEnvelope(DTO):
    owner: DesktopOwner
    command: DesktopObserve


class ActionEnvelope(DTO):
    owner: DesktopOwner
    command: DesktopAction
    lease: DesktopLease


def create_app(service: DesktopService, *, on_shutdown=None, authorize=None):
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            for session in tuple(service.sessions.values()):
                await service.stop(session.owner, session.view.session_id)
            if on_shutdown:
                on_shutdown()

    app = FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def private(request: Request, call_next):
        if authorize is not None and not authorize(request.headers):
            return JSONResponse(
                {"error": {"code": "native_transport_denied", "message": "私有原生入口认证失败。"}},
                403,
            )
        if (
            request.headers.get("origin")
            or request.headers.get("sec-fetch-site")
            or request.headers.get("transfer-encoding")
        ):
            return JSONResponse(
                {
                    "error": {
                        "code": "desktop_private_transport",
                        "message": "此入口仅供受信本机后端。",
                    }
                },
                403,
            )
        try:
            size = int(request.headers.get("content-length", "0"))
        except ValueError:
            size = 64001
        if size < 0 or size > 64000:
            return JSONResponse(
                {"error": {"code": "desktop_body_budget", "message": "桌面请求超过预算。"}}, 413
            )
        return await call_next(request)

    @app.exception_handler(AppError)
    async def failure(request, exc):
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message, "kind": exc.kind}}, exc.status
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse(
            {
                "error": {
                    "code": "desktop_request_invalid",
                    "message": "桌面请求不符合受限契约。",
                    "kind": "validation",
                }
            },
            422,
        )

    @app.get("/capability", response_model=DesktopCapability)
    async def capability():
        return await service.probe()

    @app.post("/sessions", response_model=DesktopSession)
    async def create(body: CreateEnvelope):
        return await service.create(body.owner, body.command, body.grant)

    @app.post("/sessions/{sid}/observe", response_model=DesktopObservation)
    async def observe(sid: UUID, body: ObserveEnvelope):
        return await service.observe(body.owner, str(sid), body.command)

    @app.post("/actions", response_model=DesktopActionResult)
    async def action(body: ActionEnvelope):
        return await service.action(body.owner, body.command, body.lease)

    @app.post("/sessions/{sid}/stop", response_model=DesktopStop)
    async def stop(sid: UUID, body: DesktopOwner):
        return await service.stop(body, str(sid))

    @app.post("/sessions/{sid}/takeover", response_model=DesktopStop)
    async def takeover(sid: UUID, body: DesktopOwner):
        return await service.stop(body, str(sid), takeover=True)

    return app


def main():
    parser = argparse.ArgumentParser(
        description="Private desktop observer; no CLI input enable bypass"
    )
    parser.add_argument("--enable-observation", action="store_true")
    parser.add_argument(
        "--stop",
        action="store_true",
        help="write local emergency marker without contacting API/model",
    )
    parser.add_argument("--data-dir", type=Path, default=get_settings().data_dir)
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("Windows UDS companion transport unavailable; use trusted in-process service")
    root = args.data_dir.absolute()
    runtime = private_path(root, "desktop", create=True)
    os.chmod(runtime, 0o700)
    if args.stop:
        DesktopService(root).emergency_stop()
        return
    path = runtime / "companion.sock"
    if path.exists() or path.is_symlink():
        parser.error("desktop socket already exists; never overwrite another endpoint")
    os.umask(0o077)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    os.chmod(path, 0o600)
    inode = path.stat().st_ino

    def cleanup():
        if path.exists() and not path.is_symlink() and path.stat().st_ino == inode:
            path.unlink()

    service = DesktopService(root, enabled=args.enable_observation, allow_control=False)
    import uvicorn

    try:
        uvicorn.run(
            create_app(service, on_shutdown=cleanup),
            fd=listener.fileno(),
            access_log=False,
            log_level="warning",
        )
    finally:
        listener.close()
        cleanup()


if __name__ == "__main__":
    main()
