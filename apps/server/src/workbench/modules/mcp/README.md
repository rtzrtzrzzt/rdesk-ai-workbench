# MCP adapter integration

This module owns protocol DTOs, local schema checks and disposable SDK transport
contexts. The host owns PostgreSQL ORM/migrations, ToolSpec registration, the
operation ledger, approvals, questions/A2UI, HTTP routes and LangGraph. Nothing
here starts an agent loop, calls a model, installs dependencies or opens a
connection at import time.

Dependencies are the host-installed official `mcp==2.3.0` and
`jsonschema==4.26.0`. All SDK imports are absolute `mcp` imports, while this
package is `workbench.modules.mcp`. The existing `httpx==0.28.1` remains separate;
the actual installed SDK requires/uses `httpx2==2.13.1` / `httpcore2==2.13.1`.
These were already present rather than part of the host's ten newly added
packages. No model environment was modified. See [sources.md](sources.md) for the
targeted dependency/security review and version limitations.

## Host ports and entry points

```python
sessions = SessionManager(vault_resolver, connection_gate)
service = MCPService(sessions, exchange_store)
snapshot = await service.discover(config, identity_ref)
outcome = await service.invoke(
    config, snapshot, remote_tool, arguments,
    operation_id=operation.id, attempt_id=attempt.id,
    arguments_ref=operation.arguments_ref, authorization=grant,
)
# Only this view is suitable as a model tool result:
model_result = outcome.model_view()
# At application shutdown (not after every awaiting_input response):
await sessions.aclose()
```

`ConnectionConfig` contains explicit stdio executable/argv/cwd OR StreamableHTTP
endpoint, transport/mode, vault references, allowed tool names and bounds. No
shell string, plaintext credential or arbitrary environment map is accepted.
HTTP endpoints with URL userinfo/query/fragment are rejected; credentials go
through `bearer_secret_ref`. Remote bearer authentication requires HTTPS.
Configured stdio environment credentials use explicit `env_secret_refs`.

The host `ConnectionGate.check(config, identity_ref)` must authorize the exact
local command or network origin before a transport opens. The module cannot
decide a user's network/OS sandbox policy. SDK stdio launches a private POSIX
session and reaps its own process tree; only its safe inherited environment
allow-list plus explicit vault-derived env entries reaches the child, so no
inherited database/model keys. This is native process supervision, **not** an
OS/network filesystem sandbox. A configured executable is local code requiring
host execution authorization. HTTP uses `trust_env=False`, no automatic OAuth,
no connection retries, the SDK's same-origin redirect restrictions and bounded
SSE events. Returned semantic JSON is byte bounded after parsing; this is not a
hard RSS limit for hostile stdio programs or arbitrarily large HTTP JSON bodies.
The host must enforce any stricter sandbox/profile before granting the connection.

The host `SecretResolver.resolve(ref, identity_ref)` supplies credentials only at
the transport boundary. It must enforce vault ownership/account identity.
`identity_ref` must identify the authenticated account **and credential revision**,
not merely an arbitrary display name. Changed credentials, identity or connection
configuration require a new snapshot and grant. Resolved secrets are not DTO
fields, error messages or model results; exact echoes are redacted in typed
server responses. Raw server stderr is discarded because it can echo credentials.

`discover` negotiates and fetches the real paginated tools directory. It validates
input/output schemas, rejects external `$ref/$dynamicRef/$recursiveRef`, bounds
the directory, detects duplicate names/cursor loops, and returns a snapshot tied
to connection version, identity, protocol and TTL. SDK discovery response caching
is disabled. Before dispatch the full directory fingerprint must still match;
new tools or changed descriptions/schemas/annotations require rediscovery. Only
`allowed_tools` are callable, and new directory entries never grant permissions.
Schema dialect is draft2020-12 (schemas omitting `$schema` use this default);
explicit other dialects are rejected rather than silently reinterpreted.

`Authorization` is created by host policy. It binds operation, connection/version,
identity, snapshot, tool and canonical arguments digest. The default effect policy
is `side_effect` and requires an approval ID. Only a host decision may explicitly
grant `read_only`; remote `readOnlyHint/idempotentHint` never makes that decision.
The module does not create an approval or grant itself.

## Durable exchange contract

Implement the async `ExchangeStore` Protocol with real commits:

1. `reserve(ExchangeRequest) -> Reservation` commits a unique
   `(operation_id, attempt_id, sequence)` row and the restricted request payload.
   `acquired=True` belongs to exactly one sender. A duplicate returns the prior
   outcome or an unresolved reservation, and sends nothing.
2. `mark_dispatched(exchange_id)` commits **may have been sent** before SDK
   dispatch; a crash in that window is conservatively reconcilable. It is not a
   receipt, remote idempotency key or proof of execution.
3. `finish(exchange_id, MCPOutcome)` atomically commits response, input plan and
   continuation. Retain the original request/state/answer refs. If this commit
   fails after the remote response, the service returns `outcome_unknown` with
   `response_received=True`, retained bounded response and exchange ID. It never
   treats the remote call as absent or automatically replays it.

An unresolved reservation stays unresolved until the host reconciles it. The
`workbench/operation_id` and `workbench/exchange_id` metadata are correlation hints,
not a claim that the remote service deduplicates writes. The host remains the
single retry owner. The adapter performs one `tools/call` per API invocation and
does not retry business calls, even for read-only annotations.

Outcomes distinguish `succeeded`, `business_error` (`is_error`),
`protocol_error` (including structured output mismatch), `awaiting_input`,
`unsupported`, `not_started` and `outcome_unknown`. A successful JSON-RPC response
is not automatically a successful tool result; cancellation/timeouts after
dispatch do not prove rollback. Typed decoded responses are retained before local
output-schema validation. To avoid the SDK validating and discarding such a
response first, transport uses public `session.send_request` with official
`CallToolRequest` and `TypeAdapter[CallToolResult | InputRequiredResult]`; SDK still
owns wire parsing, version validation and cancellation. No private SDK methods
are overridden.

`AcceptancePostgresStore` is an injectable **test adapter**, not production ORM.
It requires a host connection factory and explicit fresh
`mcp_acceptance_<hex>` schema. `install()` is explicit; no table/schema is created
on import or by the production service. There is no default in-memory store.

## Modern form continuation and legacy limits

For `mode=auto`, the first connection has no callbacks and negotiates normally.
If it discovers modern2026-07-28, it is closed, then a fresh transport uses the
public pinned mode plus `prior_discover` and an elicitation handler which always
rejects any active callback. A pinned modern business connection never falls
back to a legacy callback channel. `mode=legacy` always has no callbacks and
uses the real initialize handshake. Snapshots record both the probe result and
business mode.

SDK2.3's public callback argument advertises form **and URL** together; it has no
public form-only declaration. The recorded SDK advertisement is therefore
`elicitation:{form:{},url:{}}` for modern business requests. Effective host policy
is narrower: only explicit single-exchange form input is supported. URL
elicitation, sampling, roots, subscriptions/task extensions and server-initiated
agent/model/remote transfer actions are disabled. No links are opened and no
automatic answers are produced. Legacy callbacks are not registered. This policy
restriction must be visible; UI must not label URL elicitation as supported.

The SDK high-level `Client.call_tool` automatic input loop is never used. A
modern `input_required` form response becomes `MCPOutcome.awaiting_input`, with
at most4 questions /20 top-level primitive fields per form. Nested/array fields,
URL/auth elicitation, sampling, roots and empty polling rounds return explicit
unsupported outcomes. Questions, schema/version, opaque state, original
arguments ref/digest, operation/attempt, sequence, identity/config/snapshot,
deadline and last exchange are persisted before returning. `question_plan` is a
rendering plan for the host's durable Question/A2UI; it is not a callback that
waits for an HTTP browser session in memory.

After the host persists user answers, it calls:

```python
outcome = await service.resume(
    config, snapshot, persisted_continuation, original_arguments,
    answers_by_request_key,
    answer_ref=persisted_answer_id,
    authorization=grant,
)
```

Resume validates exact original bindings, question versions, answer schemas,
round limit, expiry and total deadline. It sends the same tool and arguments
with the exact original `request_state` and keyed `input_responses`, keeping the
same operation/attempt and incrementing only exchange sequence. Unsupported/failed
state never falls back to invoking the tool without state. The caller must load
the continuation from its committed store rather than trusting model/user JSON.

The SDK's stdio transport can hold process-local request-state keys. A waiting
stdio continuation keeps the **same owned transport** for a bounded idle period;
its lease ID is a disposal handle, not durable proof. A new application process
or lost stdio server yields `CONTINUATION_TRANSPORT_LOST/outcome_unknown` with no
new `tools/call`. HTTP can reopen a fresh transport with persisted state; this
works only if the remote server accepts that state after reconnect. A state
rejection/timeout stops the operation as unknown; it never restarts from scratch.
Arbitrary legacy push callbacks and long-running remote task recovery remain
unsupported. There is no claim of remote restart recovery or exactly-once writes.

## Real auxiliary validation

```sh
.venv/bin/python -m workbench.modules.mcp.acceptance \
  --output-dir /tmp/workbench-mcp-acceptance-<fresh-name>
.venv/bin/ruff check apps/server/src/workbench/modules/mcp
```

The explicit helper uses the official low-level SDK, a bounded server reading
actual project Markdown, real loopback StreamableHTTP, and a real isolated
PostgreSQL schema. It does not call a model or external API. Test database schemas
and output directories are retained for inspection, never automatically deleted.
The document-selection answers are explicit helper inputs, not product approval
or a claim that UI questions have been accepted.

Actual 2026-10-17 evidence: `/tmp/workbench-mcp-acceptance-20261017-2/result.json`,
schema `mcp_acceptance_14679e4ec3a64604818f22b8f52989e5`. Ten checks passed: modern
stdio/legacy handshake real document reads; exact SDK capability advertisement;
owned-process cleanup and no sensitive inherited environment names; annotation
does not grant approval; reserve deduplication; form state committed to PG then
reloaded using another Store/Service object; lost stdio never replays;
StreamableHTTP real read; HTTP fresh-transport form resume; actual PG error
injected at response commit yields unknown and cannot replay; external schema
reference rejected without fetching. The intentional PG `SELECT 1/0` fault is
identified in the helper; remote document data and responses are real.

Later focused evidence in that same output directory:
`directory-invalidation-check.json` proves a real current directory dispatches
while a host-invalidated whole-directory fingerprint stops before tools/call.
`owned-transport-cancellation.json` proves a pending real SDK request fails rather
than hangs when its own test transport is closed: the helper verified parent PID,
private PGID and command line before intentionally stopping only that new docs
subprocess; SDK cleanup reaped it in4.018 seconds. No unrelated process or model
service was signaled.
`last-allowed-form-round.json` verifies the round boundary with max_input_rounds1:
the single permitted question accepts its answer and reads the real document.
Continuation expiry is capped by its snapshot expiry and total deadline, so the
host UI does not advertise a later answer window than the actual authorization.

These are protocol/ledger auxiliary checks. Production ORM, ToolSpec, permission,
durable Question handling and the settings UI are now connected; their product
acceptance remains distinct from these earlier transport checks.
Auto fallback against an independently old-only server, OAuth/mTLS, remote
restart recovery, arbitrary resources, task extensions and hostile-process hard
isolation have not been verified or claimed supported.

The settings UI preserves an uncertain configuration/discovery request ID and
reads its host receipt before further action. It can explicitly close an
unconfirmed request through `POST /api/v1/requests/{id}/close`, which shares the
host admission lock with the original write. `recorded` continues reconciliation;
`sealed` permits clearing the local pending state and blocks late admission of
that ID. This is not an MCP cancellation, a rollback of remote effects, or proof
that every past operation did nothing. The current real browser closure evidence
exercises Vault through the shared host mechanism; it is not an additional claim
that every MCP settings or remote-server failure path was exercised in the UI.
