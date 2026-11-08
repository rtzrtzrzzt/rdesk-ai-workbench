"""Disposable PostgreSQL Run facts and projections; no model or UI acceptance."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_api_authorization import (  # noqa: F401 - shared native PG fixtures
    authorization_engine,
    isolated_postgres_engine,
)
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import new_id, now
from workbench.modules.changes import service as changes
from workbench.modules.changes.domain import CommandCapture
from workbench.modules.conversations import Message, Thread
from workbench.modules.identity import User
from workbench.modules.processes.domain import CommandExecution
from workbench.modules.runs import presentation, scheduler
from workbench.modules.runs.domain import Attempt, Job, Run, RunEvent, Step
from workbench.modules.tools.domain import Operation
from workbench.modules.workspaces import Workspace


@pytest.fixture
def run_facts(authorization_engine, tmp_path, monkeypatch):  # noqa: F811 - pytest fixture injection
    """Keep production sessions/SQL intact inside one rollback-only private DB connection."""
    with authorization_engine.connect() as connection:
        transaction = connection.begin()
        monkeypatch.setattr(scheduler, "engine", lambda: connection)
        monkeypatch.setattr(
            changes, "storage_path", lambda relative: tmp_path / "private" / relative
        )
        with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
            user = User(id=new_id(), username="run-presentation-evidence")
            root = tmp_path / "workspace"
            root.mkdir()
            workspace = Workspace(
                id=new_id(), user_id=user.id, name="Presentation evidence", root_path=str(root)
            )
            db.add_all([user, workspace])
            db.flush()
            thread = Thread(
                id=new_id(),
                user_id=user.id,
                workspace_id=workspace.id,
                title="Explicit protocol test",
                title_source="explicit",
            )
            db.add(thread)
            db.flush()
            run = Run(
                id=new_id(),
                user_id=user.id,
                workspace_id=workspace.id,
                thread_id=thread.id,
                input="Inspect real committed Run facts",
                status="running",
            )
            db.add(run)
            db.flush()
            db.add(
                Job(
                    run_id=run.id,
                    available_at=now(),
                    status="running",
                    lease_owner="test-worker",
                    lease_epoch=1,
                    lease_until=now() + timedelta(minutes=1),
                )
            )
            db.commit()
            yield db, run, thread, root
        transaction.rollback()


def assistant(db, run, *, provider=None, status="succeeded", role="assistant"):
    step = Step(id=new_id(), run_id=run.id, logical_key=new_id(), kind="model", status=status)
    message = Message(
        id=new_id(),
        thread_id=run.thread_id,
        run_id=run.id,
        role=role,
        content="Persisted assistant output",
        status="completed",
        provider_message=provider,
    )
    db.add_all([step, message])
    db.flush()
    attempt = Attempt(
        id=new_id(),
        step_id=step.id,
        run_id=run.id,
        status=status,
        message_id=message.id,
        finished_at=now(),
    )
    db.add(attempt)
    db.commit()
    return message, attempt


def finish(db, run, **kwargs):
    scheduler.finish(run.id, "test-worker", 1, **kwargs)
    db.expire_all()
    db.refresh(run)


def finalized_events(db, run):
    return list(
        db.scalars(
            select(RunEvent)
            .where(RunEvent.run_id == run.id, RunEvent.type == "run.reply_finalized")
            .order_by(RunEvent.seq)
        )
    )


def test_successful_finish_seals_real_attempt_once_and_projects_typed_changes(run_facts):
    db, run, thread, _ = run_facts
    message, attempt = assistant(db, run)
    finish(db, run)
    assert run.status == "succeeded"
    assert run.final_message_id == message.id and run.final_attempt_id == attempt.id
    events = finalized_events(db, run)
    assert len(events) == 1 and run.finalized_event_seq == events[0].seq
    assert db.scalar(select(Job.completion_status).where(Job.run_id == run.id)) == "succeeded"
    presentation.seal_reply(db, run)
    finish(db, run)
    assert len(finalized_events(db, run)) == 1
    result = presentation.ConversationPresentation.model_validate(
        presentation.view(db, run.user_id, thread.id)
    )
    assert result.groups[0].final_message_id == message.id
    assert result.groups[0].changes_coverage == "complete"
    assert result.groups[0].changed_files == 0
    assert result.groups[0].elapsed_ms is not None


@pytest.mark.parametrize("kind", ["tool_call", "tool_message", "failed_attempt"])
def test_tool_rounds_and_failed_attempts_never_seal_as_final(run_facts, kind):
    db, run, thread, _ = run_facts
    if kind == "tool_call":
        assistant(db, run)  # A newer tool round must not fall back to this older reply.
    assistant(
        db,
        run,
        provider={"tool_calls": [{"id": "recorded-call"}]} if kind == "tool_call" else None,
        role="tool" if kind == "tool_message" else "assistant",
        status="failed" if kind == "failed_attempt" else "succeeded",
    )
    finish(db, run)
    assert run.status == "succeeded" and run.final_message_id is None
    assert not finalized_events(db, run)
    assert presentation.view(db, run.user_id, thread.id)["groups"][0]["final_message_id"] is None


@pytest.mark.parametrize("outcome", ["failed", "cancelled"])
def test_failure_and_cancellation_leave_successful_partial_message_unfinalized(run_facts, outcome):
    db, run, thread, _ = run_facts
    assistant(db, run)
    if outcome == "failed":
        finish(db, run, error=AppError("test_execution_failure", "Recorded execution failure"))
    else:
        finish(db, run, cancellation="cancelled")
    assert run.status == outcome and run.final_message_id is None and run.final_attempt_id is None
    assert not finalized_events(db, run)
    group = presentation.view(db, run.user_id, thread.id)["groups"][0]
    assert group["final_message_id"] is None
    assert "本次任务未完成最终交付" in group["completion_summary"]
    assert "工具操作成功不等于任务验证通过" in group["completion_summary"]


def test_historical_success_fallback_is_read_only_and_branch_hides_unseen_final(run_facts):
    db, run, thread, _ = run_facts
    prefix = Message(
        id=new_id(),
        thread_id=thread.id,
        run_id=run.id,
        role="user",
        content="Immutable branch prefix",
    )
    db.add(prefix)
    db.commit()
    message, _ = assistant(db, run)
    run.status = "succeeded"
    db.commit()
    before = db.scalar(select(func.count()).select_from(RunEvent))
    original = presentation.view(db, run.user_id, thread.id)["groups"][0]
    assert original["final_message_id"] == message.id and original["elapsed_ms"] is None
    assert run.final_message_id is None and run.final_attempt_id is None
    assert db.scalar(select(func.count()).select_from(RunEvent)) == before
    branch = Thread(
        id=new_id(),
        user_id=run.user_id,
        workspace_id=run.workspace_id,
        parent_thread_id=thread.id,
        fork_message_id=prefix.id,
    )
    db.add(branch)
    db.commit()
    inherited = presentation.view(db, run.user_id, branch.id)["groups"][0]
    assert inherited["final_message_id"] is None and inherited["finalized_event_seq"] is None
    assert run.final_message_id is None


def test_branch_never_projects_stored_final_marker_beyond_its_visible_prefix(run_facts):
    db, run, thread, _ = run_facts
    prefix = Message(
        id=new_id(),
        thread_id=thread.id,
        run_id=run.id,
        role="user",
        content="Visible inherited prefix",
    )
    db.add(prefix)
    db.commit()
    final, _ = assistant(db, run)
    finish(db, run)
    assert run.final_message_id == final.id
    branch = Thread(
        id=new_id(),
        user_id=run.user_id,
        workspace_id=run.workspace_id,
        parent_thread_id=thread.id,
        fork_message_id=prefix.id,
    )
    db.add(branch)
    db.commit()
    projected = presentation.view(db, run.user_id, branch.id)["groups"][0]
    assert projected["status"] == "succeeded"
    assert projected["final_message_id"] is None and projected["finalized_event_seq"] is None
    assert run.final_message_id == final.id and len(finalized_events(db, run)) == 1


def test_final_reply_can_be_sealed_while_real_resource_ledger_and_changes_remain_pending(run_facts):
    db, run, thread, root = run_facts
    message, _ = assistant(db, run)
    operation = Operation(
        id=new_id(),
        run_id=run.id,
        tool_call_id=new_id(),
        tool_name="start_command",
        arguments={},
        status="succeeded",
        effect_status="confirmed",
    )
    db.add(operation)
    db.flush()
    db.add(
        CommandExecution(
            id=operation.id,
            operation_id=operation.id,
            user_id=run.user_id,
            workspace_id=run.workspace_id,
            run_id=run.id,
            nonce=new_id(),
            command="Pending resource ledger, never dispatched by this test",
            argv=[],
            timeout_seconds=60,
            max_output_bytes=1024,
            output_path=f"commands/{operation.id}",
            status="running",
            resources_closed=False,
        )
    )
    capture = CommandCapture(
        operation_id=operation.id,
        run_id=run.id,
        workspace_id=run.workspace_id,
        root_path=str(root),
        baseline={},
        state="pending",
        gaps=["shared_directory_observation"],
    )
    db.add(capture)
    db.commit()
    finish(db, run)
    assert run.status == "waiting_resource" and run.final_message_id == message.id
    assert db.scalar(select(Job.completion_status).where(Job.run_id == run.id)) == "succeeded"
    projected = presentation.view(db, run.user_id, thread.id)["groups"][0]
    assert (
        projected["final_message_id"] == message.id and projected["changes_coverage"] == "pending"
    )
    assert db.get(CommandCapture, capture.id).state == "pending"
    assert db.get(CommandExecution, operation.id).resources_closed is False
