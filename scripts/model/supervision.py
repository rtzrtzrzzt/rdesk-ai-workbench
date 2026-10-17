"""Linux ownership boundary for a dedicated model runner, using only the stdlib."""

from __future__ import annotations

import ctypes
import os
import signal
import sys
import time
from pathlib import Path
from uuid import UUID

# Linux wait(2): include clone children whose exit signal is not SIGCHLD.
WAIT_ALL_CHILDREN = 0x40000000


def boot_id() -> str:
    return str(UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip()))


def _identity(pid: int) -> tuple[str, int, str]:
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return fields[0], int(fields[1]), fields[19]


def process_start(pid):
    try:
        status, _, started = _identity(pid)
        return None if status == "Z" else started
    except (FileNotFoundError, ProcessLookupError):
        return None


def signal_identity(pid: int, started: str, signum: int) -> bool:
    """Signal one previously identified process, never a historical process group."""
    try:
        descriptor = os.pidfd_open(pid)
        try:
            if not started or process_start(pid) != started:
                return False
            signal.pidfd_send_signal(descriptor, signum)
            return True
        finally:
            os.close(descriptor)
    except ProcessLookupError:
        return False


class LinuxSubreaper:
    """Own all children of this otherwise child-free, dedicated runner.

    Orphans remain our children across setsid/double-fork. We repeatedly signal
    direct children through pidfds and reap them; the kernel's ECHILD receipt,
    not a /proc snapshot, proves that no descendant can still fork or write.
    The caller must not create unrelated children or concurrently reap ours.
    """

    def __init__(self):
        self.pid = os.getpid()
        self.prepared = False

    def prepare(self):
        if not sys.platform.startswith("linux") or not all(
            (hasattr(os, "pidfd_open"), hasattr(signal, "pidfd_send_signal"))
        ):
            raise RuntimeError("Model supervision requires Linux subreaper and pidfd support")
        # Do not inherit auto-reaping: the guard must retain real exit statuses.
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        try:
            os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT | WAIT_ALL_CHILDREN)
        except ChildProcessError:
            pass
        else:
            raise RuntimeError("Model supervisor must start without existing child processes")
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "Unable to enable Linux child subreaper")
        descriptor = os.pidfd_open(self.pid)
        try:
            signal.pidfd_send_signal(descriptor, 0)
        finally:
            os.close(descriptor)
        self.prepared = True

    def _reap(self, root) -> bool:
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG | WAIT_ALL_CHILDREN)
            except ChildProcessError:
                return True
            if pid == 0:
                return False
            if root is not None and pid == root.pid:
                root.returncode = os.waitstatus_to_exitcode(status)

    def _children(self) -> set[int]:
        children = set()
        for task in Path(f"/proc/{self.pid}/task").iterdir():
            try:
                children.update(int(pid) for pid in (task / "children").read_text().split())
            except FileNotFoundError:
                continue  # A thread ended; ECHILD still governs closure.
        return children

    def _signal(self, pid, signum, sent):
        try:
            before = _identity(pid)
            if before[0] == "Z" or before[1] != self.pid or (pid, before[2], signum) in sent:
                return
            descriptor = os.pidfd_open(pid)
            try:
                after = _identity(pid)
                if after[1] != self.pid or before[2] != after[2]:
                    return
                signal.pidfd_send_signal(descriptor, signum)
                sent.add((pid, before[2], signum))
            finally:
                os.close(descriptor)
        except (FileNotFoundError, ProcessLookupError):
            pass

    def close(self, root=None, *, grace=15, kill_wait=5) -> bool:
        if not self.prepared or os.getpid() != self.pid:
            raise RuntimeError("Only the original prepared supervisor can close its children")
        soft_deadline = time.monotonic() + grace
        deadline = soft_deadline + kill_wait
        sent = set()
        while True:
            try:
                if self._reap(root):
                    return True
                signum = signal.SIGTERM if time.monotonic() < soft_deadline else signal.SIGKILL
                for pid in self._children():
                    self._signal(pid, signum, sent)
            except (OSError, ValueError, IndexError):
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
