"""FK/cleanup protocol regression on disposable PG; no real model acceptance evidence."""

import asyncio
from pathlib import Path

from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_api_authorization import (  # noqa: F401 - private native PostgreSQL fixtures
    authorization_engine,
    bound_run,
    isolated_postgres_engine,
    publication,
)
from workbench.config import Settings
from workbench.infrastructure.database import new_id, now
from workbench.modules import conversation_deletion as deletion
from workbench.modules.access import service as access
from workbench.modules.access.domain import ExecutionBinding
from workbench.modules.agent_services.domain import AgentSessionBinding
from workbench.modules.changes import service as changes
from workbench.modules.changes.domain import ChangeRecord, CommandCapture
from workbench.modules.conversations import ConversationDeletion, Message, Thread
from workbench.modules.files.domain import Artifact
from workbench.modules.runs.domain import Attempt, Run, Step
from workbench.modules.runs.presentation import seal_reply
from workbench.modules.tools import filesystem
from workbench.modules.tools.domain import Operation
from workbench.modules.workspaces import Workspace


def test_normal_deletion_clears_final_change_and_external_session_foreign_keys(
    authorization_engine,  # noqa: F811 - fixture injection
    tmp_path,
    monkeypatch,
):
    """Seed structural execution facts, then run actual preview/begin/maintain cleanup."""
    dsn = authorization_engine.url.render_as_string(hide_password=False).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    settings = Settings(data_dir=tmp_path / "private", database_url=dsn)
    for module in (access, deletion, filesystem):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(deletion, "engine", lambda: authorization_engine)
    for module in (changes, deletion):
        monkeypatch.setattr(module, "storage_path", lambda relative: settings.data_dir / relative)

    with Session(authorization_engine) as db:
        published = publication(db)
        run, binding = bound_run(db, published, status="succeeded")
        db.commit()
        root = db.get(Workspace, run.workspace_id).root_path
        root = Path(root)
        operation = Operation(
            id=new_id(),
            run_id=run.id,
            tool_call_id=new_id(),
            tool_name="write_file",
            arguments={"path": "output.txt"},
            status="running",
            effect_status="may_have_started",
        )
        db.add(operation)
        db.commit()

        def capture(path, before, after):
            record = changes.prepare_managed_change(db, operation, root, path, before, after)
            identifier = record.id
            db.commit()
            return identifier

        def applied(identifier):
            changes.mark_applied(db, identifier)
            db.commit()

        result = filesystem.change_file(
            root,
            {"path": "output.txt", "expected_version": "missing", "content": "actual file\n"},
            patch=False,
            before_write=capture,
            after_write=applied,
        )
        operation.status, operation.effect_status, operation.result = (
            "succeeded",
            "confirmed",
            result,
        )
        changes.confirm_operation(db, operation, result)
        db.commit()
        assert (root / "output.txt").read_text() == "actual file\n"
        record = db.scalar(select(ChangeRecord).where(ChangeRecord.operation_id == operation.id))
        assert record.state == "confirmed" and record.execution_binding_id == binding.id

        # A persisted pre-dispatch baseline exercises its FK cleanup without
        # pretending that this protocol fixture launched an external process.
        command_operation = Operation(
            id=new_id(),
            run_id=run.id,
            tool_call_id=new_id(),
            tool_name="start_command",
            arguments={},
            status="created",
            effect_status="not_started",
        )
        db.add(command_operation)
        db.flush()
        command_capture = changes.begin_command(db, command_operation, root)
        db.commit()
        assert command_capture.state == "pending" and command_capture.baseline
        snapshots = list(db.scalars(select(Artifact).where(Artifact.kind == "change_snapshot")))
        assert snapshots
        snapshot_ids = [artifact.id for artifact in snapshots]
        snapshot_paths = [changes.storage_path(artifact.storage_path) for artifact in snapshots]
        assert all(path.is_file() for path in snapshot_paths)
        # A downloadable original has its own lifetime even when its Run is removed.
        original_path = settings.data_dir / "independent-original.txt"
        original_path.write_text("independent original\n")
        original = Artifact(
            user_id=run.user_id, workspace_id=run.workspace_id, run_id=run.id,
            display_name="independent-original.txt", mime_type="text/plain",
            byte_size=original_path.stat().st_size,
            content_hash=changes.digest(original_path.read_bytes()),
            storage_path="independent-original.txt", kind="generated", status="available",
            metadata_json={},
        )
        db.add(original)
        db.commit()
        original_id = original.id

        message = Message(
            id=new_id(),
            thread_id=run.thread_id,
            run_id=run.id,
            role="assistant",
            status="completed",
            content="Persisted final protocol reply",
        )
        step = Step(
            id=new_id(),
            run_id=run.id,
            logical_key="final-protocol-step",
            kind="model",
            status="succeeded",
        )
        db.add_all([message, step])
        db.flush()
        attempt = Attempt(
            id=new_id(),
            run_id=run.id,
            step_id=step.id,
            message_id=message.id,
            status="succeeded",
            finished_at=now(),
        )
        db.add(attempt)
        db.flush()
        db.scalar(select(Run).where(Run.id == run.id).with_for_update())
        seal_reply(db, run)
        db.commit()
        assert run.final_message_id == message.id and run.final_attempt_id == attempt.id
        session_binding = db.scalar(
            select(AgentSessionBinding).where(AgentSessionBinding.thread_id == run.thread_id)
        )
        assert session_binding is not None
        ids = {
            "run": run.id,
            "thread": run.thread_id,
            "message": message.id,
            "attempt": attempt.id,
            "step": step.id,
            "operation": operation.id,
            "command_operation": command_operation.id,
            "change": record.id,
            "capture": command_capture.id,
            "session_binding": session_binding.id,
            "execution_binding": binding.id,
        }

        async def checkpoints():
            async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
                await saver.setup()
                await saver.aput(
                    {"configurable": {"thread_id": ids["run"], "checkpoint_ns": ""}},
                    empty_checkpoint(),
                    {},
                    {},
                )

        # Checkpoint setup creates concurrent indexes, which must not wait on an
        # earlier read transaction from this same protocol-fixture connection.
        db.commit()
        asyncio.run(checkpoints())
        preview = deletion.preview(db, published.owner.id, ids["thread"])
        started = deletion.begin(
            db,
            published.owner.id,
            ids["thread"],
            {
                "request_id": new_id(),
                "preview_token": preview["preview_token"],
            },
        )
        db.commit()
        assert started["status"] == "pending"
        asyncio.run(deletion.maintain())
        db.expire_all()
        receipt = db.get(ConversationDeletion, started["id"])
        assert receipt.status == "completed", (receipt.message, receipt.intent)
        assert receipt.intent["phase"] == "completed"
        for model, key in (
            (Run, "run"),
            (Thread, "thread"),
            (Message, "message"),
            (Attempt, "attempt"),
            (Step, "step"),
            (Operation, "operation"),
            (Operation, "command_operation"),
            (ChangeRecord, "change"),
            (CommandCapture, "capture"),
            (AgentSessionBinding, "session_binding"),
        ):
            assert db.get(model, ids[key]) is None
        assert db.get(ExecutionBinding, ids["execution_binding"]) is not None
        assert db.scalar(select(func.count()).select_from(ChangeRecord)) == 0
        assert all(db.get(Artifact, identifier) is None for identifier in snapshot_ids)
        assert all(not path.exists() for path in snapshot_paths)
        assert (root / "output.txt").read_text() == "actual file\n"
        assert db.get(Artifact, original_id).run_id is None
        assert original_path.read_text() == "independent original\n"

        async def checkpoints_removed():
            async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
                assert await saver.aget_tuple({"configurable": {"thread_id": ids["run"]}}) is None

        asyncio.run(checkpoints_removed())
