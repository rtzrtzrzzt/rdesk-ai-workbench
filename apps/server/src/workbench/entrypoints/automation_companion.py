"""Opt-in private native process. No public port, automatic grants or model loop."""

import argparse
import asyncio
import os
import signal
from contextlib import contextmanager

import uvicorn
from workbench.composition import tables  # noqa: F401
from workbench.config import get_settings
from workbench.modules.automation.native import (
    GovernedBrowser,
    GovernedDesktop,
    browser_executable,
    reap,
)
from workbench.modules.automation.policy import assert_desktop_lease
from workbench.modules.automation.transport import bind_endpoint
from workbench.modules.browser.entrypoint import create_app as browser_app
from workbench.modules.desktop.entrypoint import create_app as desktop_app


class PrivateServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # One outer owner handles both UDS servers. Per-server signal re-raise
        # must not terminate this process before resource facts commit.
        yield


async def serve(nonce):
    root = get_settings().ensure_data_dir().absolute()
    browser = GovernedBrowser(root, enabled=True, executable_path=browser_executable())
    desktop = GovernedDesktop(
        root, enabled=True, allow_control=True, assert_lease=assert_desktop_lease
    )
    sockets = []
    endpoints = []
    servers = []
    stop = asyncio.Event()
    try:
        for name, factory, service in (
            ("browser", browser_app, browser),
            ("desktop", desktop_app, desktop),
        ):
            endpoint = bind_endpoint(root, name, nonce)
            endpoints.append(endpoint)
            app = factory(service, authorize=endpoint.authorize)
            sockets.append(endpoint.listener)
            if name == "desktop":

                @app.get("/foreground")
                async def foreground():
                    driver = await asyncio.to_thread(desktop._driver)
                    state = await asyncio.to_thread(driver.snapshot)
                    return {
                        "window_id": state.window_id,
                        "window_pid": state.window_pid,
                        "locked": state.locked,
                        "geometry_id": state.geometry(),
                    }

            servers.append(
                PrivateServer(uvicorn.Config(app, access_log=False, log_level="warning"))
            )

        def shutdown():
            for server in servers:
                server.should_exit = True

        # This private route is authenticated by the same endpoint middleware.
        # It permits graceful cleanup on Windows without TerminateProcess.
        for server in servers:

            @server.config.app.post("/shutdown")
            async def requested_shutdown():
                shutdown()
                return {"stopping": True}

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, shutdown)
            except NotImplementedError:
                signal.signal(sig, lambda signum, frame: loop.call_soon_threadsafe(shutdown))
        janitor = asyncio.create_task(reap(browser, desktop, nonce, stop))
        tasks = [
            asyncio.create_task(server.serve(sockets=[listener]))
            for server, listener in zip(servers, sockets, strict=True)
        ]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for server in servers:
            server.should_exit = True
        await asyncio.gather(*tasks)
        stop.set()
        await janitor
        # Lifespans have closed the actual native sessions before publishing facts.
        from workbench.modules.automation.native import confirm_closed

        for service in (browser, desktop):
            for sid, native in tuple(service.sessions.items()):
                result = await service.stop(native.owner, sid)

                # Resolve by identity without the normal live-policy gate.
                from sqlalchemy import select
                from sqlalchemy.orm import Session
                from workbench.infrastructure.database import engine
                from workbench.modules.automation.domain import AutomationSession

                with Session(engine()) as db:
                    row = db.scalar(
                        select(AutomationSession).where(
                            AutomationSession.native_id == sid,
                            AutomationSession.runtime_nonce == nonce,
                        )
                    )
                    identifier = row.id if row else None
                if identifier:
                    await asyncio.to_thread(confirm_closed, identifier, result)
    finally:
        for endpoint in endpoints:
            endpoint.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-id", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    asyncio.run(serve(args.instance_id))


if __name__ == "__main__":
    main()
