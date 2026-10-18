"""Explicit engine extensions; provider wire protocols remain independent."""

import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

import httpx

from workbench.config import PROJECT_ROOT
from workbench.contracts.errors import AppError

ENGINES = {"auto", "sglang", "vllm", "llama_cpp", "tensorrt_llm", "other"}
_TOKEN_CACHE: OrderedDict[str, tuple[float, int]] = OrderedDict()
_TOKEN_CACHE_LOCK = threading.Lock()
_MAX_TOKEN_RESPONSE_BYTES = 4 * 1024 * 1024


def is_loopback(endpoint: str) -> bool:
    return urlsplit(endpoint).hostname in {"localhost", "127.0.0.1", "::1"}


def resolve_engine(config) -> str:
    kind = config.engine_kind
    if kind not in ENGINES:
        raise AppError("unsupported_engine", "推理引擎类型不受支持。", 422)
    if kind != "auto":
        return kind
    if not is_loopback(config.endpoint):
        return "other"
    try:
        manifest = json.loads((PROJECT_ROOT / "runtime/model/deployment.json").read_text())
        expected = urlsplit(f"http://{manifest['host']}:{manifest['port']}/v1")
        actual = urlsplit(config.endpoint.rstrip("/"))
        if (
            (actual.scheme, actual.hostname, actual.port, actual.path)
            == (expected.scheme, expected.hostname, expected.port, expected.path)
            and config.model_identifier == manifest["served_model_name"]
            and manifest.get("engine") in ENGINES - {"auto"}
        ):
            return manifest["engine"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    # Old local profiles explicitly selected SGLang's usage extension before
    # engine_kind existed. A remote endpoint never inherits this inference.
    return "sglang" if config.cache_observation == "sglang" else "other"


def request_options(config) -> dict:
    engine = resolve_engine(config)
    mode, effort = config.thinking_mode, config.reasoning_effort
    if mode not in {"default", "enabled", "disabled"} or effort not in {
        None,
        "low",
        "medium",
        "high",
        "xhigh",
    }:
        raise AppError("unsupported_generation_option", "思考模式或强度不受支持。", 422)
    if mode == "default" and effort is None:
        return {}
    if mode == "disabled" and effort is not None:
        raise AppError("conflicting_generation_options", "关闭思考时不能同时指定思考强度。", 422)
    model = config.model_identifier.lower().rsplit("/", 1)[-1]
    qwen3 = model.startswith("qwen3-") or model.startswith("qwen3.")
    if (
        config.provider_type not in {"openai_compatible", "openai_chat"}
        or engine not in {"sglang", "vllm"}
        or not qwen3
        or "thinking" in model
    ):
        raise AppError(
            "unsupported_generation_option",
            "此协议、引擎或模型尚无已确认的思考参数适配；请选择默认模式。",
            422,
        )
    if effort is not None and not model.startswith("qwen3.8"):
        raise AppError("unsupported_generation_option", "此模型尚无已确认的思考强度映射。", 422)
    if effort == "high":
        raise AppError(
            "unsupported_reasoning_effort", "Qwen3.8思考强度仅支持low、medium、xhigh。", 422
        )
    kwargs = {}
    if mode != "default":
        kwargs["enable_thinking"] = mode == "enabled"
    if effort is not None:
        kwargs["reasoning_effort"] = effort
    # Keep the engine's top-level normalization and Jinja template arguments
    # consistent. SGLang and vLLM accept these confirmed Chat extensions.
    return {
        "chat_template_kwargs": kwargs,
        **({"reasoning_effort": effort} if effort is not None else {}),
    }


def validate_generation(provider_type, endpoint, model_identifier, generation):
    from workbench.modules.models.profile import Generation

    values = Generation.model_validate(generation or {}).model_dump(mode="json")
    request_options(
        SimpleNamespace(
            provider_type=provider_type,
            endpoint=endpoint,
            model_identifier=model_identifier,
            **values,
        )
    )
    return values


def reasoning_overrides(configuration: dict, effort: str | None) -> dict:
    """Freeze a task choice separately from the shared connection revision."""
    if effort is None:
        return {}
    overrides = {"thinking_mode": "enabled", "reasoning_effort": effort}
    validate_generation(
        configuration["provider_type"],
        configuration["endpoint"],
        configuration["model_identifier"],
        {**configuration.get("generation", {}), **overrides},
    )
    return overrides


def supported_reasoning_efforts(configuration: dict) -> list[str]:
    # Use the same adapter validation as execution, including the engine identity;
    # never infer support from a display name in the browser.
    supported = []
    for effort in ("low", "medium", "high", "xhigh"):
        try:
            reasoning_overrides(configuration, effort)
        except AppError:
            continue
        supported.append(effort)
    return supported


def _has_native_input(messages) -> bool:
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(block.get("type") != "text" for block in content):
            return True
        if any(
            reference.get("mode", "").startswith("native_")
            for reference in message.get("attachments") or []
        ):
            return True
    return False


def count_input_tokens(config, messages: list[dict], tools: list[dict]) -> tuple[int, str]:
    """Count the actual text Chat template locally, otherwise retain conservative reserves."""
    from workbench.modules.context.compaction import estimate
    from workbench.modules.models.providers import _request

    if (
        config.provider_type not in {"openai_compatible", "openai_chat"}
        or resolve_engine(config) != "sglang"
        or not is_loopback(config.endpoint)
        or _has_native_input(messages)
    ):
        return estimate(messages, tools), "estimated"
    if config.api_key is None and config.credential_ciphertext:
        from workbench.infrastructure.secrets import decrypt

        config = replace(config, api_key=decrypt(config.credential_ciphertext))
    _, url, headers, payload, _ = _request(config, messages, tools)
    body = json.loads(payload)
    # Use exactly the Chat input conversion and template kwargs; generation,
    # streaming, cache-observation and owned-request identifiers do not tokenize.
    body = {
        key: value
        for key, value in body.items()
        if key in {"model", "messages", "tools", "chat_template_kwargs", "reasoning_effort"}
    }
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    parsed = urlsplit(url)
    url = urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path.removesuffix("/chat/completions") + "/tokenize",
            "",
            "",
        )
    )
    digest = hashlib.sha256(url.encode() + b"\0" + payload).hexdigest()
    with _TOKEN_CACHE_LOCK:
        cached = _TOKEN_CACHE.get(digest)
        if cached and time.monotonic() - cached[0] < 30:
            _TOKEN_CACHE.move_to_end(digest)
            return cached[1], "tokenizer"
    try:
        headers = {**headers, "Accept": "application/json"}
        with httpx.Client(timeout=3, trust_env=False, follow_redirects=False) as client:
            with client.stream("POST", url, headers=headers, content=payload) as response:
                response.raise_for_status()
                raw = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    if len(raw) + len(chunk) > _MAX_TOKEN_RESPONSE_BYTES:
                        return estimate(messages, tools), "estimated"
                    raw.extend(chunk)
        data = json.loads(raw)
        count = data.get("count")
        if type(count) is not int or count < 0:
            return estimate(messages, tools), "estimated"
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return estimate(messages, tools), "estimated"
    with _TOKEN_CACHE_LOCK:
        _TOKEN_CACHE[digest] = (time.monotonic(), count)
        _TOKEN_CACHE.move_to_end(digest)
        while len(_TOKEN_CACHE) > 128:
            _TOKEN_CACHE.popitem(last=False)
    return count, "tokenizer"
