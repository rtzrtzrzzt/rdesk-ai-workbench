"""Confirmed, resumable removal of one frozen conversation descendant tree."""

import asyncio
import hashlib
import json
import logging
import shutil
from uuid import UUID, uuid5

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.conninfo import make_conninfo
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from workbench.config import get_settings
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine
from workbench.modules.conversation_service import get_thread
from workbench.modules.conversations import ConversationDeletion, Message, QueuedInput, Thread
from workbench.modules.files.domain import Artifact, Upload
from workbench.modules.files.storage import storage_path
from workbench.modules.runs import service as runs
from workbench.modules.runs.domain import TERMINAL_STATUSES, ClientRequest, Job, Run


def closure(db, user_id, thread_id):
    get_thread(db, user_id, thread_id)
    threads, run_ids = {thread_id}, set()
    while True:
        before = (len(threads), len(run_ids))
        threads.update(
            db.scalars(
                select(Thread.id).where(
                    Thread.user_id == user_id, Thread.parent_thread_id.in_(threads)
                )
            )
        )
        rows = list(
            db.scalars(
                select(Run).where(
                    Run.user_id == user_id,
                    or_(Run.thread_id.in_(threads), Run.parent_run_id.in_(run_ids)),
                )
            )
        )
        run_ids.update(row.id for row in rows)
        threads.update(row.thread_id for row in rows if row.thread_id)
        if before == (len(threads), len(run_ids)):
            return sorted(threads), sorted(run_ids)


def preview(db, user_id, thread_id):
    thread_ids, run_ids = closure(db, user_id, thread_id)
    versions = list(
        db.execute(
            select(Thread.id, Thread.version).where(Thread.id.in_(thread_ids)).order_by(Thread.id)
        )
    )
    token = hashlib.sha256(
        json.dumps(
            [user_id, thread_ids, run_ids, [list(v) for v in versions]], sort_keys=True
        ).encode()
    ).hexdigest()
    active = len(
        list(
            db.scalars(
                select(Run.id).where(Run.id.in_(run_ids), Run.status.not_in(TERMINAL_STATUSES))
            )
        )
    )
    return {
        "preview_token": token,
        "thread_count": len(thread_ids),
        "run_count": len(run_ids),
        "active_run_count": active,
        "thread_ids": thread_ids,
        "run_ids": run_ids,
    }


def deletion_view(row):
    return {"id": row.id, "status": row.status, "message": row.message}


def status(db, user_id, thread_id):
    row = db.scalar(
        select(ConversationDeletion)
        .where(
            ConversationDeletion.user_id == user_id,
            ConversationDeletion.target_thread_id == thread_id,
        )
        .order_by(ConversationDeletion.created_at.desc())
        .limit(1)
    )
    if row:
        return deletion_view(row)
    get_thread(db, user_id, thread_id)
    return None


def begin(db, user_id, thread_id, values):
    def execute():
        scope = preview(db, user_id, thread_id)
        if scope["preview_token"] != values["preview_token"]:
            raise AppError(
                "deletion_scope_changed", "会话后代或状态已变化，请重新预览删除范围。", 409
            )
        existing = db.scalar(
            select(ConversationDeletion)
            .where(
                ConversationDeletion.user_id == user_id,
                ConversationDeletion.target_thread_id == thread_id,
                ConversationDeletion.status.in_(["pending", "blocked"]),
            )
            .with_for_update()
        )
        if existing:
            if existing.status == "blocked":
                existing.status, existing.message = "pending", "正在重新核对资源与清理断点。"
            return deletion_view(existing)
        rows = list(
            db.scalars(
                select(Thread)
                .where(Thread.id.in_(scope["thread_ids"]))
                .order_by(Thread.id)
                .with_for_update()
            )
        )
        active = list(
            db.scalars(
                select(Run).where(Run.id.in_(scope["run_ids"])).order_by(Run.id).with_for_update()
            )
        )
        # A spawn/fork that won its lock must be included in a fresh confirmation.
        if preview(db, user_id, thread_id)["preview_token"] != scope["preview_token"]:
            raise AppError("deletion_scope_changed", "删除范围已变化，请重新预览。", 409)
        for row in rows:
            row.deleting, row.queue_paused = True, True
            row.version += 1
        for run in active:
            if run.status not in TERMINAL_STATUSES:
                runs.request_stop(db, run)
        row = ConversationDeletion(
            user_id=user_id,
            target_thread_id=thread_id,
            intent={
                "thread_ids": scope["thread_ids"],
                "run_ids": scope["run_ids"],
                "phase": "stopping",
            },
        )
        db.add(row)
        db.flush()
        return deletion_view(row)

    return runs.idempotent(
        db, user_id, values["request_id"], f"thread:{thread_id}:delete", values, execute
    )


def _ready(identifier):
    from workbench.modules.automation.domain import AutomationSession
    from workbench.modules.mcp.store import has_pending_transport_cleanup
    from workbench.modules.runs.resources import native_cleanup_pending

    with Session(engine()) as db:
        intent = db.get(ConversationDeletion, identifier)
        if intent is None or intent.status != "pending":
            return None
        scope = intent.intent
        for run_id in scope["run_ids"]:
            run = db.get(Run, run_id)
            if run is None:
                continue
            job = db.scalar(select(Job).where(Job.run_id == run_id))
            if (
                run.status not in TERMINAL_STATUSES
                or (job and job.status == "running")
                or native_cleanup_pending(db, run_id)
                or has_pending_transport_cleanup(db, run_id)
            ):
                return None
        sessions = list(
            db.scalars(
                select(AutomationSession).where(AutomationSession.run_id.in_(scope["run_ids"]))
            )
        )
        db.expunge_all()
        return scope, sessions


def _freeze_files(identifier):
    from workbench.modules.processes.domain import CommandExecution

    with Session(engine()) as db, db.begin():
        row = db.get(ConversationDeletion, identifier, with_for_update=True)
        intent = dict(row.intent)
        if intent.get("phase") == "files_frozen":
            return intent
        run_ids, thread_ids = intent["run_ids"], intent["thread_ids"]
        file_runs = list(db.scalars(select(Run).where(Run.id.in_(run_ids), Run.kind == "file")))
        source_uploads = {}
        for run in file_runs:
            spec = run.config_snapshot.get("export_spec")
            if not isinstance(spec, dict) or not spec.get("request_id"):
                continue
            upload = db.scalar(
                select(Upload).where(
                    Upload.user_id == row.user_id,
                    Upload.request_id == str(uuid5(UUID(spec["request_id"]), "export-source")),
                )
            )
            if upload and upload.artifact_id == run.config_snapshot.get("artifact_id"):
                source_uploads[upload.artifact_id] = upload.id
        candidates = list(
            db.scalars(
                select(Artifact)
                .where(
                    Artifact.user_id == row.user_id,
                    or_(Artifact.run_id.in_(run_ids), Artifact.id.in_(source_uploads)),
                )
                .order_by(Artifact.id)
                .with_for_update()
            )
        )
        internal = {
            a.id
            for a in candidates
            if a.kind in {"task_state", "ui_surface", "command_log", "screenshot"}
            or (a.kind == "preview" and a.metadata_json.get("automation_session_id"))
            or a.id in source_uploads
        }
        # User files and independently shared results remain available. Never
        # infer ownership of originals merely from run_id or a filename.
        for refs in db.scalars(
            select(Message.attachments).where(Message.thread_id.not_in(thread_ids))
        ):
            internal.difference_update(
                item.get(key)
                for item in refs or []
                for key in ("artifact_id", "source_artifact_id")
            )
        for payload in db.scalars(
            select(QueuedInput.payload).where(
                QueuedInput.thread_id.not_in(thread_ids), QueuedInput.status != "dispatched"
            )
        ):
            internal.difference_update(
                item.get(key)
                for item in payload.get("attachments", [])
                for key in ("artifact_id", "source_artifact_id")
            )
        for run in db.scalars(select(Run).where(Run.id.not_in(run_ids))):
            serialized = json.dumps(run.config_snapshot)
            internal.difference_update(key for key in list(internal) if key in serialized)
        for artifact in db.scalars(
            select(Artifact).where(Artifact.parent_id.in_(internal), Artifact.id.not_in(internal))
        ):
            if artifact.parent_id in source_uploads:
                artifact.parent_id = None
            else:
                internal.discard(artifact.parent_id)
        files = []
        for artifact in candidates:
            if artifact.id in internal:
                artifact.status = "deleting"
                files.append(artifact.storage_path)
        directories = [
            command.output_path
            for command in db.scalars(
                select(CommandExecution).where(CommandExecution.run_id.in_(run_ids))
            )
        ]
        directories.extend(
            f"workspaces/{run.workspace_id}/staging/{run.id}"
            for run in db.scalars(select(Run).where(Run.id.in_(run_ids)))
        )
        intent.update(
            phase="files_frozen",
            artifact_ids=sorted(internal),
            files=files,
            directories=directories,
            upload_ids=[source_uploads[key] for key in internal if key in source_uploads],
        )
        row.intent = intent
        return intent


def _remove_files(intent):
    for relative in intent.get("files", []):
        path = storage_path(relative)
        path.unlink(missing_ok=True)
    for relative in intent.get("directories", []):
        path = storage_path(relative)
        if path.is_dir():
            shutil.rmtree(path)


def _remove_records(identifier):
    from workbench.modules.automation.domain import (
        AutomationAction,
        AutomationSession,
        DesktopWriterLease,
    )
    from workbench.modules.collaboration.domain import AgentMessage, AgentSlot
    from workbench.modules.interactions import Approval, Question
    from workbench.modules.mcp.host_domain import ToolExchange
    from workbench.modules.processes.domain import CommandExecution
    from workbench.modules.runs.domain import Attempt, RunEvent, Step
    from workbench.modules.tools.domain import Operation
    from workbench.modules.usage import UsageRecord

    with Session(engine()) as db, db.begin():
        deletion = db.get(ConversationDeletion, identifier, with_for_update=True)
        intent = deletion.intent
        run_ids, thread_ids, artifact_ids = (
            intent["run_ids"],
            intent["thread_ids"],
            intent.get("artifact_ids", []),
        )
        operation_ids = list(db.scalars(select(Operation.id).where(Operation.run_id.in_(run_ids))))
        session_ids = list(
            db.scalars(select(AutomationSession.id).where(AutomationSession.run_id.in_(run_ids)))
        )
        removed_ids = set(run_ids + thread_ids + artifact_ids + operation_ids + session_ids)
        for model, condition in (
            (Message, Message.thread_id.in_(thread_ids)),
            (QueuedInput, QueuedInput.thread_id.in_(thread_ids)),
            (Approval, Approval.run_id.in_(run_ids)),
            (Question, Question.run_id.in_(run_ids)),
            (
                AgentMessage,
                or_(
                    AgentMessage.root_run_id.in_(run_ids),
                    AgentMessage.from_run_id.in_(run_ids),
                    AgentMessage.to_run_id.in_(run_ids),
                ),
            ),
            (AgentSlot, or_(AgentSlot.root_run_id.in_(run_ids), AgentSlot.run_id.in_(run_ids))),
            (Attempt, Attempt.run_id.in_(run_ids)),
        ):
            removed_ids.update(db.scalars(select(model.id).where(condition)))
        for request in db.scalars(
            select(ClientRequest).where(ClientRequest.user_id == deletion.user_id)
        ):
            body = json.dumps(request.response, ensure_ascii=False) if request.response else ""
            if request.resource_id in removed_ids or any(key in body for key in removed_ids):
                request.response, request.expired = None, True
        db.execute(
            update(DesktopWriterLease)
            .where(DesktopWriterLease.session_id.in_(session_ids))
            .values(session_id=None, revoked=True)
        )
        for model, condition in (
            (AutomationAction, AutomationAction.session_id.in_(session_ids)),
            (AutomationSession, AutomationSession.id.in_(session_ids)),
            (ToolExchange, ToolExchange.operation_id.in_(operation_ids)),
            (CommandExecution, CommandExecution.run_id.in_(run_ids)),
            (
                AgentMessage,
                or_(
                    AgentMessage.root_run_id.in_(run_ids),
                    AgentMessage.from_run_id.in_(run_ids),
                    AgentMessage.to_run_id.in_(run_ids),
                ),
            ),
            (AgentSlot, or_(AgentSlot.root_run_id.in_(run_ids), AgentSlot.run_id.in_(run_ids))),
            (UsageRecord, UsageRecord.run_id.in_(run_ids)),
            (Attempt, Attempt.run_id.in_(run_ids)),
            (Step, Step.run_id.in_(run_ids)),
            (RunEvent, RunEvent.run_id.in_(run_ids)),
            (Job, Job.run_id.in_(run_ids)),
            (Question, Question.run_id.in_(run_ids)),
            (Approval, Approval.run_id.in_(run_ids)),
            (QueuedInput, QueuedInput.thread_id.in_(thread_ids)),
        ):
            db.execute(delete(model).where(condition))
        db.execute(
            update(Thread)
            .where(Thread.id.in_(thread_ids))
            .values(fork_message_id=None, parent_thread_id=None)
        )
        db.execute(delete(Message).where(Message.thread_id.in_(thread_ids)))
        db.execute(delete(Operation).where(Operation.id.in_(operation_ids)))
        db.execute(delete(Upload).where(Upload.id.in_(intent.get("upload_ids", []))))
        db.execute(update(Artifact).where(Artifact.run_id.in_(run_ids)).values(run_id=None))
        for artifact in db.scalars(select(Artifact).where(Artifact.user_id == deletion.user_id)):
            if artifact.metadata_json.get("source_run_id") in run_ids:
                artifact.metadata_json = {**artifact.metadata_json, "source_deleted": True}
        db.execute(update(Artifact).where(Artifact.id.in_(artifact_ids)).values(parent_id=None))
        db.execute(delete(Artifact).where(Artifact.id.in_(artifact_ids)))
        db.execute(
            update(Run).where(Run.id.in_(run_ids)).values(root_run_id=None, parent_run_id=None)
        )
        db.execute(delete(Run).where(Run.id.in_(run_ids)))
        db.execute(delete(Thread).where(Thread.id.in_(thread_ids)))
        deletion.status, deletion.message = (
            "completed",
            "会话及后代已永久删除；原始文件与独立记忆已保留。",
        )
        deletion.intent = {"thread_ids": thread_ids, "run_ids": run_ids, "phase": "completed"}


async def maintain():
    def pending():
        with Session(engine()) as db:
            return list(
                db.scalars(
                    select(ConversationDeletion.id).where(ConversationDeletion.status == "pending")
                )
            )

    for identifier in await asyncio.to_thread(pending):
        try:
            ready = await asyncio.to_thread(_ready, identifier)
            if ready is None:
                continue
            scope, sessions = ready
            from workbench.modules.automation.forget import forget_closed_session

            for session in sessions:
                await forget_closed_session(session)
            intent = await asyncio.to_thread(_freeze_files, identifier)
            dsn = get_settings().database_dsn().replace("postgresql+psycopg://", "postgresql://", 1)
            dsn = make_conninfo(
                dsn, connect_timeout=3, options="-c lock_timeout=5000 -c statement_timeout=30000"
            )
            async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
                for run_id in scope["run_ids"]:
                    await saver.adelete_thread(run_id)
            await asyncio.to_thread(_remove_files, intent)
            await asyncio.to_thread(_remove_records, identifier)
        except (AppError, OSError, ValueError, SQLAlchemyError, psycopg.Error) as error:
            try:
                with Session(engine()) as db, db.begin():
                    row = db.get(ConversationDeletion, identifier)
                    row.status = "blocked"
                    row.message = (
                        error.message
                        if isinstance(error, AppError)
                        else "清理未完成，已保留删除断点；请检查本机诊断后重试。"
                    )
            except (SQLAlchemyError, psycopg.Error):
                logging.getLogger("workbench.worker").warning(
                    "Conversation deletion retained: database unavailable."
                )
