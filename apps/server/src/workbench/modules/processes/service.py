"""Approved command handles, bounded output, and truthful resource reconciliation."""

import asyncio
import hashlib
import json
import os
from uuid import UUID, uuid4, uuid5

from sqlalchemy import and_, func, or_, select, text
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
        interactive=receipt.get("interactive", False),
        stderr_merged=receipt.get("stderr_merged", False),
        terminal_rows=receipt.get("terminal_rows"),
        terminal_columns=receipt.get("terminal_columns"),
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


def _capacity(db, run_id):
    settings = get_settings()
    busy = list(
        db.scalars(
            select(CommandExecution)
            .where(CommandExecution.resources_closed.is_(False))
            .order_by(CommandExecution.created_at, CommandExecution.id)
        )
    )
    own = [row for row in busy if row.run_id == run_id]
    per_run, total = settings.command_max_active_per_run, settings.command_max_active_total
    return {
        "limits": {"per_run": per_run, "total": total},
        "occupied": {
            "run": len(own),
            "total": len(busy),
            "outside_run": len(busy) - len(own),
            "unknown_run": sum(row.status == "outcome_unknown" for row in own),
            "unknown_total": sum(row.status == "outcome_unknown" for row in busy),
        },
        "available": max(0, min(per_run - len(own), total - len(busy))),
        # Other tasks may belong to another user/workspace. Only expose their count.
        "commands": [
            {
                "command_id": row.id,
                "status": row.status,
                "command_preview": row.command[:160],
                "preview_truncated": len(row.command) > 160,
                "stop_requested": row.stop_requested,
                "owned_resources_closed": False,
                "interactive": bool((row.receipt or {}).get("interactive", False)),
                "timeout_seconds": row.timeout_seconds,
                "error": row.error,
                "supervisor_observation": (row.receipt or {}).get("supervisor_observation"),
                "created_at": row.created_at.isoformat(),
            }
            for row in own
        ],
        "unknown_resources_occupy_slots": True,
        "next_steps": [
            "用read_command_output及原command_id继续查看日志；next_offset是续读游标。",
            "交互终端需要输入时复用write_command_input；不再需要的本任务命令用stop_command停止。",
            "未知状态不能直接清除；须核对真实资源关闭回执。",
            "本任务以外的命令仅计数，不得代替其他任务停止；可先处理其他工作，稍后重新查询。",
        ],
    }


def _refresh_capacity():
    """Reclaim only proven closures, without stopping or publishing another task's logs."""
    with Session(engine()) as db:
        identifiers = list(
            db.scalars(
                select(CommandExecution.id)
                .where(CommandExecution.resources_closed.is_(False))
                .order_by(CommandExecution.created_at)
            )
        )
    for identifier in identifiers:
        try:
            reconcile(identifier, observation_only=True)
        except (AppError, OSError, ValueError, RuntimeError):
            pass  # Missing or untrusted receipts continue to occupy their slots.


async def active_commands(user_id, workspace_id, run_id):
    def inspect_capacity():
        with Session(engine()) as db:
            workspace(db, user_id, workspace_id)
            run = db.get(Run, run_id)
            if run is None or run.user_id != user_id or run.workspace_id != workspace_id:
                raise AppError("run_not_found", "任务不属于当前工作区。", 404)
        _refresh_capacity()
        with Session(engine()) as db:
            return _capacity(db, run_id)

    return await asyncio.to_thread(inspect_capacity)


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
            or operation.arguments.get("command") != values["command"]
            or operation.arguments.get("timeout_seconds") != values["timeout_seconds"]
            or operation.arguments.get("interactive", False) != values.get("interactive", False)
        ):
            raise AppError("command_approval_required", "此命令尚无参数一致的有效批准。", 403)
        existing = db.get(CommandExecution, operation_id)
        if existing:
            return existing.id, existing.status == "reserved" and not existing.resources_closed
        profile = run.config_snapshot.get("runtime_options", {}).get(
            "sandbox_profile", "native_scoped"
        )
        if values.get("interactive", False) and profile != "native_scoped":
            raise AppError(
                "terminal_sandbox_unsupported",
                "当前沙箱未实现交互终端；未降低沙箱级别。",
                409,
                "capability",
            )
        isolation = provider_for(profile).capability()
        if isolation.status != "ready":
            raise AppError("sandbox_unavailable", isolation.reason, 409, "capability")
        db.execute(text("SELECT pg_advisory_xact_lock(82139715)"))
        capacity = _capacity(db, run_id)
        if not capacity["available"]:
            limits, occupied = capacity["limits"], capacity["occupied"]
            raise AppError(
                "command_capacity",
                f"活动命令名额已满：本任务{occupied['run']}/{limits['per_run']}，"
                f"本机{occupied['total']}/{limits['total']}。本次命令尚未启动；"
                "请先用list_commands查看占位，复用原命令或等待资源关闭，不要立即反复启动。",
                409,
                retryable=True,
                details={**capacity, "not_started": True},
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
        # The approved Operation is the durable source for this optional mode;
        # old operations/plans remain noninteractive without a schema migration.
        operation = db.get(Operation, row.operation_id)
        if operation.arguments.get("interactive", False):
            plan["interactive"] = True
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
    try:
        identifier, fresh = await asyncio.to_thread(_reserve, run_id, operation_id, values)
    except AppError as error:
        if error.code != "command_capacity":
            raise
        # The periodic janitor may not yet have consumed a just-finished receipt.
        # Refresh outside the admission transaction to preserve run lock ordering.
        await asyncio.to_thread(_refresh_capacity)
        identifier, fresh = await asyncio.to_thread(_reserve, run_id, operation_id, values)
    if fresh:
        try:
            return await asyncio.to_thread(_dispatch, identifier)
        except BaseException:
            await asyncio.shield(asyncio.to_thread(_request_id_stop, identifier))
            raise
    return await asyncio.to_thread(reconcile, identifier)


def _write_input(run_id, operation_id, values):
    from workbench.modules.processes import native, terminal
    from workbench.modules.tools.service import argument_digest

    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        assert_owned(db, run_id)
        if run.parent_run_id or run.cancel_requested_at or run.status != "running":
            raise AppError("terminal_run_inactive", "仅当前根任务可向自己的活动终端输入。", 409)
        row = db.get(CommandExecution, str(values["command_id"]), with_for_update=True)
        if row is None or row.run_id != run_id:
            raise AppError("command_not_found", "命令不属于当前任务。", 404)
        operation = db.get(Operation, operation_id, with_for_update=True)
        approval = db.scalar(
            select(Approval).where(Approval.operation_id == operation_id).with_for_update()
        )
        if (
            operation is None
            or operation.run_id != run_id
            or operation.tool_name != "write_command_input"
            or operation.status != "running"
            or approval is None
            or approval.run_id != run_id
            or approval.tool_name != "write_command_input"
            or approval.status != "approved"
            or not approval.approved
            or approval.parameters_digest != argument_digest(operation.arguments)
            or str(operation.arguments.get("command_id")) != str(values["command_id"])
            or operation.arguments.get("data", "") != values.get("data", "")
            or operation.arguments.get("eof", False) != values.get("eof", False)
        ):
            raise AppError("command_approval_required", "终端输入尚无参数一致的有效批准。", 403)
        if row.stop_requested or row.resources_closed or row.status != "running":
            raise AppError("terminal_closed", "命令已结束、停止或状态未知，不能继续输入。", 409)
        try:
            state = native.read_state(storage_path(row.output_path))
            if (
                state.get("nonce") != row.nonce
                or state.get("supervisor_identity") != row.native_identity
                or not state.get("interactive", False)
            ):
                raise native.RunnerError("Interactive terminal identity mismatch.")
            receipt = terminal.submit(
                storage_path(row.output_path),
                str(operation_id),
                values.get("data", ""),
                values.get("eof", False),
            )
        except (OSError, ValueError, native.RunnerError) as error:
            raise AppError(
                "terminal_input_unavailable",
                "终端输入未确认；不得重发相同内容，请核对原操作回执。",
                409,
                "outcome_unknown",
            ) from error
        if receipt["status"] == "outcome_unknown":
            raise AppError(
                "terminal_input_unknown",
                "终端可能已收到输入；请核对日志，不得再次发送相同内容。",
                409,
                "outcome_unknown",
            )
        # Keep nonce/private details inside the execution ledger.
        return {
            "command_id": row.id,
            "operation_id": str(operation_id),
            "status": receipt["status"],
            "bytes_written": receipt.get("bytes_written"),
            "eof": values.get("eof", False),
            "stderr_merged": True,
            "terminal_rows": 24,
            "terminal_columns": 80,
            "reason": receipt.get("reason"),
            "outcome_note": "输入回执仅证明写入终端，不证明命令业务成功。",
        }


async def write_input(run_id, operation_id, values):
    """ToolExecutor calls this only after the ordinary independent approval path."""
    return await asyncio.to_thread(_write_input, run_id, operation_id, values)


async def execute_bounded(run_id, operation_id, values):
    """The short command tool shares the same durable ownership and cleanup path."""
    command = await start(run_id, operation_id, {**values, "max_output_bytes": 64 * 1024})
    try:
        while not command.owned_resources_closed:
            if command.status == "outcome_unknown":
                raise AppError(
                    "command_outcome_unknown",
                    "命令执行结果或资源关闭状态尚未确认；不会重放，日志和资源记录继续保留。",
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


def abandoned_command_condition():
    """A dead supervisor cannot resume cleanup; its missing closure stays unknown."""
    receipt = CommandExecution.receipt
    return func.coalesce(
        and_(
            CommandExecution.status == "outcome_unknown",
            func.coalesce(CommandExecution.error["code"].astext != "command_receipt_unknown", True),
            receipt["status"].astext == "outcome_unknown",
            receipt["command_id"].astext == CommandExecution.id,
            receipt["nonce"].astext == CommandExecution.nonce,
            receipt["supervisor_identity"] == CommandExecution.native_identity,
            receipt["supervisor_observation"].astext == "gone",
            receipt["owned_resources_closed"].astext == "false",
            receipt["finished_at"].astext.is_not(None),
        ),
        False,
    )


def cleanup_unconfirmed(db, run_id):
    if not cleanup_pending(db, run_id):
        return False
    return (
        db.scalar(
            select(CommandExecution.id)
            .where(
                CommandExecution.run_id == run_id,
                CommandExecution.resources_closed.is_(False),
                ~abandoned_command_condition(),
            )
            .limit(1)
        )
        is None
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


def _save_reboot_closure(identifier, proof):
    with Session(engine()) as db, db.begin():
        reference = db.get(CommandExecution, identifier)
        db.scalar(select(Run).where(Run.id == reference.run_id).with_for_update())
        row = db.get(CommandExecution, identifier, with_for_update=True)
        if row.resources_closed:
            return view(row)
        if row.id != proof["command_id"] or row.nonce != proof["nonce"]:
            raise AppError("command_receipt_unknown", "重启核对身份已变化，未释放资源记录。", 409)
        receipt = dict(row.receipt or {})
        receipt["resource_reconciliation"] = {
            **proof,
            "observed_at": now().isoformat(),
            "original_status": row.status,
            "original_resources_closed": receipt.get("owned_resources_closed"),
        }
        receipt["owned_resources_closed"] = True
        row.receipt, row.resources_closed, row.status = receipt, True, "outcome_unknown"
        row.error = {
            "code": "command_resources_released_after_reboot",
            "message": "系统重启已确认旧进程资源释放；原命令执行结果仍未知，原回执与日志已保留。",
        }
        return view(row)


def reconcile_after_reboot(identifier, *, legacy_prior_boot_id=None):
    """Legacy attribution is operator-only; never exposed as a model tool or inferred."""
    from workbench.modules.processes import native

    with write_gate():
        with Session(engine()) as db:
            row = db.get(CommandExecution, identifier)
            if row is None:
                raise AppError("command_not_found", "命令不存在。", 404)
            if row.resources_closed:
                return view(row)
            proof = native.reboot_closure_proof(
                storage_path(row.output_path),
                row.nonce,
                legacy_prior_boot_id=legacy_prior_boot_id,
                expected_identity=row.native_identity,
            )
        return _save_reboot_closure(identifier, proof) if proof else None


def reconcile(identifier, *, observation_only=False):
    from workbench.modules.processes import native

    with write_gate():
        with Session(engine()) as db:
            row = db.get(CommandExecution, identifier)
            if row is None:
                raise AppError("command_not_found", "命令不存在。", 404)
            reboot_closed = ((row.receipt or {}).get("resource_reconciliation") or {}).get(
                "method"
            ) == "host_reboot"
            if row.resources_closed and (
                reboot_closed or observation_only or row.log_artifacts or not row.receipt
            ):
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
        try:
            reboot_proof = native.reboot_closure_proof(output, nonce)
        except (OSError, ValueError, native.RunnerError):
            reboot_proof = None
        if reboot_proof:
            return _save_reboot_closure(identifier, reboot_proof)
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
        if stopping and not observation_only:
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
        if publish and not observation_only:
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
                                CommandExecution.receipt["resource_reconciliation"][
                                    "method"
                                ].astext.is_(None),
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
