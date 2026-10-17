#!/usr/bin/env python3
"""Isolated, loopback-only S0 engine runner. No model downloads or fallback."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import platform
import shlex
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from local_http import open_local
from supervision import LinuxSubreaper, boot_id, process_start, signal_identity

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads((ROOT / "runtime/model/deployment.json").read_text())
DATA = ROOT / ".local/model"
STATE = DATA / "state.json"
PRECOMPILE_STATE = DATA / "precompile-state.json"
GIB = 1024**3


def now():
    return dt.datetime.now(dt.UTC).isoformat()


def write_json(path, data):
    write_private(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())


def sync_directory(path):
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def write_private(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError("Refusing to replace a symlink JSON record")
    fd, temporary = tempfile.mkstemp(prefix=".modelctl-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def management_lock(directory=None):
    """Short modelctl decisions only; never unlink this stable flock inode."""
    directory = Path(directory) if directory is not None else DATA / "control"
    if not directory.is_absolute() or ".." in directory.parts:
        raise RuntimeError("Management lock requires an absolute private directory")
    parent = os.open(directory.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        for index, part in enumerate(directory.parts[1:]):
            if index == len(directory.parts) - 2:
                try:
                    os.mkdir(part, 0o700, dir_fd=parent)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        info = os.fstat(parent)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeError("Management lock directory must be private to its OS owner")
        fd = os.open(
            "management.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise RuntimeError("Management lock file must be an owned private regular file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        current = os.stat("management.lock", dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise RuntimeError("Management lock identity changed; no management action performed")
        yield
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def identity_status(pid, started):
    if pid is None and started is None:
        return "absent"
    if type(pid) is not int or pid <= 1 or not isinstance(started, str) or not started.isdecimal():
        return "unknown"
    try:
        if process_start(os.getpid()) is None:
            return "unknown"
        actual = process_start(pid)
    except (OSError, ValueError, IndexError):
        return "unknown"
    return "gone" if actual is None else "match" if actual == started else "unknown"


def management_record(path):
    if path.is_symlink():
        raise RuntimeError("Refusing a symlink management state")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:
        raise RuntimeError("Management state cannot be opened safely") from None
    try:
        with os.fdopen(descriptor, "r") as handle:
            info = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or info.st_size > 1024**2
            ):
                raise ValueError
            state = json.loads(handle.read(1024**2 + 1))
        if not isinstance(state, dict):
            raise ValueError
    except (OSError, ValueError, TypeError):
        raise RuntimeError(
            "Management state is unreadable; preserve it for reconciliation"
        ) from None
    return state


def record_boot(state, field="boot_id"):
    value = state.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Invalid boot identity in management state")
    return str(UUID(value))


def closed_receipt(state):
    """Legacy exit codes, not_started and snapshot booleans are not tree proofs."""
    if (
        state.get("resources_closed") is not True
        or not isinstance(state.get("status"), str)
        or state.get("status") not in {"stopped", "complete", "failed"}
        or not isinstance(state.get("ended_at"), str)
        or not state["ended_at"]
        or record_boot(state) is None
    ):
        return False
    if state.get("closure_method") == "linux_subreaper_echild":
        return True
    if state.get("closure_method") == "host_reboot":
        evidence = state.get("reconciliation")
        if isinstance(evidence, dict):
            prior = record_boot(evidence, "prior_boot_id")
            observed = record_boot(evidence, "observed_boot_id")
            return prior is not None and observed == record_boot(state) and prior != observed
    return False


def management_state(path):
    state = management_record(path)
    if state is None:
        return "idle", {"status": "stopped"}
    try:
        if closed_receipt(state):
            # Closure is the guard's final state publication. Old PID reuse must
            # not invalidate that kernel proof after the guard has exited.
            return "idle", state
        recorded_boot = record_boot(state)
        if recorded_boot is not None and recorded_boot != boot_id():
            return "unknown", state
    except ValueError:
        return "unknown", state
    status = state.get("status")
    if not isinstance(status, str) or status not in {
        "starting",
        "loading",
        "ready",
        "stopping",
        "failed",
        "stopped",
        "complete",
    }:
        return "unknown", state
    runner = identity_status(state.get("runner_pid"), state.get("runner_start"))
    if runner == "match":
        # A child can exit before its /proc identity is read; its live owning
        # guard still has authority to reap and publish the final receipt.
        return "active", state
    engine = identity_status(state.get("pid"), state.get("process_start"))
    if "unknown" in (engine, runner):
        return "unknown", state
    if engine == "match":
        return "active", state
    return "unknown", state


def merge_control(state_path, state):
    """Caller holds management_lock; never overwrite another or unknown intent."""
    phase, current = management_state(state_path)
    if phase == "unknown":
        raise RuntimeError("Unknown launch state; refusing to overwrite its receipt")
    owner = (state.get("runner_pid"), state.get("runner_start"))
    if not all(owner) or owner != (current.get("runner_pid"), current.get("runner_start")):
        raise RuntimeError("Launch intent changed; refusing to overwrite management state")
    if current.get("stop_requested"):
        state["stop_requested"] = current["stop_requested"]


def publish_state(state_path, state):
    with management_lock():
        merge_control(state_path, state)
        write_json(state_path, state)


def request_stop():
    with management_lock():
        phase, state = management_state(STATE)
        if phase == "unknown":
            raise RuntimeError("Unknown engine/launch identity; state preserved and no signal sent")
        if phase == "idle":
            return {
                "stop_requested": False,
                "stopped_owned_engine": False,
                "reason": "No active model launch; no state was changed",
            }
        if record_boot(state) != boot_id():
            raise RuntimeError(
                "Legacy state lacks a current-boot identity; stop through its original "
                "supervisor before preparing offline reconciliation; no signal sent"
            )
        state["stop_requested"] = now()
        write_json(STATE, state)
        runner = state.get("runner_pid")
        if identity_status(runner, state.get("runner_start")) == "match":
            signal_identity(runner, state["runner_start"], signal.SIGTERM)
            return {
                "stop_requested": True,
                "stopped_owned_engine": False,
                "closure": "pending guarded shutdown; inspect status",
            }
    # A replacement controller cannot inherit the dead guard's child ownership.
    # Request termination of the exact root only and retain the unresolved fence.
    if identity_status(state.get("pid"), state.get("process_start")) == "match":
        signal_identity(state["pid"], state["process_start"], signal.SIGTERM)
    return {
        "stop_requested": True,
        "stopped_owned_engine": False,
        "closure": "unknown without runner completion receipt; state preserved",
    }


def reconcile(*, prepare=False, apply=False):
    """Resolve local legacy receipts only with a recorded, observed host reboot."""
    if prepare and apply:
        raise RuntimeError("Reconciliation preparation and application are separate actions")
    with management_lock():
        current_boot = boot_id()
        pending = []
        for path in (STATE, PRECOMPILE_STATE):
            state = management_record(path)
            if state is None:
                continue
            try:
                if closed_receipt(state):
                    continue
                prior_boot = record_boot(state) or record_boot(state, "reconcile_after_boot_id")
            except ValueError:
                raise RuntimeError(
                    "Invalid boot receipt; preserve the record for manual investigation"
                ) from None
            pending.append((path, state, prior_boot))

        if prepare:
            # This merely records an observation. It does not stop processes or
            # certify closure, even if all recorded PIDs are already gone.
            for _, state, prior_boot in pending:
                if prior_boot is not None and prior_boot != current_boot:
                    continue
                identities = (
                    identity_status(state.get("runner_pid"), state.get("runner_start")),
                    identity_status(state.get("pid"), state.get("process_start")),
                )
                if "match" in identities:
                    raise RuntimeError(
                        "A recorded owner is still active; preparation did not change any state"
                    )
            prepared = []
            for path, state, prior_boot in pending:
                if prior_boot is None:
                    state.update(reconcile_after_boot_id=current_boot, reconcile_prepared_at=now())
                    write_json(path, state)
                    prepared.append(path.name)
            return {
                "prepared": prepared,
                "resources_closed": False if pending else None,
                "next_action": (
                    "preview reconciliation; a same-boot record requires a planned host reboot"
                ),
            }

        ready = all(prior is not None and prior != current_boot for _, _, prior in pending)
        result = {
            "ready": ready,
            "records": [
                {
                    "record": path.name,
                    "requirement": (
                        "prepare before a planned host reboot"
                        if prior is None
                        else "planned host reboot"
                        if prior == current_boot
                        else "host reboot observed; closure can be reconciled"
                    ),
                }
                for path, _, prior in pending
            ],
            "applied": False,
        }
        if not apply or not pending:
            return result
        if not ready:
            raise RuntimeError(
                "Closure is unproven: prepare unanchored legacy records, then complete a planned "
                "host reboot before reconcile --apply; no state changed"
            )
        archive = DATA / "control/reconciled" / uuid4().hex
        archive.parent.mkdir(mode=0o700, exist_ok=True)
        # Persist the entire archive path before a canonical receipt can refer
        # to it, including a newly created control directory. Also reject a
        # symlink archive parent before creating anything inside it.
        sync_directory(archive.parent)
        sync_directory(archive.parent.parent)
        sync_directory(DATA)
        archive.mkdir(mode=0o700)
        sync_directory(archive.parent)
        for path, state, prior_boot in pending:
            original = archive / path.name
            write_json(original, state)
            write_json(
                path,
                {
                    "status": "stopped",
                    "dispatch_phase": "reconciled",
                    "ended_at": now(),
                    "resources_closed": True,
                    "closure_method": "host_reboot",
                    "boot_id": current_boot,
                    "reason": (
                        "host reboot proves resource closure; original outcome preserved in archive"
                    ),
                    "reconciliation": {
                        "prior_boot_id": prior_boot,
                        "observed_boot_id": current_boot,
                        "original_record": str(original.relative_to(DATA)),
                    },
                },
            )
        result.update(applied=True, archive=str(archive))
        return result


def memory():
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        values[key] = int(value.split()[0]) * 1024
    return {key: values[key] for key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")}


def owned_alive(state):
    pid = state.get("pid")
    return bool(
        pid and process_start(pid) == state.get("process_start") and state.get("process_start")
    )


def state_read(path=STATE):
    return json.loads(path.read_text()) if path.exists() else {"status": "stopped"}


def group_resources(pgid):
    processes = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(")", 1)[1].split()
            if fields[0] != "Z":
                processes[int(path.parent.name)] = (int(fields[1]), int(fields[2]), int(fields[21]))
        except (OSError, ValueError, IndexError):
            continue
    members = {pid for pid, (_, group, _) in processes.items() if group == pgid}
    descendants = set(members)
    while True:
        extended = descendants | {
            pid for pid, (parent, _, _) in processes.items() if parent in descendants
        }
        if extended == descendants:
            break
        descendants = extended
    rss = sum(processes[pid][2] for pid in descendants) * os.sysconf("SC_PAGE_SIZE")
    # RSS sums include shared pages and omit some CUDA allocations; UMA pressure
    # is governed by /proc/meminfo, not this diagnostic sum.
    return {
        "group_pids": sorted(members),
        "descendant_pids": sorted(descendants),
        "process_rss_sum_bytes": rss,
    }


def command():
    c = CONFIG
    return [
        str(ROOT / c["python"]),
        "-m",
        "sglang.launch_server",
        "--model-path",
        str(ROOT / c["model_path"]),
        "--served-model-name",
        c["served_model_name"],
        "--host",
        c["host"],
        "--port",
        str(c["port"]),
        "--context-length",
        str(c["context_length"]),
        "--max-running-requests",
        str(c["max_running_requests"]),
        "--max-total-tokens",
        str(c["max_total_tokens"]),
        "--model-impl",
        "sglang",
        "--chunked-prefill-size",
        str(c["chunked_prefill_size"]),
        "--mem-fraction-static",
        str(c["mem_fraction_static"]),
        "--ple-offload-embedding",
        "--ple-offload-backend",
        "file",
        "--ple-offload-dir",
        str(ROOT / c["ple_directory"]),
        "--moe-runner-backend",
        c["moe_runner_backend"],
        "--reasoning-parser",
        "qwen3",
        "--tool-call-parser",
        "qwen3_coder",
    ]


def active_ple_directory():
    state = state_read()
    if not owned_alive(state):
        return None
    args = state.get("command", [])
    return (
        Path(args[args.index("--ple-offload-dir") + 1]).resolve()
        if "--ple-offload-dir" in args
        else None
    )


def ple_inventory():
    base = (ROOT / CONFIG["ple_generation_root"]).resolve()
    if not base.is_relative_to((DATA / "ple").resolve()):
        raise RuntimeError("PLE generations must stay under this runner's private model cache")
    items = []
    for directory in sorted(base.glob("generation-*")):
        marker = directory / ".generation.json"
        if directory.is_symlink() or not directory.is_dir() or not marker.is_file():
            continue
        record = json.loads(marker.read_text())
        if (
            record.get("owner") != "ai-workbench-modelctl"
            or record.get("model_revision") != CONFIG["model_revision"]
        ):
            continue
        files = list(directory.iterdir())
        safe = all(
            p.is_file()
            and not p.is_symlink()
            and (
                p.name == ".generation.json"
                or p.name.startswith("ple_table_")
                and p.suffix == ".bin"
            )
            for p in files
        )
        items.append(
            {
                "path": str(directory),
                "kind": "generation",
                "safe_owned_contents": safe,
                "allocated_bytes": sum(p.stat().st_blocks * 512 for p in files),
            }
        )
    return items


def fresh_ple_generation():
    items = ple_inventory()
    if len(items) >= CONFIG["ple_generation_limit"]:
        raise RuntimeError(
            "PLE generation limit reached; inspect cleanup-ple --keep 1, "
            "then explicitly use --apply"
        )
    directory = (
        ROOT
        / CONFIG["ple_generation_root"]
        / ("generation-" + dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ"))
    )
    directory.mkdir(parents=True, exist_ok=False)
    write_json(
        directory / ".generation.json",
        {
            "owner": "ai-workbench-modelctl",
            "model_revision": CONFIG["model_revision"],
            "created_at": now(),
            "reason": "explicit fresh sparse backing generation",
        },
    )
    CONFIG["ple_directory"] = str(directory.relative_to(ROOT))
    write_json(ROOT / "runtime/model/deployment.json", CONFIG)


def cleanup_ple(keep, include_legacy, apply):
    with management_lock():
        CONFIG.update(json.loads((ROOT / "runtime/model/deployment.json").read_text()))
        for path in (STATE, PRECOMPILE_STATE):
            phase, state = management_state(path)
            if phase == "unknown" or phase == "active" and not owned_alive(state):
                raise RuntimeError("Unknown or pending launch intent; PLE cleanup is blocked")
        return _cleanup_ple(keep, include_legacy, apply)


def _cleanup_ple(keep, include_legacy, apply):
    if keep < 0:
        raise ValueError("keep must be nonnegative")
    items = ple_inventory()
    candidates = items[: max(0, len(items) - keep)]
    if include_legacy:
        base = ROOT / CONFIG["ple_generation_root"]
        candidates += [
            {
                "path": str(p.resolve()),
                "kind": "legacy_file",
                "safe_owned_contents": not p.is_symlink(),
            }
            for p in base.glob("ple_table_*.bin")
            if p.is_file()
        ]
    deleted, preserved, eligible = [], [], []
    for item in candidates:
        path = Path(item["path"])
        active = active_ple_directory()
        directory = path if item["kind"] == "generation" else path.parent
        if directory == active or not item["safe_owned_contents"]:
            preserved.append(str(path))
            continue
        eligible.append(str(path))
        if apply:
            shutil.rmtree(path) if item["kind"] == "generation" else path.unlink()
            deleted.append(str(path))
    return {
        "apply": apply,
        "keep_newest_generations": keep,
        "inventory": items,
        "eligible_paths": eligible,
        "preserved_paths": preserved,
        "deleted_paths": deleted,
    }


def probe():
    """Read tensor headers only, preserving the existing complete hash evidence."""
    model = ROOT / CONFIG["model_path"]
    tensors, ple, mtp = 0, 0, 0
    dtypes = {}
    for path in sorted(model.glob("*.safetensors")):
        with path.open("rb") as handle:
            size = struct.unpack("<Q", handle.read(8))[0]
            if size > 64 * 1024**2:
                raise ValueError(f"Unreasonable safetensors header: {path.name}")
            header = json.loads(handle.read(size))
        for name, tensor in header.items():
            if name == "__metadata__":
                continue
            start, end = tensor["data_offsets"]
            count = end - start
            tensors += count
            dtypes[tensor["dtype"]] = dtypes.get(tensor["dtype"], 0) + count
            ple += count if "ngram" in name else 0
            mtp += count if "mtp" in name else 0
    result = {
        "checked_at": now(),
        "architecture": platform.machine(),
        "memory": memory(),
        "free_disk_bytes": shutil.disk_usage(DATA).free,
        "model_revision": CONFIG["model_revision"],
        "tensor_bytes": tensors,
        "ple_bytes": ple,
        "mtp_bytes": mtp,
        "bytes_by_dtype": dtypes,
        "verification": "headers_only; prior full asset verification reused",
    }
    result["gpu_driver"] = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"], text=True
    ).strip()
    result["cuda_toolkit"] = json.loads(Path("/usr/local/cuda/version.json").read_text())["cuda"][
        "version"
    ]
    write_json(DATA / "evidence/preflight.json", result)
    return result


def health():
    try:
        with open_local(f"http://{CONFIG['host']}:{CONFIG['port']}/ready", timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return False


def serve(precompile=False, fresh_ple=False):
    state_path = PRECOMPILE_STATE if precompile else STATE
    control = {"interrupted": False}

    def interrupted_handler(*_):
        control["interrupted"] = True

    signal.signal(signal.SIGTERM, interrupted_handler)
    signal.signal(signal.SIGINT, interrupted_handler)
    supervisor = LinuxSubreaper()
    supervisor.prepare()
    state = {
        "status": "starting",
        "dispatch_phase": "reserved",
        "started_at": now(),
        "boot_id": boot_id(),
        "runner_pid": os.getpid(),
        "runner_start": process_start(os.getpid()),
    }
    with management_lock():
        CONFIG.update(json.loads((ROOT / "runtime/model/deployment.json").read_text()))
        for path in (STATE, PRECOMPILE_STATE):
            phase, _ = management_state(path)
            if phase != "idle":
                raise RuntimeError(
                    "Engine/precompile is active or unknown; existing state is preserved"
                )
        write_json(state_path, state)
    try:
        return _serve_reserved(precompile, fresh_ple, state_path, state, control, supervisor)
    except BaseException:
        # Environment checks can also leave children. No terminal receipt is
        # reusable until the same supervisor has closed that ownership boundary.
        if state.get("dispatch_phase") == "reserved":
            _finish_without_pid(
                supervisor,
                state_path,
                state,
                known_not_started=True,
                stopped=control["interrupted"],
                reason="preflight ended before Popen; command was not dispatched",
            )
        raise


def _serve_reserved(precompile, fresh_ple, state_path, state, control, supervisor):
    c = CONFIG
    if not precompile:
        evidence = DATA / "evidence/precompiled-kernels.json"
        record = json.loads(evidence.read_text()) if evidence.exists() else {}
        if record.get("status") != "passed" or not Path(record.get("shared_library", "")).is_file():
            raise RuntimeError(
                "Complete modelctl.py precompile before loading weights; "
                "first MoE JIT exceeded the UMA budget"
            )
    if memory()["MemAvailable"] < c["startup_minimum_available_gib"] * GIB:
        raise RuntimeError("Insufficient available UMA memory for the S0 startup budget")
    if shutil.disk_usage(DATA).free < c["startup_free_disk_gib"] * GIB:
        raise RuntimeError("Insufficient disk for environment/cache/PLE startup budget")
    if not precompile:
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((c["host"], c["port"]))
    # Validate imports/version and CUDA before a process can touch the checkpoint.
    subprocess.run(
        [str(ROOT / c["python"]), str(ROOT / "scripts/model/check_environment.py")], check=True
    )
    if fresh_ple:
        with management_lock():
            merge_control(state_path, state)
            if not state.get("stop_requested") and not control["interrupted"]:
                fresh_ple_generation()
    launch = (
        [str(ROOT / c["python"]), str(ROOT / "scripts/model/precompile.py"), "--worker"]
        if precompile
        else command()
    )
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    logdir = DATA / "evidence" / (stamp + ("-precompile" if precompile else ""))
    logdir.mkdir(parents=True)
    env = os.environ.copy()
    env["PATH"] = str((ROOT / c["python"]).parent) + ":/usr/local/cuda/bin:" + env.get("PATH", "")
    env.update(
        {
            "CUDA_HOME": "/usr/local/cuda",
            "MAX_JOBS": "2",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "SGLANG_CACHE_DIR": str(DATA / "cache/sglang"),
            "FLASHINFER_WORKSPACE_BASE": str(DATA / "cache/flashinfer"),
            "TRITON_CACHE_DIR": str(DATA / "cache/triton"),
            "SGLANG_QWEN4_PLE_FILE_RSS_BUDGET_GB": str(c["ple_rss_budget_gib"]),
            "SGLANG_QWEN4_PLE_FILE_RSS_INTERVAL_S": "2",
        }
    )
    state.update(
        deployment_id=c["deployment_id"],
        model_revision=c["model_revision"],
        log_directory=str(logdir),
        command=launch,
        workload="precompile" if precompile else "model_service",
    )
    with (
        (logdir / "server.log").open("wb") as logs,
        (logdir / "resources.jsonl").open("w") as resources,
    ):
        proc = None
        minimum = memory()["MemAvailable"]
        ready = False
        acknowledged = False
        dispatch_attempted = False
        popen_rejected = False
        try:
            with management_lock():
                merge_control(state_path, state)
                cancelled = bool(state.get("stop_requested") or control["interrupted"])
                if not cancelled:
                    state["dispatch_phase"] = "dispatching"
                    write_json(state_path, state)
                    dispatch_attempted = True
                    try:
                        proc = subprocess.Popen(
                            launch,
                            cwd=ROOT,
                            env=env,
                            stdout=logs,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                            close_fds=True,
                        )
                    except OSError:
                        popen_rejected = True
                        raise
                    state.update(pid=proc.pid, status="loading", dispatch_phase="spawned")
                    state["process_start"] = process_start(proc.pid)
                    write_json(state_path, state)
                    acknowledged = True
            if cancelled:
                _finish_without_pid(
                    supervisor,
                    state_path,
                    state,
                    known_not_started=True,
                    stopped=True,
                    reason="stop requested before Popen; command was not dispatched",
                )
                return 0 if state["resources_closed"] else 1
            write_json(logdir / "launch.json", {**state, "configuration": c})
            while proc.poll() is None:
                measured = memory()
                minimum = min(minimum, measured["MemAvailable"])
                state.update(
                    minimum_available_bytes=minimum,
                    observed_peak_system_used_bytes=measured["MemTotal"] - minimum,
                )
                resources.write(
                    json.dumps({"at": now(), **measured, **group_resources(proc.pid)}) + "\n"
                )
                resources.flush()
                if measured["MemAvailable"] < c["minimum_available_gib"] * GIB:
                    state.update(
                        status="failed", reason="MemAvailable below 8 GiB; no automatic retry"
                    )
                    break
                if control["interrupted"] or state.get("stop_requested"):
                    state.update(status="stopping", reason="runner received stop signal")
                    break
                if not precompile and not ready and health():
                    ready = True
                    state.update(status="ready", ready_at=now())
                    publish_state(state_path, state)
                    print(json.dumps(state), flush=True)
                publish_state(state_path, state)
                time.sleep(2)
        finally:
            if proc is None:
                if state.get("dispatch_phase") == "dispatching":
                    _finish_without_pid(
                        supervisor,
                        state_path,
                        state,
                        known_not_started=not dispatch_attempted or popen_rejected,
                        stopped=False,
                        reason="dispatch ended without a returned child PID",
                    )
            else:
                _finish_owned(
                    supervisor, proc, state_path, state, logdir, minimum, precompile, acknowledged
                )
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0 if state["status"] in ("stopped", "complete") else 1


def _finish_without_pid(supervisor, state_path, state, *, known_not_started, stopped, reason):
    resources_closed = supervisor.close()
    state.update(
        status=("stopped" if stopped else "failed")
        if known_not_started and resources_closed
        else "outcome_unknown",
        ended_at=now(),
        resources_closed=resources_closed,
        closure_method="linux_subreaper_echild" if resources_closed else None,
        reason=reason
        if resources_closed
        else "supervised children could not be closed; do not restart",
    )
    if known_not_started:
        state["dispatch_phase"] = "not_started"
    publish_state(state_path, state)


def _finish_owned(supervisor, proc, state_path, state, logdir, minimum, precompile, acknowledged):
    # Root exit (including exit code 0) cannot prove that compiler/GPU descendants
    # stopped. The subreaper continues adoption, signalling and waitpid to ECHILD.
    resources_closed = supervisor.close(proc)
    proc.poll()
    if state["status"] in ("loading", "ready"):
        if precompile and proc.returncode == 0:
            state.update(status="complete", reason="offline precompile completed; model not loaded")
        else:
            state.update(status="failed", reason="worker exited")
    elif state["status"] == "stopping":
        state["status"] = "stopped"
    state.update(
        exit_code=proc.returncode,
        ended_at=now(),
        minimum_available_bytes=minimum,
        observed_peak_system_used_bytes=memory()["MemTotal"] - minimum,
    )
    if not acknowledged:
        state.update(
            status="outcome_unknown", reason="child returned but PID receipt failed; do not restart"
        )
    elif not resources_closed:
        state.update(
            status="outcome_unknown",
            reason="parent exited without confirmed descendant cleanup; do not restart",
        )
    state["resources_closed"] = resources_closed
    state["closure_method"] = "linux_subreaper_echild" if resources_closed else None
    publish_state(state_path, state)
    write_json(logdir / "result.json", state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "probe",
            "plan",
            "precompile",
            "serve",
            "status",
            "stop",
            "cleanup-ple",
            "reconcile",
        ],
    )
    parser.add_argument(
        "--fresh-ple",
        action="store_true",
        help="Create a fresh sparse backing generation before model loading",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=1,
        help="Inactive cleanup retains this many newest managed generations",
    )
    parser.add_argument(
        "--include-legacy",
        action="store_true",
        help="Include the original direct-root backing in an explicit cleanup",
    )
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="Anchor unclosed legacy receipts to this boot before a separately planned host reboot",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply cleanup or proven post-reboot reconciliation; otherwise preview only",
    )
    args = parser.parse_args()
    if args.fresh_ple and args.action != "serve":
        parser.error("--fresh-ple is only valid with serve")
    if args.prepare and (args.action != "reconcile" or args.apply):
        parser.error("--prepare is only valid with reconcile and cannot be combined with --apply")
    DATA.mkdir(parents=True, exist_ok=True)
    if args.action == "probe":
        print(json.dumps(probe(), indent=2))
    elif args.action == "plan":
        print(shlex.join(command()))
    elif args.action == "serve":
        return serve(fresh_ple=args.fresh_ple)
    elif args.action == "precompile":
        return serve(precompile=True)
    elif args.action == "cleanup-ple":
        print(json.dumps(cleanup_ple(args.keep, args.include_legacy, args.apply), indent=2))
    elif args.action == "reconcile":
        print(json.dumps(reconcile(prepare=args.prepare, apply=args.apply), indent=2))
    elif args.action == "status":
        state = state_read()
        state.update(process_alive=owned_alive(state), endpoint_ready=health())
        print(json.dumps(state, indent=2))
    elif args.action == "stop":
        print(json.dumps(request_stop()))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
