"""Private, detached Linux command supervision. Importing performs no I/O."""

from __future__ import annotations

import codecs
import ctypes
import json
import math
import os
import signal
import stat
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from workbench.infrastructure import native_process
from workbench.infrastructure.vault.private import private_directory, read_private, write_json

TERMINAL = {"completed", "failed", "cancelled", "timed_out", "output_limit", "outcome_unknown"}
MAX_OUTPUT = 64 * 1024 * 1024
MAX_STATE = 1024 * 1024


class RunnerError(RuntimeError):
    """A private plan/receipt could not be safely used."""


@dataclass(frozen=True)
class LaunchResult:
    status: str
    supervisor_identity: dict | None
    state: dict | None
    reason: str


@dataclass(frozen=True)
class StopResult:
    status: str
    owned_resources_closed: bool
    state: dict | None
    reason: str


class NativeDriver(Protocol):
    """Replace only with a driver that can prove ownership through forks and exec."""

    def prepare(self) -> None: ...
    def discover(self) -> None: ...
    def signal_owned(self, signum: int) -> None: ...
    def closed(self) -> bool: ...


def capability() -> dict:
    supported = False
    reason = (
        "Detached commands require Linux pidfd/subreaper; Windows Job Objects and "
        "reliable macOS descendant ownership are not implemented."
    )
    if (
        sys.platform == "linux"
        and hasattr(os, "pidfd_open")
        and hasattr(signal, "pidfd_send_signal")
    ):
        descriptor = None
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            current = ctypes.c_int()
            if libc.prctl(37, ctypes.byref(current), 0, 0, 0) != 0:
                raise OSError("PR_GET_CHILD_SUBREAPER unavailable")
            descriptor = os.pidfd_open(os.getpid())
            signal.pidfd_send_signal(descriptor, 0)
            supported = True
            reason = (
                "Linux pidfd and subreaper query available; runner checks setup before dispatch."
            )
        except (OSError, AttributeError):
            reason = "Linux kernel/policy does not provide usable pidfd and subreaper primitives."
        finally:
            if descriptor is not None:
                os.close(descriptor)
    return {
        "status": "available" if supported else "unsupported",
        "supported": supported,
        "platform": sys.platform,
        "driver": "linux-pidfd-subreaper" if supported else None,
        "reason": reason,
        "os_sandbox": False,
    }


def _directory(path: str | Path) -> Path:
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise RunnerError("A private absolute command directory is required.")
    # Reject symlink ancestors, including ones outside the private leaf.
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise RunnerError("Command paths cannot traverse symbolic links.")
    private_directory(path)
    return path


def _json(path: Path, maximum: int = MAX_STATE) -> dict:
    from workbench.infrastructure.vault.errors import VaultError

    # The shared exclusive writer links its fsynced temporary file, then removes
    # that link. A reader may briefly observe nlink=2; never accept it as private,
    # but retry this precise transition rather than reject a valid new receipt.
    deadline = time.monotonic() + 0.1
    while True:
        try:
            raw = read_private(path, maximum)
            break
        except VaultError as error:
            info = path.lstat()
            transient = (
                error.code == "INVALID_PRIVATE_FILE"
                and stat.S_ISREG(info.st_mode)
                and info.st_nlink == 2
                and info.st_uid == os.geteuid()
                and not stat.S_IMODE(info.st_mode) & 0o077
            )
            if not transient or time.monotonic() >= deadline:
                raise RunnerError(
                    "Private command file failed ownership/type/size checks."
                ) from error
            time.sleep(0.005)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RunnerError("Private JSON must contain an object.")
    return value


def _uuid(value) -> str:
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise RunnerError("Canonical UUID identifiers are required.")
    return value


def read_plan(plan_path: str | Path) -> dict:
    path = Path(plan_path)
    if not path.is_absolute():
        raise RunnerError("plan_path must be absolute.")
    _directory(path.parent)
    plan = _json(path, 256 * 1024)
    fields = {
        "schema",
        "command_id",
        "nonce",
        "argv",
        "cwd",
        "timeout_seconds",
        "max_output_bytes",
        "output_dir",
        "sandbox_profile",
        "protected_roots",
    }
    if set(plan) - fields or plan.get("schema") != 1:
        raise RunnerError("Unsupported command plan schema or fields.")
    _uuid(plan.get("command_id"))
    _uuid(plan.get("nonce"))
    arguments = plan.get("argv")
    if (
        not isinstance(arguments, list)
        or not 1 <= len(arguments) <= 256
        or any(not isinstance(a, str) or "\0" in a or len(a) > 32768 for a in arguments)
        or not arguments[0]
    ):
        raise RunnerError("argv must contain bounded non-NUL strings.")
    cwd = plan.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
        raise RunnerError("cwd must be an existing absolute directory.")
    seconds = plan.get("timeout_seconds")
    budget = plan.setdefault("max_output_bytes", 32 * 1024 * 1024)
    if type(seconds) is not int or not 1 <= seconds <= 3600:
        raise RunnerError("timeout_seconds must be 1..3600.")
    if type(budget) is not int or not 1 <= budget <= MAX_OUTPUT:
        raise RunnerError("The combined log budget must be 1..64 MiB.")
    output = _directory(plan.get("output_dir", ""))
    if plan.setdefault("sandbox_profile", "native_scoped") not in {"native_scoped", "linux_bwrap"}:
        raise RunnerError("Unknown command sandbox profile.")
    protected = plan.setdefault("protected_roots", [])
    if (
        not isinstance(protected, list)
        or len(protected) > 8
        or any(not isinstance(item, str) or not Path(item).is_absolute() for item in protected)
    ):
        raise RunnerError("Protected roots must be host-created absolute paths.")
    if output.name != plan["command_id"]:
        raise RunnerError("output_dir must be the private command UUID directory.")
    return plan


def clean_environment() -> dict[str, str]:
    # No inherited HOME, credentials, Python path, proxy, or shell configuration.
    return {"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


def _identity_state(record: dict) -> str:
    """Read-only strict supervisor probe; false from matches is never a receipt."""
    import psutil

    try:
        if (
            record.get("version") != 1
            or record.get("platform") != sys.platform
            or type(record.get("pid")) is not int
            or record["pid"] <= 1
            or record.get("nonce_flag") != "--instance-id"
        ):
            return "unknown"
        process = psutil.Process(record["pid"])
        if (
            process.create_time() != record["create_time"]
            or process.uids().effective != os.geteuid()
        ):
            return "unknown"
        if process.status() in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}:
            return "gone"
        return "match" if native_process.matches(record) else "unknown"
    except psutil.NoSuchProcess:
        return "gone"
    except (psutil.Error, KeyError, TypeError, ValueError, OSError):
        return "unknown"


def read_state(output_dir: str | Path) -> dict:
    directory = _directory(output_dir)
    state = _json(directory / "state.json")
    if (
        state.get("schema") != 1
        or state.get("command_id") != directory.name
        or _uuid(state.get("nonce")) != state.get("supervisor_identity", {}).get("nonce")
        or state.get("status") not in TERMINAL | {"starting", "running", "stopping"}
        or type(state.get("owned_resources_closed")) is not bool
    ):
        raise RunnerError("Incomplete or mismatched command state; outcome is unknown.")
    for field in ("stdout_bytes", "stderr_bytes"):
        if type(state.get(field)) is not int or state[field] < 0:
            raise RunnerError("Invalid native byte counts.")
    if state["owned_resources_closed"] and state["status"] not in TERMINAL:
        raise RunnerError("Nonterminal command cannot claim closed resources.")
    identity = state["supervisor_identity"]
    if (
        not isinstance(identity, dict)
        or identity.get("version") != 1
        or identity.get("platform") != "linux"
        or type(identity.get("pid")) is not int
        or identity["pid"] <= 1
        or type(identity.get("create_time")) not in {int, float}
        or not math.isfinite(identity["create_time"])
        or identity["create_time"] <= 0
        or not isinstance(identity.get("executable"), str)
        or not os.path.isabs(identity["executable"])
        or identity.get("nonce_flag") != "--instance-id"
        or type(state.get("max_output_bytes")) is not int
        or not 1 <= state["max_output_bytes"] <= MAX_OUTPUT
        or state["stdout_bytes"] + state["stderr_bytes"] > state["max_output_bytes"]
        or state.get("exit_code") is not None
        and type(state["exit_code"]) is not int
        or not isinstance(state.get("started_at"), str)
        or state["status"] in TERMINAL
        and not isinstance(state.get("finished_at"), str)
    ):
        raise RunnerError("Invalid or incomplete native identity/terminal receipt.")
    observation = _identity_state(identity)
    state["supervisor_observation"] = observation
    state["effective_status"] = (
        "outcome_unknown"
        if observation != "match" and not state["owned_resources_closed"]
        else state["status"]
    )
    return state


def launch(plan_path: str | Path, *, acknowledgement_timeout: float = 10) -> LaunchResult:
    if not capability()["supported"]:
        raise RunnerError(capability()["reason"])
    plan_path = Path(plan_path)
    plan = read_plan(plan_path)
    directory = Path(plan["output_dir"])
    intent = directory / "launch.json"
    if intent.exists():
        previous = _json(intent)
        if previous.get("plan") != plan:
            raise RunnerError("An existing launch intent binds this directory to another plan.")
        try:
            state = read_state(directory)
            acknowledged = (
                state["supervisor_observation"] == "match" and bool(state.get("child_identity"))
            ) or (state["owned_resources_closed"] and state["status"] in TERMINAL)
            return LaunchResult(
                "acknowledged" if acknowledged else "outcome_unknown",
                state["supervisor_identity"],
                state,
                "Existing intent; command was not replayed.",
            )
        except (OSError, ValueError, RunnerError):
            return LaunchResult(
                "outcome_unknown", None, None, "Existing intent lacks a valid receipt."
            )
    write_json(intent, {"schema": 1, "plan": plan})
    # Resolve this package without inheriting an untrusted PYTHONPATH.
    source_root = str(Path(__file__).resolve().parents[3])
    bootstrap = (
        "import sys;sys.path.insert(0,sys.argv.pop(1));"
        "from workbench.entrypoints.command_runner import main;main()"
    )
    try:
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                bootstrap,
                source_root,
                "--instance-id",
                plan["nonce"],
                "--plan",
                str(plan_path),
            ],
            env=clean_environment(),
            cwd=plan["cwd"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as error:
        return LaunchResult(
            "outcome_unknown",
            None,
            None,
            f"Supervisor start failed: {type(error).__name__}; launch intent retained, no replay.",
        )
    identity = None
    deadline = time.monotonic() + max(0.1, min(acknowledgement_timeout, 30))
    while time.monotonic() < deadline:
        if identity is None:
            try:
                identity = native_process.capture(child.pid, plan["nonce"])
                write_json(directory / "supervisor.json", identity)
            except native_process.NativeProcessError:
                pass
        try:
            state = read_state(directory)
            if state["nonce"] != plan["nonce"]:
                raise RunnerError("Supervisor receipt nonce mismatch.")
            if state.get("child_identity") or state["status"] in TERMINAL:
                return LaunchResult(
                    "acknowledged",
                    state["supervisor_identity"],
                    state,
                    "Private native supervisor receipt recorded.",
                )
        except FileNotFoundError:
            pass
        if child.poll() is not None:
            break
        time.sleep(0.02)
    return LaunchResult(
        "outcome_unknown",
        identity,
        None,
        "No complete acknowledgement; do not replay this command.",
    )


def stop(
    output_dir: str | Path, *, expected_identity: dict | None = None, timeout: float = 15
) -> StopResult:
    directory = _directory(output_dir)
    try:
        state = read_state(directory)
    except (OSError, ValueError, RunnerError) as error:
        return StopResult(
            "unknown", False, None, f"Native receipt unavailable: {type(error).__name__}"
        )
    identity = state["supervisor_identity"]
    if expected_identity is not None and identity != expected_identity:
        return StopResult(
            "unknown", False, state, "Supervisor identity does not match caller receipt."
        )
    marker = directory / "stop.json"
    if not marker.exists():
        write_json(
            marker, {"schema": 1, "command_id": state["command_id"], "nonce": state["nonce"]}
        )
    # The runner polls the marker. SIGTERM also reaches its installed handler;
    # never force-kill the supervisor before it has cleaned its own descendants.
    if _identity_state(identity) == "match":
        native_process.terminate_owned(identity, timeout=0, force=False)
    deadline = time.monotonic() + max(0, min(timeout, 60))
    while True:
        try:
            state = read_state(directory)
        except (OSError, ValueError, RunnerError) as error:
            return StopResult(
                "unknown",
                False,
                state,
                f"Closure receipt became unavailable: {type(error).__name__}; keep the record.",
            )
        if state["owned_resources_closed"] and state["supervisor_observation"] == "gone":
            return StopResult(
                "confirmed", True, state, "Native closed receipt and supervisor exit confirmed."
            )
        if time.monotonic() >= deadline:
            return StopResult(
                "unknown", False, state, "Owned resource closure is not confirmed; keep the record."
            )
        time.sleep(0.05)


def read_log(
    output_dir: str | Path, stream: str = "stdout", offset: int = 0, limit: int = 65536
) -> dict:
    directory = _directory(output_dir)
    state = read_state(directory)
    terminal = state["status"] in TERMINAL and state["owned_resources_closed"]
    return read_log_file(directory / (stream + ".log"), stream, offset, limit, terminal=terminal)


def read_log_file(
    path: Path,
    stream: str = "stdout",
    offset: int = 0,
    limit: int = 65536,
    *,
    terminal: bool = True,
) -> dict:
    """Use the same UTF-8 cursor rules for live logs and immutable backup artifacts."""
    if stream not in {"stdout", "stderr"} or type(offset) is not int or offset < 0:
        raise RunnerError("A fixed log stream and nonnegative byte offset are required.")
    if type(limit) is not int or not 1 <= limit <= 65536:
        raise RunnerError("Log pages are limited to 65536 bytes.")
    from workbench.infrastructure.vault.private import check_private

    check_private(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_OUTPUT:
            raise RunnerError("Invalid private log file.")
        if offset > info.st_size:
            raise RunnerError("Byte offset is beyond the current log.")
        raw = os.pread(descriptor, limit, offset)
    finally:
        os.close(descriptor)
    start = offset
    # A caller may resume inside a multibyte character; report skipped bytes.
    while raw and raw[0] & 0xC0 == 0x80:
        start += 1
        raw = raw[1:]
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    text = decoder.decode(raw, final=terminal and offset + limit >= info.st_size)
    pending = len(decoder.getstate()[0])
    end = start + len(raw) - pending
    try:
        raw[: len(raw) - pending].decode("utf-8", "strict")
        replacements = False
    except UnicodeDecodeError:
        replacements = True
    return {
        "stream": stream,
        "text": text,
        "requested_offset": offset,
        "start_offset": start,
        "next_offset": end,
        "total_bytes": info.st_size,
        "eof": end >= info.st_size,
        "utf8_boundary_adjusted": start != offset or pending != 0,
        "boundary_pending": pending != 0,
        "decode_replacements": replacements,
    }


def enable_subreaper() -> None:
    if not capability()["supported"]:
        raise RunnerError(capability()["reason"])
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        raise RunnerError(f"Linux subreaper setup failed (errno {ctypes.get_errno()}).")
    # Actual kernel support, not merely presence of the Python wrappers.
    descriptor = os.pidfd_open(os.getpid())
    try:
        signal.pidfd_send_signal(descriptor, 0)
    finally:
        os.close(descriptor)
