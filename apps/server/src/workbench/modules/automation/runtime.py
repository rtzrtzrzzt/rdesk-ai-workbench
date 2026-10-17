"""Explicit native executor lifecycle; PID alone never proves ownership."""

import asyncio
import json
import os
import subprocess
import sys
import time
from uuid import uuid4

from workbench.config import PROJECT_ROOT, get_settings
from workbench.contracts.errors import AppError
from workbench.infrastructure.locks import advisory_connection
from workbench.infrastructure.native_process import capture, matches, terminate_owned
from workbench.modules.automation.dto import RuntimeView
from workbench.modules.files.storage import publish_bytes, storage_path


def state():
    path = storage_path("runtime/automation.json")
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        raise AppError("automation_state_unknown", "原生执行器记录损坏，请先核对。", 409) from None


def status():
    record = state()
    if record is None:
        return RuntimeView(status="stopped", reason="可选原生执行器尚未启动。")
    if matches(record):
        return RuntimeView(
            status="running", reason="当前原生进程身份已确认。", instance_id=record["nonce"]
        )
    result = terminate_owned(record, timeout=0, force=False)
    return RuntimeView(
        status="stopped" if result.status == "confirmed" else "unknown",
        reason="原生进程已退出。"
        if result.status == "confirmed"
        else "原生进程归属无法确认，不会按PID操作。",
        instance_id=record.get("nonce"),
    )


def start():
    from workbench.modules.automation.transport import inspect_endpoint, release_dead_endpoint

    with advisory_connection("automation-native-lifecycle"):
        existing = state()
        current = status()
        if current.status == "running":
            return current
        if current.status == "unknown":
            raise AppError("automation_owner_unknown", current.reason, 409, "outcome_unknown")
        # Remove only our dead executor's exact socket inode. A live/foreign
        # endpoint must never be overwritten by a fresh companion.
        if existing:
            for name in existing.get("endpoints", []):
                cleaned = release_dead_endpoint(get_settings().data_dir, name, existing["nonce"])
                if cleaned["status"] not in {"released", "missing"}:
                    raise AppError(
                        "automation_endpoint_unknown",
                        "旧原生端点归属未确认，不能覆盖。",
                        409,
                        "outcome_unknown",
                    )
            for name, saved in existing.get("sockets", {}).items():
                path = storage_path(f"{name}/companion.sock")
                if path.exists() and not path.is_symlink() and path.stat().st_ino == saved:
                    path.unlink()
        nonce = str(uuid4())
        log = storage_path("runtime/automation.log")
        log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        env = dict(os.environ)
        env["WORKBENCH_DATA_DIR"] = str(get_settings().data_dir.absolute())
        env["WORKBENCH_DATABASE_URL"] = get_settings().database_dsn()
        env["LANGSMITH_TRACING"] = "false"
        with log.open("ab") as out:
            os.chmod(log, 0o600)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "workbench.entrypoints.automation_companion",
                    "--instance-id",
                    nonce,
                ],
                cwd=PROJECT_ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=out,
                start_new_session=True,
            )
        try:
            record = capture(process.pid, nonce)
        except Exception:
            process.terminate()
            process.wait(timeout=10)
            raise
        publish_bytes(storage_path("runtime/automation.json"), json.dumps(record).encode())
        for _ in range(100):
            if process.poll() is not None:
                raise AppError(
                    "automation_start_failed", "原生执行器启动失败，请查看本机诊断。", 503
                )
            endpoints = [
                inspect_endpoint(get_settings().data_dir, name) for name in ("browser", "desktop")
            ]
            if all(item["status"] == "bound" for item in endpoints):
                record["endpoints"] = ["browser", "desktop"]
                publish_bytes(storage_path("runtime/automation.json"), json.dumps(record).encode())
                return status()
            time.sleep(0.1)
        result = terminate_owned(record, timeout=10)
        raise AppError(
            "automation_start_timeout",
            "原生执行器未及时就绪，进程已退出。"
            if result.status == "confirmed"
            else "启动结果与关闭状态待核对。",
            503,
        )


def stop():
    with advisory_connection("automation-native-lifecycle"):
        record = state()
        if record is None:
            return status()
        if matches(record) and record.get("endpoints"):

            async def request_shutdown():
                from pydantic import BaseModel

                from workbench.modules.browser.client import BrowserClient

                class Receipt(BaseModel):
                    stopping: bool

                return await BrowserClient(get_settings().data_dir, timeout_seconds=5)._request(
                    "POST", "/shutdown", {}, Receipt
                )

            try:
                asyncio.run(request_shutdown())
            except Exception:
                pass  # A lost receipt is followed by actual process identity checks.
            for _ in range(200):
                if not matches(record):
                    break
                time.sleep(0.1)
            if os.name == "nt" and matches(record):
                raise AppError(
                    "automation_stop_pending",
                    "原生执行器仍在关闭资源，请稍后只读核对。",
                    409,
                    "outcome_unknown",
                )
        result = terminate_owned(record, timeout=20)
        if result.status != "confirmed":
            raise AppError(
                "automation_stop_unconfirmed",
                "原生进程退出未确认，保留记录供核对。",
                409,
                "outcome_unknown",
            )
        storage_path("runtime/automation.json").unlink(missing_ok=True)
        return RuntimeView(
            status="stopped", reason="原生进程已确认退出；历史动作效果仍以实际结果为准。"
        )
