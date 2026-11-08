"""Offline protocol boundaries; real model/cache/UI evidence is recorded separately."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from workbench.config import PROJECT_ROOT
from workbench.contracts.errors import AppError
from workbench.modules.context.compaction import estimate
from workbench.modules.models import managed, providers
from workbench.modules.tools.registry import definitions

README = (PROJECT_ROOT / "README.md").read_text()


@pytest.fixture
def local_config(tmp_path, monkeypatch):
    manifest = {
        "engine": "sglang",
        "engine_version": "0.5.21",
        "engine_source_commit": "e00930c5489053f26d86b179cee0d087f846acbb",
        "host": "127.0.0.1",
        "port": 30000,
        "model_path": "models/checkpoint",
        "served_model_name": "Qwen3.8-Flash-Next",
    }
    path = tmp_path / "runtime/model/deployment.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(managed, "PROJECT_ROOT", tmp_path)
    managed._CONTRACTS.clear()
    return providers.ProviderConfig(
        provider_type="openai_compatible",
        endpoint="http://127.0.0.1:30000/v1",
        model_identifier=manifest["served_model_name"],
        engine_kind="sglang",
        cache_observation="sglang",
        thinking_mode="enabled",
        reasoning_effort="medium",
        local_contract=managed.SGLANG_CONTRACT,
        run_id="protocol-run",
    )


@pytest.mark.parametrize("version", ["0.5.21", "0.5.20", None])
def test_local_semantics_require_live_version_and_exact_deployment(
    local_config, monkeypatch, version
):
    requests = []
    original = httpx.Client

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "version": version,
                "served_model_name": local_config.model_identifier,
                "model_path": str(managed.PROJECT_ROOT / "models/checkpoint"),
            },
        )

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    expected = managed.SGLANG_CONTRACT if version == "0.5.21" else None
    assert managed.verified_contract(local_config) == expected
    assert managed.verified_contract(local_config) == expected
    assert len(requests) == 1 and requests[0].method == "GET"
    assert requests[0].url.path == "/server_info"
    for changes in (
        {"endpoint": "https://example.com/v1"},
        {"endpoint": "http://127.0.0.1:30001/v1"},
        {"model_identifier": "other-model"},
        {"engine_kind": "vllm"},
    ):
        assert managed.verified_contract(replace(local_config, **changes)) is None
    assert len(requests) == 1


async def frames(*, details=None, finish=True, usage=True, done=True):
    yield (
        "",
        json.dumps(
            {
                "choices": [
                    {"delta": {"content": README[:20]}, "finish_reason": "stop" if finish else None}
                ]
            }
        ),
    )
    if usage:
        value = {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}
        if details is not None:
            value["sglext"] = {"cached_tokens_details": details}
        yield "", json.dumps(value)
    if done:
        yield "", "[DONE]"


def collect_usage(**kwargs):
    async def collect():
        return [
            event.data
            async for event in providers._chat(
                frames(**kwargs.pop("frames", {})), {}, "sglang", **kwargs
            )
            if event.kind == "usage"
        ]

    return asyncio.run(collect())


def test_zero_missing_details_is_known_only_for_verified_complete_local_usage():
    assert collect_usage()[0]["cache_read_input_tokens"] is None
    usage = collect_usage(zero_cache_on_missing=True)[0]
    assert usage["cache_read_input_tokens"] == 0
    assert usage["cache_source"] == "sglang.0.5.21.zero_without_details"
    assert "cache_details" not in usage
    assert collect_usage(zero_cache_on_missing=True, frames={"usage": False}) == []
    for incomplete in ({"done": False}, {"finish": False}):
        with pytest.raises(AppError):
            collect_usage(zero_cache_on_missing=True, frames=incomplete)


def test_observed_or_malformed_cache_details_are_not_replaced_with_zero():
    assert (
        collect_usage(zero_cache_on_missing=True, frames={"details": {"device": 64, "host": 0}})[0][
            "cache_read_input_tokens"
        ]
        == 64
    )
    assert (
        collect_usage(zero_cache_on_missing=True, frames={"details": {"device": True, "host": 0}})[
            0
        ]["cache_read_input_tokens"]
        is None
    )


def chain(config, tools):
    messages = [
        {"role": "system", "content": README.splitlines()[0]},
        {"role": "user", "content": "读取项目README"},
    ]
    arguments = '{\n  "path": "README.md"\n}'
    for index in range(2):
        providers.prepare_chat_replay(config, messages, tools)
        messages += [
            {
                "role": "assistant",
                "content": "读取文件。",
                "_run_id": config.run_id,
                "_native_replay": {"context": config.replay_context, "reasoning": README[:200]},
                "tool_calls": [
                    {
                        "id": f"read-{index}",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": arguments,
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": f"read-{index}", "content": README[:100]},
        ]
    return messages


def test_current_chain_replays_exact_native_fields_and_counts_their_budget(local_config):
    tools = definitions(["read_file"])
    messages = chain(local_config, tools)
    prepared = providers.prepare_chat_replay(local_config, messages, tools)
    assert [m.get("reasoning_content") for m in prepared if m["role"] == "assistant"] == [
        README[:200],
        README[:200],
    ]
    assert estimate(prepared, tools) > estimate(messages, tools)
    body = json.loads(providers._request(local_config, prepared, tools)[3])
    assert body["messages"][2]["tool_calls"] == messages[2]["tool_calls"]
    assert body["messages"][2]["reasoning_content"] == README[:200]
    assert local_config.replay_message_count == 2
    assert local_config.replay_characters == 400
    assert all("_native_replay" not in message for message in body["messages"])
    assert all("reasoning_content" not in message for message in messages)


def test_persisted_runtime_observations_preserve_the_verified_tool_chain(local_config):
    from workbench.infrastructure.database import now
    from workbench.modules.runs.budget import generation_budget
    from workbench.modules.runs.domain import Run

    tools = definitions(["read_file"])
    run = Run(created_at=now(), config_snapshot={})
    messages = [
        {"role": "system", "content": README[:40]},
        {"role": "user", "content": "读取项目README"},
    ]
    for index in range(2):
        messages.append(generation_budget(run, index).notice(local_config.run_id))
        providers.prepare_chat_replay(local_config, messages, tools)
        messages += [
            {
                "role": "assistant",
                "content": "读取文件。",
                "_run_id": local_config.run_id,
                "_native_replay": {
                    "context": local_config.replay_context,
                    "reasoning": README[:200],
                },
                "tool_calls": [
                    {
                        "id": f"read-{index}",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path":"README.md"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": f"read-{index}", "content": README[:100]},
        ]
    messages.append(generation_budget(run, 2).notice(local_config.run_id))
    prepared = providers.prepare_chat_replay(local_config, messages, tools)
    assert [m.get("reasoning_content") for m in prepared if m["role"] == "assistant"] == [
        README[:200],
        README[:200],
    ]
    # Budget observations remain part of the causal hash. Changing an old one
    # cannot preserve reasoning generated from different inputs.
    messages[2] = {**messages[2], "content": messages[2]["content"] + "changed"}
    prepared = providers.prepare_chat_replay(local_config, messages, tools)
    assert not any(m.get("reasoning_content") for m in prepared)


@pytest.mark.parametrize("change", ["system", "source", "tools", "new_user", "run", "remote"])
def test_live_source_changes_and_new_turns_do_not_reintroduce_old_reasoning(local_config, change):
    tools = definitions(["read_file"])
    messages = chain(local_config, tools)
    if change == "system":
        messages[0] = {"role": "system", "content": "规则已经更新"}
    elif change == "source":
        messages[3] = {**messages[3], "content": "原来源已经撤权，旧正文不再可读。"}
    elif change == "tools":
        tools = definitions(["read_file", "list_files"])
    elif change == "new_user":
        messages.append({"role": "user", "content": "新的要求"})
    elif change == "run":
        local_config = replace(local_config, run_id="new-protocol-run")
    else:
        local_config = replace(local_config, endpoint="https://example.com/v1")
    prepared = providers.prepare_chat_replay(local_config, messages, tools)
    # A changed earlier tool source invalidates dependent reasoning, while the
    # first thought (which predates reading that source) can remain legitimate.
    selected = prepared[4:] if change == "source" else prepared
    assert all("reasoning_content" not in message for message in selected)


def test_native_tool_json_is_validated_without_reformatting():
    raw = '{\n "path": "README.md"\n}'
    event = providers._tool_event(
        {"id": "read", "name": "read_file", "arguments": raw},
        {"read_file": {}},
        "openai_compatible",
        0,
    )
    assert event.data["arguments"] == {"path": "README.md"}
    assert event.data["_arguments_json"] == raw
