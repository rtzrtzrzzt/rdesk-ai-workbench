"""Real short-lived local SDK resource server; no DB, app, models, or network.

The server reads actual repository documentation. In-memory persistence is an
explicit test boundary; these checks do not claim application/UI acceptance.
"""

import asyncio
import json
import os
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from mcp import types as protocol
from pydantic import ValidationError
from workbench.config import PROJECT_ROOT
from workbench.modules.mcp.domain import Authorization, Reservation
from workbench.modules.mcp.dto import ConnectionConfig
from workbench.modules.mcp.resources import (
    ListMCPResources,
    ReadMCPResource,
    ResourceService,
)
from workbench.modules.mcp.schema import digest, json_bytes
from workbench.modules.mcp.service import MCPService
from workbench.modules.mcp.transport import SessionManager

SERVER = """
import asyncio, os, sys
from pathlib import Path
from mcp import types as t
from mcp.server import Server
from mcp.server.stdio import stdio_server
root, marker, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
marker.write_text(str(os.getpid()))
documents = sorted((root / "docs").glob("*.md"))
async def tools(ctx, params):
    return t.ListToolsResult(tools=[])
async def resources(ctx, params):
    start = int(params.cursor.split("-")[-1]) if params and params.cursor else 0
    selected = documents[start:start+2]
    return t.ListResourcesResult(
        resources=[t.Resource(name=p.name, uri="docs://"+p.name,
                              description="Project documentation", mime_type="text/markdown",
                              meta={"hidden":"never-model-state"}) for p in selected],
        next_cursor=("opaque-page-"+str(start+2)) if start+2<len(documents) else None,
        meta={"hidden":"never-model-state"})
async def templates(ctx, params):
    return t.ListResourceTemplatesResult(resource_templates=[
        t.ResourceTemplate(name="Project document", uri_template="docs://{name}",
                           mime_type="text/markdown", meta={"hidden":"never-model-state"})])
async def read(ctx, params):
    if mode == "hang":
        await asyncio.sleep(30)
    name = params.uri.removeprefix("docs://")
    target = root / "docs" / name
    if target not in documents:
        raise ValueError("Document is not advertised")
    return t.ReadResourceResult(contents=[t.TextResourceContents(
        uri=params.uri, text=target.read_text(), mime_type="text/markdown",
        meta={"hidden":"never-model-state"})], meta={"hidden":"never-model-state"})
server=Server("Real repository docs resources", version="1", on_list_tools=tools,
              on_list_resources=resources if mode!="unsupported" else None,
              on_list_resource_templates=templates if mode!="unsupported" else None,
              on_read_resource=read if mode!="unsupported" else None)
async def main():
    async with stdio_server() as streams:
        await server.run(*streams, server.create_initialization_options())
asyncio.run(main())
"""


class MemoryStore:
    def __init__(self):
        self.requests = {}
        self.outcomes = {}
        self.dispatched = set()

    async def reserve(self, request):
        key = (request.operation_id, request.attempt_id, request.sequence)
        if key in self.requests:
            exchange = self.requests[key][0]
            return Reservation(exchange, False, self.outcomes.get(exchange))
        exchange = uuid.uuid4().hex
        self.requests[key] = (exchange, request)
        return Reservation(exchange, True)

    async def mark_dispatched(self, exchange):
        self.dispatched.add(exchange)

    async def finish(self, exchange, outcome):
        self.outcomes[exchange] = outcome.model_copy(deep=True)


class NoSecrets:
    async def resolve(self, *_):
        raise AssertionError("No credentials exist in these tests")


class Gate:
    def __init__(self):
        self.enabled = True
        self.checks = 0
        self.revoke_at = None

    async def check(self, config, identity):
        self.checks += 1
        if self.revoke_at is not None and self.checks >= self.revoke_at:
            self.enabled = False
        if not self.enabled or identity != "resource-test":
            raise PermissionError("Resource connection is revoked")
        assert config.command == sys.executable


def configuration(tmp_path, *, mode="normal", legacy=False):
    script = tmp_path / "resource_server.py"
    script.write_text(SERVER)
    marker = tmp_path / "resource-server.pid"
    return ConnectionConfig(
        connection_id="resource_test",
        name="Resource protocol helper",
        version=1,
        transport="stdio",
        command=sys.executable,
        cwd=str(PROJECT_ROOT),
        args=[str(script), str(PROJECT_ROOT), str(marker), mode],
        mode="legacy" if legacy else "auto",
        call_timeout_seconds=2,
    ), marker


def grant(config, snapshot, method, arguments, operation):
    return Authorization(
        operation_id=operation,
        connection_id=config.connection_id,
        connection_version=config.version,
        identity_ref=snapshot.identity_ref,
        snapshot_id=snapshot.snapshot_id,
        remote_tool=method,
        arguments_digest=digest(arguments),
        allowed=True,
        effect_policy="side_effect",
        approval_id="test-only-approval",
    )


async def invoke(
    service, config, snapshot, method, arguments, *, operation=None, authorization=None
):
    operation = operation or uuid.uuid4().hex
    return await service.invoke(
        config,
        snapshot,
        method,
        arguments,
        operation_id=operation,
        attempt_id="attempt-1",
        authorization=authorization or grant(config, snapshot, method, arguments, operation),
        total_deadline=datetime.now(UTC) + timedelta(seconds=8),
    )


def assert_closed(manager, marker):
    assert not manager._leases
    if os.name != "nt":
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)


@pytest.mark.parametrize("legacy", [False, True])
def test_real_sdk_resources_templates_reads_pagination_and_durable_dedup(tmp_path, legacy):
    async def run():
        config, marker = configuration(tmp_path, legacy=legacy)
        manager = SessionManager(NoSecrets(), Gate())
        store = MemoryStore()
        service = ResourceService(manager, store)
        try:
            snapshot = await MCPService(manager, store).discover(config, "resource-test")
            result = await invoke(
                service, config, snapshot, "resources/list", {"offset": 0, "limit": 1}
            )
            assert result.status == "succeeded", result.model_view()
            view = result.structured_content
            actual = sorted((PROJECT_ROOT / "docs").glob("*.md"))
            assert view["items"][0]["uri"] == "docs://" + actual[0].name
            assert view["next_offset"] == 1 and view["untrusted"] is True
            assert "opaque-page" not in json.dumps(result.model_view())
            assert "never-model-state" not in json.dumps(result.model_view())
            page = await invoke(
                service, config, snapshot, "resources/list", {"offset": 2, "limit": 1}
            )
            assert page.structured_content["items"][0]["uri"] == "docs://" + actual[2].name
            templates = await invoke(
                service,
                config,
                snapshot,
                "resources/templates/list",
                {
                    "offset": 0,
                    "limit": 10,
                },
            )
            assert templates.structured_content["items"][0]["uriTemplate"] == "docs://{name}"
            args = {
                "uri": "docs://" + actual[0].name,
                "content_index": 0,
                "offset": 0,
                "max_characters": 30,
                "expected_version": None,
            }
            operation = uuid.uuid4().hex
            read = await invoke(
                service, config, snapshot, "resources/read", args, operation=operation
            )
            assert read.status == "succeeded", read.model_view()
            assert read.structured_content["content"] == actual[0].read_text()[:30]
            assert read.structured_content["next_offset"] == 30
            assert "never-model-state" not in json.dumps(read.model_view())
            assert "never-model-state" in json.dumps(read.raw_response)
            again = await invoke(
                service, config, snapshot, "resources/read", args, operation=operation
            )
            assert again.exchange_id == read.exchange_id
            assert again.structured_content == read.structured_content
            rest = await invoke(
                service,
                config,
                snapshot,
                "resources/read",
                {
                    **args,
                    "offset": 30,
                    "expected_version": read.structured_content["resource_version"],
                },
            )
            assert rest.structured_content["content"] == actual[0].read_text()[30:60]
            changed = await invoke(
                service,
                config,
                snapshot,
                "resources/read",
                {
                    **args,
                    "expected_version": "0" * 64,
                },
            )
            assert changed.code == "RESOURCE_VERSION_CHANGED"
            assert_closed(manager, marker)
        finally:
            await manager.aclose()

    asyncio.run(run())


def test_approval_connection_binding_and_live_revocation_prevent_read(tmp_path):
    async def run():
        config, marker = configuration(tmp_path)
        gate = Gate()
        manager = SessionManager(NoSecrets(), gate)
        store = MemoryStore()
        service = ResourceService(manager, store)
        try:
            snapshot = await MCPService(manager, store).discover(config, "resource-test")
            args = {"offset": 0, "limit": 1}
            operation = uuid.uuid4().hex
            approval = grant(config, snapshot, "resources/list", args, operation)
            for invalid in [
                replace(approval, approval_id=None),
                replace(approval, connection_id="another-connection"),
                replace(approval, arguments_digest=digest({"offset": 5})),
            ]:
                denied = await invoke(
                    service,
                    config,
                    snapshot,
                    "resources/list",
                    args,
                    operation=operation,
                    authorization=invalid,
                )
                assert denied.status == "not_started" and not denied.dispatched
            assert not store.requests
            gate.enabled = False
            revoked = await invoke(service, config, snapshot, "resources/list", args)
            assert revoked.status == "not_started" and not revoked.dispatched
            assert_closed(manager, marker)
        finally:
            await manager.aclose()

    asyncio.run(run())


def test_server_declared_unsupported_is_honest_and_does_not_start_transport(tmp_path):
    async def run():
        config, marker = configuration(tmp_path, mode="unsupported")
        manager = SessionManager(NoSecrets(), Gate())
        store = MemoryStore()
        try:
            snapshot = await MCPService(manager, store).discover(config, "resource-test")
            marker.unlink()
            result = await invoke(
                ResourceService(manager, store),
                config,
                snapshot,
                "resources/list",
                {"offset": 0, "limit": 1},
            )
            assert result.status == "unsupported" and not result.dispatched
            assert not marker.exists() and not store.requests
        finally:
            await manager.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_reaps_real_resource_transport_and_preserves_no_replay_receipt(tmp_path, cancel):
    async def run():
        config, marker = configuration(tmp_path, mode="hang")
        manager = SessionManager(NoSecrets(), Gate())
        store = MemoryStore()
        try:
            snapshot = await MCPService(manager, store).discover(config, "resource-test")
            name = sorted((PROJECT_ROOT / "docs").glob("*.md"))[0].name
            args = {
                "uri": "docs://" + name,
                "offset": 0,
                "max_characters": 30,
                "content_index": 0,
                "expected_version": None,
            }
            operation = uuid.uuid4().hex
            service = ResourceService(manager, store)
            marker.unlink()
            task = asyncio.create_task(
                invoke(service, config, snapshot, "resources/read", args, operation=operation)
            )
            if cancel:
                async with asyncio.timeout(2):
                    while not store.dispatched:
                        await asyncio.sleep(0.01)
                task.cancel()
            result = await task
            assert result.status == "outcome_unknown" and result.dispatched
            assert_closed(manager, marker)
            marker.unlink()
            duplicate = await invoke(
                service, config, snapshot, "resources/read", args, operation=operation
            )
            assert duplicate.exchange_id == result.exchange_id and not marker.exists()
        finally:
            await manager.aclose()

    asyncio.run(run())


def test_revocation_between_resource_pages_stops_further_rpc_and_hides_body(tmp_path):
    async def run():
        config, marker = configuration(tmp_path, legacy=True)
        gate = Gate()
        manager = SessionManager(NoSecrets(), gate)
        store = MemoryStore()
        try:
            snapshot = await MCPService(manager, store).discover(config, "resource-test")
            gate.checks, gate.revoke_at = 0, 5
            result = await invoke(
                ResourceService(manager, store),
                config,
                snapshot,
                "resources/list",
                {"offset": 2, "limit": 1},
            )
            assert result.status == "protocol_error" and result.response_received
            assert result.structured_content is None
            assert len(result.raw_response["pages"]) == 1
            assert_closed(manager, marker)
        finally:
            await manager.aclose()

    asyncio.run(run())


def test_resource_text_output_budget_and_no_arbitrary_tool_transport_fields():
    class Lease:
        config = type("Config", (), {"max_output_bytes": 100000})()
        redact = staticmethod(lambda value: value)

    resource = protocol.ReadResourceResult(
        contents=[
            protocol.TextResourceContents(
                uri="docs://unicode",
                text="文\n" * 10000,
            )
        ]
    )
    view = ResourceService._read_view(
        Lease(),
        resource,
        {
            "content_index": 0,
            "offset": 0,
            "max_characters": 24000,
        },
    )
    assert len(json_bytes(view)) <= 28 * 1024 and view["next_offset"] is not None
    with pytest.raises(ValidationError):
        ReadMCPResource(
            connection_id=uuid.uuid4(),
            snapshot_id="a" * 32,
            uri="file:///etc/passwd",
            command="cat",
        )
    with pytest.raises(ValidationError):
        ListMCPResources(connection_id=uuid.uuid4(), snapshot_id="a" * 32, cursor="opaque")
