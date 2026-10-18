"""Durable progress guard. A heartbeat is deliberately not evidence of progress."""

import hashlib
import json
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, now
from workbench.modules.runs.domain import Run
from workbench.modules.runs.service import emit, enqueue_resume, get, idempotent, set_status, view

# Timestamps and per-call IDs cannot turn the same observation into progress.
VOLATILE = {
    "id",
    "operation_id",
    "tool_call_id",
    "created_at",
    "updated_at",
    "elapsed_ms",
    "duration_ms",
    "fetched_at",
    "observed_at",
    "observation_id",
    "timestamp",
}


def observation_digest(value):
    def clean(item):
        if isinstance(item, dict):
            return {key: clean(val) for key, val in item.items() if key not in VOLATILE}
        if isinstance(item, list):
            return [clean(val) for val in item]
        return item

    return hashlib.sha256(
        json.dumps(clean(value), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def observe(run_id: str, operation_id: str, name: str, arguments: dict, result: dict):
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        config = dict(run.config_snapshot)
        guard = dict(config.get("safety", {}))
        recent = list(guard.get("observations", []))
        if any(item["operation_id"] == operation_id for item in recent):
            return
        fingerprint = observation_digest({"tool": name, "arguments": arguments, "result": result})
        repeated = sum(item["fingerprint"] == fingerprint for item in recent)
        # Waiting on bounded native jobs is valid; fresh bytes/cursors/state still
        # change the digest. These tools already have real resource deadlines.
        waiting = waiting_observation(name, result)
        progress = not result.get("error") and (repeated == 0 or waiting)
        if progress:
            guard["progress_round"] = guard.get("current_round", 0)
        recent.append({"operation_id": operation_id, "fingerprint": fingerprint})
        guard["observations"] = recent[-64:]
        if repeated >= 2 and not waiting:
            warned = list(guard.get("warned_fingerprints", []))
            if fingerprint in warned:
                guard["pending_pause"] = {
                    "code": "repeated_tool_call",
                    "message": "同一工具及参数反复返回相同结果，已保留断点。请调整要求后继续。",
                }
            else:
                warned.append(fingerprint)
                guard["warned_fingerprints"] = warned[-64:]
                guard["notice"] = "同一工具与参数已连续多次没有新结果，请改变方案；再次重复将暂停。"
                emit(db, run.id, "run.progress_warning", {"message": guard["notice"]})
        config["safety"] = guard
        run.config_snapshot = config


def waiting_observation(name, result):
    if name in {"read_command_output", "file_task_status"}:
        return result.get("status") in {"queued", "running", "starting", "launching"}
    if name in {"inspect_agents", "wait_agents"}:
        return any(
            item.get("run", {}).get("status") not in {"succeeded", "failed", "cancelled", None}
            for item in result.get("agents", [])
        )
    return False


def before_model(run_id: str, round_number: int, before_next_model=True) -> dict | None:
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        config = dict(run.config_snapshot)
        guard = dict(config.get("safety", {}))
        if guard.get("wait"):
            return guard["wait"]
        options = config.get("runtime_options", {})
        reason = guard.pop("pending_pause", None)
        if before_next_model and round_number >= options.get("max_model_rounds", 1024):
            reason = {
                "code": "round_limit",
                "message": "已到安全总轮次上限，结果和断点已保留，可扩展预算继续。",
            }
        if before_next_model and round_number - guard.get("progress_round", 0) >= 10:
            reason = reason or {
                "code": "no_progress",
                "message": "连续10轮没有新的有效工具观察，已暂停以避免空转。",
            }
        stage = options.get("checkpoint_rounds", 32)
        boundary = (
            before_next_model
            and round_number > 0
            and round_number - guard.get("stage_round", 0) >= stage
        )
        guard["current_round"] = round_number
        wait = None
        if reason or boundary:
            wait = {
                "kind": "safety" if reason else "continuation",
                "run_id": run_id,
                "id": str(uuid5(UUID(run_id), f"guard:{round_number}:{run.version}")),
                "round_number": round_number,
            }
            guard["wait"] = wait
            if reason:
                guard["paused_reason"] = reason
                set_status(db, run, "paused")
                emit(db, run_id, "run.paused", reason)
            else:
                emit(db, run_id, "run.checkpointed", {"round_number": round_number})
        config["safety"] = guard
        run.config_snapshot = config
        return wait


def acknowledge(run_id: str, wait: dict):
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        config = dict(run.config_snapshot)
        guard = dict(config.get("safety", {}))
        if wait["kind"] == "safety" and guard.get("accepted_wait") != wait["id"]:
            raise AppError("run_still_paused", "请明确继续任务后再运行。", 409, "pause")
        guard.pop("wait", None)
        guard.pop("accepted_wait", None)
        guard["stage_round"] = wait["round_number"]
        config["safety"] = guard
        run.config_snapshot = config


def pause(db, run, reason):
    config = dict(run.config_snapshot)
    guard = dict(config.get("safety", {}))
    guard["paused_reason"] = reason
    config["safety"] = guard
    run.config_snapshot = config
    set_status(db, run, "paused")
    emit(db, run.id, "run.paused", reason)


def resume(db, user_id, run_id, values):
    def execute():
        from workbench.modules.tools.domain import Operation

        target = get(db, user_id, run_id)
        get(db, user_id, target.root_run_id or target.id, lock=True)
        run = get(db, user_id, run_id, lock=True)
        if run.status != "paused":
            raise AppError("run_not_paused", "只有暂停的任务可以继续。", 409)
        if db.scalar(
            select(Operation.id)
            .where(Operation.run_id == run_id, Operation.status.in_(["running", "outcome_unknown"]))
            .limit(1)
        ):
            raise AppError(
                "tool_outcome_unknown",
                "工具实际结果尚未确认，不能自动重放。请先核对并停止此任务。",
                409,
            )
        config = dict(run.config_snapshot)
        guard = dict(config.get("safety", {}))
        if guard.get("paused_reason", {}).get("code") == "model_output_limit":
            guard["continue_partial_output"] = True
        if values.get("extend_budget"):
            if guard.get("paused_reason", {}).get("code") == "model_output_limit":
                config["max_output_tokens"] = None
            else:
                extend_runtime_budget(db, user_id, run, config)
        if guard.get("wait"):
            guard["accepted_wait"] = guard["wait"]["id"]
        guard["progress_round"] = guard.get("current_round", 0)
        guard.pop("paused_reason", None)
        guard.pop("pending_pause", None)
        guard["manual_retry_grants"] = guard.get("manual_retry_grants", 0) + 1
        config["safety"] = guard
        run.config_snapshot = config
        run.error = None
        enqueue_resume(db, run)
        # A user can resume after the pause record commits but before the graph
        # interrupt reaches scheduler.finish. Mark readiness even with a live lease.
        set_status(db, run, "queued")
        emit(db, run.id, "run.resumed", {"explicit": True, "at": now().isoformat()})
        return view(db, run)

    return idempotent(db, user_id, values["request_id"], f"run:{run_id}:resume", values, execute)


def extend_runtime_budget(db, user_id, run, config):
    options = dict(config.get("runtime_options", {}))
    options["max_model_rounds"] = min(65536, options.get("max_model_rounds", 1024) + 1024)
    config["runtime_options"] = options
    root = get(db, user_id, run.root_run_id or run.id, lock=True)
    root_config = config if root.id == run.id else dict(root.config_snapshot)
    root_options = dict(root_config.get("runtime_options", {}))
    root_options["root_model_attempt_limit"] = min(
        524288, root_options.get("root_model_attempt_limit", 8192) + 8192
    )
    root_config["runtime_options"] = root_options
    if root.id != run.id:
        root.config_snapshot = root_config
