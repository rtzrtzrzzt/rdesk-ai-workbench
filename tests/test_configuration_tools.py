"""Offline configuration contracts; no database, provider or product acceptance."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from workbench.contracts.errors import AppError
from workbench.modules.configuration import catalog, dto, service
from workbench.modules.preferences.configuration import DeveloperTools, LanguageServer
from workbench.modules.tools.registry import TOOLS


@pytest.mark.parametrize(
    "values",
    [
        {"api_key": "credential-boundary-probe"},
        {"configuration": {"Authorization": "credential-boundary-probe"}},
        {"configuration": {"args": ["--token", "credential-boundary-probe"]}},
    ],
)
def test_secrets_rejected_by_tool_schema_before_operation_persistence(values):
    with pytest.raises(ValidationError):
        TOOLS["configure_settings"].schema.model_validate({"section": "mcp", "values": values})


def test_safe_view_drops_inline_arguments_and_nested_credentials():
    value = service.public(
        {
            "configuration": {
                "args": ["--token", "credential-boundary-probe"],
                "settings": {"Authorization": "credential-boundary-probe"},
            },
            "command": ["/usr/bin/server", "--token", "credential-boundary-probe"],
        }
    )
    assert "credential-boundary-probe" not in str(value)
    assert value["configuration"]["arguments_configured"]
    assert value["command_executable"] == "/usr/bin/server"


def test_server_configuration_matches_executor_limits():
    item = {"language": "python", "command": ["/usr/bin/server"]}
    DeveloperTools(language_servers=[item] * 8)
    with pytest.raises(ValidationError):
        DeveloperTools(language_servers=[item] * 9)
    for command in ([""], ["/usr/bin/server", "a\0b"]):
        with pytest.raises(ValidationError):
            LanguageServer(language="python", command=command)


def test_scope_and_operator_boundary_are_explicit():
    assert service.scoped(dto.ReadSettings(section="mcp")).scope == "workspace"
    with pytest.raises(AppError) as denied:
        service.scoped(dto.ReadSettings(section="mcp", scope="user"))
    assert denied.value.code == "settings_scope_mismatch"
    with pytest.raises(AppError) as operator:
        service.validate(dto.SettingsChange(section="launcher", values={}))
    assert operator.value.code == "settings_action_unavailable"
    sections = catalog.discover(include_schemas=True)["sections"]
    assert {item["section"] for item in sections} == set(catalog.SECTIONS)
    assert all(item["effective_at"] and item["instructions"] for item in sections)
    for name, section in catalog.SECTIONS.items():
        for scope in section.scopes:
            assert service.scoped(dto.ReadSettings(section=name, scope=scope)).scope == scope


def test_preview_only_reads_and_rejects_stale_version(monkeypatch):
    monkeypatch.setattr(service, "workspace", lambda *_: None)
    monkeypatch.setattr(service, "_read_values", lambda *_: {"version": 2})
    command = dto.SettingsChange(
        section="experience",
        values={
            "expected_version": 2,
            "theme": "dark",
            "input_mode": "queue",
        },
    )
    result = service.preview(object(), "unused", "unused", command)
    assert result["saved"] is False and result["proposed"]["theme"] == "dark"
    with pytest.raises(AppError) as stale:
        service.preview(
            object(),
            "unused",
            "unused",
            command.model_copy(
                update={
                    "values": {
                        **command.values,
                        "expected_version": 1,
                    }
                }
            ),
        )
    assert stale.value.code == "settings_conflict"


def test_model_keys_preserved_and_mcp_hidden_arguments_not_cleared(monkeypatch):
    from workbench.modules.mcp import host

    configuration = {
        "connection_id": str(uuid4()),
        "version": 1,
        "name": "local-documents",
        "enabled": True,
        "transport": "stdio",
        "command": "/usr/bin/server",
        "cwd": str(Path.cwd()),
        "args": ["--token", "credential-boundary-probe"],
    }
    monkeypatch.setattr(
        host, "get_connection", lambda *_: SimpleNamespace(configuration=configuration)
    )
    command = dto.SettingsChange(
        section="mcp",
        resource_id=uuid4(),
        values={
            "expected_version": 1,
            "configuration": {key: value for key, value in configuration.items() if key != "args"},
        },
    )
    parsed = service.validate_target(object(), "unused", "unused", command)
    assert parsed.configuration.args == configuration["args"]
    assert "credential-boundary-probe" not in str(service.public(parsed.model_dump(mode="json")))


def test_applied_receipt_replay_does_not_revalidate_a_removed_target(monkeypatch):
    command = dto.ApplySettings(section="mcp", request_id=uuid4(), resource_id=uuid4())
    previous = {"protocol": "workbench-settings/1", "status": "applied"}
    db = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = db
    monkeypatch.setattr(service, "Session", lambda *_: context)
    monkeypatch.setattr(service, "engine", lambda: None)
    monkeypatch.setattr(service, "workspace", lambda *_: None)
    monkeypatch.setattr(service, "idempotent", lambda *_: previous)
    validate = MagicMock(side_effect=AssertionError("replayed receipt must not need its target"))
    write = MagicMock(side_effect=AssertionError("replayed receipt must not write again"))
    monkeypatch.setattr(service, "validate_target", validate)
    monkeypatch.setattr(service, "_write", write)
    assert asyncio.run(service.apply("unused", "unused", command)) == previous
    validate.assert_not_called()
    write.assert_not_called()


@pytest.mark.parametrize("actor_cancelled", [False, True])
def test_post_dispatch_failure_remains_unknown_and_cancel_guard_stops_write(
    monkeypatch, actor_cancelled
):
    row = SimpleNamespace(response=None)
    db = MagicMock()
    db.scalar.side_effect = lambda statement: (
        SimpleNamespace(cancel_requested_at=True, status="running")
        if "FROM runs" in str(statement)
        else row
    )
    context = MagicMock()
    context.__enter__.return_value = db
    monkeypatch.setattr(service, "Session", lambda *_: context)
    monkeypatch.setattr(service, "engine", lambda: None)
    monkeypatch.setattr(service, "workspace", lambda *_: None)
    monkeypatch.setattr(
        service, "idempotent", lambda _db, _user, _request, _scope, _values, execute: execute()
    )
    monkeypatch.setattr("workbench.modules.runs.ownership.assert_owned", lambda *_: None)
    called = []

    async def write(_db, _user, _workspace, _command, _parsed, guard):
        if actor_cancelled:
            guard(db)
        called.append("published")
        # The real memory writer can report this after a committed publication.
        raise AppError("memory_lock_cleanup", "写入后清理未确认", 503, "dependency")

    monkeypatch.setattr(service, "_write", write)
    command = dto.ApplySettings(
        section="experience",
        request_id=uuid4(),
        values={
            "expected_version": 0,
            "theme": "system",
            "input_mode": "queue",
        },
    )
    result = asyncio.run(
        service.apply(
            "unused", "unused", command, actor_run_id=str(uuid4()) if actor_cancelled else None
        )
    )
    assert result["status"] == "outcome_unknown"
    assert called == ([] if actor_cancelled else ["published"])
    assert row.response == result


def test_live_developer_tools_catalog_exposes_nested_language_server_contract():
    schema = catalog.describe("developer_tools")["actions"]["save"]
    variants = schema["properties"]["language_servers"]["anyOf"]
    array = next(item for item in variants if item.get("type") == "array")
    definition = schema["$defs"][array["items"]["$ref"].rsplit("/", 1)[1]]
    assert {"language", "command", "settings", "initialization_options"} <= set(
        definition["properties"]
    )
    assert "language" in definition["required"]
    assert "command" not in definition["required"]
    assert definition["properties"]["command"]["description"]


def test_existing_language_server_edit_preserves_omitted_hidden_command(monkeypatch):
    # Synthetic configuration data exercises the storage boundary without running an LSP.
    original = ["protocol-language-server", "--mode", "protocol-stdio"]
    monkeypatch.setattr(
        service.state,
        "get_state",
        lambda *_: SimpleNamespace(
            values={"language_servers": [{"language": "python", "command": original}]}
        ),
    )
    command = dto.SettingsChange(
        section="developer_tools",
        scope="workspace",
        values={
            "expected_version": 1,
            "language_servers": [{"language": "python", "settings": {"protocol_check": True}}],
        },
    )
    parsed = service.validate_target(object(), "protocol-user", "protocol-workspace", command)
    assert parsed.language_servers[0].command == original
    assert parsed.language_servers[0].settings == {"protocol_check": True}


def test_new_language_server_without_command_is_rejected_before_write(monkeypatch):
    monkeypatch.setattr(service.state, "get_state", lambda *_: None)
    command = dto.SettingsChange(
        section="developer_tools",
        scope="workspace",
        values={"expected_version": 0, "language_servers": [{"language": "python"}]},
    )
    with pytest.raises(AppError) as invalid:
        service.validate_target(object(), "protocol-user", "protocol-workspace", command)
    assert invalid.value.code == "invalid_tool_arguments"
    assert any(item["field"].endswith("command") for item in invalid.value.details["fields"])
