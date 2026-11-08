"""Controlled wire fixtures only; no model generation or live product acceptance."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from workbench.contracts.errors import AppError
from workbench.modules.models import engine_adapters as adapters
from workbench.modules.models import service
from workbench.modules.models.profile import Generation
from workbench.modules.models.providers import ProviderConfig, _request, abort_generation
from workbench.modules.tools.registry import definitions


def _config(**updates):
    config = ProviderConfig(
        provider_type="openai_compatible",
        endpoint="http://127.0.0.1:30000/v1",
        model_identifier="Qwen3.8-Flash-Next",
        engine_kind="sglang",
    )
    return replace(config, **updates)


def _messages():
    return [{"role": "user", "content": Path("README.md").read_text()[:200]}]


def _body(config):
    return json.loads(_request(config, _messages(), definitions(["read_file"]))[3])


def test_old_profiles_and_requests_keep_default_template_behavior():
    generation = Generation.model_validate({"cache_observation": "standard"})
    assert generation.engine_kind == "auto"
    assert generation.thinking_mode == "default"
    assert generation.reasoning_effort is None
    body = _body(_config())
    assert "chat_template_kwargs" not in body and "reasoning_effort" not in body


def test_task_effort_overrides_are_validated_without_mutating_connection():
    configuration = {
        "provider_type": "openai_compatible",
        "endpoint": "http://127.0.0.1:30000/v1",
        "model_identifier": "Qwen3.8-Flash-Next",
        "generation": Generation(engine_kind="sglang", thinking_mode="disabled").model_dump(),
    }
    assert adapters.reasoning_overrides(configuration, None) == {}
    assert adapters.reasoning_overrides(configuration, "disabled") == {
        "thinking_mode": "disabled",
        "reasoning_effort": None,
    }
    assert adapters.reasoning_overrides(configuration, "low") == {
        "thinking_mode": "enabled",
        "reasoning_effort": "low",
    }
    assert configuration["generation"]["thinking_mode"] == "disabled"
    assert configuration["generation"]["reasoning_effort"] is None
    assert adapters.supported_reasoning_efforts(configuration) == [
        "disabled",
        "low",
        "medium",
        "xhigh",
    ]
    with pytest.raises(AppError, match="仅支持"):
        adapters.reasoning_overrides(configuration, "high")
    configuration["generation"]["engine_kind"] = "llama_cpp"
    assert adapters.supported_reasoning_efforts(configuration) == []
    with pytest.raises(AppError) as unsupported:
        adapters.reasoning_overrides(configuration, "low")
    assert unsupported.value.code == "unsupported_generation_option"


@pytest.mark.parametrize("engine", ["sglang", "vllm"])
@pytest.mark.parametrize("effort", ["low", "medium", "xhigh"])
def test_qwen38_chat_extensions_use_matching_template_and_effort(engine, effort):
    body = _body(_config(engine_kind=engine, thinking_mode="enabled", reasoning_effort=effort))
    assert body["chat_template_kwargs"] == {"enable_thinking": True, "reasoning_effort": effort}
    assert body["reasoning_effort"] == effort
    assert _body(_config(engine_kind=engine, thinking_mode="disabled"))["chat_template_kwargs"] == {
        "enable_thinking": False
    }


@pytest.mark.parametrize(
    "updates",
    [
        {"reasoning_effort": "high"},
        {"thinking_mode": "disabled", "reasoning_effort": "low"},
        {"provider_type": "openai_responses", "thinking_mode": "enabled"},
        {"provider_type": "anthropic", "thinking_mode": "enabled"},
        {"engine_kind": "llama_cpp", "thinking_mode": "enabled"},
        {"engine_kind": "tensorrt_llm", "reasoning_effort": "low"},
        {"model_identifier": "unknown-template", "thinking_mode": "enabled"},
        {"model_identifier": "Qwen3-8B", "reasoning_effort": "low"},
    ],
)
def test_unconfirmed_or_conflicting_nondefault_options_are_rejected(updates):
    with pytest.raises(AppError) as failed:
        _body(_config(**updates))
    assert failed.value.status == 422
    assert failed.value.code in {
        "unsupported_generation_option",
        "unsupported_reasoning_effort",
        "conflicting_generation_options",
    }


def test_auto_engine_uses_actual_managed_identity_but_never_guesses_remote(tmp_path, monkeypatch):
    # Exercise the declared identity without requiring a developer's private deployment.
    manifest = {
        "served_model_name": "Qwen3.8-Flash-Next",
        "host": "127.0.0.1",
        "port": 30000,
        "engine": "sglang",
    }
    directory = tmp_path / "runtime/model"
    directory.mkdir(parents=True)
    (directory / "deployment.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(adapters, "PROJECT_ROOT", tmp_path)
    config = _config(
        engine_kind="auto",
        model_identifier=manifest["served_model_name"],
        endpoint=f"http://{manifest['host']}:{manifest['port']}/v1",
    )
    assert adapters.resolve_engine(config) == manifest["engine"]
    assert adapters.resolve_engine(replace(config, model_identifier="unmatched-model")) == "other"
    assert (
        adapters.resolve_engine(
            replace(config, endpoint="https://example.com/v1", cache_observation="sglang")
        )
        == "other"
    )
    assert (
        adapters.resolve_engine(
            replace(config, endpoint="http://127.0.0.1:30001/v1", cache_observation="sglang")
        )
        == "sglang"
    )


def test_owned_request_id_is_independent_of_usage_cache_setting():
    body = _body(_config(cache_observation="standard", request_id="owned-request"))
    assert body["rid"] == "owned-request"
    assert "return_cached_tokens_details" not in body
    assert "rid" not in _body(_config(engine_kind="vllm", request_id="owned-request"))


def test_abort_targets_only_owned_sglang_request_on_loopback(monkeypatch):
    observed = []
    original = httpx.AsyncClient

    def handler(request):
        observed.append(request)
        return httpx.Response(200, json={})

    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    asyncio.run(abort_generation(_config(request_id="owned-request")))
    assert len(observed) == 1
    assert observed[0].url.path == "/abort_request"
    assert json.loads(observed[0].content) == {"rid": "owned-request", "abort_all": False}
    asyncio.run(
        abort_generation(_config(request_id="owned-request", endpoint="https://example.com/v1"))
    )
    asyncio.run(abort_generation(_config(request_id="owned-request", engine_kind="vllm")))
    assert len(observed) == 1


def _token_client(monkeypatch, handler):
    original = httpx.Client

    def client(**kwargs):
        assert kwargs == {"timeout": 3, "trust_env": False, "follow_redirects": False}
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    adapters._TOKEN_CACHE.clear()


def test_tokenizer_reuses_chat_conversion_and_keeps_only_input_fields(monkeypatch):
    observed = []

    def handler(request):
        observed.append(request)
        return httpx.Response(200, json={"tokens": [1, 2], "count": 2, "max_model_len": 32768})

    _token_client(monkeypatch, handler)
    config = _config(
        thinking_mode="enabled",
        reasoning_effort="low",
        temperature=0.7,
        request_id="owned-request",
        cache_observation="sglang",
    )
    tools = definitions(["read_file"])
    actual_chat = _body(config)
    assert adapters.count_input_tokens(config, _messages(), tools) == (2, "tokenizer")
    assert adapters.count_input_tokens(config, _messages(), tools) == (2, "tokenizer")
    assert len(observed) == 1
    assert observed[0].url.path == "/v1/tokenize"
    payload = json.loads(observed[0].content)
    assert payload == {
        key: actual_chat[key]
        for key in ("model", "messages", "tools", "chat_template_kwargs", "reasoning_effort")
    }
    # Template choices must be part of the digest, not reused across modes.
    adapters.count_input_tokens(replace(config, reasoning_effort="medium"), _messages(), tools)
    assert len(observed) == 2


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(302, headers={"Location": "https://example.com"}),
        httpx.Response(200, json={"count": True}),
        httpx.Response(200, json={"count": -1}),
        httpx.Response(200, content=b"not JSON"),
    ],
)
def test_tokenizer_failure_is_explicitly_estimated(monkeypatch, response):
    from workbench.modules.context.compaction import estimate

    _token_client(monkeypatch, lambda _: response)
    messages, tools = _messages(), definitions(["read_file"])
    assert adapters.count_input_tokens(_config(), messages, tools) == (
        estimate(messages, tools),
        "estimated",
    )


def test_multimodal_and_unsupported_engines_keep_native_reserves_without_network(monkeypatch):
    from workbench.modules.context.compaction import estimate

    def forbidden(_):
        pytest.fail("Unsupported and native inputs must not use the text tokenizer route")

    _token_client(monkeypatch, forbidden)
    messages = _messages()
    messages[0]["attachments"] = [{"mode": "native_document"}]
    assert adapters.count_input_tokens(_config(), messages, []) == (
        estimate(messages, []),
        "estimated",
    )
    assert (
        adapters.count_input_tokens(_config(engine_kind="vllm"), _messages(), [])[1] == "estimated"
    )
    assert (
        adapters.count_input_tokens(_config(endpoint="https://example.com/v1"), _messages(), [])[1]
        == "estimated"
    )


def test_tokenizer_response_and_cache_are_bounded(monkeypatch):
    _token_client(monkeypatch, lambda _: httpx.Response(200, json={"count": 2}))
    config = _config()
    source = _messages()[0]["content"]
    for index in range(129):
        messages = [{"role": "user", "content": source + str(index)}]
        assert adapters.count_input_tokens(config, messages, [])[1] == "tokenizer"
    assert len(adapters._TOKEN_CACHE) == 128
    assert all(len(key) == 64 for key in adapters._TOKEN_CACHE)
    monkeypatch.setattr(adapters, "_MAX_TOKEN_RESPONSE_BYTES", 4)
    adapters._TOKEN_CACHE.clear()
    assert adapters.count_input_tokens(config, _messages(), [])[1] == "estimated"
    assert not adapters._TOKEN_CACHE


def test_save_rejects_unsupported_settings_before_mutation(monkeypatch):
    values = {
        "name": "protocol fixture",
        "provider_type": "openai_compatible",
        "endpoint": "http://127.0.0.1:30000/v1",
        "model_identifier": "Qwen3.8-Flash-Next",
        "allow_remote": False,
        "context_window": 32768,
        "generation": {"engine_kind": "sglang", "reasoning_effort": "high"},
    }
    db = MagicMock()
    with pytest.raises(AppError, match="仅支持"):
        service.create(db, "protocol-user", dict(values))
    db.add.assert_not_called()
    db.flush.assert_not_called()
    row = SimpleNamespace(version=1, generation={})
    monkeypatch.setattr(service, "get", lambda *_, **__: row)
    with pytest.raises(AppError, match="仅支持"):
        service.update(
            db, "protocol-user", "protocol-connection", {**values, "expected_version": 1}
        )
    assert row.version == 1 and row.generation == {}
    db.flush.assert_not_called()
