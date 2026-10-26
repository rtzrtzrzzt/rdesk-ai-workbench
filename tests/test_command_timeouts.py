"""Contract checks only; actual model, command and UI acceptance is separate."""

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from workbench.contracts.command_limits import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    MAX_COMMAND_TIMEOUT_SECONDS,
)
from workbench.contracts.errors import AppError
from workbench.contracts.requests import RunOptions
from workbench.modules.processes.dto import StartCommand
from workbench.modules.processes.native import read_plan
from workbench.modules.runs.safety import waiting_observation
from workbench.modules.tools.registry import ExecCommand, definitions
from workbench.modules.tools.validation import command_timeout, parameter_error


def test_reported_timeouts_and_frozen_schema_share_task_budget():
    budget = RunOptions().command_timeout_seconds
    assert budget == DEFAULT_COMMAND_TIMEOUT_SECONDS
    for seconds in (150, 180, 200):
        for schema in (ExecCommand, StartCommand):
            values = schema(command="python3 --version", timeout_seconds=seconds).model_dump()
            assert command_timeout(values, {"command_timeout_seconds": budget}) == values
    for definition in definitions(["exec_command", "start_command"], command_timeout_seconds=200):
        field = definition["function"]["parameters"]["properties"]["timeout_seconds"]
        integer = next(choice for choice in field["anyOf"] if choice["type"] == "integer")
        assert integer["maximum"] == 200
    with pytest.raises(AppError) as denied:
        command_timeout({"timeout_seconds": 201}, {"command_timeout_seconds": 200})
    assert denied.value.details["fields"][0]["field"] == "timeout_seconds"
    assert "200秒" in denied.value.message


def test_omitted_timeout_preserves_existing_approved_parameters():
    values = ExecCommand(command="python3 --version").model_dump()
    assert command_timeout(values, {})["timeout_seconds"] == DEFAULT_COMMAND_TIMEOUT_SECONDS
    recorded = {**values, "timeout_seconds": 30}
    assert command_timeout(values, {}, recorded) == recorded
    explicit = {**values, "timeout_seconds": 180}
    assert command_timeout(explicit, {}, recorded) == explicit


def test_validation_errors_never_echo_values_or_extra_field_names():
    text = Path("README.md").read_text()
    extra = text.splitlines()[0]
    with pytest.raises(ValidationError) as failed:
        ExecCommand.model_validate({"command": text, "timeout_seconds": text, extra: text})
    error = parameter_error(failed.value, ExecCommand)
    visible = error.message + json.dumps(error.details, ensure_ascii=False)
    assert text not in visible and extra not in visible
    assert "timeout_seconds" in visible and "必须是整数" in visible
    assert "未声明字段" in visible


def test_native_plan_accepts_same_maximum_as_settings_and_tool_schema(tmp_path):
    identifier = str(uuid4())
    output = tmp_path / identifier
    output.mkdir(mode=0o700)
    path = tmp_path / "plan.json"
    plan = {
        "schema": 1,
        "command_id": identifier,
        "nonce": str(uuid4()),
        "argv": [sys.executable, "--version"],
        "cwd": str(Path.cwd()),
        "timeout_seconds": MAX_COMMAND_TIMEOUT_SECONDS,
        "max_output_bytes": 65536,
        "output_dir": str(output),
    }
    path.write_text(json.dumps(plan))
    path.chmod(0o600)
    assert (
        read_plan(path)["timeout_seconds"]
        == RunOptions(command_timeout_seconds=MAX_COMMAND_TIMEOUT_SECONDS).command_timeout_seconds
    )


def test_command_waiting_requires_an_active_native_receipt():
    active = {"command": {"status": "running", "owned_resources_closed": False}}
    assert waiting_observation("read_command_output", active)
    assert not waiting_observation("read_command_output", {"status": "running"})
    assert not waiting_observation(
        "read_command_output", {"command": {"status": "completed", "owned_resources_closed": True}}
    )
    assert not waiting_observation(
        "read_command_output",
        {"command": {"status": "outcome_unknown", "owned_resources_closed": False}},
    )
