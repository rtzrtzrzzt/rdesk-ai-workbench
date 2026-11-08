"""Real temporary file effects and isolated evidence tables; no product/UI acceptance."""

import json
import subprocess
import sys

import pytest
from sqlalchemy import JSON, MetaData, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from workbench.composition import tables  # noqa: F401 - resolves model foreign keys
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import Base, new_id
from workbench.modules.access import domain as access_domain  # noqa: F401
from workbench.modules.agent_services import domain as service_domain  # noqa: F401
from workbench.modules.changes import service
from workbench.modules.changes.domain import ChangeRecord, CommandCapture
from workbench.modules.conversations import Message, Thread
from workbench.modules.files.domain import Artifact
from workbench.modules.identity import User
from workbench.modules.processes.domain import CommandExecution
from workbench.modules.runs.domain import Run, RunEvent
from workbench.modules.tools import filesystem, notebooks
from workbench.modules.tools.domain import Operation
from workbench.modules.workspaces import Workspace


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    """Use actual ORM queries/transactions with a private temporary SQLite database."""
    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        copied = table.to_metadata(metadata)
        for column in copied.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
    engine = create_engine(f"sqlite:///{tmp_path / 'evidence.db'}")
    metadata.create_all(
        engine,
        tables=[
            metadata.tables[model.__tablename__]
            for model in (
                User,
                Workspace,
                Thread,
                Message,
                Run,
                RunEvent,
                Operation,
                Artifact,
                ChangeRecord,
                CommandCapture,
                CommandExecution,
            )
        ],
    )
    store = tmp_path / "private"
    monkeypatch.setattr(service, "storage_path", lambda relative: store / relative)
    root = tmp_path / "workspace"
    root.mkdir()
    with Session(engine) as db:
        user = User(id=new_id(), username="isolated-file-evidence")
        workspace = Workspace(
            id=new_id(), user_id=user.id, name="Temporary files", root_path=str(root)
        )
        thread = Thread(id=new_id(), user_id=user.id, workspace_id=workspace.id)
        db.add_all([user, workspace, thread])
        db.commit()
        run = Run(
            id=new_id(),
            user_id=user.id,
            workspace_id=workspace.id,
            thread_id=thread.id,
            input="File evidence",
        )
        db.add(run)
        db.commit()
        yield db, run, root
    engine.dispose()


def operation(db, run, name="write_file"):
    row = Operation(
        run_id=run.id,
        tool_call_id=new_id(),
        tool_name=name,
        arguments={},
        effect_status="confirmed",
        status="succeeded",
    )
    db.add(row)
    db.commit()
    return row


def write(db, run, root, path, content):
    op = operation(db, run)

    def before_write(target, before, after):
        row = service.prepare_managed_change(db, op, root, target, before, after)
        identifier = row.id
        db.commit()
        return identifier

    def after_write(identifier):
        service.mark_applied(db, identifier)
        db.commit()

    target = root / path
    version = filesystem.read_text(target)[1] if target.exists() else "missing"
    result = filesystem.change_file(
        root,
        {"path": path, "expected_version": version, "content": content},
        patch=False,
        before_write=before_write,
        after_write=after_write,
    )
    op.result = result
    service.confirm_operation(db, op, result)
    db.commit()
    return op, result


def test_repeated_writes_are_net_content_changes_and_reversion_disappears(evidence):
    db, run, root = evidence
    (root / "notes.txt").write_text("one\ntwo\n")
    first, _ = write(db, run, root, "notes.txt", "one\nchanged\n")
    second, result = write(db, run, root, "notes.txt", "one\nchanged\nthree\n")
    summary = service.summary_for_visible(db, [first.id, second.id])
    assert summary.coverage == "complete"
    assert summary.totals.model_dump() == {"files": 1, "added_lines": 2, "removed_lines": 1}
    assert summary.files[0].id == result["change_id"]
    third, _ = write(db, run, root, "notes.txt", "one\ntwo\n")
    assert not service.summary_for_visible(db, [first.id, second.id, third.id]).files


def test_external_edit_breaks_chain_and_visible_operation_filter_hides_future(evidence):
    db, run, root = evidence
    first, _ = write(db, run, root, "notes.txt", "initial\n")
    (root / "notes.txt").write_text("external\n")
    second, _ = write(db, run, root, "notes.txt", "later\n")
    prefix = service.summary_for_visible(db, [first.id])
    assert prefix.totals.added_lines == 1 and prefix.files[0].kind == "added"
    summary = service.summary_for_visible(db, [first.id, second.id])
    assert summary.coverage == "partial"
    assert "discontinuous_version_chain" in summary.gaps
    assert summary.totals.added_lines is None and summary.files[0].added_lines is None


def test_intent_is_durable_but_never_claims_effect_or_replays(evidence):
    db, run, root = evidence
    target = root / "notes.txt"
    target.write_bytes(b"before")
    op = operation(db, run)
    row = service.prepare_managed_change(db, op, root, target, b"before", b"after")
    db.commit()
    target.write_bytes(b"after")  # An external matching observation is not our write receipt.
    summary = service.summary_for_visible(db, [op.id])
    assert summary.coverage == "partial" and not summary.files
    assert summary.totals.added_lines is None
    with pytest.raises(AppError) as error:
        service.prepare_managed_change(db, op, root, target, b"before", b"after")
    assert error.value.kind == "outcome_unknown"
    assert db.get(ChangeRecord, row.id).state == "intent"


def test_root_child_net_scope_and_branch_prefix_never_include_future_child(evidence):
    db, run, root = evidence
    first, first_result = write(db, run, root, "notes.txt", "prefix\n")
    db.add(Message(thread_id=run.thread_id, run_id=run.id, role="tool", operation_id=first.id))
    db.commit()
    spawn = operation(db, run, "spawn_agent")
    db.add(Message(thread_id=run.thread_id, run_id=run.id, role="tool", operation_id=spawn.id))
    anchor = Message(
        id=new_id(),
        thread_id=run.thread_id,
        run_id=run.id,
        role="assistant",
        content="Branch boundary",
    )
    db.add(anchor)
    db.commit()
    child_thread = Thread(id=new_id(), user_id=run.user_id, workspace_id=run.workspace_id)
    child = Run(
        id=new_id(),
        user_id=run.user_id,
        workspace_id=run.workspace_id,
        thread_id=child_thread.id,
        input="Child file",
        parent_run_id=run.id,
        root_run_id=run.id,
    )
    branch = Thread(
        id=new_id(),
        user_id=run.user_id,
        workspace_id=run.workspace_id,
        parent_thread_id=run.thread_id,
        fork_message_id=anchor.id,
    )
    db.add_all([child_thread, child, branch])
    db.commit()
    child_op, child_result = write(db, child, root, "notes.txt", "child\n")
    final, _ = write(db, run, root, "notes.txt", "future\n")
    own = service.view(db, run.user_id, run.thread_id, run.id)
    assert own.coverage == "complete" and own.totals.files == 1
    assert own.totals.added_lines == 1 and own.totals.removed_lines == 0
    child_view = service.view(db, run.user_id, child_thread.id, child.id)
    assert child_view.files[0].id == child_result["change_id"]
    assert child_view.totals.added_lines == child_view.totals.removed_lines == 1
    prefix = service.view(db, run.user_id, branch.id, run.id)
    assert prefix.files[0].id == first_result["change_id"]
    assert prefix.coverage == "partial" and "inherited_child_scope_unavailable" in prefix.gaps
    assert final.id not in service._visible_ids(db, run.user_id, branch.id)
    assert child_op.id not in service._visible_ids(db, run.user_id, branch.id)
    assert "prefix" in service.details(db, run.user_id, branch.id, first_result["change_id"]).diff
    with pytest.raises(AppError) as error:
        service.details(db, run.user_id, branch.id, child_result["change_id"])
    assert error.value.status == 404


def test_timed_out_command_keeps_real_pre_timeout_file_effect(evidence):
    db, run, root = evidence
    op = operation(db, run, "exec_command")
    service.begin_command(db, op, root)
    db.commit()
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import time; "
            "Path('partial.txt').write_text('persisted\\n'); time.sleep(10)",
        ],
        cwd=root,
    )
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=0.2)
    finally:
        process.kill()
        process.wait(timeout=5)
    assert process.returncode is not None
    command = type(
        "Closed", (), {"operation_id": op.id, "run_id": run.id, "resources_closed": True}
    )()
    service.finish_command(db, command)
    db.commit()
    summary = service.summary_for_visible(db, [op.id])
    assert summary.files[0].path == "partial.txt" and summary.totals.added_lines == 1


def test_capture_excludes_protected_files_symlinks_and_replaced_root(evidence):
    db, run, root = evidence
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("protected")
    (root / ".env").write_text("protected")
    (root / "ordinary.txt").write_text("visible")
    outside = root.parent / "outside.txt"
    outside.write_text("outside")
    (root / "linked.txt").symlink_to(outside)
    (root / "linked-internal.txt").symlink_to(root / "ordinary.txt")
    manifest, gaps = service.scan(db, run, root)
    assert set(manifest) == {"ordinary.txt"}
    assert "non_regular_file_excluded" in gaps
    moved = root.with_name("moved")
    root.rename(moved)
    root.symlink_to(moved, target_is_directory=True)
    with pytest.raises(AppError) as error:
        service.scan(db, run, root)
    assert error.value.code == "change_root_unavailable"


def test_version_is_rechecked_after_durable_intent_before_replace(evidence):
    _, _, root = evidence
    target = root / "notes.txt"
    target.write_text("before")
    version = filesystem.read_text(target)[1]

    def changed_after_intent(*_):
        target.write_text("external")
        return new_id()

    with pytest.raises(AppError) as error:
        filesystem.change_file(
            root,
            {"path": "notes.txt", "expected_version": version, "content": "after"},
            patch=False,
            before_write=changed_after_intent,
        )
    assert error.value.code == "file_version_conflict"
    assert target.read_text() == "external"


@pytest.mark.parametrize("exit_code", [0, 7])
def test_command_persisted_baseline_captures_create_delete_and_failure(evidence, exit_code):
    db, run, root = evidence
    (root / "deleted.txt").write_text("gone\n")
    op = operation(db, run, "exec_command")
    capture = service.begin_command(db, op, root)
    db.commit()
    command = CommandExecution(
        id=op.id,
        operation_id=op.id,
        user_id=run.user_id,
        workspace_id=run.workspace_id,
        run_id=run.id,
        nonce=new_id(),
        command="temporary file effects",
        argv=[],
        timeout_seconds=5,
        max_output_bytes=1024,
        output_path=f"commands/{op.id}",
    )
    db.add(command)
    db.commit()
    completed = subprocess.run(
        ["/bin/sh", "-c", f"rm deleted.txt; printf 'new\\n' > created.txt; exit {exit_code}"],
        cwd=root,
        timeout=5,
        check=False,
    )
    assert completed.returncode == exit_code
    service.finish_command(db, command)
    assert capture.state == "pending"
    assert service.summary_for_visible(db, [op.id]).coverage == "pending"
    command.resources_closed = True
    service.finish_command(db, command)
    db.commit()
    summary = service.summary_for_visible(db, [op.id])
    assert summary.coverage == "partial" and capture.state == "closed"
    assert {(row.path, row.kind) for row in summary.files} == {
        ("created.txt", "added"),
        ("deleted.txt", "deleted"),
    }
    assert all(row.attribution == "observed" for row in summary.files)
    assert summary.totals.added_lines == summary.totals.removed_lines == 1
    service.finish_command(db, command)
    assert len(list(db.scalars(select(ChangeRecord)))) == 2


def test_binary_large_files_and_scan_budget_do_not_fabricate_zero_counts(evidence, monkeypatch):
    db, run, root = evidence
    op = operation(db, run, "start_command")
    (root / "binary.bin").write_bytes(b"before\x00")
    (root / "large.txt").write_bytes(b"a" * 50)
    monkeypatch.setattr(service, "SNAPSHOT_LIMIT", 32)
    service.begin_command(db, op, root)
    db.commit()
    (root / "binary.bin").write_bytes(b"after\x00")
    (root / "large.txt").write_bytes(b"b" * 50)
    command = type(
        "Closed", (), {"operation_id": op.id, "run_id": run.id, "resources_closed": True}
    )()
    service.finish_command(db, command)
    db.commit()
    summary = service.summary_for_visible(db, [op.id])
    assert summary.coverage == "partial" and len(summary.files) == 2
    assert summary.totals.added_lines is None and summary.totals.removed_lines is None
    assert all(row.added_lines is None for row in summary.files)


def test_notebook_write_uses_same_snapshot_hooks(evidence):
    db, run, root = evidence
    path = root / "notes.ipynb"
    path.write_text(json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": []}))
    op = operation(db, run, "edit_notebook")
    seen = []

    def capture(target, before, after):
        assert target == path and json.loads(before)["cells"] == []
        row = service.prepare_managed_change(db, op, root, target, before, after)
        db.commit()
        seen.append(row.id)
        return row.id

    result = notebooks.edit_notebook(
        root,
        {
            "path": path.name,
            "expected_version": filesystem.read_text(path)[1],
            "operations": [{"action": "insert", "index": 0, "source": "real source"}],
        },
        before_write=capture,
    )
    assert result["change_id"] == seen[0] and result["executed"] is False
    assert json.loads(path.read_text())["cells"][0]["source"] == ["real source"]


def test_pending_capture_and_missing_legacy_evidence_are_explicit(evidence, monkeypatch):
    db, run, root = evidence
    op = operation(db, run, "exec_command")
    assert service.summary_for_visible(db, [op.id]).coverage == "unknown"
    monkeypatch.setattr(service, "SCAN_FILES", 0)
    (root / "file.txt").write_text("bounded")
    service.begin_command(db, op, root)
    db.commit()
    summary = service.summary_for_visible(db, [op.id])
    assert summary.coverage == "pending" and "scan_budget" in summary.gaps
    assert summary.totals.added_lines is None


def test_no_change_command_still_publishes_pending_and_closed_coverage(evidence):
    db, run, root = evidence
    op = operation(db, run, "exec_command")
    service.begin_command(db, op, root)
    db.commit()
    pending = service.summary_for_visible(db, [op.id])
    command = type(
        "Closed", (), {"operation_id": op.id, "run_id": run.id, "resources_closed": True}
    )()
    service.finish_command(db, command)
    db.commit()
    closed = service.summary_for_visible(db, [op.id])
    events = list(db.scalars(select(RunEvent).order_by(RunEvent.seq)))
    assert len(events) == 2 and all(event.type == "changes.updated" for event in events)
    assert events[0].payload["coverage"] == "pending"
    assert events[1].payload["coverage"] == "partial"
    assert closed.revision != pending.revision and not closed.files
    assert all(set(event.payload) == {"revision", "ids", "coverage"} for event in events)


def closed_command(op):
    return type(
        "Closed", (), {"operation_id": op.id, "run_id": op.run_id, "resources_closed": True}
    )()


def test_command_reuses_unchanged_content_and_releases_closed_baseline(evidence, monkeypatch):
    db, run, root = evidence
    (root / "first.txt").write_text("same content\n")
    (root / "second.txt").write_text("same content\n")
    published = []
    publish = service.publish_bytes

    def track_publish(path, data):
        published.append(path)
        publish(path, data)

    monkeypatch.setattr(service, "publish_bytes", track_publish)
    op = operation(db, run, "exec_command")
    capture = service.begin_command(db, op, root)
    db.commit()
    assert len(published) == 1
    assert len({item["artifact_id"] for item in capture.baseline.values()}) == 1
    service.finish_command(db, closed_command(op))
    assert published[0].is_file()  # Removal waits for the database commit.
    db.commit()
    assert len(published) == 1 and not published[0].exists()
    assert capture.baseline == {} and capture.state == "closed"
    assert not list(db.scalars(select(Artifact)))
    assert not list(db.scalars(select(ChangeRecord)))
    assert (root / "first.txt").read_text() == "same content\n"


def test_pending_capture_and_change_records_keep_shared_snapshots(evidence):
    db, run, root = evidence
    (root / "notes.txt").write_text("before\n")
    first = operation(db, run, "exec_command")
    first_capture = service.begin_command(db, first, root)
    db.commit()
    before_id = first_capture.baseline["notes.txt"]["artifact_id"]
    second = operation(db, run, "exec_command")
    second_capture = service.begin_command(db, second, root)
    db.commit()
    assert second_capture.baseline["notes.txt"]["artifact_id"] == before_id
    service.finish_command(db, closed_command(first))
    db.commit()
    assert db.get(Artifact, before_id) is not None
    assert service._content(db, before_id, True) == "before\n"
    (root / "notes.txt").write_text("after\n")
    service.finish_command(db, closed_command(second))
    db.commit()
    record = db.scalar(select(ChangeRecord).where(ChangeRecord.operation_id == second.id))
    assert record.before_artifact_id == before_id
    assert len(list(db.scalars(select(Artifact)))) == 2

    # Old closed captures can still contain a baseline after an upgrade. Its
    # removal must respect ChangeRecords that independently reference the bytes.
    first_capture.baseline = {"notes.txt": {"artifact_id": before_id}}
    db.commit()
    third = operation(db, run, "exec_command")
    service.begin_command(db, third, root)
    service.finish_command(db, closed_command(third))
    db.commit()
    assert first_capture.baseline == {} and second_capture.baseline == {}
    assert service._content(db, record.before_artifact_id, True) == "before\n"
    assert service._content(db, record.after_artifact_id, True) == "after\n"


@pytest.mark.parametrize("nested", [False, True])
def test_snapshot_release_rollback_keeps_pending_baseline_readable(evidence, nested):
    db, run, root = evidence
    (root / "notes.txt").write_text("before\n")
    op = operation(db, run, "exec_command")
    capture = service.begin_command(db, op, root)
    db.commit()
    identifier = capture.baseline["notes.txt"]["artifact_id"]
    path = service.storage_path(db.get(Artifact, identifier).storage_path)
    transaction = db.begin_nested() if nested else db.get_transaction()
    service.finish_command(db, closed_command(op))
    transaction.rollback()
    assert capture.state == "pending" and capture.baseline["notes.txt"]["artifact_id"] == identifier
    assert db.get(Artifact, identifier) is not None and path.read_text() == "before\n"
    db.commit()  # A later unrelated commit must not execute the cancelled unlink.
    assert path.is_file()
    service.finish_command(db, closed_command(op))
    db.commit()
    assert not path.exists()


def test_savepoint_commit_defers_snapshot_removal_until_outer_commit(evidence):
    db, run, root = evidence
    (root / "notes.txt").write_text("before\n")
    op = operation(db, run, "exec_command")
    capture = service.begin_command(db, op, root)
    db.commit()
    identifier = capture.baseline["notes.txt"]["artifact_id"]
    path = service.storage_path(db.get(Artifact, identifier).storage_path)
    with db.begin_nested():
        service.finish_command(db, closed_command(op))
    assert path.is_file()
    db.commit()
    assert not path.exists()


def test_managed_snapshots_reuse_only_within_the_same_run(evidence):
    db, run, root = evidence
    first, _ = write(db, run, root, "notes.txt", "first\n")
    second, _ = write(db, run, root, "notes.txt", "second\n")
    first_record = db.scalar(select(ChangeRecord).where(ChangeRecord.operation_id == first.id))
    second_record = db.scalar(select(ChangeRecord).where(ChangeRecord.operation_id == second.id))
    assert first_record.after_artifact_id == second_record.before_artifact_id
    other_run = Run(
        id=new_id(), user_id=run.user_id, workspace_id=run.workspace_id,
        thread_id=run.thread_id, input="Independent snapshot lifetime",
    )
    db.add(other_run)
    db.commit()
    other, _ = write(db, other_run, root, "other.txt", "first\n")
    other_record = db.scalar(select(ChangeRecord).where(ChangeRecord.operation_id == other.id))
    assert other_record.after_artifact_id != first_record.after_artifact_id
    assert len(list(db.scalars(select(Artifact)))) == 3
