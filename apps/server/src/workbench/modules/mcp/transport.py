"""Own each SDK context in one task; only disposable transport state lives here."""

import asyncio
import contextlib
import os
import uuid
from contextlib import AsyncExitStack
from typing import Any

import anyio
import httpx2
from mcp import Client, StdioServerParameters
from mcp import types as protocol
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import TypeAdapter

from workbench.modules.mcp.domain import ConnectionGate, SecretResolver
from workbench.modules.mcp.dto import ConnectionConfig


class TransportUnavailable(Exception):
    pass


class SessionLease:
    """An actor prevents SDK/AnyIO context scopes crossing HTTP request tasks."""

    def __init__(
        self,
        config: ConnectionConfig,
        identity_ref: str,
        secrets: dict[str, str],
        *,
        modern_discover=None,
    ):
        self.lease_id = uuid.uuid4().hex
        self.config = config
        self.identity_ref = identity_ref
        self._secrets = dict(secrets)
        self._modern_discover = modern_discover
        self.negotiation = {
            "requested_mode": config.mode,
            "business_mode": "2026-07-28" if modern_discover else config.mode,
            "advertised_callbacks": {"elicitation": {"form": {}, "url": {}}}
            if modern_discover
            else {},
            "effective_policy": {
                "modern_form_single_exchange": bool(modern_discover),
                "url_elicitation": False,
                "sampling": False,
                "roots": False,
                "legacy_callbacks": False,
            },
        }
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._ready = asyncio.get_running_loop().create_future()
        self._scope: anyio.CancelScope | None = None
        self._task = asyncio.create_task(self._run(), name=f"mcp-{config.connection_id}")
        self.client: Client | None = None

    @property
    def alive(self) -> bool:
        return not self._task.done()

    async def ready(self) -> None:
        try:
            await asyncio.wait_for(asyncio.shield(self._ready), self.config.connect_timeout_seconds)
        except BaseException:
            await self.close()
            raise

    def redact(self, value: Any) -> Any:
        if isinstance(value, str):
            for secret in self._secrets.values():
                if secret:
                    value = value.replace(secret, "[credential redacted]")
            return value
        if isinstance(value, list):
            return [self.redact(x) for x in value]
        if isinstance(value, dict):
            return {k: self.redact(v) for k, v in value.items()}
        return value

    async def request(self, method: str, **kwargs):
        if not self.alive:
            raise TransportUnavailable("Owned MCP transport is no longer alive")
        future = asyncio.get_running_loop().create_future()
        await self._queue.put((method, kwargs, future))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # The ledger records unknown and closes this owned transport. Consume
            # its eventual exception without abandoning a live SDK request task.
            future.add_done_callback(
                lambda value: value.exception() if not value.cancelled() else None
            )
            raise

    async def close(self) -> None:
        if self._scope:
            self._scope.cancel()
        if not self._task.done():
            # Cancel through AnyIO's owning scope, not native task.cancel().
            with contextlib.suppress(BaseException):
                await asyncio.shield(self._task)

    async def _run(self) -> None:
        current = None
        try:
            with anyio.CancelScope() as scope:
                self._scope = scope
                async with AsyncExitStack() as stack:
                    c = self.config
                    if c.transport == "stdio":
                        env = {name: self._secrets[ref] for name, ref in c.env_secret_refs.items()}
                        env["PYTHONNOUSERSITE"] = "1"
                        params = StdioServerParameters(
                            command=c.command, args=c.args, cwd=c.cwd, env=env
                        )
                        # Server stderr is untrusted and can echo credentials. The
                        # host receives bounded typed errors, never this raw stream.
                        errlog = stack.enter_context(open(os.devnull, "w"))
                        target = stdio_client(params, errlog=errlog)
                    else:
                        headers = {}
                        if c.bearer_secret_ref:
                            headers["Authorization"] = (
                                "Bearer " + self._secrets[c.bearer_secret_ref]
                            )
                        http = await stack.enter_async_context(
                            httpx2.AsyncClient(
                                headers=headers,
                                trust_env=False,
                                timeout=httpx2.Timeout(
                                    c.connect_timeout_seconds, read=c.call_timeout_seconds
                                ),
                                transport=httpx2.AsyncHTTPTransport(retries=0),
                            )
                        )
                        target = streamable_http_client(
                            c.endpoint, http_client=http, max_sse_event_size=c.max_output_bytes
                        )

                    async def reject_callback(context, params):
                        return protocol.ErrorData(
                            code=-32601,
                            message="Server-initiated callback is unsupported by host policy",
                        )

                    self.client = await stack.enter_async_context(
                        Client(
                            target,
                            mode="2026-07-28" if self._modern_discover else c.mode,
                            prior_discover=self._modern_discover,
                            read_timeout_seconds=c.call_timeout_seconds,
                            cache=None,
                            elicitation_callback=reject_callback if self._modern_discover else None,
                            # Never use Client.call_tool's callback loop. Legacy has no callback.
                        )
                    )
                    self._ready.set_result(None)
                    while True:
                        try:
                            current = await asyncio.wait_for(
                                self._queue.get(), c.continuation_ttl_seconds
                            )
                        except TimeoutError:
                            break
                        method, kwargs, future = current
                        try:
                            timeout = kwargs.pop("_timeout_seconds", c.call_timeout_seconds)
                            with anyio.fail_after(min(c.call_timeout_seconds, timeout)):
                                if method == "list_tools":
                                    result = await self.client.session.list_tools(**kwargs)
                                else:
                                    # call_tool validates output before returning, losing
                                    # a malformed business response to the ledger. The
                                    # public typed send_request keeps that response for
                                    # our durable recording and local schema validation.
                                    result = await self.client.session.send_request(
                                        protocol.CallToolRequest(
                                            params=protocol.CallToolRequestParams(**kwargs)
                                        ),
                                        TypeAdapter(
                                            protocol.CallToolResult | protocol.InputRequiredResult
                                        ),
                                        request_read_timeout_seconds=c.call_timeout_seconds,
                                    )
                            if not future.done():
                                future.set_result(result)
                        except BaseException as error:
                            if not future.done():
                                future.set_exception(
                                    error
                                    if isinstance(error, Exception)
                                    else TransportUnavailable("Owned MCP request cancelled")
                                )
                            if not isinstance(error, Exception):
                                raise
                        finally:
                            current = None
        except BaseException as error:
            if not self._ready.done():
                self._ready.set_exception(error)
        finally:
            if current is not None and not current[2].done():
                current[2].set_exception(TransportUnavailable("Owned MCP transport closed"))
            while not self._queue.empty():
                _, _, future = self._queue.get_nowait()
                if not future.done():
                    future.set_exception(TransportUnavailable("Owned MCP transport closed"))
            self._secrets.clear()


class SessionManager:
    def __init__(self, secrets: SecretResolver, gate: ConnectionGate):
        self.secrets = secrets
        self.gate = gate
        self._leases: dict[str, SessionLease] = {}

    async def create(self, config: ConnectionConfig, identity_ref: str) -> SessionLease:
        if not config.enabled:
            raise TransportUnavailable("MCP connection is disabled")
        await self.gate.check(config, identity_ref)
        references = set(config.env_secret_refs.values())
        if config.bearer_secret_ref:
            references.add(config.bearer_secret_ref)
        resolved = {}
        for reference in references:
            resolved[reference] = await self.secrets.resolve(reference, identity_ref)
        await self.gate.check(config, identity_ref)
        lease = SessionLease(config, identity_ref, resolved)
        await lease.ready()
        if config.mode == "auto" and lease.client.protocol_version == "2026-07-28":
            # Public API only: probe without callbacks, then pin that discovered
            # modern era on a fresh transport. A changed/old peer fails; it can
            # never fall back to a legacy session advertising callbacks.
            discovered = lease.client.session.discover_result
            await lease.close()
            await self.gate.check(config, identity_ref)
            lease = SessionLease(config, identity_ref, resolved, modern_discover=discovered)
            await lease.ready()
            lease.negotiation["probe_protocol_version"] = "2026-07-28"
        self._leases[lease.lease_id] = lease
        lease._task.add_done_callback(lambda _: self._leases.pop(lease.lease_id, None))
        return lease

    def get(self, lease_id: str | None) -> SessionLease | None:
        lease = self._leases.get(lease_id or "")
        return lease if lease and lease.alive else None

    async def release(self, lease: SessionLease) -> None:
        await lease.close()
        self._leases.pop(lease.lease_id, None)

    async def aclose(self) -> None:
        for lease in list(self._leases.values()):
            await self.release(lease)
