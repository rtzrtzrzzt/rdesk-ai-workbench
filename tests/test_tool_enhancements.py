"""Focused replay and protocol checks, separate from real model/UI acceptance."""

import asyncio
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import now
from workbench.modules import conversation_deletion
from workbench.modules.conversation_deletion import _preserve_generation_runs
from workbench.modules.files import inputs
from workbench.modules.files import service as files
from workbench.modules.files.dto import ProcessFile
from workbench.modules.files.storage import file_digest
from workbench.modules.runs.domain import Run
from workbench.modules.runs.ownership import ExecutionOwner, assert_owned, current_owner
from workbench.modules.tools import service
from workbench.modules.tools.registry import CORE_TOOL_NAMES
from workbench.modules.tools.task_plan import UpdatePlan


@pytest.mark.parametrize(
    ("name", "arguments", "recorded"),
    [
        (
            "read_file",
            {"path": "README.md"},
            {"path": "README.md", "start_line": 1, "end_line": 200},
        ),
        (
            "start_command",
            {"command": "pwd"},
            {"command": "pwd", "timeout_seconds": 30, "max_output_bytes": 33554432},
        ),
    ],
)
def test_old_calls_keep_exact_recorded_arguments(monkeypatch, name, arguments, recorded):
    run = SimpleNamespace(
        parent_run_id=None,
        cancel_requested_at=None,
        config_snapshot={"tool_names": [*CORE_TOOL_NAMES, "start_command"]},
    )
    operation = SimpleNamespace(
        id="operation",
        tool_name=name,
        arguments=recorded,
        status="succeeded",
        result={"recorded": True},
    )
    db = MagicMock()
    db.scalar.side_effect = [run, operation]
    context = MagicMock()
    context.__enter__.return_value = db
    monkeypatch.setattr(service, "Session", lambda *_: context)
    monkeypatch.setattr(service, "engine", lambda: None)
    _, result, normalized = service.ensure_operation(
        "unused", {"id": "call", "name": name, "arguments": arguments}
    )
    assert result == operation.result
    assert normalized == recorded
    assert operation.arguments == recorded


def test_plan_rejects_duplicate_ids_and_concurrent_current_steps():
    for steps in (
        [{"id": "one", "step": "read"}, {"id": "one", "step": "write"}],
        [
            {"id": "one", "step": "read", "status": "in_progress"},
            {"id": "two", "step": "write", "status": "in_progress"},
        ],
    ):
        with pytest.raises(ValidationError):
            UpdatePlan(expected_version=0, steps=steps)


@pytest.fixture
def readable_artifact(tmp_path, monkeypatch):
    # Exercise actual bytes and hashes without starting the product database.
    source = Path(__file__).resolve().parents[1] / "README.md"
    line = " ".join(source.read_text(encoding="utf-8").splitlines())
    path = tmp_path / "readme.txt"
    path.write_text(line + "\n" + source.name, encoding="utf-8")
    row = SimpleNamespace(
        id="readme",
        content_hash=file_digest(path),
        kind="original",
        metadata_json={},
        status="available",
        byte_size=path.stat().st_size,
        storage_path=str(path),
        mime_type="text/plain",
        display_name=source.name,
        parent_id=None,
    )
    monkeypatch.setattr(inputs, "get_artifact", lambda *_: row)
    monkeypatch.setattr(inputs, "storage_path", lambda _: path)
    return row, path, line


def test_artifact_long_line_continuation_is_lossless_and_versioned(readable_artifact):
    row, _, line = readable_artifact
    request = {"start": 1, "limit": 1, "max_chars": 256}
    pieces = []
    for _ in range(len(line) // 256 + 2):
        result = inputs.read_artifact_text(None, "user", "workspace", row.id, **request)
        pieces.append(result["text"])
        next_read = result["next"]
        assert next_read["expected_version"] == row.content_hash
        if next_read["start_line"] == 2:
            break
        assert next_read["start_character"] > request.get("start_character", 0)
        request = {
            "start": next_read["start_line"],
            **{
                key: value
                for key, value in next_read.items()
                if key not in {"artifact_id", "start_line"}
            },
        }
    else:
        pytest.fail("continuation did not reach the next line")
    assert "".join(pieces) == line
    final = inputs.read_artifact_text(
        None, "user", "workspace", row.id, start=2, expected_version=row.content_hash
    )
    assert final["text"] == "README.md"
    assert final["next"] is None


def test_artifact_continuation_rejects_changed_content(readable_artifact):
    row, path, _ = readable_artifact
    path.write_text("", encoding="utf-8")
    with pytest.raises(AppError) as failure:
        inputs.read_artifact_text(
            None, "user", "workspace", row.id, expected_version=row.content_hash
        )
    assert failure.value.code == "artifact_version_changed"


@pytest.mark.parametrize("tool_created", [False, True])
def test_file_job_acceptance_respects_authorizing_owner(monkeypatch, tool_created):
    parent = SimpleNamespace(id=str(uuid4()), root_run_id=str(uuid4())) if tool_created else None
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(
        lease_owner="worker",
        lease_epoch=1,
        status="running",
        lease_until=now() + timedelta(seconds=60),
    )
    db.scalars.return_value = []
    monkeypatch.setattr(
        files,
        "get_artifact",
        lambda *_: SimpleNamespace(id=str(uuid4()), status="available", display_name="README.md"),
    )
    monkeypatch.setattr(
        files, "idempotent", lambda _db, _user, _request, _scope, _values, execute: execute()
    )
    events = []

    def emit(database, identifier, kind, payload):
        assert_owned(database, identifier)
        events.append((identifier, kind))

    monkeypatch.setattr(files, "emit", emit)
    owner = ExecutionOwner(parent.id, "worker", 1) if parent else None
    token = current_owner.set(owner)
    try:
        result = files.create_processing(
            db, "user", "workspace", "artifact", ProcessFile(request_id=uuid4()), actor=parent
        )
    finally:
        current_owner.reset(token)
    child = next(call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], Run))
    assert child.id == result["id"]
    assert child.parent_run_id == (parent.id if parent else None)
    assert child.root_run_id == (parent.root_run_id if parent else child.id)
    assert events == [(parent.id, "file.created") if parent else (child.id, "run.accepted")]


def test_deleting_chat_retains_generation_and_preview_without_chat_foreign_keys():
    root = Run(id="chat", version=1, thread_id="thread", root_run_id="chat")
    media = Run(id="media", version=2, parent_run_id="chat", root_run_id="chat")
    preview = Run(id="preview", version=1, parent_run_id="media", root_run_id="chat")
    ordinary = Run(id="file", version=1, parent_run_id="chat", root_run_id="chat")
    db = MagicMock()
    db.scalars.side_effect = [[media.id], [root, media, preview, ordinary]]
    removed, retained = _preserve_generation_runs(
        db, [row.id for row in [root, media, preview, ordinary]]
    )
    assert removed == ["chat", "file"]
    assert retained == ["media", "preview"]
    for row in (media, preview):
        assert row.thread_id is None and row.root_run_id == media.id
    assert media.parent_run_id is None
    assert preview.parent_run_id == media.id
    assert ordinary.parent_run_id == root.id


def test_deletion_cleans_checkpoints_only_for_frozen_removal_set(monkeypatch):
    db = MagicMock()
    db.scalars.return_value = ["deletion"]
    context = MagicMock()
    context.__enter__.return_value = db
    monkeypatch.setattr(conversation_deletion, "Session", lambda *_: context)
    monkeypatch.setattr(conversation_deletion, "engine", lambda: None)
    monkeypatch.setattr(
        conversation_deletion, "_ready", lambda _: ({"run_ids": ["chat", "media"]}, [])
    )
    frozen = {"run_ids": ["chat"], "retained_generation_run_ids": ["media"]}
    monkeypatch.setattr(conversation_deletion, "_freeze_files", lambda _: frozen)
    remove_files, remove_records = MagicMock(), MagicMock()
    monkeypatch.setattr(conversation_deletion, "_remove_files", remove_files)
    monkeypatch.setattr(conversation_deletion, "_remove_records", remove_records)
    monkeypatch.setattr(conversation_deletion, "get_settings", lambda: MagicMock())
    monkeypatch.setattr(conversation_deletion, "make_conninfo", lambda *_, **__: "unused")
    saver = MagicMock()
    delete_checkpoint = AsyncMock()
    saver.__aenter__ = AsyncMock(return_value=SimpleNamespace(adelete_thread=delete_checkpoint))
    saver.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(
        conversation_deletion.AsyncPostgresSaver, "from_conn_string", lambda _: saver
    )
    asyncio.run(conversation_deletion.maintain())
    delete_checkpoint.assert_awaited_once_with("chat")
    remove_files.assert_called_once_with(frozen)
    remove_records.assert_called_once_with("deletion")
