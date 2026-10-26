"""Real temporary PTYs and focused approval checks; no database/services required."""

import json
import sys
import time
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from workbench.contracts.errors import AppError
from workbench.infrastructure.vault.private import write_json
from workbench.modules.processes import native, service, terminal
from workbench.modules.processes.domain import CommandExecution
from workbench.modules.processes.dto import StartCommand, WriteCommandInput
from workbench.modules.runs.domain import Run
from workbench.modules.tools.domain import Operation
from workbench.modules.tools.service import argument_digest


def make_plan(tmp_path, *, interactive=True):
    identifier = str(uuid4())
    directory = tmp_path / identifier
    directory.mkdir(mode=0o700)
    plan = {
        "schema": 1,
        "command_id": identifier,
        "nonce": str(uuid4()),
        "argv": [
            sys.executable,
            "-u",
            "-c",
            "import os,sys; "
            "print('TTY',os.isatty(0),os.isatty(1),os.isatty(2),os.get_terminal_size(0)); "
            "print('ERR_CHANNEL',file=sys.stderr); "
            "exec(\"for line in sys.stdin:\\n print('GOT:'+line.strip())\"); "
            "print('INPUT_ENDED')",
        ],
        "cwd": str(tmp_path),
        "timeout_seconds": 12,
        "max_output_bytes": 65536,
        "output_dir": str(directory),
        "sandbox_profile": "native_scoped",
        "protected_roots": [],
    }
    if interactive is not None:
        plan["interactive"] = interactive
    write_json(directory / "plan.json", plan)
    return directory, plan


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError("Temporary native command did not reach its expected state.")


@contextmanager
def running_terminal(tmp_path):
    if not native.capability()["supported"]:
        pytest.skip("Linux pidfd/subreaper unavailable")
    directory, plan = make_plan(tmp_path)
    launched = native.launch(directory / "plan.json")
    try:
        assert launched.status == "acknowledged"
        wait_for(lambda: "TTY True True True" in native.read_log(directory)["text"])
        yield directory, plan
    finally:
        stopped = native.stop(directory, timeout=5)
        assert stopped.owned_resources_closed
        assert stopped.state["supervisor_observation"] == "gone"


def test_real_pty_input_eof_merged_logs_and_duplicate_operation(tmp_path):
    with running_terminal(tmp_path) as (directory, _):
        state = native.read_state(directory)
        assert state["stderr_merged"] and state["terminal_rows"] == 24
        assert state["terminal_columns"] == 80
        output = native.read_log(directory)["text"]
        assert "columns=80, lines=24" in output
        assert "ERR_CHANNEL" in output
        assert native.read_log(directory, "stderr")["text"] == ""
        operation_id = str(uuid4())
        first = terminal.submit(directory, operation_id, "ONE\n", False)
        assert first["status"] == "sent"
        assert terminal.submit(directory, operation_id, "ONE\n", False) == first
        wait_for(lambda: "GOT:ONE" in native.read_log(directory)["text"])
        assert native.read_log(directory)["text"].count("GOT:ONE") == 1
        with pytest.raises(native.RunnerError, match="different input"):
            terminal.submit(directory, operation_id, "TWO\n", False)
        assert terminal.submit(directory, str(uuid4()), "", True)["status"] == "sent"
        wait_for(lambda: native.read_state(directory)["owned_resources_closed"])
        output = native.read_log(directory)["text"]
        assert "INPUT_ENDED" in output and "GOT:TWO" not in output
        with pytest.raises(native.RunnerError, match="closed"):
            terminal.submit(directory, str(uuid4()), "AFTER\n", False)


def test_stop_refuses_new_input_and_closes_real_pty(tmp_path):
    with running_terminal(tmp_path) as (directory, plan):
        write_json(
            directory / "stop.json",
            {
                "schema": 1,
                "command_id": plan["command_id"],
                "nonce": plan["nonce"],
            },
        )
        with pytest.raises(native.RunnerError, match="closed"):
            terminal.submit(directory, str(uuid4()), "NO\n", False)
        wait_for(lambda: native.read_state(directory)["owned_resources_closed"])
        assert native.read_state(directory)["status"] == "cancelled"


def test_real_command_boot_anchor_cannot_close_same_boot_resources(tmp_path):
    with running_terminal(tmp_path) as (directory, plan):
        current_boot = native.boot_id()
        if current_boot is None:
            pytest.skip("Linux boot identity unavailable")
        intent = native._json(directory / "launch.json")
        assert intent["boot_id"] == current_boot
        assert native.read_state(directory)["boot_id"] == current_boot
        assert native.reboot_closure_proof(directory, plan["nonce"]) is None
        with pytest.raises(native.RunnerError):
            native.reboot_closure_proof(directory, str(uuid4()))


def test_existing_sending_fence_is_never_typed_again(tmp_path, monkeypatch):
    directory, plan = make_plan(tmp_path)
    operation_id = str(uuid4())
    folder = terminal._inputs(directory)
    request = {
        "schema": 1,
        "operation_id": operation_id,
        "command_id": plan["command_id"],
        "nonce": plan["nonce"],
        "data": "ONCE\n",
        "eof": False,
        "expires_at": time.time() + 2,
    }
    write_json(folder / f"{operation_id}.json", request)
    receipt = {key: request[key] for key in ("operation_id", "command_id", "nonce")}
    write_json(folder / f"{operation_id}.receipt.json", {**receipt, "status": "sending"})
    instance = terminal.Terminal()
    try:

        def forbidden_write(*args):
            raise AssertionError("A sending fence must never be replayed")

        monkeypatch.setattr(terminal.os, "write", forbidden_write)
        instance.pump_inputs(directory, plan, accepting=True)
        result = terminal.submit(directory, operation_id, "ONCE\n", False, timeout=0)
        assert result["status"] == "outcome_unknown"
    finally:
        instance.close()


def test_expired_request_is_rejected_without_write(tmp_path):
    directory, plan = make_plan(tmp_path)
    operation_id = str(uuid4())
    folder = terminal._inputs(directory)
    write_json(
        folder / f"{operation_id}.json",
        {
            "schema": 1,
            "operation_id": operation_id,
            "command_id": plan["command_id"],
            "nonce": plan["nonce"],
            "data": "STALE\n",
            "eof": False,
            "expires_at": time.time() - 1,
        },
    )
    instance = terminal.Terminal()
    try:
        instance.pump_inputs(directory, plan, accepting=True)
        receipt = json.loads((folder / f"{operation_id}.receipt.json").read_text())
        assert receipt["status"] == "rejected" and receipt["bytes_written"] == 0
    finally:
        instance.close()


def test_old_plan_and_default_contract_keep_noninteractive_compatibility(tmp_path):
    directory, plan = make_plan(tmp_path, interactive=None)
    assert native.read_plan(directory / "plan.json") == plan
    assert StartCommand(command="pwd").interactive is False
    assert WriteCommandInput(command_id=uuid4(), eof=True).data == ""
    plan.update(interactive=True, sandbox_profile="linux_bwrap")
    write_json(directory / "plan.json", plan, exclusive=False)
    with pytest.raises(native.RunnerError, match="native_scoped"):
        native.read_plan(directory / "plan.json")
    with pytest.raises(native.RunnerError):
        terminal.input_bytes("界" * 2000, False)


def test_raw_terminal_eof_is_explicitly_rejected_without_closing_output(tmp_path):
    import tty

    directory, plan = make_plan(tmp_path)
    operation_id = str(uuid4())
    folder = terminal._inputs(directory)
    write_json(
        folder / f"{operation_id}.json",
        {
            "schema": 1,
            "operation_id": operation_id,
            "command_id": plan["command_id"],
            "nonce": plan["nonce"],
            "data": "",
            "eof": True,
            "expires_at": time.time() + 2,
        },
    )
    instance = terminal.Terminal()
    try:
        tty.setraw(instance.slave)
        instance.pump_inputs(directory, plan, accepting=True)
        receipt = json.loads((folder / f"{operation_id}.receipt.json").read_text())
        assert receipt["status"] == "rejected" and "canonical" in receipt["reason"]
        assert not instance.input_closed and not instance.stream.closed
    finally:
        instance.close()


@pytest.mark.parametrize(
    "failure",
    [
        "child",
        "cancelled",
        "paused",
        "foreign",
        "closed",
        "revoked",
        "digest",
        "changed",
        "allowed",
    ],
)
def test_service_refuses_unowned_inactive_or_unapproved_input(monkeypatch, failure):
    run_id, command_id, operation_id = (str(uuid4()) for _ in range(3))
    values = {"command_id": command_id, "data": "approved\n", "eof": False}
    run = SimpleNamespace(id=run_id, parent_run_id=None, cancel_requested_at=None, status="running")
    identity = {"test": "identity"}
    command = SimpleNamespace(
        id=command_id,
        run_id=run_id,
        stop_requested=False,
        resources_closed=False,
        status="running",
        output_path="unused",
        nonce="test-nonce",
        native_identity=identity,
    )
    operation = SimpleNamespace(
        run_id=run_id,
        tool_name="write_command_input",
        arguments=values.copy(),
        status="running",
    )
    approval = SimpleNamespace(
        status="approved",
        approved=True,
        parameters_digest=argument_digest(operation.arguments),
        run_id=run_id,
        tool_name="write_command_input",
    )
    if failure == "child":
        run.parent_run_id = str(uuid4())
    elif failure == "cancelled":
        run.cancel_requested_at = object()
    elif failure == "paused":
        run.status = "paused"
    elif failure == "foreign":
        command.run_id = str(uuid4())
    elif failure == "closed":
        command.resources_closed = True
    elif failure == "revoked":
        approval.approved = False
    elif failure == "digest":
        approval.parameters_digest = "incorrect"
    elif failure == "changed":
        values = {**values, "data": "different\n"}

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def begin(self):
            return self

        def scalar(self, query):
            return run if query.column_descriptions[0]["entity"] is Run else approval

        def get(self, cls, identifier, **kwargs):
            return command if cls is CommandExecution else operation if cls is Operation else None

    monkeypatch.setattr(service, "Session", lambda *_: FakeSession())
    monkeypatch.setattr(service, "engine", lambda: None)
    monkeypatch.setattr(service, "assert_owned", lambda *_: None)
    if failure == "allowed":
        monkeypatch.setattr(service, "storage_path", lambda path: path)
        monkeypatch.setattr(
            native,
            "read_state",
            lambda *_: {
                "nonce": "test-nonce",
                "supervisor_identity": identity,
                "interactive": True,
            },
        )
        monkeypatch.setattr(terminal, "submit", lambda *_: {"status": "sent", "bytes_written": 9})
        result = service._write_input(run_id, operation_id, values)
        assert result["status"] == "sent" and result["bytes_written"] == 9
    else:
        monkeypatch.setattr(
            terminal, "submit", lambda *_: pytest.fail("Input must be rejected before I/O")
        )
        with pytest.raises(AppError):
            service._write_input(run_id, operation_id, values)
