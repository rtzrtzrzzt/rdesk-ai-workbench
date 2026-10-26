"""Synthetic protocol checks only; no user DB, model, or UI acceptance evidence."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from workbench.composition import tables
from workbench.contracts.errors import AppError
from workbench.modules.collaboration.domain import AgentMessage, AgentTurn
from workbench.modules.collaboration.inbox import (
    has_new_directive,
    has_pending_inputs,
    mailbox_runs,
    pending_condition,
)
from workbench.modules.collaboration.policy import CHILD_COLLABORATION_TOOLS, child_tool_allowed
from workbench.modules.tools.registry import TOOLS, AgentFollowup
from workbench.modules.tools.registry import AgentMessage as MessageArguments
from workbench.modules.tools.service import enforce_agent_scope


def test_mail_wait_requires_mail_or_timeout_even_when_child_has_no_children(
    protocol_db, monkeypatch
):
    from workbench.infrastructure.database import now
    from workbench.modules.collaboration import service

    run = SimpleNamespace(wait_expires_at=now() + timedelta(seconds=300))
    monkeypatch.setattr(protocol_db, "get", lambda *_: run)
    monkeypatch.setattr(service, "pending", lambda *_: False)
    assert service.wait_ready(protocol_db, "recipient")
    assert not service.wait_ready(protocol_db, "recipient", messages_only=True)
    message = add_message(protocol_db, "new-reply", kind="reply")
    assert service.wait_ready(protocol_db, "recipient", messages_only=True)
    message.delivered_attempt_id = "consuming-attempt"
    protocol_db.flush()
    assert not service.wait_ready(protocol_db, "recipient", messages_only=True)
    run.wait_expires_at = now() - timedelta(seconds=1)
    assert service.wait_ready(protocol_db, "recipient", messages_only=True)


def test_child_can_address_parent_but_not_siblings_or_send_control():
    from workbench.modules.collaboration.service import resolve_recipient

    root, child = SimpleNamespace(id="root"), SimpleNamespace(parent_run_id="root")
    assert resolve_recipient(None, root, child, "parent", "question") is root
    for target, kind in [
        ("00000000-0000-0000-0000-000000000001", "note"),
        ("parent", "instruction"),
    ]:
        with pytest.raises(AppError) as error:
            resolve_recipient(None, root, child, target, kind)
        assert error.value.status == 403


@pytest.fixture
def protocol_db():
    """Use isolated in-memory protocol tables, never the configured engine."""
    engine = create_engine("sqlite://")
    tables.AgentMessage.metadata.create_all(
        engine, tables=[tables.AgentMessage.__table__, tables.AgentTurn.__table__]
    )
    with Session(engine) as db:
        yield db
    engine.dispose()


def add_message(db, identifier, *, kind="note", target="recipient", **values):
    row = AgentMessage(
        id=identifier,
        root_run_id="root",
        from_run_id="sender",
        to_run_id=target,
        operation_id=f"operation-{identifier}",
        kind=kind,
        content="Synthetic protocol input",
        **values,
    )
    db.add(row)
    db.flush()
    return row


@pytest.mark.parametrize("kind", ["note", "question", "reply", "result"])
def test_information_wakes_mailbox_without_steering(protocol_db, kind):
    row = add_message(protocol_db, "information", kind=kind)
    assert has_pending_inputs(protocol_db, "recipient")
    assert not has_new_directive(protocol_db, "recipient")
    assert not has_pending_inputs(protocol_db, "another-run")
    row.delivered_attempt_id = "consuming-attempt"
    protocol_db.flush()
    assert not has_pending_inputs(protocol_db, "recipient")


@pytest.mark.parametrize("values", [{"kind": "instruction"}, {"source": "user"}])
def test_directive_only_steers_its_recipient_until_included_or_delivered(protocol_db, values):
    row = add_message(protocol_db, "directive", **values)
    assert has_new_directive(protocol_db, "recipient")
    assert not has_new_directive(protocol_db, "another-run")
    assert not has_new_directive(protocol_db, "recipient", included=[row.id])
    assert not protocol_db.scalar(
        select(AgentMessage.id).where(*pending_condition("recipient", excluded=[row.id]))
    )
    row.delivered_attempt_id = "consuming-attempt"
    protocol_db.flush()
    assert not has_new_directive(protocol_db, "recipient")


def test_mailbox_follows_stable_agent_identity_across_turns(protocol_db):
    protocol_db.add_all(
        [
            AgentTurn(agent_id="stable-agent", run_id="first-turn"),
            AgentTurn(agent_id="stable-agent", run_id="followup-turn"),
            AgentTurn(agent_id="sibling-agent", run_id="sibling-turn"),
        ]
    )
    protocol_db.flush()
    assert set(mailbox_runs(protocol_db, SimpleNamespace(id="followup-turn"))) == {
        "first-turn",
        "followup-turn",
    }
    assert mailbox_runs(protocol_db, SimpleNamespace(id="root")) == ["root"]


def test_child_communication_is_allowed_but_delegation_and_control_are_denied():
    restricted = {
        name
        for name, spec in TOOLS.items()
        if spec.module == "collaboration" and name not in CHILD_COLLABORATION_TOOLS
    } | {"exec_command", "start_command"}
    child = SimpleNamespace(
        parent_run_id="root",
        config_snapshot={
            "allowed_tools": sorted(restricted | CHILD_COLLABORATION_TOOLS),
            "agent_mode": "read_only",
        },
    )
    for name in CHILD_COLLABORATION_TOOLS:
        assert child_tool_allowed(name, TOOLS[name].module)
        enforce_agent_scope(None, child, name, {})
    # Even a stale or overbroad snapshot cannot grant control over siblings.
    for name in restricted:
        assert not child_tool_allowed(name, TOOLS[name].module)
        with pytest.raises(AppError) as error:
            enforce_agent_scope(None, child, name, {})
        assert error.value.status == 403


def test_parent_is_a_message_target_but_cannot_be_a_followup_control_target():
    question_id = "00000000-0000-0000-0000-000000000001"
    reply = MessageArguments(
        agent_id="parent", message="Synthetic reply", kind="reply", reply_to_id=question_id
    )
    assert reply.agent_id == "parent" and reply.reply_to_id == UUID(question_id)
    with pytest.raises(ValidationError):
        AgentFollowup(agent_id="parent", message="Synthetic instruction")
    with pytest.raises(ValidationError):
        MessageArguments(agent_id="sibling", message="Synthetic note")
    with pytest.raises(ValidationError):
        MessageArguments(agent_id="parent", message="Synthetic result", kind="result")
