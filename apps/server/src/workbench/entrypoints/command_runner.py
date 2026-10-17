"""Detached command supervisor; deliberately has no database or service imports."""

from __future__ import annotations

import argparse
import os
import select
import selectors
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from workbench.infrastructure.vault.private import write_json
from workbench.modules.processes.native import (
    RunnerError,
    enable_subreaper,
    read_plan,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class LinuxDriver:
    """Own only descendants proven while this subreaper is alive.

    pidfds remain attached to a process across exec and cannot target PID reuse.
    Durable receipts are observations; a restarted host cannot recreate these
    kernel handles or recover unknown ownership after supervisor death.
    """

    def __init__(self, nonce: str):
        import psutil

        self.psutil = psutil
        self.process = psutil.Process()
        self.nonce = nonce
        self.handles: dict[int, int] = {}
        self.identities: dict[int, dict] = {}
        self.reaped: list[dict] = []
        self.uncertain = False
        self.root: subprocess.Popen | None = None
        self.root_exit: int | None = None
        self.no_children = False

    def prepare(self) -> None:
        enable_subreaper()

    def _capture(self, child, parent) -> dict | None:
        psutil = self.psutil
        descriptor = None
        try:
            parent_birth = parent.create_time()
            created = child.create_time()
            if child.uids().effective != os.geteuid() or child.ppid() != parent.pid:
                return None
            descriptor = os.pidfd_open(child.pid)
            # Guard both PID lookups around acquiring the lifetime handle.
            if (
                psutil.Process(child.pid).create_time() != created
                or psutil.Process(parent.pid).create_time() != parent_birth
                or child.ppid() != parent.pid
            ):
                os.close(descriptor)
                return None
            try:
                executable = child.exe() or None
            except psutil.ZombieProcess:
                executable = None
            identity = {
                "version": 1,
                "kind": "linux-owned-lineage",
                "platform": "linux",
                "pid": child.pid,
                "create_time": created,
                "executable": executable,
                "uid": os.geteuid(),
                "parent_pid": parent.pid,
                "parent_create_time": parent_birth,
                "owner_nonce": self.nonce,
                "captured_at": _now(),
            }
            old = self.handles.get(child.pid)
            if old is not None:
                os.close(old)
            self.handles[child.pid] = descriptor
            self.identities[child.pid] = identity
            return identity
        except (psutil.NoSuchProcess, ProcessLookupError):
            if descriptor is not None:
                os.close(descriptor)
            return None
        except (psutil.Error, OSError):
            if descriptor is not None:
                os.close(descriptor)
            self.uncertain = True
            return None

    def discover(self) -> None:
        psutil = self.psutil
        # A long-lived command may itself reap thousands of short children.
        # Release exited lifetime handles without waiting to adopt/reap them.
        for pid, descriptor in list(self.handles.items()):
            poller = select.poll()
            poller.register(descriptor, select.POLLIN)
            if poller.poll(0):
                os.close(descriptor)
                del self.handles[pid]
        queue = [self.process]
        visited = set()
        while queue:
            parent = queue.pop()
            if parent.pid in visited:
                continue
            visited.add(parent.pid)
            try:
                if parent.pid != self.process.pid:
                    saved = self.identities.get(parent.pid)
                    if saved is None or parent.create_time() != saved["create_time"]:
                        self.uncertain = True
                        continue
                for child in parent.children():
                    saved = self.identities.get(child.pid)
                    if saved is None or saved["create_time"] != child.create_time():
                        saved = self._capture(child, parent)
                    if saved is not None:
                        queue.append(child)
            except psutil.NoSuchProcess:
                continue
            except (psutil.Error, OSError):
                self.uncertain = True

    def signal_owned(self, signum: int) -> None:
        for _pid, descriptor in list(self.handles.items()):
            try:
                signal.pidfd_send_signal(descriptor, signum)
            except ProcessLookupError:
                pass
            except OSError:
                self.uncertain = True

    def reap(self) -> None:
        # This process creates exactly the requested command tree. Kernel waitpid
        # receipts also account for fast adopted descendants that exited between
        # discovery snapshots; no signal is sent to an unrecorded PID.
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                self.no_children = True
                return
            if pid == 0:
                self.no_children = False
                return
            descriptor = self.handles.pop(pid, None)
            if descriptor is not None:
                os.close(descriptor)
            code = os.waitstatus_to_exitcode(status)
            if self.root is not None and pid == self.root.pid:
                self.root_exit = code
                self.root.returncode = code
            if len(self.reaped) < 1024:
                self.reaped.append(
                    {
                        "pid": pid,
                        "exit_code": code,
                        "reaped_at": _now(),
                        "identity_captured": pid in self.identities,
                    }
                )

    def closed(self) -> bool:
        self.discover()
        self.reap()
        if self.uncertain or not self.no_children:
            return False
        for descriptor in self.handles.values():
            poller = select.poll()
            poller.register(descriptor, select.POLLIN)
            if not poller.poll(0):
                return False
        return True

    def release(self) -> None:
        for descriptor in self.handles.values():
            os.close(descriptor)
        self.handles.clear()


def _self_identity(nonce: str) -> dict:
    import psutil

    process = psutil.Process()
    arguments = process.cmdline()
    occurrences = [
        arguments[i + 1] if i + 1 < len(arguments) else None
        for i, item in enumerate(arguments)
        if item == "--instance-id"
    ]
    if occurrences != [nonce] or process.uids().effective != os.geteuid():
        raise RunnerError("Supervisor nonce or user identity is invalid.")
    return {
        "version": 1,
        "platform": sys.platform,
        "pid": process.pid,
        "create_time": process.create_time(),
        "executable": process.exe(),
        "nonce": nonce,
        "nonce_flag": "--instance-id",
    }


def supervise(plan_path: Path, nonce: str) -> int:
    plan = read_plan(plan_path)
    if plan["nonce"] != nonce:
        raise RunnerError("Command plan nonce does not match supervisor.")
    directory = Path(plan["output_dir"])
    from workbench.modules.processes.native import _json

    if _json(directory / "launch.json").get("plan") != plan:
        raise RunnerError("Plan does not match the committed launch intent.")
    requested_stop = False

    def request_stop(_signum, _frame):
        nonlocal requested_stop
        requested_stop = True

    # SIG_IGN survives exec; explicitly retain child exit receipts even when
    # a launching host had disabled SIGCHLD collection.
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    identity = _self_identity(nonce)
    driver = LinuxDriver(nonce)
    state = {
        "schema": 1,
        "command_id": plan["command_id"],
        "nonce": nonce,
        "status": "starting",
        "exit_code": None,
        "stdout_bytes": 0,
        "stderr_bytes": 0,
        "stdout_observed_bytes": 0,
        "stderr_observed_bytes": 0,
        "truncated": False,
        "error": None,
        "owned_resources_closed": False,
        "supervisor_identity": identity,
        "child_identity": None,
        "started_at": _now(),
        "finished_at": None,
        "reason": "Supervisor captured before dispatch.",
        "max_output_bytes": plan["max_output_bytes"],
        "owned_identities": [],
        "reaped": [],
    }
    write_json(directory / "state.json", state)
    selector = selectors.DefaultSelector()
    logs = {}
    child = None
    desired = None
    stopping_at = None
    kill_sent = False
    started = time.monotonic()
    last_saved = started

    def save():
        # Bound receipt size without losing the kernel closure verdict. The
        # complete command's root identity and aggregate counts are always kept.
        identities = list(driver.identities.values())
        state["owned_identities"] = identities[-512:]
        state["identity_receipts_omitted"] = max(0, len(identities) - 512)
        state["reaped"] = driver.reaped[-512:]
        write_json(directory / "state.json", state, exclusive=False)

    try:
        driver.prepare()
        for name in ("stdout", "stderr"):
            descriptor = os.open(
                directory / (name + ".log"),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            logs[name] = os.fdopen(descriptor, "wb", buffering=0)
        if (directory / "stop.json").exists() or requested_stop:
            desired = "cancelled"
            driver.reap()
        else:
            from workbench.modules.sandbox import SandboxProfile, provider_for

            profile = SandboxProfile(
                workspace_root=Path(plan["cwd"]),
                protected_roots=tuple(Path(item) for item in plan["protected_roots"]),
            )
            with provider_for(plan["sandbox_profile"]).prepare_command(
                plan["argv"], Path(plan["cwd"]), profile
            ) as prepared:
                child = subprocess.Popen(
                    prepared.argv,
                    cwd=prepared.cwd,
                    env=prepared.env,
                    pass_fds=prepared.pass_fds,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    close_fds=True,
                )
                state["sandbox_profile"] = prepared.capability.provider
                state["filesystem_isolation"] = prepared.capability.filesystem_isolation
                state["network_isolation"] = prepared.capability.network_isolation
            driver.root = child
            import psutil

            state["child_identity"] = driver._capture(psutil.Process(child.pid), driver.process)
            if state["child_identity"] is None:
                # No replay: the command may already have run. Cleanup continues.
                desired = "outcome_unknown"
                state["reason"] = "Child started but native identity capture failed."
            for name in ("stdout", "stderr"):
                stream = getattr(child, name)
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            state["status"] = "running"
            save()
        while True:
            driver.discover()
            driver.reap()
            now = time.monotonic()
            if driver.uncertain and desired is None:
                desired = "outcome_unknown"
                state["reason"] = "Native descendant identity could not be read safely."
            if desired is None:
                marker = directory / "stop.json"
                if marker.exists():
                    request = _json(marker)
                    if (
                        request.get("command_id") != plan["command_id"]
                        or request.get("nonce") != nonce
                    ):
                        raise RunnerError("Stop marker identity mismatch.")
                    requested_stop = True
                if requested_stop:
                    desired = "cancelled"
                elif now - started >= plan["timeout_seconds"]:
                    desired = "timed_out"
                elif driver.root_exit is not None:
                    desired = "completed" if driver.root_exit == 0 else "failed"
            if desired is not None and stopping_at is None:
                stopping_at = now
                state["status"] = "stopping"
                driver.signal_owned(signal.SIGTERM)
                save()
            elif stopping_at is not None:
                # New orphan descendants can be adopted after the first signal.
                driver.signal_owned(signal.SIGKILL if kill_sent else signal.SIGTERM)
                if now - stopping_at >= 1 and not kill_sent:
                    driver.signal_owned(signal.SIGKILL)
                    kill_sent = True
            for key, _mask in selector.select(0.03):
                raw = os.read(key.fileobj.fileno(), 65536)
                if not raw:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                name = key.data
                state[name + "_observed_bytes"] += len(raw)
                remaining = plan["max_output_bytes"] - state["stdout_bytes"] - state["stderr_bytes"]
                kept = raw[:remaining]
                while kept:
                    written = logs[name].write(kept)
                    if not written:
                        raise OSError("Log write did not make progress.")
                    state[name + "_bytes"] += written
                    kept = kept[written:]
                if len(raw) >= remaining:
                    state["truncated"] = True
                    desired = "output_limit"
                    # Signal immediately on this first observed budget boundary.
                    driver.discover()
                    driver.signal_owned(signal.SIGTERM)
            closed = driver.closed()
            if desired is not None and closed and not selector.get_map():
                state["owned_resources_closed"] = True
                break
            if stopping_at is not None and now - stopping_at > 10:
                desired = "outcome_unknown"
                state["reason"] = "Native descendant closure was not proved within cleanup budget."
                break
            if now - last_saved >= 0.25:
                save()
                last_saved = now
        state["status"] = desired or "outcome_unknown"
        state["exit_code"] = driver.root_exit
        if state["reason"] == "Supervisor captured before dispatch.":
            state["reason"] = {
                "completed": "Command exited successfully; native resources closed.",
                "failed": f"Command exited with code {driver.root_exit}; native resources closed.",
                "cancelled": "Stop requested; native resources closed.",
                "timed_out": (
                    f"Reached {plan['timeout_seconds']}s deadline; native resources closed."
                ),
                "output_limit": "Combined log byte budget reached; native resources closed.",
            }.get(state["status"], "Native closure or command outcome unknown.")
    except BaseException as error:
        # Never turn a post-dispatch failure into permission to retry the command.
        state["status"] = "outcome_unknown" if child is not None else "failed"
        state["reason"] = f"Supervisor failure: {type(error).__name__}; command will not replay."
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                driver.discover()
                driver.signal_owned(signal.SIGKILL)
                if driver.closed():
                    state["owned_resources_closed"] = True
                    break
                time.sleep(0.05)
        except Exception:
            state["owned_resources_closed"] = False
    finally:
        for key in list(selector.get_map().values()):
            key.fileobj.close()
        selector.close()
        for stream in logs.values():
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
        if state["status"] in {"failed", "timed_out", "output_limit", "outcome_unknown"}:
            state["error"] = {
                "code": "COMMAND_" + state["status"].upper(),
                "message": state["reason"],
            }
        state["finished_at"] = _now()
        save()
        driver.release()
    return 0 if state["status"] == "completed" else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--plan", required=True, type=Path)
    arguments = parser.parse_args()
    raise SystemExit(supervise(arguments.plan, arguments.instance_id))


if __name__ == "__main__":
    main()
