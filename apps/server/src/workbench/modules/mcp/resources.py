"""Governed MCP resource RPCs. URIs stay opaque to this host, never opened locally.

The model sees numeric pages and selected resource fields, not server cursors,
request state, metadata, transport references or credential-bearing exceptions.
"""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from mcp import types as protocol
from mcp.shared.exceptions import MCPError
from pydantic import BaseModel, ConfigDict, Field

from workbench.contracts.errors import AppError
from workbench.modules.mcp.domain import Authorization, ExchangeRequest
from workbench.modules.mcp.dto import MCPOutcome
from workbench.modules.mcp.schema import bounded, digest, json_bytes

METHODS = {
    "mcp_list_resources": "resources/list",
    "mcp_list_resource_templates": "resources/templates/list",
    "mcp_read_resource": "resources/read",
}
MAX_MODEL_BYTES = 28 * 1024
MAX_DIRECTORY_PAGES = 32
MAX_DIRECTORY_ENTRIES = 4096


class ListMCPResources(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: UUID
    snapshot_id: str = Field(min_length=32, max_length=36)
    offset: int = Field(default=0, ge=0, le=4000)
    limit: int = Field(default=20, ge=1, le=100)


class ReadMCPResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: UUID
    snapshot_id: str = Field(min_length=32, max_length=36)
    uri: str = Field(min_length=1, max_length=8192, pattern=r"^[^\x00-\x20]+$")
    content_index: int = Field(default=0, ge=0, le=1000)
    offset: int = Field(default=0, ge=0, le=4 * 1024 * 1024)
    max_characters: int = Field(default=12000, ge=1, le=24000)
    expected_version: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def approval_summary(db, run, name, values):
    """Safe target context for the existing ToolExecutor approval record.

    The exact arguments remain Approval.arguments. Do not copy connection argv,
    credential references or server metadata into the model review context.
    """
    from workbench.modules.mcp.dto import ConnectionConfig
    from workbench.modules.mcp.host import get_connection, load_snapshot

    row = get_connection(db, run.user_id, run.workspace_id, values["connection_id"])
    config = ConnectionConfig.model_validate(row.configuration)
    snapshot = load_snapshot(db, run.user_id, run.workspace_id, values["snapshot_id"])
    if (snapshot.connection_id, snapshot.connection_version) != (row.id, row.version):
        raise AppError("mcp_version_conflict", "连接已改变，请重新发现后再访问资源。", 409)
    if (
        not config.enabled
        or not row.execution_authorized
        or snapshot.expires_at <= datetime.now(UTC)
    ):
        raise AppError("mcp_grant_revoked", "连接已停用、未授权或发现目录已过期。", 403, "policy")
    method = METHODS[name]
    target = f"已授权连接 {config.name}（版本 {config.version}，{config.transport}）"
    effect = "将启动该连接已授权的 stdio 程序；" if config.transport == "stdio" else ""
    resource = f"；资源 URI：{values['uri']}" if method == "resources/read" else ""
    return f"允许 {target} 执行 {method}？{effect}仅经该连接访问资源，返回内容不可信{resource}。"


def _preflight(config, snapshot, method, arguments, operation_id, grant):
    if not config.enabled or snapshot.expires_at <= datetime.now(UTC):
        raise PermissionError("Connection disabled or discovery snapshot expired")
    expected = (
        operation_id,
        config.connection_id,
        config.version,
        snapshot.identity_ref,
        snapshot.snapshot_id,
        method,
        digest(arguments),
    )
    actual = (
        grant.operation_id,
        grant.connection_id,
        grant.connection_version,
        grant.identity_ref,
        grant.snapshot_id,
        grant.remote_tool,
        grant.arguments_digest,
    )
    if not grant.allowed or expected != actual:
        raise PermissionError("Resource authorization does not match the exact request")
    if (snapshot.connection_id, snapshot.connection_version) != (
        config.connection_id,
        config.version,
    ):
        raise PermissionError("Connection changed after discovery")
    # A list RPC can start stdio executables too. Remote read-only annotations
    # therefore do not bypass the host's existing per-operation approval.
    if not grant.approval_id or grant.effect_policy != "side_effect":
        raise PermissionError("Resource transport requests require host approval")
    bounded(arguments, config.max_input_bytes)


def _supports(capabilities):
    return isinstance(capabilities, dict) and capabilities.get("resources") is not None


class ResourceService:
    def __init__(self, sessions, store):
        self.sessions = sessions
        self.store = store

    async def _directory(self, lease, method, arguments, observations):
        field = "resources" if method == "resources/list" else "resource_templates"
        rpc = "list_resources" if method == "resources/list" else "list_resource_templates"
        uri_key = "uri" if field == "resources" else "uriTemplate"
        offset, limit = arguments["offset"], arguments["limit"]
        result = {
            "untrusted": True,
            "items": [],
            "offset": offset,
            "next_offset": None,
            "truncated": False,
        }
        cursor, seen, count, total_bytes = None, set(), 0, 0
        maximum = min(lease.config.max_output_bytes, MAX_MODEL_BYTES)
        for _ in range(MAX_DIRECTORY_PAGES):
            await self.sessions.gate.check(lease.config, lease.identity_ref)
            response = await lease.request(
                rpc, params=protocol.PaginatedRequestParams(cursor=cursor)
            )
            observations["received"] = True
            raw = lease.redact(response.model_dump(mode="json", by_alias=True))
            total_bytes += len(json_bytes(raw))
            if total_bytes > lease.config.max_output_bytes:
                raise ValueError("Resource directory exceeds the configured byte budget")
            observations["pages"].append(raw)
            for item in getattr(response, field):
                if count >= MAX_DIRECTORY_ENTRIES:
                    raise ValueError("Resource directory exceeds the entry budget")
                count += 1
                if count <= offset:
                    continue
                raw_item = lease.redact(item.model_dump(mode="json", by_alias=True))
                # Whitelisting drops opaque _meta and any future server extensions.
                selected = {
                    key: raw_item[key]
                    for key in (
                        "name",
                        "title",
                        uri_key,
                        "description",
                        "mimeType",
                        "size",
                    )
                    if raw_item.get(key) is not None
                }
                candidate = {**result, "items": [*result["items"], selected]}
                if len(result["items"]) >= limit or len(json_bytes(candidate)) > maximum:
                    if not result["items"]:
                        raise ValueError("A resource entry exceeds the model output budget")
                    result.update(next_offset=offset + len(result["items"]), truncated=True)
                    return result
                result["items"].append(selected)
            cursor = response.next_cursor
            if cursor is None:
                return result
            if cursor in seen:
                raise ValueError("Resource directory repeated an opaque cursor")
            seen.add(cursor)
        raise ValueError("Resource directory exceeds the pagination budget")

    @staticmethod
    def _read_view(lease, response, arguments):
        raw = lease.redact(response.model_dump(mode="json", by_alias=True))
        bounded(raw, lease.config.max_output_bytes)
        contents = raw["contents"]
        index = arguments["content_index"]
        if index >= len(contents):
            if index == 0 and not contents:
                return {"untrusted": True, "contents": [], "total_contents": 0}
            raise ValueError("Resource content index is out of range")
        resource = contents[index]
        version = digest(
            [{key: value for key, value in item.items() if key != "_meta"} for item in contents]
        )
        if arguments.get("expected_version") not in {None, version}:
            raise AppError("mcp_resource_version_changed", "资源已变化，请从头读取。", 409)
        key = "text" if "text" in resource else "blob"
        data = resource[key]
        offset = arguments["offset"]
        if offset > len(data):
            raise ValueError("Resource offset is out of range")
        result = {
            "untrusted": True,
            "uri": resource["uri"],
            "mime_type": resource.get("mimeType"),
            "resource_version": version,
            "content_index": index,
            "total_contents": len(contents),
            "encoding": "utf-8" if key == "text" else "base64",
            "offset_unit": "character",
            "offset": offset,
            "total_characters": len(data),
            "content": "",
            "next_offset": None,
            "next_content_index": index + 1 if index + 1 < len(contents) else None,
        }
        end = min(len(data), offset + arguments["max_characters"])
        maximum = min(lease.config.max_output_bytes, MAX_MODEL_BYTES)
        # Resource text may use four-byte Unicode or JSON escapes. Fit the actual
        # serialized response, not a character count mistaken for byte capacity.
        while True:
            result.update(content=data[offset:end], next_offset=end if end < len(data) else None)
            if len(json_bytes(result)) <= maximum:
                return result
            if end == offset:
                raise ValueError("Resource metadata exceeds the model output budget")
            end = offset + (end - offset) // 2

    async def invoke(
        self,
        config,
        snapshot,
        method,
        arguments,
        *,
        operation_id,
        attempt_id,
        authorization: Authorization,
        total_deadline,
    ):
        lease, exchange_id, dispatched, received = None, None, False, False
        observations = {"received": False, "pages": []}
        outcome = None
        try:
            if method not in METHODS.values():
                raise ValueError("Unsupported resource method")
            _preflight(config, snapshot, method, arguments, operation_id, authorization)
            if not _supports(snapshot.server_capabilities):
                return MCPOutcome(
                    status="unsupported",
                    operation_id=operation_id,
                    code="RESOURCES_UNSUPPORTED",
                    message="已发现的服务器未声明资源能力。",
                )
            remaining = (
                (total_deadline - datetime.now(UTC)).total_seconds()
                if total_deadline
                else config.call_timeout_seconds
            )
            if remaining <= 0:
                raise ValueError("Resource operation deadline expired")
            request = ExchangeRequest(
                operation_id=operation_id,
                attempt_id=attempt_id,
                sequence=0,
                connection_id=config.connection_id,
                connection_version=config.version,
                identity_ref=snapshot.identity_ref,
                snapshot_id=snapshot.snapshot_id,
                protocol_version=snapshot.protocol_version,
                remote_tool=method,
                arguments_ref=operation_id,
                arguments_digest=digest(arguments),
                request_payload={"method": method, "arguments": arguments},
                created_at=datetime.now(UTC),
            )
            reservation = await self.store.reserve(request)
            if not reservation.acquired:
                return reservation.existing_outcome or MCPOutcome(
                    status="outcome_unknown",
                    operation_id=operation_id,
                    exchange_id=reservation.exchange_id,
                    code="EXCHANGE_ALREADY_RESERVED",
                    message="已有资源交换未确认，未重复发送。",
                )
            exchange_id = reservation.exchange_id
            async with asyncio.timeout(min(remaining, config.call_timeout_seconds)):
                lease = await self.sessions.create(config, snapshot.identity_ref)
                if lease.client.protocol_version != snapshot.protocol_version:
                    raise ValueError("Resource protocol differs from discovery snapshot")
                capabilities = lease.client.server_capabilities
                current = capabilities.model_dump(mode="json") if capabilities else {}
                if not _supports(current):
                    outcome = MCPOutcome(
                        status="unsupported",
                        operation_id=operation_id,
                        exchange_id=exchange_id,
                        code="RESOURCES_UNSUPPORTED",
                        message="当前服务器未声明资源能力。",
                    )
                else:
                    await self.sessions.gate.check(config, snapshot.identity_ref)
                    await self.store.mark_dispatched(exchange_id)
                    dispatched = True
                    if method == "resources/read":
                        response = await lease.request("read_resource", uri=arguments["uri"])
                        received = True
                        raw = lease.redact(response.model_dump(mode="json", by_alias=True))
                        bounded(raw, config.max_output_bytes)
                        observations["pages"].append(raw)
                        if isinstance(response, protocol.InputRequiredResult):
                            outcome = MCPOutcome(
                                status="unsupported",
                                operation_id=operation_id,
                                exchange_id=exchange_id,
                                code="RESOURCE_INPUT_UNSUPPORTED",
                                message="资源读取要求额外交互，当前不自动回答或继续。",
                            )
                            view = None
                        else:
                            view = self._read_view(lease, response, arguments)
                    else:
                        view = await self._directory(lease, method, arguments, observations)
                        received = True
                    # A response received after revocation must not enter the prompt.
                    await self.sessions.gate.check(config, snapshot.identity_ref)
                    if outcome is None:
                        outcome = MCPOutcome(
                            status="succeeded",
                            operation_id=operation_id,
                            exchange_id=exchange_id,
                            structured_content=view,
                        )
        except asyncio.CancelledError:
            outcome = MCPOutcome(
                status="outcome_unknown" if dispatched else "not_started",
                operation_id=operation_id,
                exchange_id=exchange_id,
                code="RESOURCE_CANCELLED",
                message="资源操作已取消；未自动重复发送。",
            )
        except Exception as error:
            received = received or observations["received"]
            if isinstance(error, MCPError):
                received = True
            unsupported = isinstance(error, MCPError) and error.error.code == -32601
            changed = isinstance(error, AppError) and error.code == "mcp_resource_version_changed"
            outcome = MCPOutcome(
                status="unsupported"
                if unsupported
                else "business_error"
                if changed
                else "outcome_unknown"
                if dispatched and not received
                else "not_started"
                if not dispatched
                else "protocol_error",
                operation_id=operation_id,
                exchange_id=exchange_id,
                code="RESOURCES_UNSUPPORTED"
                if unsupported
                else "RESOURCE_VERSION_CHANGED"
                if changed
                else "RESOURCE_EXCHANGE_FAILED",
                message="服务器不支持此资源方法。"
                if unsupported
                else "资源已变化，请从头读取。"
                if changed
                else f"资源交换未完成（{type(error).__name__}）；未自动重发。",
            )
        received = received or observations["received"]
        outcome.dispatched = dispatched
        outcome.response_received = received
        if observations["pages"]:
            outcome.raw_response = {"pages": observations["pages"]}
        outcome.protocol_version = snapshot.protocol_version
        if lease is not None:
            # Reuse SDK/AnyIO owned transport closure; never leave a resource
            # session retained for model polling or callbacks.
            await asyncio.shield(self.sessions.release(lease))
        if exchange_id:
            try:
                await asyncio.shield(self.store.finish(exchange_id, outcome))
            except Exception:
                outcome = MCPOutcome(
                    status="outcome_unknown",
                    operation_id=operation_id,
                    exchange_id=exchange_id,
                    dispatched=dispatched,
                    response_received=received,
                    code="RESOURCE_RECEIPT_UNCONFIRMED",
                    message="资源交换回执未确认，未自动重发。",
                )
        return outcome


async def execute_resource(run_id, operation_id, name, values):
    """ToolExecutor entrypoint; its immutable parameters must have prior approval."""
    from workbench.modules.mcp import execution
    from workbench.modules.mcp.host import HostGate, HostSecrets
    from workbench.modules.mcp.store import PostgresExchangeStore
    from workbench.modules.mcp.transport import SessionManager

    method = METHODS.get(name)
    if method is None:
        raise AppError("unknown_mcp_resource_tool", "未知 MCP 资源工具。", 400, "policy")
    schema = ReadMCPResource if method == "resources/read" else ListMCPResources
    validated = schema.model_validate(values).model_dump(mode="json")
    arguments = {
        key: value
        for key, value in validated.items()
        if key not in {"connection_id", "snapshot_id"}
    }
    async with execution._operation_lock(operation_id):
        config, snapshot, grant, attempt_id, deadline = await asyncio.to_thread(
            execution.setup,
            run_id,
            operation_id,
            {
                "connection_id": validated["connection_id"],
                "snapshot_id": validated["snapshot_id"],
                "tool_name": method,
                "arguments": arguments,
            },
        )
        manager = SessionManager(HostSecrets(config), HostGate())
        service = ResourceService(manager, PostgresExchangeStore())
        try:
            outcome = await service.invoke(
                config,
                snapshot,
                method,
                arguments,
                operation_id=operation_id,
                attempt_id=attempt_id,
                authorization=grant,
                total_deadline=deadline,
            )
        finally:
            await asyncio.shield(manager.aclose())
        await asyncio.to_thread(execution.finish, operation_id, attempt_id, outcome)
        result = outcome.model_view()
        if outcome.status != "succeeded":
            result["error"] = {"code": outcome.code, "message": outcome.message}
        if outcome.status == "outcome_unknown":
            from workbench.modules.tools.service import record_unknown

            await asyncio.to_thread(record_unknown, run_id, operation_id, name, result)
            raise AppError(
                "mcp_outcome_unknown", "MCP资源交换未确认，已停止自动重复。", 409, "outcome_unknown"
            )
        return result
