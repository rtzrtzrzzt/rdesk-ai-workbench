"""Controlled protocol/state fixtures; these are not live model or product acceptance."""

import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from workbench.contracts.errors import AppError
from workbench.modules.models import providers
from workbench.modules.tools import extensions
from workbench.modules.tools.registry import CORE_TOOL_NAMES, DiscoverTools, definitions


def test_discovery_defaults_to_add_and_describes_next_request():
    assert DiscoverTools(names=["read_task_history"]).action == "add"
    description = definitions(["discover_tools"])[0]["function"]["description"]
    assert "下一轮" in description and "本轮不能" in description


@pytest.fixture
def discovery_state(monkeypatch):
    # Only the storage boundary is replaced; registry permissions and discover logic run normally.
    run = SimpleNamespace(
        user_id="protocol-user",
        workspace_id="protocol-workspace",
        config_snapshot={"tool_names": [*CORE_TOOL_NAMES, "web_search"]},
    )
    db = MagicMock()
    db.scalar.return_value = run
    context = MagicMock()
    context.__enter__.return_value = db
    monkeypatch.setattr(extensions, "Session", lambda _: context)
    monkeypatch.setattr(extensions, "engine", lambda: None)
    monkeypatch.setattr(extensions, "emit", MagicMock())
    monkeypatch.setattr("workbench.modules.runs.ownership.assert_owned", lambda *_: None)
    connection = SimpleNamespace(enabled=True)
    monkeypatch.setattr(extensions.search, "get_connection", lambda *_: connection)
    return run, connection


def test_loading_history_preserves_web_tools_and_explicit_remove_works(discovery_state):
    run, _ = discovery_state
    loaded = extensions.discover("protocol-run", ["read_task_history"])["loaded"]
    assert "web_search" in loaded and "read_task_history" in loaded
    assert run.config_snapshot["tool_names"] == loaded
    loaded = extensions.discover("protocol-run", ["web_search"], "remove")["loaded"]
    assert "web_search" not in loaded and "read_task_history" in loaded
    assert extensions.discover("protocol-run", [], "replace")["loaded"] == CORE_TOOL_NAMES


def test_catalog_does_not_change_loaded_tools(discovery_state):
    run, _ = discovery_state
    before = list(run.config_snapshot["tool_names"])
    result = extensions.discover("protocol-run", [])
    assert "catalog" in result
    assert run.config_snapshot["tool_names"] == before


def test_targeted_discovery_loads_the_advertised_tool_for_next_request(discovery_state):
    run, _ = discovery_state
    result = extensions.discover("protocol-run", [], query="read_notebook")
    assert result["auto_loaded"] == ["read_notebook"]
    assert "read_notebook" in result["loaded"]
    assert run.config_snapshot["tool_names"] == result["loaded"]
    repeated = extensions.discover("protocol-run", [], query="read_notebook")
    assert repeated["auto_loaded"] == []
    assert "read_notebook" in repeated["loaded"]


def test_targeted_discovery_bounds_loading_and_skips_unavailable_search(discovery_state):
    _, connection = discovery_state
    result = extensions.discover("protocol-run", [], module="filesystem", limit=100)
    assert 0 < len(result["auto_loaded"]) <= 6
    connection.enabled = False
    result = extensions.discover("protocol-run", [], module="search")
    assert result["auto_loaded"] == []


def test_discovery_respects_allowed_tools_and_search_enabled(discovery_state):
    run, connection = discovery_state
    run.config_snapshot["allowed_tools"] = ["discover_tools", "read_task_history"]
    with pytest.raises(AppError) as denied:
        extensions.discover("protocol-run", ["web_search"])
    assert denied.value.code == "tool_scope_denied"
    result = extensions.discover("protocol-run", ["read_task_history"])
    assert set(result["loaded"]) == {"discover_tools", "read_task_history"}
    del run.config_snapshot["allowed_tools"]
    connection.enabled = False
    with pytest.raises(AppError) as disabled:
        extensions.discover("protocol-run", ["web_search"])
    assert disabled.value.code == "search_disabled"
    assert (
        "web_search" not in extensions.discover("protocol-run", ["web_search"], "remove")["loaded"]
    )


def _call(name="read_file", identifier="call-1"):
    return {"id": identifier, "name": name, "arguments": {"path": "README.md"}}


@pytest.mark.parametrize(
    ("call", "reason"),
    [
        (_call(identifier=None), "missing_call_id"),
        (_call(identifier=12), "invalid_call_id"),
        (_call(name=[]), "invalid_tool_name"),
        (_call(name="web_search"), "unknown_tool_name"),
    ],
)
def test_identity_errors_are_specific_and_bounded(call, reason):
    with pytest.raises(AppError) as failed:
        providers._complete_calls([call], {"read_file": {}}, "openai_compatible")
    error = failed.value
    assert error.code == "provider_tool_identity" and error.kind == "provider"
    assert error.details["reason"] == reason
    assert error.details["call_index"] == 0
    assert error.details["provider"] == "openai_compatible"
    assert error.details["allowed_tool_count"] == 1
    assert (
        error.details["allowed_tools_fingerprint"]
        == hashlib.sha256(providers._json(["read_file"]).encode()).hexdigest()
    )
    assert "README.md" not in json.dumps(error.details)


def test_unknown_name_is_bounded_without_copying_arguments():
    call = _call(name="unknown\n" * 200)
    call["arguments"] = {"private": "must not appear"}
    with pytest.raises(AppError) as failed:
        providers._complete_calls([call], {}, "openai_compatible")
    details = failed.value.details
    assert len(details["tool_name"]) == 128
    assert details["tool_name_truncated"] is True
    assert "\n" not in details["tool_name"]
    assert "must not appear" not in json.dumps(details)


def test_duplicate_id_is_rejected_at_its_batch_position():
    with pytest.raises(AppError) as failed:
        providers._complete_calls([_call(), _call()], {"read_file": {}}, "openai_compatible")
    assert failed.value.details["reason"] == "duplicate_call_id"
    assert failed.value.details["call_index"] == 1


async def _frames(values):
    for value in values:
        yield "", json.dumps(value) if isinstance(value, dict) else value


def _stream(provider, calls):
    if provider == "openai_compatible":
        parts = [
            {
                "index": index,
                "id": call["id"],
                "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])},
            }
            for index, call in enumerate(calls)
        ]
        frames = [
            {"choices": [{"delta": {"tool_calls": parts}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}},
            "[DONE]",
        ]
        return providers._chat(_frames(frames), {"read_file": {}})
    if provider == "openai_responses":
        output = [
            {
                "type": "function_call",
                "call_id": call["id"],
                "name": call["name"],
                "arguments": json.dumps(call["arguments"]),
            }
            for call in calls
        ]
        frames = [
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": output,
                    "usage": {"input_tokens": 7, "output_tokens": 3},
                },
            }
        ]
        return providers._responses(_frames(frames), {"read_file": {}})
    frames = [
        {
            "type": "message_start",
            "message": {
                "usage": {
                    "input_tokens": 7,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                }
            },
        }
    ]
    for index, call in enumerate(calls):
        frames.extend(
            [
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["name"],
                        "input": call["arguments"],
                    },
                },
                {"type": "content_block_stop", "index": index},
            ]
        )
    frames.extend(
        [
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 3},
            },
            {"type": "message_stop"},
        ]
    )
    return providers._anthropic(_frames(frames), {"read_file": {}})


@pytest.mark.parametrize("provider", ["openai_compatible", "openai_responses", "anthropic"])
@pytest.mark.parametrize("invalid", [True, False])
def test_usage_is_retained_and_no_partial_tool_batch_is_exposed(provider, invalid):
    async def consume():
        observed = []
        calls = [_call()]
        if invalid:
            calls.append(_call("web_search", "call-2"))
        try:
            async for event in _stream(provider, calls):
                observed.append(event)
        except AppError as error:
            assert invalid
            assert error.details["reason"] == "unknown_tool_name"
            assert error.details["call_index"] == 1
            assert error.details["provider"] == provider
        else:
            assert not invalid
        usages = [event for event in observed if event.kind == "usage"]
        assert len(usages) == 1
        assert usages[0].data["input_tokens_total"] == 7
        assert usages[0].data["output_tokens_total"] == 3
        if invalid:
            assert not any(
                event.kind in {"tool_call", "provider_output", "done"} for event in observed
            )
        else:
            assert sum(event.kind == "tool_call" for event in observed) == 1
            assert observed[-1].kind == "done"

    asyncio.run(consume())
