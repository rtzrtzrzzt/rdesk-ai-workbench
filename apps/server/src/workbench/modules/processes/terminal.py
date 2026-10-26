"""PTY transport inside the existing command supervisor, with durable input fences.

Only the approved process service publishes input requests. A request is consumed
once: a persisted ``sending`` receipt precedes the irreversible PTY write. If the
supervisor dies in between, recovery reports uncertainty rather than typing again.
"""

from __future__ import annotations

import errno
import os
import sys
import time
from pathlib import Path

from workbench.infrastructure.vault.private import private_directory, write_json
from workbench.modules.processes.native import RunnerError, _directory, _json, _uuid, read_state

ROWS, COLUMNS = 24, 80
MAX_INPUT_BYTES = 4096


def input_bytes(data: str, eof: bool) -> bytes:
    if not isinstance(data, str) or type(eof) is not bool:
        raise RunnerError("Terminal input requires text data and a boolean eof.")
    raw = data.encode("utf-8")
    if len(raw) > MAX_INPUT_BYTES or (not raw and not eof):
        raise RunnerError("Terminal input must contain 1..4096 UTF-8 bytes or eof.")
    return raw


def _inputs(directory: Path) -> Path:
    path = directory / "inputs"
    private_directory(path, create=True)
    return path


def _receipt(path: Path, request: dict) -> dict | None:
    if not path.exists():
        return None
    receipt = _json(path)
    if any(receipt.get(key) != request[key] for key in ("operation_id", "command_id", "nonce")):
        raise RunnerError("Terminal input receipt identity mismatch.")
    if receipt.get("status") not in {"sending", "sent", "rejected", "outcome_unknown"}:
        raise RunnerError("Invalid terminal input receipt.")
    return receipt


def submit(directory: Path, operation_id: str, data: str, eof: bool, *, timeout=2.0) -> dict:
    """Called while the service holds Run/command/approval locks; never requeue an ID."""
    directory = _directory(directory)
    operation_id = _uuid(operation_id)
    input_bytes(data, eof)
    plan = _json(directory / "plan.json")
    if not plan.get("interactive", False):
        raise RunnerError("This command does not have an interactive terminal.")
    folder = _inputs(directory)
    request_path = folder / f"{operation_id}.json"
    receipt_path = folder / f"{operation_id}.receipt.json"
    binding = {
        "schema": 1,
        "operation_id": operation_id,
        "command_id": plan["command_id"],
        "nonce": plan["nonce"],
        "data": data,
        "eof": eof,
    }
    if request_path.exists():
        request = _json(request_path)
        if any(request.get(key) != value for key, value in binding.items()):
            raise RunnerError("Terminal operation ID is already bound to different input.")
        # Existing intent is never extended or recreated, even without a receipt.
    else:
        state = read_state(directory)
        if (
            state["status"] != "running"
            or state["supervisor_observation"] != "match"
            or state["owned_resources_closed"]
            or state.get("input_closed")
            or (directory / "stop.json").exists()
        ):
            raise RunnerError("Terminal is closed, stopping, or its ownership is unknown.")
        request = {**binding, "expires_at": time.time() + min(timeout, 2.0)}
        write_json(request_path, request)
    deadline = time.monotonic() + min(timeout, 2.0)
    while True:
        receipt = _receipt(receipt_path, request)
        if receipt and receipt["status"] != "sending":
            return receipt
        if time.monotonic() >= deadline:
            return (
                {**receipt, "status": "outcome_unknown"}
                if receipt
                else {
                    "operation_id": operation_id,
                    "command_id": plan["command_id"],
                    "nonce": plan["nonce"],
                    "status": "outcome_unknown",
                    "reason": "Input acknowledgement is unavailable; do not resend this input.",
                }
            )
        time.sleep(0.02)


def child_terminal() -> None:
    """Single-threaded supervisor pre-exec hook: give the child its controlling TTY."""
    import fcntl
    import termios

    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


class Terminal:
    def __init__(self):
        if sys.platform != "linux":
            raise RunnerError("Interactive PTY requires Linux.")
        import fcntl
        import struct
        import termios

        master, self.slave = os.openpty()
        try:
            fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLUMNS, 0, 0))
            # Keep canonical input, signals and echo; tools may change tty modes.
            os.set_blocking(master, False)
            self.stream = os.fdopen(master, "rb", buffering=0)
        except BaseException:
            os.close(master)
            os.close(self.slave)
            raise
        self.input_closed = False
        self.seen: set[str] = set()
        self.next_scan = 0.0

    def child_started(self):
        os.close(self.slave)
        self.slave = None

    def pump_inputs(self, directory: Path, plan: dict, *, accepting: bool) -> None:
        now = time.monotonic()
        if now < self.next_scan:
            return
        self.next_scan = now + 0.05
        folder = _inputs(directory)
        for path in sorted(folder.glob("*.json")):
            if path.name.endswith(".receipt.json") or path.name in self.seen:
                continue
            operation_id = _uuid(path.stem)
            request = _json(path, 32 * 1024)
            if (
                request.get("schema") != 1
                or request.get("operation_id") != operation_id
                or request.get("command_id") != plan["command_id"]
                or request.get("nonce") != plan["nonce"]
                or type(request.get("expires_at")) not in {int, float}
            ):
                raise RunnerError("Terminal request identity is invalid.")
            raw = input_bytes(request.get("data"), request.get("eof"))
            receipt_path = folder / f"{operation_id}.receipt.json"
            if _receipt(receipt_path, request):
                self.seen.add(path.name)
                continue
            receipt = {key: request[key] for key in ("operation_id", "command_id", "nonce")}
            receipt.update(status="sending", bytes_written=0, eof=request["eof"])
            if (
                not accepting
                or self.input_closed
                or self.stream.closed
                or (directory / "stop.json").exists()
                or time.time() >= request["expires_at"]
            ):
                receipt.update(
                    status="rejected", reason="Terminal is closed, stopping, or request expired."
                )
                write_json(receipt_path, receipt)
            else:
                if request["eof"]:
                    import termios

                    attributes = termios.tcgetattr(self.stream.fileno())
                    veof = attributes[6][termios.VEOF]
                    if not attributes[3] & termios.ICANON or veof == b"\0":
                        receipt.update(
                            status="rejected", reason="EOF requires canonical terminal mode."
                        )
                        write_json(receipt_path, receipt)
                        self.seen.add(path.name)
                        continue
                    # PTYs have no input-only close. The first VEOF flushes an
                    # unfinished line; the second produces the EOF indication.
                    raw += veof * 2
                write_json(receipt_path, receipt)
                try:
                    written = os.write(self.stream.fileno(), raw)
                    receipt["bytes_written"] = written
                    receipt["status"] = "sent" if written == len(raw) else "outcome_unknown"
                    if request["eof"]:
                        self.input_closed = True
                except OSError as error:
                    receipt.update(
                        status="outcome_unknown", reason=f"PTY write failed: {error.errno}."
                    )
                write_json(receipt_path, receipt, exclusive=False)
            self.seen.add(path.name)

    def read(self, size: int) -> bytes:
        try:
            return os.read(self.stream.fileno(), size)
        except OSError as error:
            if error.errno == errno.EIO:
                return b""  # Linux PTY hangup after the last slave closes.
            raise

    def close(self):
        if self.slave is not None:
            os.close(self.slave)
            self.slave = None
        self.stream.close()
