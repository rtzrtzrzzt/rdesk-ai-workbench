"""Real temporary file/isolated PG approval contracts; no model acceptance claims."""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import func, select
from test_changes import write
from test_generation_budget import configure_connection
from test_run_presentation import (  # noqa: F401 - shared private PG fixtures
    authorization_engine,
    isolated_postgres_engine,
    run_facts,
)
from workbench.contracts.errors import AppError
from workbench.contracts.requests import RunOptions
from workbench.infrastructure.database import new_id
from workbench.modules.changes.domain import ChangeRecord
from workbench.modules.conversations import Message
from workbench.modules.interactions import Approval
from workbench.modules.runs import safety
from workbench.modules.tools import file_approval, filesystem, review, service
from workbench.modules.tools.domain import Operation


@pytest.fixture
def approval_files(run_facts, monkeypatch):  # noqa: F811
    db, run, _, root = run_facts
    run.config_snapshot = {"runtime_options": RunOptions().model_dump()}
    db.commit()
    monkeypatch.setattr(file_approval, "engine", lambda: db.get_bind())
    monkeypatch.setattr(service, "engine", lambda: db.get_bind())
    monkeypatch.setattr(safety, "engine", lambda: db.get_bind())
    return db, run, root


def approved_new_file(db, run, root, *, existing=False, path="notes.txt"):
    content = Path("README.md").read_text()[:500]
    if existing:
        (root / path).write_text(content[:100])
    op, result = write(db, run, root, path, content)
    op.arguments = {
        "path": path,
        "content": content,
        "expected_version": result["previous_version"],
    }
    approval = Approval(
        run_id=run.id,
        operation_id=op.id,
        summary="Approved creation contract fixture",
        tool_name="write_file",
        arguments=op.arguments,
        parameters_digest=service.argument_digest(op.arguments),
        status="approved",
        approved=True,
        review={
            "decision": "allow",
            "source": "local_model",
            "file_rules_digest": file_approval.rules_digest(run),
        },
    )
    db.add(approval)
    db.commit()
    values = {"path": path, "expected_version": result["version"], "content": content + "\n"}
    return values, filesystem.prepare_file_change(root, values, patch=False)


def test_approved_creation_is_reused_with_exact_parameters_and_recorded_source(approval_files):
    db, run, root = approval_files
    values, prepared = approved_new_file(db, run, root)
    op = Operation(run_id=run.id, tool_call_id=new_id(), tool_name="write_file", arguments=values)
    db.add(op)
    db.commit()
    result = service.prepare_approval(run.id, op.id, "write_file", values, file_preflight=prepared)
    assert result["status"] == "approved"
    assert result["review"]["source"] == "file_creation_policy"
    assert result["review"]["parameters_digest"] == service.argument_digest(values)
    assert result["review"]["file_creation_evidence"]["expected_version"] == prepared.version


@pytest.mark.parametrize(
    "boundary",
    [
        "existing",
        "external_edit",
        "new_input",
        "rules_change",
        "unconfirmed",
        "empty",
        "command",
        "binding",
        "rules_file",
    ],
)
def test_creation_reuse_is_narrow_and_ambiguous_changes_fall_back(approval_files, boundary):
    db, run, root = approval_files
    path = "AGENTS.md" if boundary == "rules_file" else "notes.txt"
    values, prepared = approved_new_file(db, run, root, existing=boundary == "existing", path=path)
    name = "write_file"
    if boundary == "external_edit":
        (root / path).write_text(Path("README.md").read_text()[:400])
        values["expected_version"] = filesystem.read_text(root / path)[1]
        prepared = filesystem.prepare_file_change(root, values, patch=False)
    elif boundary == "new_input":
        db.add(Message(thread_id=run.thread_id, run_id=run.id, role="user", content=run.input))
    elif boundary == "rules_change":
        run.config_snapshot = {
            **run.config_snapshot,
            "context_manifest": {
                "rules": [
                    {"source_path": "AGENTS.md", "version": filesystem.read_text(root / path)[1]}
                ]
            },
        }
    elif boundary == "unconfirmed":
        db.scalar(select(ChangeRecord).where(ChangeRecord.run_id == run.id)).state = "intent"
    elif boundary == "empty":
        prepared = filesystem.prepare_file_change(root, {**values, "content": ""}, patch=False)
    elif boundary == "command":
        name = "exec_command"
    elif boundary == "binding":
        with db.no_autoflush:
            run.execution_binding_id = new_id()
            assert file_approval.reusable_creation(db, run, name, prepared) is None
            run.execution_binding_id = None
    db.commit()
    if boundary != "binding":
        assert file_approval.reusable_creation(db, run, name, prepared) is None


def test_manual_approval_stays_pending_even_for_own_new_file(approval_files):
    db, run, root = approval_files
    values, prepared = approved_new_file(db, run, root)
    run.config_snapshot = {"runtime_options": {"approval_mode": "manual"}}
    op = Operation(run_id=run.id, tool_call_id=new_id(), tool_name="write_file", arguments=values)
    db.add(op)
    db.commit()
    result = service.prepare_approval(run.id, op.id, "write_file", values, file_preflight=prepared)
    assert result["status"] == "pending"
    assert not db.get(Approval, result["id"]).approved


def test_wrong_version_returns_before_review_and_without_file_effects(approval_files, monkeypatch):
    db, run, root = approval_files
    content = Path("README.md").read_text()[:500]
    (root / "notes.txt").write_text(content)
    monkeypatch.setattr(service, "rule_notice", lambda *_: None)

    async def no_model(*_):
        pytest.fail("A deterministic version conflict must not request model approval")

    monkeypatch.setattr(review, "review", no_model)
    result = asyncio.run(
        service.execute_one(
            run.id,
            {
                "id": new_id(),
                "name": "write_file",
                "arguments": {
                    "path": "notes.txt",
                    "expected_version": "missing",
                    "content": content + "\n",
                },
            },
        )
    )
    assert result["error"]["code"] == "file_version_conflict"
    db.expire_all()
    assert (
        db.scalar(select(func.count()).select_from(Approval).where(Approval.run_id == run.id)) == 0
    )
    op = db.scalar(select(Operation).where(Operation.run_id == run.id))
    assert op.effect_status == "not_started" and op.status == "failed"
    assert (root / "notes.txt").read_text() == content


def test_preflight_read_error_does_not_claim_unknown_write(approval_files, monkeypatch):
    _, run, _ = approval_files

    def unreadable(*_, **__):
        raise PermissionError

    monkeypatch.setattr(filesystem, "prepare_file_change", unreadable)
    with pytest.raises(AppError) as error:
        file_approval.preflight(run.id, "write_file", {})
    assert error.value.code == "file_preflight_unavailable"
    assert error.value.kind != "outcome_unknown"


def test_review_preparation_preserves_creation_scope_metadata(approval_files, monkeypatch):
    db, run, _ = approval_files
    configure_connection(db, run, monkeypatch)
    monkeypatch.setattr(review, "engine", lambda: db.get_bind())
    values = {
        "path": "notes.txt",
        "content": Path("README.md").read_text()[:200],
        "expected_version": "missing",
    }
    op = Operation(run_id=run.id, tool_call_id=new_id(), tool_name="write_file", arguments=values)
    db.add(op)
    db.commit()
    queued = service.prepare_approval(run.id, op.id, "write_file", values)
    digest = queued["review"]["file_rules_digest"]
    assert review._prepare(run.id, queued["id"]) is not None
    db.expire_all()
    approval = db.get(Approval, queued["id"])
    assert approval.review["decision"] == "reviewing"
    assert approval.review["file_rules_digest"] == digest
    assert review.queued_review(approval)["file_rules_digest"] == digest
