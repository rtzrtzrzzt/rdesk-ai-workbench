"""Offline state/protocol boundaries, using public README text only.

These checks do not create business records or call a model/database. Controlled
provider events exercise the adapter boundary, not real generation acceptance.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import pytest
from workbench.contracts.errors import AppError
from workbench.modules import conversation_naming as naming
from workbench.modules.models import admission, providers
from workbench.modules.models.engine_adapters import request_options

README = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
TITLE = README.splitlines()[0].removeprefix("# ")
SOURCE_ID = str(uuid5(NAMESPACE_URL, "README.md:naming-boundary"))


def configuration():
    return {
        "provider_type": "openai_compatible",
        "endpoint": "http://127.0.0.1:30000/v1",
        "model_identifier": "Qwen3.8-Flash-Next-NVFP4",
        "context_window": 131072,
        "generation": {
            "engine_kind": "sglang",
            "thinking_mode": "enabled",
            "reasoning_effort": "medium",
            "timeout_seconds": 180,
        },
    }


def thread_state(source="automatic", **changes):
    return SimpleNamespace(
        **{
            "title": "新对话",
            "title_source": source,
            "title_naming": {"state": "awaiting_input"} if source == "automatic" else {},
            "parent_thread_id": None,
            "archived": False,
            "deleting": False,
            "version": 1,
            **changes,
        }
    )


def test_first_input_has_immediate_title_and_stable_source():
    thread = thread_state()
    source = SimpleNamespace(id=SOURCE_ID, input=README, parent_run_id=None)
    naming.initialize(thread, source)
    assert thread.title == " ".join(README.split())[:40]
    assert thread.title_naming["source_run_id"] == SOURCE_ID
    assert thread.version == 2
    before = deepcopy(vars(thread))
    naming.initialize(thread, SimpleNamespace(id="later", input=README, parent_run_id=None))
    assert vars(thread) == before


@pytest.mark.parametrize("source", ["legacy", "explicit", "manual", "branch", "generated"])
def test_existing_and_explicit_titles_are_preserved_even_when_called_new_conversation(source):
    thread = thread_state(source)
    before = deepcopy(vars(thread))
    naming.initialize(thread, SimpleNamespace(id=SOURCE_ID, input=README, parent_run_id=None))
    assert vars(thread) == before


@pytest.mark.parametrize("change", [{"parent_thread_id": SOURCE_ID}, {"title_naming": {}}])
def test_branch_and_unenrolled_threads_are_never_initialized(change):
    thread = thread_state(**change)
    naming.initialize(thread, SimpleNamespace(id=SOURCE_ID, input=README, parent_run_id=None))
    assert thread.title == "新对话"


@pytest.mark.parametrize(
    "change",
    [
        {"archived": True},
        {"deleting": True},
        {"title_source": "manual"},
        {"parent_thread_id": SOURCE_ID},
        {"title_naming": {"state": "cancelled", "run_id": SOURCE_ID}},
        {"title_naming": {"state": "running", "run_id": "different"}},
    ],
)
def test_late_result_cannot_override_user_lifecycle_or_another_task(change):
    thread = thread_state(title_naming={"state": "running", "run_id": SOURCE_ID})
    assert naming.eligible(thread, SOURCE_ID)
    for key, value in change.items():
        setattr(thread, key, value)
    assert not naming.eligible(thread, SOURCE_ID)


def test_title_request_keeps_local_frozen_model_and_disables_thinking():
    frozen = configuration()
    before = deepcopy(frozen)
    config = naming.title_configuration(frozen)
    assert frozen == before
    assert config.model_identifier == frozen["model_identifier"]
    assert config.context_window == 131072
    assert config.max_output_tokens == 160
    assert config.timeout_seconds == 25
    assert config.thinking_mode == "disabled"
    assert request_options(config) == {"chat_template_kwargs": {"enable_thinking": False}}
    frozen["endpoint"] = "https://example.com/v1"
    with pytest.raises(AppError, match="外部"):
        naming.title_configuration(frozen)


def test_input_is_bounded_json_data_not_an_interpolated_system_instruction():
    messages = naming.title_messages(README, README)
    assert len(messages) == 2
    assert json.loads(messages[1]["content"]) == {
        "user": README[:4000],
        "assistant": README[:2000],
    }
    assert README[:100] not in messages[0]["content"]


@pytest.mark.parametrize(
    "output",
    [
        "",
        TITLE,
        json.dumps({"title": TITLE, "extra": True}),
        json.dumps({"title": ""}),
        json.dumps({"title": " " + TITLE}),
        json.dumps({"title": TITLE + "\n" + TITLE}),
        json.dumps({"title": TITLE + "\u202e"}),
        json.dumps({"title": "x" * 41}),
        json.dumps({"title": "# " + TITLE}),
        json.dumps({"title": None}),
        '{"title":"first","title":"second"}',
        "```json\n" + json.dumps({"title": TITLE}) + "\n```",
    ],
)
def test_explanations_multiline_controls_and_invalid_structures_are_rejected(output):
    with pytest.raises(AppError) as caught:
        naming.parse_title(output)
    assert caught.value.code == "naming_output_invalid"


def test_valid_title_uses_public_readme_heading():
    assert naming.parse_title(json.dumps({"title": TITLE}, ensure_ascii=False)) == TITLE


def run_protocol(monkeypatch, events):
    calls = []
    config = naming.title_configuration(configuration())

    @asynccontextmanager
    async def permit(run_id):
        calls.append("acquired")
        yield
        calls.append("released")

    def begin(run_id):
        assert calls == ["acquired"]
        return config, naming.title_messages(README, README), "offline-protocol-attempt"

    async def stream(received, messages, tools):
        assert received is config and tools == []
        for event in events:
            yield providers.ProviderEvent(*event)

    async def abort(received):
        calls.append("abort")

    monkeypatch.setattr(admission, "model_permit", permit)
    monkeypatch.setattr(naming, "_begin", begin)
    monkeypatch.setattr(naming, "_finish", lambda *args, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(providers, "stream_model", stream)
    monkeypatch.setattr(providers, "abort_generation", abort)
    return calls, config


def test_protocol_commits_only_complete_tool_free_title_under_permit(monkeypatch):
    calls, config = run_protocol(
        monkeypatch,
        [
            ("text_delta", {"delta": json.dumps({"title": TITLE})}),
            ("done", {"finish_reason": "stop"}),
        ],
    )
    assert asyncio.run(naming.process(SOURCE_ID)) == {}
    assert calls == ["acquired", {"title": TITLE}, "released"]
    assert config.api_key is None


@pytest.mark.parametrize(
    "events,code",
    [
        ([("text_delta", {"delta": json.dumps({"title": TITLE})})], "naming_incomplete"),
        ([("tool_call", {})], "naming_output_invalid"),
        ([("text_delta", {"delta": "x" * 1025})], "naming_output_invalid"),
        ([("done", {"finish_reason": "length"})], "naming_incomplete"),
    ],
)
def test_failed_protocol_records_failure_and_aborts_without_committing_title(
    monkeypatch, events, code
):
    calls, config = run_protocol(monkeypatch, events)
    with pytest.raises(AppError) as caught:
        asyncio.run(naming.process(SOURCE_ID))
    assert caught.value.code == code
    assert calls[0:2] == ["acquired", "abort"]
    assert calls[2]["error"].code == code
    assert config.api_key is None
