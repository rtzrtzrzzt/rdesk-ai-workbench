"""UMA admission and resident-phase receipts, using the existing owned command tree."""

import asyncio
import json
import time
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import psutil
from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.config import PROJECT_ROOT
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, now
from workbench.infrastructure.vault.private import write_json
from workbench.modules.files.storage import storage_path
from workbench.modules.media.domain import MediaResidency, MediaTask
from workbench.modules.media.locking import lock_run
from workbench.modules.processes import native
from workbench.modules.runs.domain import Job, Run
from workbench.modules.runs.ownership import current_owner
from workbench.modules.runs.service import emit


def active_run_ids(db):
    return list(db.scalars(select(MediaResidency.run_id).where(MediaResidency.status != "closed")))


def boot_id():
    # Native GPU stages currently require Linux. A different kernel boot proves
    # that its old CUDA processes cannot still occupy this host's shared RAM.
    try:
        return str(UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip()))
    except (OSError, ValueError):
        return None


def reserve(run_id, phase, configuration):
    identifier = str(uuid5(UUID(run_id), "media-phase:" + phase))
    directory = storage_path(f"media/jobs/{run_id}/commands/{identifier}")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with Session(engine()) as db, db.begin():
        lock_run(db, run_id)
        row = db.get(MediaResidency, identifier)
        if row is not None and (directory / "launch.json").exists():
            return directory, row.status
        # model_permit is held before this transaction. Current residency is
        # already included in MemAvailable; do not deduct the Qwen weight size.
        if db.scalar(
            select(MediaResidency.id)
            .where(MediaResidency.status != "closed", MediaResidency.id != identifier)
            .limit(1)
        ):
            raise AppError(
                "media_residency_pending",
                "前一生成阶段尚未确认回收，未加载更多权重。",
                409,
                "resource",
            )
        available = psutil.virtual_memory().available
        requested = int(configuration["memory_limit_gib"] * 1024**3)
        headroom = int(configuration["headroom_gib"] * 1024**3)
        if available < requested + headroom:
            raise AppError(
                "media_memory_insufficient",
                f"当前可用{available / 1024**3:.1f}GiB；此阶段需预留{requested / 1024**3:.1f}GiB"
                f"及系统余量{headroom / 1024**3:.1f}GiB。主模型保持运行，未加载生图权重。",
                409,
                "resource",
            )
        if row is None:
            row = MediaResidency(
                id=identifier,
                run_id=run_id,
                phase=phase,
                reserved_bytes=requested,
                headroom_bytes=headroom,
                command_directory=str(directory),
            )
            db.add(row)
        else:
            row.status, row.closed_at = "reserved", None
        task = db.scalar(select(MediaTask).where(MediaTask.run_id == run_id).with_for_update())
        task.resources_closed = False
        phases = dict(task.metadata_json.get("phases", {}))
        phases[phase] = {**phases.get(phase, {}), "reservation_boot_id": boot_id()}
        task.metadata_json = {**task.metadata_json, "phases": phases}
        emit(
            db,
            run_id,
            "media.memory_reserved",
            {
                "phase": phase,
                "reserved_bytes": requested,
                "available_bytes": available,
                "headroom_bytes": headroom,
            },
        )
    return directory, "reserved"


def close_previous_boot(run_id, phase):
    current = boot_id()
    if current is None:
        return False
    with Session(engine()) as db, db.begin():
        lock_run(db, run_id, owned=False)
        row = db.scalar(
            select(MediaResidency)
            .where(MediaResidency.run_id == run_id, MediaResidency.phase == phase)
            .with_for_update()
        )
        task = db.scalar(select(MediaTask).where(MediaTask.run_id == run_id).with_for_update())
        previous = task.metadata_json.get("phases", {}).get(phase, {}).get("reservation_boot_id")
        if not previous or previous == current:
            return False
        row.status, row.closed_at = "closed", now()
        db.flush()
        task.resources_closed = not bool(
            db.scalar(
                select(MediaResidency.id)
                .where(MediaResidency.run_id == run_id, MediaResidency.status != "closed")
                .limit(1)
            )
        )
        emit(db, run_id, "media.reservation_released", {"phase": phase, "reason": "host_restarted"})
        return True


def launch_phase(run_id, directory, plan_path):
    # This Job lock is also the barrier for reclaiming a reservation without a
    # launch intent. An old worker cannot create an intent after reclamation.
    with Session(engine()) as db, db.begin():
        lock_run(db, run_id)
        run = db.get(Run, run_id)
        if run.cancel_requested_at:
            raise AppError("media_cancelled", "生成任务已请求停止。", 409)
        return native.launch(plan_path)


def close_unlaunched(run_id, phase, *, finishing=False):
    with Session(engine()) as db, db.begin():
        lock_run(db, run_id, owned=False)
        job = db.scalar(select(Job).where(Job.run_id == run_id))
        row = db.scalar(
            select(MediaResidency)
            .where(MediaResidency.run_id == run_id, MediaResidency.phase == phase)
            .with_for_update()
        )
        if row is None or (storage_path(row.command_directory) / "launch.json").exists():
            return False
        owner = current_owner.get()
        own_finish = (
            finishing
            and owner
            and owner.worker_id == job.lease_owner
            and owner.epoch == job.lease_epoch
        )
        inactive = job.status != "running" or not job.lease_until or job.lease_until <= now()
        if not (inactive or own_finish):
            return False
        row.status, row.closed_at = "closed", now()
        db.flush()
        task = db.scalar(select(MediaTask).where(MediaTask.run_id == run_id).with_for_update())
        task.resources_closed = not bool(
            db.scalar(
                select(MediaResidency.id)
                .where(MediaResidency.run_id == run_id, MediaResidency.status != "closed")
                .limit(1)
            )
        )
        emit(
            db,
            run_id,
            "media.reservation_released",
            {"phase": phase, "reason": "launch_not_started"},
        )
        return True


def phase_plan(run_id, phase, directory, configuration):
    plan_path = directory / "plan.json"
    if plan_path.exists():
        return plan_path
    plan = {
        "schema": 1,
        "command_id": directory.name,
        "nonce": str(uuid4()),
        "argv": [
            str(PROJECT_ROOT / ".runtime/media-env/bin/python"),
            "-I",
            str(PROJECT_ROOT / "scripts/media/flux_phase.py"),
            "--request",
            str(storage_path(f"media/jobs/{run_id}/request.json")),
            "--phase",
            phase,
        ],
        "cwd": str(storage_path(f"media/jobs/{run_id}")),
        "timeout_seconds": configuration["timeout_seconds"],
        "max_output_bytes": 16 * 1024**2,
        "output_dir": str(directory),
        "sandbox_profile": "native_scoped",
        "protected_roots": [],
    }
    write_json(plan_path, plan)
    return plan_path


def record_receipt(run_id, phase, state):
    with Session(engine()) as db, db.begin():
        lock_run(db, run_id, owned=False)
        row = db.scalar(
            select(MediaResidency)
            .where(MediaResidency.run_id == run_id, MediaResidency.phase == phase)
            .with_for_update()
        )
        if row is None:
            return
        row.supervisor_identity = state.get("supervisor_identity")
        if state.get("owned_resources_closed") and state.get("supervisor_observation") == "gone":
            row.status, row.closed_at = "closed", now()
        elif row.status != "closed":
            row.status = "running" if state.get("status") == "running" else "unknown"
        task = db.scalar(select(MediaTask).where(MediaTask.run_id == run_id).with_for_update())
        task.resources_closed = not bool(
            db.scalar(
                select(MediaResidency.id)
                .where(MediaResidency.run_id == run_id, MediaResidency.status != "closed")
                .limit(1)
            )
        )


def record_progress(run_id, phase, event, minimum_available):
    with Session(engine()) as db, db.begin():
        lock_run(db, run_id)
        task = db.scalar(select(MediaTask).where(MediaTask.run_id == run_id).with_for_update())
        metadata = dict(task.metadata_json)
        phases = dict(metadata.get("phases", {}))
        phases[phase] = {**phases.get(phase, {}), **event}
        metadata.update(
            phases=phases,
            minimum_available_bytes=min(
                metadata.get("minimum_available_bytes", minimum_available), minimum_available
            ),
            residency_policy="phase_exit",
            precision="NVFP4",
        )
        # Sampling progress belongs to its phase, not the complete generation.
        if task.phase != phase:
            task.progress = None
        task.metadata_json, task.phase = metadata, phase
        if (
            type(event.get("step")) is int
            and type(event.get("total")) is int
            and 0 <= event["step"] <= event["total"]
            and event["total"] > 0
        ):
            task.progress = event["step"] / event["total"]
        emit(
            db,
            run_id,
            "media.progress",
            {"phase": phase, "progress": task.progress, "status": "running"},
        )


async def run_phase(run_id, phase, configuration):
    directory, status = await asyncio.to_thread(reserve, run_id, phase, configuration)
    if status == "closed":
        state = await asyncio.to_thread(native.read_state, directory)
        if state.get("exit_code") == 0 and state["status"] == "completed":
            return
        raise AppError("media_phase_failed", "此前阶段已失败，未重新执行，请查看生成记录。", 409)
    cursor, pending, minimum = 0, "", psutil.virtual_memory().available
    failed_event = None
    try:
        plan_path = await asyncio.to_thread(phase_plan, run_id, phase, directory, configuration)
        launched = await asyncio.to_thread(launch_phase, run_id, directory, plan_path)
        if launched.state:
            await asyncio.to_thread(record_receipt, run_id, phase, launched.state)
        if launched.status != "acknowledged":
            raise AppError(
                "media_launch_unknown",
                "生图阶段启动回执待核对，未重复加载。",
                409,
                "outcome_unknown",
            )
        next_report = 0.0
        while True:
            state = await asyncio.to_thread(native.read_state, directory)
            available = psutil.virtual_memory().available
            minimum = min(minimum, available)
            if available < configuration["headroom_gib"] * 1024**3:
                await asyncio.to_thread(
                    record_progress,
                    run_id,
                    phase,
                    {"status": "stopped", "reason": "memory_guard", "available_bytes": available},
                    minimum,
                )
                await asyncio.to_thread(native.stop, directory, timeout=15)
                raise AppError(
                    "media_memory_guard",
                    f"{phase}阶段可用内存降至{available / 1024**3:.1f}GiB，"
                    f"低于保留余量{configuration['headroom_gib']:.1f}GiB；已停止自身，主模型保持运行。",
                    409,
                    "resource",
                )
            path = directory / "stdout.log"
            if path.exists():
                with path.open("rb") as log:
                    log.seek(cursor)
                    raw = log.read(65536)
                    cursor += len(raw)
                pending += raw.decode("utf-8", errors="replace")
                complete, _, pending = pending.rpartition("\n")
                latest = None
                for line in complete.splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get("phase") in {None, phase}:
                        latest = event
                        if event.get("status") == "failed":
                            failed_event = event
                if latest and (
                    time.monotonic() >= next_report or latest.get("status") != "running"
                ):
                    await asyncio.to_thread(record_progress, run_id, phase, latest, minimum)
                    next_report = time.monotonic() + 1
            if (
                state.get("owned_resources_closed")
                and state.get("supervisor_observation") == "gone"
            ):
                await asyncio.to_thread(record_receipt, run_id, phase, state)
                if state["status"] != "completed" or state.get("exit_code") != 0:
                    reason = (failed_event or {}).get("message", "请查看本机阶段日志。")
                    raise AppError("media_phase_failed", f"{phase}阶段未完成：{reason[:500]}", 409)
                return
            if state.get("supervisor_observation") == "unknown":
                raise AppError(
                    "media_process_unknown",
                    "生图进程归属待核对，预留仍保持。",
                    409,
                    "outcome_unknown",
                )
            await asyncio.sleep(0.1)
    finally:
        # This path also runs on lease loss, cancellation and guard failure.
        # The existing Linux subreaper/pidfd driver proves the entire tree closed.
        released = await asyncio.to_thread(close_unlaunched, run_id, phase, finishing=True)
        if not released:
            result = await asyncio.to_thread(native.stop, directory, timeout=15)
            if result.state:
                await asyncio.to_thread(record_receipt, run_id, phase, result.state)


def cleanup_pending(db, run_id):
    return bool(
        db.scalar(
            select(MediaResidency.id)
            .where(MediaResidency.run_id == run_id, MediaResidency.status != "closed")
            .limit(1)
        )
    )


def stop_all():
    """Called after application workers stop; retain any unproven native owner."""
    with Session(engine()) as db:
        pending = [
            (row.run_id, row.phase, row.command_directory, row.supervisor_identity)
            for row in db.scalars(select(MediaResidency).where(MediaResidency.status != "closed"))
        ]
    for run_id, phase, directory, identity in pending:
        if close_previous_boot(run_id, phase):
            continue
        if close_unlaunched(run_id, phase):
            continue
        result = native.stop(directory, expected_identity=identity, timeout=15)
        if result.state:
            record_receipt(run_id, phase, result.state)
    with Session(engine()) as db:
        return not active_run_ids(db)


def request_cleanup(db, run_id):
    return int(cleanup_pending(db, run_id))


async def maintain():
    def pending():
        with Session(engine()) as db:
            return [
                (
                    row.run_id,
                    row.phase,
                    row.command_directory,
                    row.supervisor_identity,
                    db.get(Run, row.run_id).cancel_requested_at is not None,
                    db.scalar(select(Job.completion_status).where(Job.run_id == row.run_id)),
                )
                for row in db.scalars(
                    select(MediaResidency).where(MediaResidency.status != "closed").limit(32)
                )
            ]

    for run_id, phase, directory, identity, cancel, finishing in await asyncio.to_thread(pending):
        try:
            if await asyncio.to_thread(close_previous_boot, run_id, phase):
                continue
            if await asyncio.to_thread(close_unlaunched, run_id, phase):
                continue
            if cancel or finishing:
                stopped = await asyncio.to_thread(
                    native.stop, directory, expected_identity=identity, timeout=0
                )
                state = stopped.state
            else:
                state = await asyncio.to_thread(native.read_state, directory)
            if state:
                await asyncio.to_thread(record_receipt, run_id, phase, state)
        except (OSError, ValueError, native.RunnerError):
            # Missing closure proof is retained; it never releases admission.
            continue
