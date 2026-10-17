"""One host tool operation, resumable exchanges, the existing LangGraph interrupt."""

import asyncio
import logging
from uuid import UUID, uuid5

from langgraph.types import interrupt
from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, now
from workbench.modules.generated.forms import check_form
from workbench.modules.interactions import Approval, Question
from workbench.modules.mcp.domain import Authorization
from workbench.modules.mcp.dto import ConnectionConfig, InputAnswer, MCPOutcome
from workbench.modules.mcp.host import (
    HostGate,
    HostSecrets,
    identity,
    load_snapshot,
    vault_unlocked,
)
from workbench.modules.mcp.host_domain import MCPConnection, ToolExchange
from workbench.modules.mcp.schema import digest
from workbench.modules.mcp.service import MCPService
from workbench.modules.mcp.store import (
    PostgresExchangeStore,
    cleanup_targets,
    confirm_transport_cleanup,
    fence,
)
from workbench.modules.mcp.transport import SessionManager
from workbench.modules.runs.domain import Attempt, Run, Step
from workbench.modules.runs.service import emit, question_view, set_status

_services: dict[str, MCPService] = {}
_owned_transports = {}
_operation_locks = {}
_cleanup_lock = asyncio.Lock()
log = logging.getLogger("workbench.mcp")


def _operation_lock(operation_id):
    return _operation_locks.setdefault(operation_id, asyncio.Lock())


def _remember_transport(service, outcome):
    if outcome.continuation and outcome.continuation.transport_lease_id:
        lease_id = outcome.continuation.transport_lease_id
        lease = service.sessions.get(lease_id)
        if lease is not None:
            _owned_transports[lease_id] = (service.sessions, lease)


async def cleanup_services_once(*, force=False):
    """Worker event-loop hook, independent of whether a graph task is active.

    Cancellation/TTL is read from committed Run/Question/ToolExchange facts.
    A missing owner object is pending, not proof that its process was reaped.
    No tool request or continuation is sent by cleanup.
    """
    report = {"closed": 0, "pending": 0, "retained": 0}
    async with _cleanup_lock:
        targets = await asyncio.to_thread(cleanup_targets, force=force)
        for candidate in targets:
            async with _operation_lock(candidate["operation_id"]):
                # A resume may have completed while we waited for its lock.
                fresh = await asyncio.to_thread(cleanup_targets, force=force)
                target = next(
                    (item for item in fresh if item["lease_id"] == candidate["lease_id"]), None
                )
                if target is None:
                    continue
                lease_id, reason = target["lease_id"], target["reason"]
                if target["requires_vault"] and not await asyncio.to_thread(vault_unlocked):
                    reason = "vault_locked_or_unavailable"
                owned = _owned_transports.get(lease_id)
                if owned is None:
                    service = _services.get(target["identity_ref"])
                    lease = service.sessions.get(lease_id) if service else None
                    if lease is not None:
                        owned = (service.sessions, lease)
                        _owned_transports[lease_id] = owned
                if owned is None:
                    await asyncio.to_thread(
                        confirm_transport_cleanup,
                        lease_id,
                        reason=reason or "owner_transport_missing",
                        closed=False,
                        error_code="OWNED_TRANSPORT_NOT_FOUND",
                    )
                    report["pending"] += 1
                    continue
                manager, lease = owned
                if lease.alive and not reason:
                    report["retained"] += 1
                    continue
                reason = reason or "transport_lost"
                if lease.alive:
                    try:
                        async with asyncio.timeout(3):
                            await lease.close()
                    except TimeoutError:
                        pass
                closed = not lease.alive
                await asyncio.to_thread(
                    confirm_transport_cleanup,
                    lease_id,
                    reason=reason,
                    closed=closed,
                    error_code=None if closed else "TRANSPORT_CLOSE_PENDING",
                )
                if closed:
                    # SessionManager.release removes its reference even if a
                    # cancelled close has not finished; inspect alive first.
                    manager._leases.pop(lease_id, None)
                    _owned_transports.pop(lease_id, None)
                    report["closed"] += 1
                else:
                    report["pending"] += 1
    return report


async def serve_cleanup(stop: asyncio.Event, *, interval_seconds=0.5):
    """Start as a sibling Worker task, not its idle-only/60-second maintenance."""
    if not 0.1 <= interval_seconds <= 30:
        raise ValueError("MCP cleanup interval must be between 0.1 and 30 seconds")
    while not stop.is_set():
        try:
            await cleanup_services_once()
        except Exception as error:
            # No remote response, form answer or credential-bearing exception text.
            log.warning("MCP cleanup remains unconfirmed (%s)", type(error).__name__)
        try:
            await asyncio.wait_for(stop.wait(), interval_seconds)
        except TimeoutError:
            pass
    return await close_services()


async def close_services():
    report = await cleanup_services_once(force=True)
    # Non-retained requests are normally drained by execute_owned cancellation.
    # Keep references if any SDK context still has not finished its own cleanup.
    for key, service in list(_services.items()):
        for lease in list(service.sessions._leases.values()):
            if lease.lease_id in _owned_transports:
                continue
            try:
                async with asyncio.timeout(3):
                    await lease.close()
            except TimeoutError:
                pass
            if not lease.alive:
                service.sessions._leases.pop(lease.lease_id, None)
            else:
                report["pending"] += 1
        if not service.sessions._leases:
            _services.pop(key, None)
    return report


def setup(run_id, operation_id, values):
    with Session(engine()) as db, db.begin():
        fence(db, operation_id)
        run = db.get(Run, run_id)
        row = db.get(MCPConnection, values["connection_id"])
        if row is None or row.user_id != run.user_id or row.workspace_id != run.workspace_id:
            raise AppError("mcp_connection_missing", "MCP连接不存在或不可访问。", 404)
        snapshot = load_snapshot(db, run.user_id, run.workspace_id, values["snapshot_id"])
        config = ConnectionConfig.model_validate(row.configuration)
        approval = db.scalar(select(Approval).where(Approval.operation_id == operation_id))
        if approval is None or approval.status != "approved":
            raise AppError("mcp_approval_required", "MCP调用需要此次参数的真实批准。", 403)
        step_id = str(uuid5(UUID(operation_id), "tool-step"))
        attempt_id = str(uuid5(UUID(operation_id), "tool-attempt:1"))
        if db.get(Step, step_id) is None:
            db.add(Step(id=step_id, run_id=run_id, logical_key=f"tool:{operation_id}", kind="tool"))
            db.flush()
            db.add(Attempt(id=attempt_id, run_id=run_id, step_id=step_id, status="running"))
            emit(db, run_id, "step.started", {"step_id": step_id, "kind": "tool"})
        grant = Authorization(
            operation_id,
            row.id,
            row.version,
            identity(row),
            snapshot.snapshot_id,
            values["tool_name"],
            digest(values["arguments"]),
            True,
            effect_policy="side_effect",
            approval_id=approval.id,
        )
        return config, snapshot, grant, attempt_id, run.deadline_at


def previous(operation_id, sequence):
    with Session(engine()) as db:
        row = db.scalar(
            select(ToolExchange)
            .where(
                ToolExchange.operation_id == operation_id,
                ToolExchange.sequence == sequence,
            )
            .order_by(ToolExchange.created_at.desc())
            .limit(1)
        )
        return MCPOutcome.model_validate(row.outcome) if row and row.outcome else None


def questions(run_id, operation_id, outcome):
    with Session(engine()) as db, db.begin():
        fence(db, operation_id)
        run = db.get(Run, run_id)
        ids = []
        for plan in outcome.question_plan:
            key = f"mcp:{outcome.exchange_id}:{plan.key}"
            row = db.scalar(select(Question).where(Question.interaction_key == key))
            if row is None:
                # Unsupported/sensitive fields are never converted to an ordinary text box.
                try:
                    check_form(plan.requested_schema)
                except AppError:
                    schema = {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "unsupported": True,
                    }
                else:
                    schema = plan.requested_schema
                row = Question(
                    run_id=run_id,
                    interaction_key=key,
                    prompt=plan.message,
                    kind="form",
                    options=[],
                    form_schema=schema,
                )
                db.add(row)
                db.flush()
                emit(db, run_id, "question.created", {"question": question_view(row)})
            ids.append(row.id)
        if any(db.get(Question, qid).status == "pending" for qid in ids):
            set_status(db, run, "waiting_user")
        return ids


def answers(question_ids, continuation):
    with Session(engine()) as db:
        values = {}
        for question_id, plan in zip(question_ids, continuation.questions, strict=True):
            row = db.get(Question, question_id)
            if row.status != "answered":
                raise AppError("mcp_answer_pending", "远端工具表单尚未全部提交。", 409)
            answer = row.answer["value"]
            values[plan.key] = InputAnswer(
                question_version=plan.question_version,
                action=answer["action"],
                content=answer["fields"] if answer["action"] == "accept" else None,
            )
        return values


def finish(operation_id, attempt_id, outcome):
    with Session(engine()) as db, db.begin():
        operation = fence(db, operation_id)
        attempt = db.get(Attempt, attempt_id)
        attempt.status = "succeeded" if outcome.status == "succeeded" else "failed"
        attempt.finished_at = now()
        step = db.get(Step, attempt.step_id)
        step.status = attempt.status
        emit(
            db,
            operation.run_id,
            "attempt.finished",
            {
                "step_id": step.id,
                "attempt_id": attempt.id,
                "status": attempt.status,
            },
        )
        emit(db, operation.run_id, "step.finished", {"step_id": step.id, "status": step.status})


async def execute(run_id, operation_id, values):
    async with _operation_lock(operation_id):
        return await _execute(run_id, operation_id, values)


async def _execute(run_id, operation_id, values):
    config, snapshot, grant, attempt_id, deadline = await asyncio.to_thread(
        setup, run_id, operation_id, values
    )
    key = grant.identity_ref
    service = _services.get(key)
    if service is None:
        service = MCPService(
            SessionManager(HostSecrets(config), HostGate()), PostgresExchangeStore()
        )
        _services[key] = service
    continuation, accepted, answer_ref = None, None, None
    for sequence in range(config.max_input_rounds + 1):
        outcome = await asyncio.to_thread(previous, operation_id, sequence)
        if outcome is None:
            if continuation is None:
                outcome = await service.invoke(
                    config,
                    snapshot,
                    values["tool_name"],
                    values["arguments"],
                    operation_id=operation_id,
                    attempt_id=attempt_id,
                    arguments_ref=operation_id,
                    authorization=grant,
                    total_deadline=deadline,
                )
            else:
                outcome = await service.resume(
                    config,
                    snapshot,
                    continuation,
                    values["arguments"],
                    accepted,
                    answer_ref=answer_ref,
                    authorization=grant,
                )
        _remember_transport(service, outcome)
        if outcome.status != "awaiting_input":
            await asyncio.to_thread(finish, operation_id, attempt_id, outcome)
            result = outcome.model_view()
            from workbench.modules.mcp.store import interaction_receipt

            result.update(await asyncio.to_thread(interaction_receipt, operation_id))
            if outcome.status != "succeeded":
                result["error"] = {
                    "code": outcome.code or outcome.status,
                    "message": outcome.message or "MCP工具未成功完成。",
                }
            if outcome.status == "outcome_unknown":
                from workbench.modules.tools.service import record_unknown

                await asyncio.to_thread(record_unknown, run_id, operation_id, "mcp_call", result)
                raise AppError(
                    "mcp_outcome_unknown",
                    "MCP操作结果未确认，已停止自动重复。",
                    409,
                    "outcome_unknown",
                )
            return result
        question_ids = await asyncio.to_thread(questions, run_id, operation_id, outcome)
        # Visit every persisted round in order, keeping interrupt ordinals stable on replay.
        interrupt({"kind": "questions", "question_ids": question_ids})
        continuation = outcome.continuation
        accepted = await asyncio.to_thread(answers, question_ids, continuation)
        answer_ref = ",".join(question_ids)
    raise AppError("mcp_input_limit", "MCP表单已达到继续次数上限。", 409, "budget")
