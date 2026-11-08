import json
import os
from pathlib import Path

import pytest
from workbench.modules.project_configuration import service


def write_configuration(root: Path, value) -> Path:
    directory = root / ".rdesk"
    directory.mkdir(exist_ok=True)
    path = directory / "config.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_missing_configuration_has_no_side_effects(tmp_path):
    assert service.load(tmp_path).status == "missing"
    assert service.runtime_defaults(tmp_path) == {}
    assert service.context_declarations(tmp_path) == ""
    assert list(tmp_path.iterdir()) == []


def test_valid_configuration_limits_context_to_declarations(tmp_path):
    write_configuration(
        tmp_path,
        {
            "version": 1,
            "interpreter": [".venv/bin/python", "-I"],
            "commands": {"test": [".venv/bin/python", "-m", "pytest"]},
            "defaults": {"command_timeout_seconds": 12, "context_target_ratio": 0.8},
        },
    )
    view = service.load(tmp_path)
    assert view.status == "valid"
    assert service.runtime_defaults(tmp_path) == {
        "command_timeout_seconds": 12,
        "context_target_ratio": 0.8,
    }
    context = service.context_declarations(tmp_path)
    assert '"interpreter": [".venv/bin/python", "-I"]' in context
    assert '"test": [".venv/bin/python", "-m", "pytest"]' in context
    assert "command_timeout_seconds" not in context


@pytest.mark.parametrize(
    "value",
    [
        {"version": True},
        {"version": 1.0},
        {"version": "1"},
        {"version": 2},
        {"version": 1, "auth": {"token": "must-not-leak"}},
        {"version": 1, "defaults": {"sandbox_profile": "native_scoped"}},
        {"version": 1, "defaults": {"approval_mode": "smart"}},
        {"version": 1, "defaults": {"model_connection_id": "must-not-leak"}},
        {"version": 1, "defaults": {"command_timeout_seconds": 0}},
        {"version": 1, "defaults": {"command_timeout_seconds": "12"}},
        {"version": 1, "defaults": {"model_retry_limit": 3}},
        {"version": 1, "defaults": {"context_target_ratio": float("nan")}},
        {"version": 1, "interpreter": ["python", "--api-key=must-not-leak"]},
        {"version": 1, "commands": {"test": ["tool", "--password", "must-not-leak"]}},
        {"version": 1, "commands": {"../outside": ["python"]}},
        {"version": 1, "commands": {"test": "python -m pytest"}},
        {"version": 1, "commands": {"test": ["python", "\0"]}},
    ],
)
def test_invalid_configuration_never_partly_applies_or_echoes_inputs(tmp_path, value):
    write_configuration(tmp_path, value)
    view = service.load(tmp_path)
    assert view.status == "invalid"
    assert view.configuration is None
    assert "must-not-leak" not in view.model_dump_json()
    assert service.runtime_defaults(tmp_path) == {}
    assert service.context_declarations(tmp_path) == ""


@pytest.mark.parametrize(
    "raw",
    [
        b'{"version":1,"version":1}',
        b'{"version":1,"defaults":{"model_retry_limit":0,"model_retry_limit":2}}',
        b'{"version":1,',
        b"\xff",
        b"[" * 1100 + b"]" * 1100,
        b" " * (service.MAX_BYTES + 1),
    ],
)
def test_malformed_duplicate_deep_or_oversized_configuration(tmp_path, raw):
    path = write_configuration(tmp_path, {"version": 1})
    path.write_bytes(raw)
    assert service.load(tmp_path).status == "invalid"


def test_file_symlink_does_not_escape_project(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text('{"version":1}', encoding="utf-8")
    directory = tmp_path / "project"
    (directory / ".rdesk").mkdir(parents=True)
    (directory / ".rdesk/config.json").symlink_to(outside)
    assert service.load(directory).status == "invalid"


def test_directory_symlink_and_substituted_root_are_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    write_configuration(outside, {"version": 1})
    directory = tmp_path / "project"
    directory.mkdir()
    (directory / ".rdesk").symlink_to(outside / ".rdesk", target_is_directory=True)
    assert service.load(directory).status == "invalid"
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(outside, target_is_directory=True)
    assert service.load(linked_root).status == "invalid"


def test_hardlinks_and_special_files_are_rejected(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text('{"version":1}', encoding="utf-8")
    directory = tmp_path / "project"
    (directory / ".rdesk").mkdir(parents=True)
    target = directory / ".rdesk/config.json"
    os.link(outside, target)
    assert service.load(directory).status == "invalid"
    target.unlink()
    os.mkfifo(target)
    assert service.load(directory).status == "invalid"


def test_platform_without_secure_descriptor_support_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "supports_dir_fd", set())
    assert service.load(tmp_path).status == "unavailable"
