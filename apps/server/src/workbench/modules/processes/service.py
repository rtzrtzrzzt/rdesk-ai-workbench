"""Approved command handles, bounded output, and truthful resource reconciliation."""

import asyncio
import hashlib
import json
import os
from uuid import UUID, uuid4, uuid5

from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session
from workbench.config import PROJECT_ROOT, get_settings
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, now
from workbench.infrastructure.mutations import write_gate
from workbench.modules.files.domain import Artifact
from workbench.modules.files.storage import file_digest, publish_bytes, storage_path
from workbench.modules.interactions import Approval
from workbench.modules.processes import dto
from workbench.modules.processes.domain import CommandExecution
from workbench.modules.runs.domain import TERMINAL_STATUSES, Run
from workbench.modules.runs.ownership import assert_owned
from workbench.modules.sandbox import provider_for
from workbench.modules.tools.domain import Operation
from workbench.modules.workspace_service import get as workspace


def view(row):
    receipt = row.receipt or {}
    return dto.CommandView(
        id=row.id,
        run_id=row.run_id,
        command=row.command,
        status=row.status,
        timeout_seconds=row.timeout_seconds,
        max_output_bytes=row.max_output_bytes,
        stop_requested=row.stop_requested,
        owned_resources_closed=row.resources_closed,
        exit_code=receipt.get("exit_code"),
        stdout_bytes=receipt.get("stdout_bytes"),
        stderr_bytes=receipt.get("stderr_bytes"),
        started_at=receipt.get("started_at"),
        finished_at=receipt.get("finished_at"),
        log_artifacts=row.log_artifacts,
        sandbox=row.sandbox_profile,
        error=row.error,
        created_at=row.created_at,
    )


def owned(db, user_id, workspace_id, identifier, *, run_id=None):
    workspace(db, user_id, workspace_id)
    row = db.scalar(
        select(CommandExecution).where(
            CommandExecution.id == str(identifier),
            CommandExecution.user_id == user_id,
            CommandExecution.workspace_id == workspace_id,
        )
    )
    if row is None or (run_id is not None and row.run_id != run_id):
        raise AppError("command_not_found", "命令不属于当前任务或工作区。", 404)
    return row


def listing(db, user_id, workspace_id):
    workspace(db, user_id, workspace_id)
    return [
        view(row)
        for row in db.scalars(
            select(CommandExecution)
            .where(
                CommandExecution.user_id == user_id, CommandExecution.workspace_id == workspace_id
            )
            .order_by(CommandExecution.created_at.desc())
            .limit(100)
        )
    ]


def _reserve(run_id, operation_id, values):
    from workbench.modules.processes import native

    capability = native.capability()
    if capability["status"] != "available":
        raise AppError("command_driver_unsupported", capability["reason"], 409, "capability")
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        assert_owned(db, run_id)
        if run.parent_run_id:
            raise AppError("agent_native_command_denied", "子任务不能启动未隔离主机命令。", 403)
        if run.cancel_requested_at or run.status in TERMINAL_STATUSES:
            raise AppError("run_finished", "任务已停止，不能启动新命令。", 409)
        operation = db.get(Operation, operation_id)
        approval = db.scalar(select(Approval).where(Approval.operation_id == operation_id))
        from workbench.modules.tools.service import argument_digest

        if (
            operation is None
            or operation.run_id != run_id
            or operation.tool_name not in {"start_command", "exec_command"}
            or approval is None
            or approval.status != "approved"
            or not approval.approved
            or approval.parameters_digest != argument_digest(operation.arguments)
        ):
            raise AppError("command_approval_required", "此命令尚无参数一致的有效批准。", 403)
        existing = db.get(CommandExecution, operation_id)
        if existing:
            return existing.id, existing.status == "reserved" and not existing.resources_closed
        profile = run.config_snapshot.get("runtime_options", {}).get(
            "sandbox_profile", "native_scoped"
        )
        isolation = provider_for(profile).capability()
        if isolation.status != "ready":
            raise AppError("sandbox_unavailable", isolation.reason, 409, "capability")
        db.execute(text("SELECT pg_advisory_xact_lock(82139715)"))
        busy = list(
            db.scalars(select(CommandExecution).where(CommandExecution.resources_closed.is_(False)))
        )
        if len(busy) >= 2 or any(row.run_id == run_id for row in busy):
            raise AppError(
                "command_capacity",
                "每任务最多一个活动长命令，本机最多两个；未知资源仍占名额。",
                409,
            )
        argv = (
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", values["command"]]
            if os.name == "nt"
            else ["/bin/sh", "-c", values["command"]]
        )
        row = CommandExecution(
            id=operation_id,
            operation_id=operation_id,
            user_id=run.user_id,
            workspace_id=run.workspace_id,
            run_id=run_id,
            nonce=str(uuid4()),
            command=values["command"],
            sandbox_profile=profile,
            argv=argv,
            timeout_seconds=values["timeout_seconds"],
            max_output_bytes=values["max_output_bytes"],
            output_path=f"workspaces/{run.workspace_id}/commands/{operation_id}",
        )
        db.add(row)
        db.flush()
        return row.id, True


def _dispatch(identifier):
    from workbench.modules.processes import native

    # A durable dispatch fence is committed before external process creation.
    with Session(engine()) as db, db.begin():
        reference = db.get(CommandExecution, identifier)
        run = db.scalar(select(Run).where(Run.id == reference.run_id).with_for_update())
        assert_owned(db, run.id)
        row = db.get(CommandExecution, identifier, with_for_update=True)
        if row.status != "reserved":
            return view(row)
        row.status = "launching"
    with Session(engine()) as db, db.begin():
        reference = db.get(CommandExecution, identifier)
        run = db.scalar(select(Run).where(Run.id == reference.run_id).with_for_update())
        assert_owned(db, run.id)
        row = db.get(CommandExecution, identifier, with_for_update=True)
        if row.stop_requested or run.cancel_requested_at:
            row.status, row.resources_closed = "cancelled", True
            return view(row)
        root = workspace(db, run.user_id, run.workspace_id)
        output = storage_path(row.output_path)
        output.mkdir(mode=0o700, parents=True, exist_ok=True)
        plan = {
            "schema": 1,
            "command_id": row.id,
            "nonce": row.nonce,
            "argv": row.argv,
            "cwd": root.root_path,
            "timeout_seconds": row.timeout_seconds,
            "max_output_bytes": row.max_output_bytes,
            "output_dir": str(output),
            "sandbox_profile": row.sandbox_profile,
            "protected_roots": [
                str(get_settings().data_dir.absolute()),
                str(PROJECT_ROOT / "models"),
            ],
        }
        plan_path = output / "plan.json"
        if plan_path.exists():
            raise AppError(
                "command_dispatch_unknown",
                "已有启动计划，不能重新启动此命令。",
                409,
                "outcome_unknown",
            )
        publish_bytes(plan_path, json.dumps(plan).encode())
        result = native.launch(plan_path)
        row.native_identity = result.supervisor_identity
        row.receipt = result.state
        row.status = (result.state or {}).get("status", "outcome_unknown")
        row.resources_closed = bool((result.state or {}).get("owned_resources_closed", False))
        if result.status != "acknowledged":
            row.error = {
                "code": "command_launch_unknown",
                "message": "启动回执尚未确认，不会重复启动。",
            }
        return view(row)


async def start(run_id, operation_id, values):
    identifier, fresh = await asyncio.to_thread(_reserve, run_id, operation_id, values)
    if fresh:
        try:
            return await asyncio.to_thread(_dispatch, identifier)
        except BaseException:
            await asyncio.shield(asyncio.to_thread(_request_id_stop, identifier))
            raise
    return await asyncio.to_thread(reconcile, identifier)


async def execute_bounded(run_id, operation_id, values):
    """The short command tool shares the same durable ownership and cleanup path."""
    command = await start(run_id, operation_id, {**values, "max_output_bytes": 64 * 1024})
    try:
        while not command.owned_resources_closed:
            if command.status == "outcome_unknown":
                raise AppError(
                    "command_outcome_unknown",
                    "命令归属尚未确认；不会重放，资源记录继续保留。",
                    409,
                    "outcome_unknown",
                )
            await asyncio.sleep(0.25)
            command = await asyncio.to_thread(reconcile, command.id)
    except BaseException:
        await asyncio.shield(asyncio.to_thread(_request_id_stop, command.id))
        raise
    user_id, workspace_id = await asyncio.to_thread(_scope, command.id)
    result = {
        "command_id": command.id,
        "exit_code": command.exit_code,
        "sandbox": command.sandbox,
        "owned_resources_closed": True,
        "status": command.status,
        "timed_out": command.status == "timed_out",
        "truncated": False,
    }
    for stream in ("stdout", "stderr"):
        page = await read(user_id, workspace_id, command.id, stream, 0, 16000, run_id=run_id)
        result[stream] = page.text
        result["truncated"] |= not page.eof
    if command.status in {"outcome_unknown", "output_limit"}:
        raise AppError(
            "command_outcome_unknown",
            "命令达到输出预算或结果未知；请先核对已保存的日志和产物。",
            409,
            "outcome_unknown",
        )
    return result


def _scope(identifier):
    with Session(engine()) as db:
        row = db.get(CommandExecution, identifier)
        return row.user_id, row.workspace_id


def _request_id_stop(identifier):
    with Session(engine()) as db, db.begin():
        reference = db.get(CommandExecution, identifier)
        if reference:
            db.scalar(select(Run).where(Run.id == reference.run_id).with_for_update())
            row = db.get(CommandExecution, identifier, with_for_update=True)
            row.stop_requested = True


def request_cleanup(db, run_id):
    rows = list(
        db.scalars(
            select(CommandExecution)
            .where(CommandExecution.run_id == run_id, CommandExecution.resources_closed.is_(False))
            .order_by(CommandExecution.id)
            .with_for_update()
        )
    )
    for row in rows:
        row.stop_requested = True
    return len(rows)


def cleanup_pending(db, run_id):
    return (
        db.scalar(
            select(CommandExecution.id)
            .where(CommandExecution.run_id == run_id, CommandExecution.resources_closed.is_(False))
            .limit(1)
        )
        is not None
    )


def _publish_logs(identifier):
    with Session(engine()) as db:
        row = db.get(CommandExecution, identifier)
        if not row.resources_closed or not row.receipt:
            return
        output, workspace_id, user_id, run_id = (
            storage_path(row.output_path),
            row.workspace_id,
            row.user_id,
            row.run_id,
        )
        maximum = row.max_output_bytes
        receipt = dict(row.receipt)
    references = {}
    for stream in ("stdout", "stderr"):
        source = output / (stream + ".log")
        if source.is_symlink() or not source.is_file() or source.stat().st_size > maximum:
            raise AppError("command_log_unavailable", "命令日志不符合已确认的受管范围。", 409)
        data = source.read_bytes()
        if len(data) != receipt.get(stream + "_bytes"):
            raise AppError("command_log_changed", "命令日志长度与执行器结束回执不一致。", 409)
        digest = hashlib.sha256(data).hexdigest()
        artifact_id = str(uuid5(UUID(identifier), stream))
        relative = f"workspaces/{workspace_id}/artifacts/{artifact_id}/{stream}.log"
        with Session(engine()) as db, db.begin():
            db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            current = db.get(CommandExecution, identifier, with_for_update=True)
            if not current.resources_closed:
                raise AppError("command_log_busy", "进程尚未确认结束，不能发布不可变日志。", 409)
            artifact = db.get(Artifact, artifact_id)
            if artifact is None:
                db.add(
                    Artifact(
                        id=artifact_id,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        run_id=run_id,
                        display_name=f"command-{identifier[:8]}-{stream}.log",
                        mime_type="text/plain",
                        byte_size=len(data),
                        content_hash=digest,
                        storage_path=relative,
                        kind="command_log",
                        status="publishing",
                        metadata_json={
                            "command_id": identifier,
                            "stream": stream,
                            "sandbox": "native_scoped",
                        },
                    )
                )
            elif artifact.content_hash != digest:
                raise AppError("command_log_changed", "已确认结束的命令日志发生变化。", 409)
        target = storage_path(relative)
        if not target.exists():
            publish_bytes(target, data)
        elif file_digest(target) != digest:
            raise AppError("command_log_changed", "已发布日志发生变化。", 409)
        with Session(engine()) as db, db.begin():
            db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            db.get(Artifact, artifact_id).status = "available"
        references[stream] = artifact_id
    with Session(engine()) as db, db.begin():
        db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        db.get(CommandExecution, identifier, with_for_update=True).log_artifacts = references


def reconcile(identifier):
    from workbench.modules.processes import native

    with write_gate():
        with Session(engine()) as db:
            row = db.get(CommandExecution, identifier)
            if row is None:
                raise AppError("command_not_found", "命令不存在。", 404)
            if row.resources_closed and (row.log_artifacts or not row.receipt):
                return view(row)
            publish_closed = row.resources_closed
            output, expected, stopping, nonce = (
                storage_path(row.output_path),
                row.native_identity,
                row.stop_requested,
                row.nonce,
            )
            status = row.status
            age = (now() - row.created_at).total_seconds()
        if publish_closed:
            _publish_logs(identifier)
            with Session(engine()) as db:
                return view(db.get(CommandExecution, identifier))
        if not (output / "state.json").is_file():
            if stopping and status == "reserved":
                with Session(engine()) as db, db.begin():
                    reference = db.get(CommandExecution, identifier)
                    db.scalar(select(Run).where(Run.id == reference.run_id).with_for_update())
                    row = db.get(CommandExecution, identifier, with_for_update=True)
                    if row.status == "reserved":
                        row.status, row.resources_closed = "cancelled", True
                    return view(row)
            # Missing acknowledgement does not prove that no process was created.
            if age > 30:
                return _unknown(identifier, "执行器启动回执仍未确认；保留资源记录，不会重启命令。")
            with Session(engine()) as db:
                return view(db.get(CommandExecution, identifier))
        try:
            state = native.read_state(output)
        except (OSError, ValueError, native.RunnerError):
            return _unknown(identifier, "执行器回执不可核对；保留资源记录，不会重启命令。")
        identity = state.get("supervisor_identity")
        if (
            state.get("command_id") != identifier
            or state.get("nonce") != nonce
            or not identity
            or identity.get("nonce") != nonce
            or (expected is not None and identity != expected)
        ):
            return _unknown(identifier, "命令执行器身份未确认，未发送信号。")
        if stopping:
            result = native.stop(output, expected_identity=identity)
            state = result.state
        if state is None:
            with Session(engine()) as db:
                return view(db.get(CommandExecution, identifier))
        if state.get("nonce") != nonce or state.get("supervisor_identity") != identity:
            return _unknown(identifier, "停止后的回执身份已变化，资源关闭尚未确认。")
        with Session(engine()) as db, db.begin():
            reference = db.get(CommandExecution, identifier)
            db.scalar(select(Run).where(Run.id == reference.run_id).with_for_update())
            row = db.get(CommandExecution, identifier, with_for_update=True)
            if not row.resources_closed:
                row.native_identity, row.receipt = identity, state
                row.status = state.get("effective_status", state["status"])
                row.resources_closed = state.get("owned_resources_closed") is True
                row.error = state.get("error")
            publish = row.resources_closed and len(row.log_artifacts) != 2
        if publish:
            _publish_logs(identifier)
        with Session(engine()) as db:
            return view(db.get(CommandExecution, identifier))


def _unknown(identifier, message):
    with Session(engine()) as db, db.begin():
        reference = db.get(CommandExecution, identifier)
        db.scalar(select(Run).where(Run.id == reference.run_id).with_for_update())
        row = db.get(CommandExecution, identifier, with_for_update=True)
        if not row.resources_closed:
            row.status = "outcome_unknown"
            row.error = {"code": "command_receipt_unknown", "message": message}
        return view(row)


def _authorize(user_id, workspace_id, identifier, run_id):
    with Session(engine()) as db:
        return owned(db, user_id, workspace_id, identifier, run_id=run_id).output_path


async def inspect(user_id, workspace_id, identifier, *, run_id=None):
    await asyncio.to_thread(_authorize, user_id, workspace_id, identifier, run_id)
    return await asyncio.to_thread(reconcile, str(identifier))


async def read(user_id, workspace_id, identifier, stream, offset, limit, *, run_id=None):
    from workbench.modules.processes import native

    command = await inspect(user_id, workspace_id, identifier, run_id=run_id)
    if command.owned_resources_closed and stream in command.log_artifacts:
        path = await asyncio.to_thread(
            _log_artifact, user_id, workspace_id, command.log_artifacts[stream]
        )
        page = await asyncio.to_thread(
            native.read_log_file, path, stream=stream, offset=offset, limit=limit
        )
    else:
        relative = await asyncio.to_thread(_authorize, user_id, workspace_id, identifier, run_id)
        output = storage_path(relative)
        try:
            page = await asyncio.to_thread(
                native.read_log, output, stream=stream, offset=offset, limit=limit
            )
        except (OSError, ValueError, native.RunnerError):
            raise AppError(
                "command_log_not_ready", "执行器日志暂不可核对；请保留当前游标并稍后读取。", 409
            ) from None
    return dto.CommandOutput(command=command, **page)


def _log_artifact(user_id, workspace_id, artifact_id):
    from workbench.modules.files.service import get_artifact

    with Session(engine()) as db:
        row = get_artifact(db, user_id, workspace_id, artifact_id)
        path = storage_path(row.storage_path)
        if (
            row.status != "available"
            or row.kind != "command_log"
            or path.stat().st_size != row.byte_size
            or file_digest(path) != row.content_hash
        ):
            raise AppError("command_log_changed", "已保存的命令日志无法按原版本核对。", 409)
        return path


async def stop(user_id, workspace_id, identifier, *, run_id=None):
    await asyncio.to_thread(_authorize, user_id, workspace_id, identifier, run_id)
    await asyncio.to_thread(_request_id_stop, str(identifier))
    return await asyncio.to_thread(reconcile, str(identifier))


async def serve_cleanup(stop_event):
    def candidates():
        with Session(engine()) as db:
            return list(
                db.scalars(
                    select(CommandExecution.id)
                    .where(
                        or_(
                            CommandExecution.resources_closed.is_(False),
                            and_(
                                CommandExecution.receipt["status"].astext.is_not(None),
                                CommandExecution.log_artifacts == {},
                            ),
                        )
                    )
                    .order_by(CommandExecution.created_at)
                    .limit(20)
                )
            )

    while not stop_event.is_set():
        identifiers = await asyncio.to_thread(candidates)
        for identifier in identifiers:
            try:
                await asyncio.to_thread(reconcile, identifier)
            except (AppError, OSError, ValueError, RuntimeError):
                pass  # Unknown ownership is retained; unrelated jobs can still progress.
        try:
            await asyncio.wait_for(stop_event.wait(), 0.5)
        except TimeoutError:
            pass


def stop_all():
    """Final sweep after API/worker shutdown, including commands launched while stopping."""
    from sqlalchemy import inspect as inspect_database

    if not inspect_database(engine()).has_table("command_executions"):
        return []
    with Session(engine()) as db:
        identifiers = list(
            db.scalars(
                select(CommandExecution.id).where(CommandExecution.resources_closed.is_(False))
            )
        )
    results = []
    for identifier in identifiers:
        _request_id_stop(identifier)
        results.append(reconcile(identifier))
    return results
