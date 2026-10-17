"""Bound JSON bodies while receiving, including requests without Content-Length."""

from starlette.exceptions import HTTPException


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        limit = 4 * 1024**2 if scope["path"].endswith("/chunks") else 2 * 1024**2
        received = 0

        async def bounded_receive():
            nonlocal received
            event = await receive()
            received += len(event.get("body", b""))
            if received > limit:
                raise HTTPException(413)
            return event

        return await self.app(scope, bounded_receive, send)
