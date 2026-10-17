"""Own-process identities using psutil's supported native implementations.

Only a caller's direct freshly spawned child can be captured. Persist the record
privately; later launchers can match/stop that identity after parent exit. No
process-name search, descendant signalling, process-group kill, or environment read.
"""

import math
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass


class NativeProcessError(RuntimeError):
    pass


def _psutil():
    if sys.platform not in {"linux", "win32", "darwin"}:
        raise NativeProcessError("Native process control is unsupported on this platform.")
    try:
        import psutil

        return psutil
    except ImportError:
        raise NativeProcessError(
            "psutil is unavailable; install the project's locked dependencies."
        ) from None


def _executable(value):
    if not isinstance(value, str) or not value or not os.path.isabs(value):
        raise NativeProcessError("Process executable identity is unavailable.")
    return os.path.normcase(os.path.normpath(value))


def _nonce(value, flag):
    if (
        not isinstance(value, str)
        or not 16 <= len(value) <= 256
        or any(ord(c) < 33 or ord(c) > 126 for c in value)
    ):
        raise NativeProcessError(
            "A fresh explicit process nonce of 16..256 ASCII characters is required."
        )
    if (
        not isinstance(flag, str)
        or not flag.startswith("--")
        or len(flag) > 80
        or not all(c.isalnum() or c in "-_" for c in flag)
    ):
        raise NativeProcessError("Invalid nonce argument flag.")


def _has_nonce(arguments, value, flag):
    found = []
    for index, item in enumerate(arguments):
        if item.startswith(flag + "="):
            found.append(item[len(flag) + 1 :])
        elif item == flag:
            found.append(arguments[index + 1] if index + 1 < len(arguments) else None)
    return found == [value]


def _record(record):
    if not isinstance(record, Mapping) or record.get("version") != 1:
        raise NativeProcessError("Unsupported or legacy process record; refusing to signal it.")
    pid = record.get("pid")
    created = record.get("create_time")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1 or pid == os.getpid():
        raise NativeProcessError("Invalid owned process PID.")
    if (
        isinstance(created, bool)
        or not isinstance(created, (float, int))
        or not math.isfinite(created)
        or created <= 0
    ):
        raise NativeProcessError("Invalid process creation time.")
    if record.get("platform") != sys.platform:
        raise NativeProcessError("Process record belongs to another platform.")
    _nonce(record.get("nonce"), record.get("nonce_flag"))
    _executable(record.get("executable"))
    return record


def _same_user(process, psutil):
    if os.name == "posix":
        return process.uids().effective == os.geteuid()
    return process.username().casefold() == psutil.Process().username().casefold()


def capture(pid: int, nonce: str, *, nonce_flag="--instance-id") -> dict:
    """Capture one newly spawned direct child, never discover a process by name.

    Raises NativeProcessError if native fields cannot be read or do not match.
    The executable is the actual native executable, not cmdline[0]/a launcher shim.
    """
    _nonce(nonce, nonce_flag)
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1 or pid == os.getpid():
        raise NativeProcessError("Capture requires a separate child PID.")
    psutil = _psutil()
    try:
        process = psutil.Process(pid)
        if process.ppid() != os.getpid() or not _same_user(process, psutil):
            raise NativeProcessError(
                "Capture only accepts this caller's direct child under the same user."
            )
        created = process.create_time()
        executable = _executable(process.exe())
        if not _has_nonce(process.cmdline(), nonce, nonce_flag) or not process.is_running():
            raise NativeProcessError("Child process does not contain the exact nonce argument.")
        result = {
            "version": 1,
            "platform": sys.platform,
            "pid": pid,
            "create_time": created,
            "executable": executable,
            "nonce": nonce,
            "nonce_flag": nonce_flag,
        }
        if _identity(result)[0] != "match":
            raise NativeProcessError("Child identity changed during capture.")
        return result
    except (psutil.Error, OSError, ValueError):
        raise NativeProcessError(
            "Native child identity could not be read; no signal was sent."
        ) from None


def _identity(record):
    """Return match/gone/unknown and the identity-checked psutil.Process object."""
    try:
        record = _record(record)
    except NativeProcessError:
        return "unknown", None
    try:
        psutil = _psutil()
    except NativeProcessError:
        return "unknown", None
    try:
        process = psutil.Process(record["pid"])
        if process.create_time() != record["create_time"] or not _same_user(process, psutil):
            return "unknown", None
        if process.status() in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}:
            return "gone", None
        if _executable(process.exe()) != _executable(record["executable"]):
            return "unknown", None
        if not _has_nonce(process.cmdline(), record["nonce"], record["nonce_flag"]):
            return "unknown", None
        if not process.is_running():
            return "gone", None
        # A fresh lookup guards the read sequence, while psutil guards reuse again
        # immediately inside terminate()/kill(). No cached process_iter objects.
        if psutil.Process(record["pid"]).create_time() != record["create_time"]:
            return "unknown", None
        return "match", process
    except psutil.NoSuchProcess:
        return "gone", None
    except (psutil.Error, OSError, ValueError, NativeProcessError):
        return "unknown", None


def matches(record: Mapping) -> bool:
    """Fail closed on changed, inaccessible, legacy, foreign or exited identities."""
    return _identity(record)[0] == "match"


@dataclass(frozen=True)
class TerminationResult:
    status: str  # confirmed | unknown
    reason: str
    signalled: bool
    forced: bool


def terminate_owned(
    record: Mapping, *, timeout: float = 15, force: bool = False, kill_timeout: float = 5
) -> TerminationResult:
    """Signal only the exact recorded process. Never signal children or a group.

    Windows terminate() uses TerminateProcess, not a graceful POSIX SIGTERM.
    This confirms process exit only, never application transaction/effect rollback.
    """
    if not 0 <= timeout <= 300 or not 0 <= kill_timeout <= 60:
        raise ValueError("Termination time budgets are out of range.")
    identity, process = _identity(record)
    if identity == "gone":
        return TerminationResult(
            "confirmed", "Recorded process is no longer executing.", False, False
        )
    if identity != "match":
        return TerminationResult(
            "unknown", "Identity cannot be verified; no signal sent.", False, False
        )
    psutil = _psutil()
    try:
        process.terminate()
    except psutil.NoSuchProcess:
        return TerminationResult(
            "confirmed", "Recorded process exited before signalling.", False, False
        )
    except (psutil.Error, OSError):
        return TerminationResult(
            "unknown", "Native termination could not be confirmed.", False, False
        )

    def wait(seconds):
        deadline = time.monotonic() + seconds
        while True:
            state, _ = _identity(record)
            if state == "gone":
                return state
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return state
            # During actual exit, exe/cmdline can disappear before status becomes
            # zombie/gone. Polling is safe; never send another signal on unknown.
            time.sleep(min(0.05, remaining))

    state = wait(timeout)
    if state == "gone":
        return TerminationResult("confirmed", "Recorded process exited.", True, False)
    if state == "unknown":
        return TerminationResult(
            "unknown",
            "Identity became inaccessible or changed; no further signal sent.",
            True,
            False,
        )
    if not force:
        return TerminationResult(
            "unknown", "Termination deadline reached; forced kill was not authorized.", True, False
        )
    state, process = _identity(record)
    if state == "gone":
        return TerminationResult("confirmed", "Recorded process exited.", True, False)
    if state != "match":
        return TerminationResult(
            "unknown", "Identity changed before forced kill; kill refused.", True, False
        )
    try:
        process.kill()
    except psutil.NoSuchProcess:
        return TerminationResult(
            "confirmed", "Recorded process exited before forced kill.", True, False
        )
    except (psutil.Error, OSError):
        return TerminationResult(
            "unknown", "Forced termination could not be confirmed.", True, False
        )
    state = wait(kill_timeout)
    return TerminationResult(
        "confirmed" if state == "gone" else "unknown",
        "Recorded process exited after forced kill."
        if state == "gone"
        else "No exit confirmation after forced kill.",
        True,
        True,
    )
