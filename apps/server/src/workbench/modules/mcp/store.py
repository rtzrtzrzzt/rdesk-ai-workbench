"""Committed exchanges and truthful retained-transport cleanup facts.

The scheduler calls request_transport_cleanup inside its own transaction, then
keeps the Run in cleanup while has_pending_transport_cleanup is true. Only the
owning event loop may confirm transport closure; requesting it is not closure.
"""

import asyncio
import os
from dataclasses import asdict
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, now
from workbench.modules.interactions import Question
from workbench.modules.mcp.domain import Reservation
from workbench.modules.mcp.dto import MCPOutcome
from workbench.modules.mcp.host_domain import MCPConnection, ToolExchange
from workbench.modules.runs.domain import TERMINAL_STATUSES, Job, Run
from workbench.modules.runs.ownership import assert_owned
from workbench.modules.tools.domain import Operation

OWNER_INSTANCE = uuid4().hex
CLOSED_TRANSPORT_STATES = frozenset({"closed"})


def interaction_receipt(operation_id):
    """Only persisted human-interaction facts, never opaque state or form values."""
    with Session(engine()) as db:
        exchanges = list(
            db.scalars(
                select(ToolExchange)
                .where(ToolExchange.operation_id == operation_id)
                .order_by(ToolExchange.sequence)
            )
        )
        rounds, forms = 0, []
        for exchange in exchanges:
            plans = (exchange.outcome or {}).get("question_plan", [])
            if not plans:
                continue
            rounds += 1
            for plan in plans:
                row = db.scalar(
                    select(Question).where(
                        Question.interaction_key == f"mcp:{exchange.id}:{plan['key']}"
                    )
                )
                forms.append(
                    {
                        "question_id": row.id if row else None,
                        "status": row.status if row else "not_created",
                    }
                )
        return {
            "human_input": {
                "rounds": rounds,
                "form_count": len(forms),
                "answered_count": sum(item["status"] == "answered" for item in forms),
                "forms": forms,
            }
        }


def fence(db, operation_id):
    operation = db.get(Operation, operation_id)
    if operation is None:
        raise AppError("mcp_operation_missing", "MCP操作不存在。", 404)
    db.scalar(select(Run).where(Run.id == operation.run_id).with_for_update())
    assert_owned(db, operation.run_id)
    return operation


def _transport(row):
    outcome = row.outcome or {}
    marker = outcome.get("reconciliation", {}).get("transport_cleanup")
    if marker:
        return dict(marker)
    continuation = outcome.get("continuation") or {}
    if continuation.get("transport_lease_id"):
        # Old records have no proven live owner. Never call them closed merely
        # because a new Worker has no corresponding in-memory SessionLease.
        return {
            "state": "owner_unknown",
            "lease_id": continuation["transport_lease_id"],
            "owner_instance": None,
            "identity_ref": continuation.get("identity_ref"),
        }
    return None


def _put_transport(row, marker):
    outcome = dict(row.outcome or {})
    reconciliation = dict(outcome.get("reconciliation", {}))
    reconciliation["transport_cleanup"] = marker
    outcome["reconciliation"] = reconciliation
    row.outcome = outcome


def _run_exchanges(db, run_id):
    return list(
        db.scalars(
            select(ToolExchange)
            .join(Operation, Operation.id == ToolExchange.operation_id)
            .where(Operation.run_id == run_id)
            .order_by(ToolExchange.created_at, ToolExchange.sequence)
        )
    )


def request_transport_cleanup(db, run_id, reason="run_stopped") -> int:
    """Sync scheduler/API hook; caller owns/commits the Run transaction.

    This never touches an event loop, closes a foreign process, or sends a tool
    call. Return the number of resources still requiring actual owner cleanup.
    """
    pending = set()
    for row in _run_exchanges(db, run_id):
        marker = _transport(row)
        if marker and marker.get("state") not in CLOSED_TRANSPORT_STATES:
            marker.update(state="close_requested", reason=str(reason)[:80])
            _put_transport(row, marker)
            pending.add((marker.get("owner_instance"), marker["lease_id"]))
    return len(pending)


def has_pending_transport_cleanup(db, run_id) -> bool:
    """Retained/unknown/failed cleanup remains pending until actual confirmation."""
    return any(
        marker and marker.get("state") not in CLOSED_TRANSPORT_STATES
        for row in _run_exchanges(db, run_id)
        if (marker := _transport(row))
    )


def mcp_inputs_ready(db, run_id) -> bool:
    """Gate enqueue_resume after a Question answer; no partial MCP-round resume.

    Each round must keep exactly one public LangGraph interrupt on every replay.
    Ordinary non-MCP questions do not affect this helper's result.
    """
    latest = {}
    for row in _run_exchanges(db, run_id):
        latest[(row.operation_id, row.attempt_id)] = row
    for row in latest.values():
        if row.status != "awaiting_input":
            continue
        for plan in (row.outcome or {}).get("question_plan", []):
            question = db.scalar(
                select(Question).where(
                    Question.run_id == run_id,
                    Question.interaction_key == f"mcp:{row.id}:{plan['key']}",
                )
            )
            if question is None or question.status != "answered":
                return False
    return True


def cleanup_targets(*, force=False):
    """Read persistent lifecycle facts; only this process's leases are actionable."""
    with Session(engine()) as db:
        rows = list(
            db.execute(
                select(ToolExchange, Operation, Run)
                .join(Operation, Operation.id == ToolExchange.operation_id)
                .join(Run, Run.id == Operation.run_id)
                .where(
                    ToolExchange.outcome["reconciliation"]["transport_cleanup"][
                        "owner_instance"
                    ].astext
                    == OWNER_INSTANCE
                )
                .order_by(ToolExchange.created_at, ToolExchange.sequence)
            )
        )
        latest = {}
        targets = {}
        instant = now()
        for row, operation, run in rows:
            marker = _transport(row)
            if not marker or marker.get("state") in CLOSED_TRANSPORT_STATES:
                continue
            if marker.get("owner_instance") != OWNER_INSTANCE:
                continue  # Foreign/missing owner is unknown, never assumed closed.
            reason = marker.get("reason")
            key = (operation.id, row.attempt_id)
            if key not in latest:
                latest[key] = db.scalar(
                    select(ToolExchange)
                    .where(
                        ToolExchange.operation_id == operation.id,
                        ToolExchange.attempt_id == row.attempt_id,
                    )
                    .order_by(ToolExchange.sequence.desc(), ToolExchange.created_at.desc())
                    .limit(1)
                )
            current = latest[key]
            outcome = current.outcome or {}
            continuation = outcome.get("continuation") or {}
            connection = (
                db.get(MCPConnection, continuation.get("connection_id"))
                if continuation.get("connection_id")
                else None
            )
            requires_vault = bool(
                marker.get("requires_vault")
                or (
                    connection
                    and (
                        connection.configuration.get("env_secret_refs")
                        or connection.configuration.get("bearer_secret_ref")
                    )
                )
            )
            if force:
                reason = "worker_stopping"
            elif run.cancel_requested_at or run.status in TERMINAL_STATUSES:
                reason = "run_stopped"
            elif run.deadline_at and run.deadline_at <= instant:
                reason = "run_deadline"
            elif current.status != "awaiting_input":
                reason = "exchange_finished"
            elif continuation:
                parsed = MCPOutcome.model_validate(outcome).continuation
                if parsed and min(parsed.expires_at, parsed.total_deadline) <= instant:
                    reason = "continuation_expired"
                elif any(
                    db.scalar(
                        select(Question.status).where(
                            Question.run_id == run.id,
                            Question.interaction_key == f"mcp:{current.id}:{plan['key']}",
                        )
                    )
                    in {"invalidated", "cancelled", "expired"}
                    for plan in outcome.get("question_plan", [])
                ):
                    reason = "question_invalidated"
            # An alive lease without a stop reason must remain resumable. The
            # async owner also inspects naturally completed/idle-expired tasks.
            key = marker["lease_id"]
            target = targets.setdefault(
                key,
                {
                    "lease_id": key,
                    "identity_ref": marker.get("identity_ref"),
                    "operation_id": operation.id,
                    "run_id": run.id,
                    "reason": reason,
                    "requires_vault": requires_vault,
                },
            )
            if reason:
                target["reason"] = reason
        return list(targets.values())


def confirm_transport_cleanup(lease_id, *, reason, closed, error_code=None):
    """Only called after inspecting this owner's actual SessionLease task state."""
    with Session(engine()) as db, db.begin():
        rows = list(
            db.scalars(
                select(ToolExchange)
                .where(
                    ToolExchange.outcome["reconciliation"]["transport_cleanup"][
                        "owner_instance"
                    ].astext
                    == OWNER_INSTANCE,
                    ToolExchange.outcome["reconciliation"]["transport_cleanup"]["lease_id"].astext
                    == lease_id,
                )
                .order_by(ToolExchange.created_at)
            )
        )
        affected = [
            row
            for row in rows
            if (marker := _transport(row))
            and marker.get("owner_instance") == OWNER_INSTANCE
            and marker.get("lease_id") == lease_id
        ]
        operations = {row.operation_id for row in affected}
        for operation_id in sorted(operations):
            operation = db.get(Operation, operation_id)
            run = db.scalar(select(Run).where(Run.id == operation.run_id).with_for_update())
            current = db.scalar(
                select(ToolExchange)
                .where(ToolExchange.operation_id == operation_id)
                .order_by(ToolExchange.sequence.desc(), ToolExchange.created_at.desc())
                .limit(1)
            )
            for row in (item for item in affected if item.operation_id == operation_id):
                marker = _transport(row)
                marker.update(
                    state="closed" if closed else "close_pending",
                    reason=reason,
                    checked_at=now().isoformat(),
                )
                if error_code:
                    marker["error_code"] = error_code
                _put_transport(row, marker)
            if closed and current and current.id in {row.id for row in affected}:
                if current.status == "awaiting_input" and reason != "exchange_finished":
                    # Preserve the actual input-required response/continuation in
                    # the restricted receipt; closing is not a remote rollback.
                    outcome = MCPOutcome.model_validate(current.outcome)
                    outcome.status = "outcome_unknown"
                    outcome.code = "CONTINUATION_TRANSPORT_CLOSED"
                    outcome.message = "Original MCP transport closed; no original call replay"
                    outcome.reconciliation["received_status"] = "awaiting_input"
                    current.outcome = outcome.model_dump(mode="json")
                    current.status = outcome.status
                    operation.status = "outcome_unknown"
                    operation.effect_status = "needs_reconciliation"
                    from workbench.modules.runs.service import emit, set_status

                    emit(
                        db,
                        run.id,
                        "tool.outcome_unknown",
                        {"operation_id": operation.id, "tool_name": "mcp_call"},
                    )
                    for plan in outcome.question_plan:
                        question = db.scalar(
                            select(Question).where(
                                Question.run_id == run.id,
                                Question.interaction_key == f"mcp:{current.id}:{plan.key}",
                            )
                        )
                        if question and question.status == "pending":
                            question.status = "invalidated"
                            emit(db, run.id, "question.invalidated", {"question_id": question.id})
                    job = db.scalar(select(Job).where(Job.run_id == run.id).with_for_update())
                    if (
                        job
                        and job.status == "waiting"
                        and not run.cancel_requested_at
                        and run.status not in TERMINAL_STATUSES
                    ):
                        job.status, job.available_at = "queued", now()
                        set_status(db, run, "queued")


class PostgresExchangeStore:
    async def reserve(self, request):
        return await asyncio.to_thread(self._reserve, request)

    @staticmethod
    def _reserve(request):
        with Session(engine()) as db, db.begin():
            fence(db, request.operation_id)
            row = db.scalar(
                select(ToolExchange).where(
                    ToolExchange.operation_id == request.operation_id,
                    ToolExchange.attempt_id == request.attempt_id,
                    ToolExchange.sequence == request.sequence,
                )
            )
            if row:
                return Reservation(
                    row.id, False, MCPOutcome.model_validate(row.outcome) if row.outcome else None
                )
            connection = db.get(MCPConnection, request.connection_id)
            if connection is None or connection.version != request.connection_version:
                raise AppError("mcp_version_conflict", "MCP连接版本已改变。", 409, "policy")
            payload = jsonable_encoder(asdict(request))
            payload["host_requires_vault"] = bool(
                connection.configuration.get("env_secret_refs")
                or connection.configuration.get("bearer_secret_ref")
            )
            row = ToolExchange(
                operation_id=request.operation_id,
                attempt_id=request.attempt_id,
                sequence=request.sequence,
                request=payload,
            )
            db.add(row)
            db.flush()
            return Reservation(row.id, True)

    async def mark_dispatched(self, exchange_id):
        def update():
            with Session(engine()) as db, db.begin():
                row = db.get(ToolExchange, exchange_id)
                fence(db, row.operation_id)
                row.dispatched = True
                row.status = "dispatched"

        await asyncio.to_thread(update)

    async def finish(self, exchange_id, outcome):
        def update():
            with Session(engine()) as db, db.begin():
                row = db.get(ToolExchange, exchange_id)
                operation = fence(db, row.operation_id)
                value = outcome.model_dump(mode="json")
                old_marker = _transport(row)
                if outcome.continuation and outcome.continuation.transport_lease_id:
                    value["reconciliation"]["transport_cleanup"] = old_marker or {
                        "state": "retained",
                        "lease_id": outcome.continuation.transport_lease_id,
                        "identity_ref": outcome.continuation.identity_ref,
                        "owner_instance": OWNER_INSTANCE,
                        "owner_pid": os.getpid(),
                        "requires_vault": bool(row.request.get("host_requires_vault")),
                    }
                elif old_marker:
                    value["reconciliation"]["transport_cleanup"] = old_marker
                row.outcome = value
                row.status = outcome.status
                if outcome.status == "awaiting_input":
                    operation.status = "waiting_input"

        await asyncio.to_thread(update)
