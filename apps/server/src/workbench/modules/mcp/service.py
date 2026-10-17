"""One governed MCP exchange at a time; no SDK callback loop or business replay."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from mcp import types as protocol

from workbench.modules.mcp.domain import Authorization, ExchangeRequest, ExchangeStore
from workbench.modules.mcp.dto import (
    ConnectionConfig,
    Continuation,
    InputAnswer,
    InputQuestion,
    MCPOutcome,
    RemoteTool,
    ToolSnapshot,
)
from workbench.modules.mcp.schema import bounded, check_schema, digest, validate
from workbench.modules.mcp.transport import SessionLease, SessionManager


class MCPService:
    def __init__(self, sessions: SessionManager, store: ExchangeStore):
        self.sessions = sessions
        self.store = store

    async def _directory(self, lease: SessionLease) -> list[RemoteTool]:
        tools, seen, cursors, cursor = [], set(), set(), None
        while True:
            result = await lease.request(
                "list_tools", params=protocol.PaginatedRequestParams(cursor=cursor)
            )
            bounded(result.model_dump(mode="json"), lease.config.max_output_bytes)
            for item in result.tools:
                if item.name in seen:
                    raise ValueError("Server returned duplicate tool names")
                seen.add(item.name)
                check_schema(item.input_schema)
                if item.output_schema is not None:
                    check_schema(item.output_schema)
                raw = lease.redact(item.model_dump(mode="json"))
                if len(raw.get("description") or "") > 8192:
                    raise ValueError("Tool description exceeds limit")
                tools.append(
                    RemoteTool(
                        name=item.name,
                        description=raw.get("description"),
                        input_schema=raw["input_schema"],
                        output_schema=raw.get("output_schema"),
                        annotations=raw.get("annotations"),
                        schema_digest=digest(raw),
                    )
                )
                if len(tools) > lease.config.max_tools:
                    raise ValueError("Tool directory exceeds configured limit")
            cursor = result.next_cursor
            if cursor is None:
                break
            if cursor in cursors or len(cursors) >= lease.config.max_tools:
                raise ValueError("Invalid or unbounded directory pagination")
            cursors.add(cursor)
        bounded([t.model_dump(mode="json") for t in tools], lease.config.max_output_bytes)
        return tools

    async def discover(self, config: ConnectionConfig, identity_ref: str) -> ToolSnapshot:
        lease = await self.sessions.create(config, identity_ref)
        try:
            tools = await self._directory(lease)
            now = datetime.now(UTC)
            client = lease.client
            return ToolSnapshot(
                snapshot_id=uuid.uuid4().hex,
                connection_id=config.connection_id,
                connection_version=config.version,
                identity_ref=identity_ref,
                protocol_version=client.protocol_version,
                server_info=lease.redact(client.server_info.model_dump(mode="json"))
                if client.server_info
                else None,
                server_capabilities=lease.redact(client.server_capabilities.model_dump(mode="json"))
                if client.server_capabilities
                else {},
                discovered_at=now,
                expires_at=now + timedelta(seconds=config.snapshot_ttl_seconds),
                tools=tools,
                directory_digest=digest(sorted((t.name, t.schema_digest) for t in tools)),
                negotiation=lease.negotiation,
            )
        finally:
            await self.sessions.release(lease)

    @staticmethod
    def _preflight(
        config: ConnectionConfig,
        snapshot: ToolSnapshot,
        tool_name: str,
        arguments: dict,
        operation_id: str,
        grant: Authorization,
    ) -> RemoteTool:
        if not config.enabled or snapshot.expires_at <= datetime.now(UTC):
            raise ValueError("Disabled connection or expired tool snapshot")
        binding = (
            config.connection_id,
            config.version,
            snapshot.identity_ref,
            snapshot.snapshot_id,
            tool_name,
            digest(arguments),
            operation_id,
        )
        authorized = (
            grant.connection_id,
            grant.connection_version,
            grant.identity_ref,
            grant.snapshot_id,
            grant.remote_tool,
            grant.arguments_digest,
            grant.operation_id,
        )
        if binding != authorized or not grant.allowed:
            raise PermissionError("Host authorization does not match this exact tool call")
        if (
            snapshot.connection_id != config.connection_id
            or snapshot.connection_version != config.version
        ):
            raise PermissionError("Connection version changed after discovery")
        if tool_name not in config.allowed_tools:
            raise PermissionError("Remote tool is not explicitly allowed")
        if grant.effect_policy not in {"read_only", "side_effect"}:
            raise PermissionError("Unknown host effect policy")
        if grant.effect_policy == "side_effect" and not grant.approval_id:
            raise PermissionError("Side-effect calls require a host approval")
        tool = next((x for x in snapshot.tools if x.name == tool_name), None)
        if tool is None:
            raise ValueError("Tool is absent from the approved discovery snapshot")
        validate(tool.input_schema, arguments, config.max_input_bytes)
        return tool

    async def invoke(
        self,
        config: ConnectionConfig,
        snapshot: ToolSnapshot,
        remote_tool: str,
        arguments: dict,
        *,
        operation_id: str,
        attempt_id: str,
        arguments_ref: str,
        authorization: Authorization,
        total_deadline: datetime | None = None,
    ) -> MCPOutcome:
        return await self._exchange(
            config,
            snapshot,
            remote_tool,
            arguments,
            operation_id=operation_id,
            attempt_id=attempt_id,
            arguments_ref=arguments_ref,
            authorization=authorization,
            total_deadline=total_deadline
            or datetime.now(UTC)
            + timedelta(seconds=config.call_timeout_seconds + config.continuation_ttl_seconds),
            continuation=None,
            answers=None,
            answer_ref=None,
        )

    async def resume(
        self,
        config: ConnectionConfig,
        snapshot: ToolSnapshot,
        continuation: Continuation,
        arguments: dict,
        answers: dict[str, InputAnswer],
        *,
        answer_ref: str,
        authorization: Authorization,
    ) -> MCPOutcome:
        return await self._exchange(
            config,
            snapshot,
            continuation.remote_tool,
            arguments,
            operation_id=continuation.operation_id,
            attempt_id=continuation.attempt_id,
            arguments_ref=continuation.arguments_ref,
            authorization=authorization,
            total_deadline=continuation.total_deadline,
            continuation=continuation,
            answers=answers,
            answer_ref=answer_ref,
        )

    @staticmethod
    def _answers(continuation: Continuation, answers: dict[str, InputAnswer]) -> dict:
        if set(answers) != {q.key for q in continuation.questions}:
            raise ValueError("Answer keys must match the persisted input requests")
        result = {}
        for question in continuation.questions:
            answer = answers[question.key]
            if answer.question_version != question.question_version:
                raise ValueError("Answer is for a different question version")
            if answer.action == "accept":
                validate(question.requested_schema, answer.content, 16384)
            elif answer.content is not None:
                raise ValueError("Decline/cancel cannot carry form content")
            result[question.key] = protocol.ElicitResult(
                action=answer.action, content=answer.content
            )
        return result

    @staticmethod
    def _questions(result: protocol.InputRequiredResult) -> list[InputQuestion]:
        if not result.input_requests or len(result.input_requests) > 4:
            raise ValueError("Empty polling or more than four input requests is unsupported")
        questions = []
        for key, request in result.input_requests.items():
            if not isinstance(request, protocol.ElicitRequest) or request.params.mode != "form":
                raise ValueError("Only explicit non-sensitive form elicitation is supported")
            schema = request.params.requested_schema
            check_schema(schema, 16384)
            properties = schema.get("properties", {})
            if schema.get("type") != "object" or len(properties) > 20:
                raise ValueError("Form must have at most twenty top-level primitive fields")
            for field in properties.values():
                if field.get("type") not in {"string", "number", "integer", "boolean"}:
                    raise ValueError("Nested/array form fields are unsupported")
            if len(key) > 128 or len(request.params.message) > 8000:
                raise ValueError("Input question exceeds configured limits")
            questions.append(
                InputQuestion(
                    key=key,
                    message=request.params.message,
                    requested_schema=schema,
                    question_version=digest(request.model_dump(mode="json")),
                )
            )
        return questions

    async def _exchange(
        self,
        config: ConnectionConfig,
        snapshot: ToolSnapshot,
        remote_tool: str,
        arguments: dict,
        *,
        operation_id: str,
        attempt_id: str,
        arguments_ref: str,
        authorization: Authorization,
        total_deadline: datetime,
        continuation: Continuation | None,
        answers: dict[str, InputAnswer] | None,
        answer_ref: str | None,
    ) -> MCPOutcome:
        lease, exchange_id, dispatched, received = None, None, False, False
        outcome = None
        try:
            tool = self._preflight(
                config, snapshot, remote_tool, arguments, operation_id, authorization
            )
            now = datetime.now(UTC)
            if total_deadline <= now:
                raise ValueError("Total operation deadline expired")
            input_responses = None
            sequence = 0
            if continuation:
                expected = (
                    operation_id,
                    attempt_id,
                    config.connection_id,
                    config.version,
                    snapshot.identity_ref,
                    snapshot.snapshot_id,
                    snapshot.protocol_version,
                    remote_tool,
                    arguments_ref,
                    digest(arguments),
                )
                actual = (
                    continuation.operation_id,
                    continuation.attempt_id,
                    continuation.connection_id,
                    continuation.connection_version,
                    continuation.identity_ref,
                    continuation.snapshot_id,
                    continuation.protocol_version,
                    continuation.remote_tool,
                    continuation.arguments_ref,
                    continuation.arguments_digest,
                )
                if expected != actual or continuation.expires_at <= now:
                    raise ValueError("Continuation expired or its durable binding changed")
                if continuation.round > config.max_input_rounds or not answer_ref:
                    raise ValueError("Continuation round limit reached or answer reference missing")
                input_responses = self._answers(continuation, answers or {})
                sequence = continuation.round
                if config.transport == "stdio":
                    lease = self.sessions.get(continuation.transport_lease_id)
                    if lease is None:
                        return MCPOutcome(
                            status="outcome_unknown",
                            operation_id=operation_id,
                            code="CONTINUATION_TRANSPORT_LOST",
                            message="Original stdio process is gone; "
                            "the original call will not be replayed",
                            reconciliation={"last_exchange_id": continuation.last_exchange_id},
                        )
            payload = {"name": remote_tool, "arguments": arguments}
            if continuation:
                payload.update(
                    request_state=continuation.request_state,
                    input_responses={
                        k: v.model_dump(mode="json", by_alias=True)
                        for k, v in input_responses.items()
                    },
                    continuation=continuation.model_dump(mode="json"),
                    answer_ref=answer_ref,
                )
            bounded(payload, config.max_input_bytes + 65536)
            request = ExchangeRequest(
                operation_id=operation_id,
                attempt_id=attempt_id,
                sequence=sequence,
                connection_id=config.connection_id,
                connection_version=config.version,
                identity_ref=snapshot.identity_ref,
                snapshot_id=snapshot.snapshot_id,
                protocol_version=snapshot.protocol_version,
                remote_tool=remote_tool,
                arguments_ref=arguments_ref,
                arguments_digest=digest(arguments),
                request_payload=payload,
                created_at=now,
            )
            reservation = await self.store.reserve(request)
            if not reservation.acquired:
                return reservation.existing_outcome or MCPOutcome(
                    status="outcome_unknown",
                    operation_id=operation_id,
                    exchange_id=reservation.exchange_id,
                    code="EXCHANGE_ALREADY_RESERVED",
                    message="Existing exchange must be reconciled; no duplicate send",
                )
            exchange_id = reservation.exchange_id
            if lease is None:
                lease = await self.sessions.create(config, snapshot.identity_ref)
            else:
                # Re-authorize an existing process when the host's policy may have changed.
                await self.sessions.gate.check(config, snapshot.identity_ref)
            if lease.client.protocol_version != snapshot.protocol_version:
                raise ValueError("Negotiated protocol differs from the approved snapshot")
            current = await self._directory(lease)
            if (
                digest(sorted((t.name, t.schema_digest) for t in current))
                != snapshot.directory_digest
            ):
                raise ValueError("Remote tool directory changed; approved snapshot is stale")
            refreshed = next((x for x in current if x.name == remote_tool), None)
            if refreshed is None or refreshed.schema_digest != tool.schema_digest:
                raise ValueError("Remote tool changed; rediscovery and new authorization required")
            remaining = (total_deadline - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                raise ValueError("Total operation deadline expired before dispatch")
            # Directory refresh can await many network pages. Consent/version
            # checked before that wait is not authority to dispatch afterwards.
            await self.sessions.gate.check(config, snapshot.identity_ref)
            await self.store.mark_dispatched(exchange_id)
            dispatched = True
            result = await lease.request(
                "call_tool",
                name=remote_tool,
                arguments=arguments,
                input_responses=input_responses,
                request_state=continuation.request_state if continuation else None,
                _meta={
                    "workbench/operation_id": operation_id,
                    "workbench/exchange_id": exchange_id,
                },
                _timeout_seconds=remaining,
            )
            received = True
            raw = lease.redact(result.model_dump(mode="json", by_alias=True))
            bounded(raw, config.max_output_bytes)
            outcome = MCPOutcome(
                status="succeeded",
                operation_id=operation_id,
                exchange_id=exchange_id,
                dispatched=True,
                response_received=True,
                protocol_version=lease.client.protocol_version,
                raw_response=raw,
            )
            if isinstance(result, protocol.InputRequiredResult):
                if snapshot.protocol_version != "2026-07-28":
                    raise ValueError("Legacy persistent input callbacks are unsupported")
                try:
                    if sequence >= config.max_input_rounds:
                        raise ValueError(
                            "Continuation round limit reached; no additional input is accepted"
                        )
                    questions = self._questions(result)
                except ValueError as error:
                    outcome.status = "unsupported"
                    outcome.code = "UNSUPPORTED_INPUT_REQUEST"
                    outcome.message = str(error)
                else:
                    outcome.status = "awaiting_input"
                    outcome.question_plan = questions
                    outcome.continuation = Continuation(
                        continuation_id=uuid.uuid4().hex,
                        operation_id=operation_id,
                        attempt_id=attempt_id,
                        connection_id=config.connection_id,
                        connection_version=config.version,
                        identity_ref=snapshot.identity_ref,
                        snapshot_id=snapshot.snapshot_id,
                        protocol_version=snapshot.protocol_version,
                        remote_tool=remote_tool,
                        arguments_ref=arguments_ref,
                        arguments_digest=digest(arguments),
                        request_state=result.request_state,
                        questions=questions,
                        round=sequence + 1,
                        last_exchange_id=exchange_id,
                        created_at=now,
                        expires_at=min(
                            now + timedelta(seconds=config.continuation_ttl_seconds),
                            total_deadline,
                            snapshot.expires_at,
                        ),
                        total_deadline=total_deadline,
                        transport_lease_id=lease.lease_id if config.transport == "stdio" else None,
                    )
            else:
                outcome.status = "business_error" if result.is_error else "succeeded"
                outcome.content = lease.redact(
                    [x.model_dump(mode="json", by_alias=True) for x in result.content]
                )
                outcome.structured_content = lease.redact(result.structured_content)
                if not result.is_error and tool.output_schema is not None:
                    try:
                        validate(
                            tool.output_schema, result.structured_content, config.max_output_bytes
                        )
                    except Exception:
                        outcome.status = "protocol_error"
                        outcome.code = "OUTPUT_SCHEMA_MISMATCH"
                        outcome.message = (
                            "Remote response received but structured output violates its schema"
                        )
        except asyncio.CancelledError:
            outcome = MCPOutcome(
                status="outcome_unknown" if dispatched else "not_started",
                operation_id=operation_id,
                exchange_id=exchange_id,
                dispatched=dispatched,
                response_received=received,
                code="CALL_CANCELLED",
                message="Cancellation does not prove remote rollback",
            )
        except Exception as error:
            if outcome is None:
                outcome = MCPOutcome(
                    status="outcome_unknown" if dispatched else "not_started",
                    operation_id=operation_id,
                    exchange_id=exchange_id,
                    dispatched=dispatched,
                    response_received=received,
                    code="REMOTE_EXCHANGE_FAILED" if dispatched else "PREFLIGHT_FAILED",
                    # Raw exception strings can contain headers, input or credentials.
                    message=f"MCP exchange failed ({type(error).__name__}); no automatic replay",
                )
            else:
                outcome.status = "protocol_error"
                outcome.code = "REMOTE_PROTOCOL_ERROR"
                outcome.message = f"Response received but rejected ({type(error).__name__})"
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
                    code="RESPONSE_PERSISTENCE_FAILED",
                    message="Remote response could not be committed; "
                    "reconcile the reserved exchange",
                    raw_response=outcome.raw_response,
                    reconciliation={"received_status": outcome.status},
                )
        if lease and outcome.status != "awaiting_input":
            await asyncio.shield(self.sessions.release(lease))
        elif lease and config.transport == "streamable_http":
            # Modern HTTP continuation is the durable requestState, not this session.
            await asyncio.shield(self.sessions.release(lease))
        return outcome
