"""Bounded, non-retrying HTTP adapters; credentials are injected by the model service."""

from __future__ import annotations

import asyncio
import codecs
import json
import math
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from workbench.contracts.errors import AppError

MAX_STREAM_BYTES = 32 * 1024 * 1024
MAX_EVENT_BYTES = 2 * 1024 * 1024
MAX_ARGUMENT_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_ITEMS = 128
PROVIDERS = {
    "openai_responses": "openai_responses",
    "openai_compatible": "openai_compatible",
    "openai_chat": "openai_compatible",
    "anthropic": "anthropic",
    "anthropic_messages": "anthropic",
}


@dataclass
class ProviderConfig:
    provider_type: str
    endpoint: str
    model_identifier: str
    api_key: str | None = None
    credential_ciphertext: str | None = None
    max_output_tokens: int = 4096
    timeout_seconds: float = 180
    temperature: float | None = None
    cache_observation: str = "standard"


@dataclass
class ProviderEvent:
    kind: str
    data: dict


def _error(code: str, message: str, *, retryable: bool = False) -> AppError:
    return AppError(
        code,
        message,
        status=502,
        kind="transient" if retryable else "provider",
        retryable=retryable,
    )


def _protocol(message: str = "模型服务返回了不完整或无效的流协议。") -> AppError:
    return _error("provider_protocol_error", message)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _load(value: str) -> Any:
    # Python otherwise accepts non-JSON NaN/Infinity, which tool execution must reject.
    def invalid_constant(_: str):
        raise ValueError("Non-finite JSON value")

    return json.loads(value, parse_constant=invalid_constant)


def _object(value: Any) -> dict:
    if not isinstance(value, dict):
        raise _protocol()
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise _protocol()
    return value


def _event_type(event: str, value: dict) -> str:
    kind = value.get("type", event)
    if not isinstance(kind, str) or not kind or (event and event != kind):
        raise _protocol("模型 SSE 事件名与 JSON 类型不一致。")
    return kind


def _index(value: Any) -> int:
    if type(value) is not int or not 0 <= value < MAX_ITEMS:
        raise _protocol("模型服务的内容块或工具调用索引超出限制。")
    return value


def _bounded(value: Any, limit: int = MAX_EVENT_BYTES) -> None:
    if len(_json(value).encode()) > limit:
        raise _error("provider_buffer_limit", "模型流或工具参数超过接收上限。")


def _append(previous: str, delta: Any, limit: int = MAX_ARGUMENT_BYTES) -> str:
    value = previous + _text(delta)
    if len(value.encode()) > limit:
        raise _error("provider_buffer_limit", "模型流或工具参数超过接收上限。")
    return value


def _arguments(value: Any) -> dict:
    if isinstance(value, str):
        if len(value.encode()) > MAX_ARGUMENT_BYTES:
            raise _error("provider_buffer_limit", "工具参数超过接收上限。")
        try:
            value = _load(value)
        except (ValueError, RecursionError):
            raise _error(
                "provider_tool_arguments", "模型返回的完整工具参数不是有效 JSON。"
            ) from None
    if not isinstance(value, dict):
        raise _error("provider_tool_arguments", "模型工具参数必须是 JSON 对象。")
    _bounded(value, MAX_ARGUMENT_BYTES)
    return value


def _content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(
        isinstance(x, dict) and x.get("type") == "text" and isinstance(x.get("text"), str)
        for x in value
    ):
        return "\n".join(x["text"] for x in value)
    raise _error(
        "provider_unsupported_content", "当前适配器仅接收文本和工具消息；附件需先通过输入计划。"
    )


def _input_content(value: Any, provider: str, role: str) -> str | list[dict]:
    if not isinstance(value, list):
        return _content(value)
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise _protocol("输入内容块无效。")
        if item.get("type") == "text":
            text = _text(item.get("text"))
            result.append(
                {"type": "input_text" if provider == "openai_responses" else "text", "text": text}
            )
        elif (
            item.get("type") in {"native_document", "native_audio", "native_video"}
            and role == "user"
        ):
            from workbench.modules.models.provider_inputs import encode_extra

            result.append(encode_extra(item, provider))
        elif item.get("type") == "image" and role == "user":
            media, data = item.get("media_type"), item.get("data")
            if (
                media not in {"image/png", "image/jpeg", "image/webp"}
                or not isinstance(data, str)
                or len(data) > 7 * 1024**2
            ):
                raise _error("provider_image_input", "图像内容块格式或大小不受支持。")
            url = f"data:{media};base64,{data}"
            if provider == "openai_compatible":
                result.append({"type": "image_url", "image_url": {"url": url}})
            elif provider == "openai_responses":
                result.append({"type": "input_image", "image_url": url, "detail": "auto"})
            else:
                result.append(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media, "data": data},
                    }
                )
        else:
            raise _error("provider_unsupported_content", "当前适配器不支持此输入块或消息位置。")
    return result


def _tools(tools: list[dict]) -> dict[str, dict]:
    if len(tools) > MAX_ITEMS:
        raise _error("provider_input_limit", "工具数量超过适配器上限。")
    result = {}
    for tool in tools:
        if tool.get("type") != "function":
            raise _error("provider_unsupported_tool", "当前适配器仅支持 function 工具。")
        function = _object(tool.get("function"))
        name = function.get("name")
        if not isinstance(name, str) or not name or name in result:
            raise _error("provider_invalid_tools", "工具名为空或重复。")
        result[name] = function
    return result


def _tool_event(call: dict, allowed: dict) -> ProviderEvent:
    identifier, name = call.get("id"), call.get("name")
    if not isinstance(identifier, str) or not identifier or name not in allowed:
        raise _error("provider_tool_identity", "模型返回未知工具或缺少工具调用 ID。")
    return ProviderEvent(
        "tool_call",
        {"id": identifier, "name": name, "arguments": _arguments(call.get("arguments"))},
    )


def _complete_calls(calls: list[dict], allowed: dict) -> list[ProviderEvent]:
    result = [_tool_event(call, allowed) for call in calls]
    identifiers = [event.data["id"] for event in result]
    if len(set(identifiers)) != len(identifiers):
        raise _error("provider_tool_identity", "模型返回了重复的工具调用 ID。")
    return result


def _opaque(message: dict, provider: str) -> list[dict] | None:
    saved = message.get("provider_output")
    if saved is None:
        return None
    saved = _object(saved)
    if PROVIDERS.get(saved.get("provider_type")) != provider:
        raise _error("provider_context_mismatch", "历史协议数据属于其他提供方；请重新编译上下文。")
    output = saved.get("output")
    if message.get("role") != "assistant" or not isinstance(output, list):
        raise _protocol("保存的模型协议数据格式无效。")
    if len(output) > MAX_ITEMS or not all(isinstance(item, dict) for item in output):
        raise _protocol("保存的模型协议内容块无效。")
    return output


def _responses_input(messages: list[dict]) -> list[dict]:
    result = []
    for message in messages:
        saved = _opaque(message, "openai_responses")
        if saved is not None:
            result.extend(saved)
            continue
        role = message.get("role")
        if role == "tool":
            result.append(
                {
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": _content(message.get("content")),
                }
            )
            continue
        if role not in ("system", "developer", "user", "assistant"):
            raise _error("provider_unsupported_role", "模型消息包含不支持的角色。")
        content = _input_content(message.get("content"), "openai_responses", role)
        if content:
            result.append({"role": role, "content": content})
        for call in message.get("tool_calls") or []:
            function = _object(call.get("function"))
            arguments = function.get("arguments", "{}")
            result.append(
                {
                    "type": "function_call",
                    "call_id": call["id"],
                    "name": function["name"],
                    "arguments": arguments if isinstance(arguments, str) else _json(arguments),
                }
            )
    return result


def _anthropic_input(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    system, result = [], []
    for message in messages:
        role = message.get("role")
        if role in ("system", "developer"):
            system.append({"type": "text", "text": _content(message.get("content"))})
            continue
        saved = _opaque(message, "anthropic")
        if saved is not None:
            blocks = saved
        elif role == "tool":
            role = "user"
            blocks = [
                {
                    "type": "tool_result",
                    "tool_use_id": message["tool_call_id"],
                    "content": _content(message.get("content")),
                }
            ]
        else:
            content = _input_content(message.get("content"), "anthropic", role)
            blocks = (
                content
                if isinstance(content, list)
                else ([{"type": "text", "text": content}] if content else [])
            )
            for call in message.get("tool_calls") or []:
                function = _object(call.get("function"))
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": function["name"],
                        "input": _arguments(function.get("arguments", "{}")),
                    }
                )
        if role not in ("user", "assistant"):
            raise _error("provider_unsupported_role", "模型消息包含不支持的角色。")
        if not blocks:
            continue
        # Consecutive tool results form one user message, preserving the tool round trip.
        if result and result[-1]["role"] == role:
            result[-1]["content"].extend(blocks)
        else:
            result.append({"role": role, "content": list(blocks)})
    return system, result


def _request(config: ProviderConfig, messages: list[dict], tools: list[dict]):
    provider = PROVIDERS.get(config.provider_type)
    if not provider:
        raise _error("provider_unknown", "模型 API 协议类型不受支持。")
    if not config.model_identifier.strip() or config.max_output_tokens <= 0:
        raise _error("provider_invalid_config", "模型标识或输出上限无效。")
    if not math.isfinite(config.timeout_seconds) or config.timeout_seconds <= 0:
        raise _error("provider_invalid_config", "模型请求超时必须是正有限数。")
    if config.temperature is not None and not math.isfinite(config.temperature):
        raise _error("provider_invalid_config", "temperature 必须是有限数。")
    if config.api_key and any(x in config.api_key for x in ("\r", "\n")):
        raise _error("provider_invalid_config", "模型凭据格式无效。")
    endpoint = urlsplit(config.endpoint.strip())
    if (
        endpoint.scheme not in ("http", "https")
        or not endpoint.hostname
        or any((endpoint.username, endpoint.password, endpoint.query, endpoint.fragment))
    ):
        raise _error(
            "provider_invalid_endpoint", "模型基础 URL 必须是无嵌入凭据或查询参数的 HTTP(S) 地址。"
        )
    path = endpoint.path.rstrip("/")
    resource = {
        "openai_responses": "/responses",
        "openai_compatible": "/chat/completions",
        "anthropic": "/messages",
    }[provider]
    if not path.endswith(resource):
        path = (path or "/v1") + resource
    url = urlunsplit((endpoint.scheme, endpoint.netloc, path, "", ""))
    allowed = _tools(tools)
    body = {"model": config.model_identifier, "stream": True}
    headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
    if config.api_key:
        headers["x-api-key" if provider == "anthropic" else "Authorization"] = (
            config.api_key if provider == "anthropic" else f"Bearer {config.api_key}"
        )
    if provider == "openai_responses":
        body.update(
            input=_responses_input(messages),
            store=False,
            include=["reasoning.encrypted_content"],
            max_output_tokens=config.max_output_tokens,
        )
        if tools:
            body["tools"] = [{"type": "function", **function} for function in allowed.values()]
    elif provider == "openai_compatible":
        # Only transport known OpenAI message fields, never private checkpoint metadata.
        body.update(
            messages=[
                {
                    key: _input_content(value, provider, message.get("role"))
                    if key == "content"
                    else value
                    for key, value in message.items()
                    if key in ("role", "content", "tool_calls", "tool_call_id", "name")
                }
                for message in messages
            ],
            max_tokens=config.max_output_tokens,
            stream_options={"include_usage": True},
        )
        for message in messages:
            if message.get("provider_output") is not None:
                raise _error(
                    "provider_context_mismatch", "Chat 协议不能重放其他提供方的 opaque 历史。"
                )
        if tools:
            body["tools"] = tools
        if config.cache_observation == "sglang":
            body["return_cached_tokens_details"] = True
    else:
        system, converted = _anthropic_input(messages)
        body.update(messages=converted, max_tokens=config.max_output_tokens)
        headers["anthropic-version"] = "2023-06-01"
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {
                    "name": name,
                    "description": function.get("description", ""),
                    "input_schema": function.get("parameters", {"type": "object"}),
                }
                for name, function in allowed.items()
            ]
    if config.temperature is not None:
        body["temperature"] = config.temperature
    payload = _json(body).encode()
    if len(payload) > MAX_REQUEST_BYTES:
        raise _error("provider_input_limit", "模型请求超过适配器输入上限。")
    return provider, url, headers, payload, allowed


async def _sse(response: httpx.Response) -> AsyncIterator[tuple[str, str]]:
    """Parse SSE incrementally, bounding decoded wire bytes before line accumulation."""
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    pending, event, data = "", "", []
    total = frame_size = 0
    first = True
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_STREAM_BYTES:
            raise _error("provider_buffer_limit", "模型流超过总接收上限。")
        pending += decoder.decode(chunk)
        if first and pending:
            pending = pending.removeprefix("\ufeff")
            first = False
        while True:
            # SSE accepts LF, CRLF and bare CR. Retain a trailing CR until its LF is known.
            positions = [i for i in (pending.find("\n"), pending.find("\r")) if i >= 0]
            if not positions:
                break
            position = min(positions)
            if pending[position] == "\r" and position == len(pending) - 1:
                break
            width = 2 if pending[position : position + 2] == "\r\n" else 1
            line, pending = pending[:position], pending[position + width :]
            frame_size += len(line.encode()) + width
            if frame_size > MAX_EVENT_BYTES:
                raise _error("provider_buffer_limit", "模型 SSE 事件超过接收上限。")
            if not line:
                if data:
                    yield event, "\n".join(data)
                event, data, frame_size = "", [], 0
            elif not line.startswith(":"):
                field, separator, value = line.partition(":")
                if separator and value.startswith(" "):
                    value = value[1:]
                if field == "event":
                    event = value
                elif field == "data":
                    data.append(value)
        if len(pending.encode()) + frame_size > MAX_EVENT_BYTES:
            raise _error("provider_buffer_limit", "模型 SSE 行超过接收上限。")
    decoder.decode(b"", final=True)
    if pending == "\r" and data:
        yield event, "\n".join(data)
    # An unterminated SSE frame is deliberately discarded; the protocol requires a final event.


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _usage(provider: str, raw: dict) -> ProviderEvent:
    if provider == "anthropic":
        uncached = _count(raw.get("input_tokens"))
        read = _count(raw.get("cache_read_input_tokens"))
        write = _count(raw.get("cache_creation_input_tokens"))
        # Do not turn an absent component into a fabricated zero or partial total.
        total = sum((uncached, read, write)) if None not in (uncached, read, write) else None
        output, reasoning = _count(raw.get("output_tokens")), None
    else:
        responses = provider == "openai_responses"
        total = _count(raw.get("input_tokens" if responses else "prompt_tokens"))
        output = _count(raw.get("output_tokens" if responses else "completion_tokens"))
        inputs = raw.get("input_tokens_details" if responses else "prompt_tokens_details") or {}
        outputs = (
            raw.get("output_tokens_details" if responses else "completion_tokens_details") or {}
        )
        read = _count(inputs.get("cached_tokens"))
        write = _count(inputs.get("cache_write_tokens"))
        reasoning = _count(outputs.get("reasoning_tokens"))
    return ProviderEvent(
        "usage",
        {
            "input_tokens_total": total,
            "output_tokens_total": output,
            "cache_read_input_tokens": read,
            "cache_write_input_tokens": write,
            "reasoning_tokens": reasoning,
            "raw": raw,
        },
    )


def _remote_error(status: int, body: dict | None = None) -> AppError:
    error = (body or {}).get("error", body or {})
    error = error if isinstance(error, dict) else {}
    category = error.get("code") or error.get("type")
    if (
        category
        in (
            "insufficient_quota",
            "billing_hard_limit_reached",
            "credit_balance_too_low",
            "billing_error",
        )
        or status == 402
    ):
        return _error("provider_quota_exhausted", "模型服务额度不足；请检查账户余额或配额。")
    if status == 401 or category == "authentication_error":
        return _error("provider_authentication", "模型服务拒绝凭据；请检查 API key。")
    if status == 403 or category == "permission_error":
        return _error("provider_permission", "模型服务拒绝访问该资源或模型。")
    if status == 429 or category == "rate_limit_error":
        return _error(
            "provider_rate_limit", "模型服务触发速率限制；本次请求未自动重试。", retryable=True
        )
    if category in ("overloaded_error", "server_error", "api_error") or status >= 500:
        return _error(
            "provider_unavailable", "模型服务暂时不可用；本次请求未自动重试。", retryable=True
        )
    if status == 404:
        return _error("provider_not_found", "模型服务路径或模型不存在；请检查配置。")
    return _error("provider_request_rejected", "模型服务拒绝请求；请检查协议、模型能力和参数。")


async def _check_http(response: httpx.Response) -> None:
    if response.is_success:
        if "text/event-stream" not in response.headers.get("content-type", "").lower():
            raise _protocol("模型服务未返回 SSE 流；请检查协议和基础 URL。")
        return
    raw = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=4096):
        raw.extend(chunk)
        if len(raw) > 65536:
            break
    try:
        body = _object(_load(raw.decode()))
    except (ValueError, AppError):
        body = None
    raise _remote_error(response.status_code, body)


async def _chat(
    events, allowed: dict, cache_observation="standard"
) -> AsyncIterator[ProviderEvent]:
    calls, usage, finish = {}, None, None
    cache_details = None
    async for _, raw in events:
        if raw == "[DONE]":
            if not finish:
                raise _protocol("Chat 流缺少明确结束原因。")
            complete = _complete_calls([call for _, call in sorted(calls.items())], allowed)
            if bool(complete) != (finish == "tool_calls"):
                raise _protocol("工具调用未以 tool_calls 正常结束。")
            if usage is not None:
                event = _usage("openai_compatible", usage)
                if cache_observation == "sglang":
                    # Standard details can contain a default zero while reporting is
                    # disabled. Only observed extension counters are evidence here.
                    event.data["cache_read_input_tokens"] = None
                    if cache_details is not None:
                        counts = [_count(cache_details.get(key)) for key in ("device", "host")]
                        storage = _count(cache_details.get("storage"))
                        if all(count is not None for count in counts):
                            event.data["cache_read_input_tokens"] = sum(counts) + (storage or 0)
                        event.data["cache_details"] = cache_details
                        event.data["cache_source"] = "sglang.request.cached_tokens_details"
                yield event
            for call in complete:
                yield call
            yield ProviderEvent("done", {"finish_reason": finish})
            return
        value = _object(_load(raw))
        if "error" in value:
            raise _remote_error(400, value)
        if value.get("usage") is not None:
            usage = _object(value["usage"])
        extension = value.get("sglext") or {}
        if cache_observation == "sglang" and extension.get("cached_tokens_details") is not None:
            cache_details = _object(extension["cached_tokens_details"])
        for choice in value.get("choices", []):
            if choice.get("index", 0) != 0:
                raise _protocol("适配器仅接受单个模型输出候选。")
            delta = _object(choice.get("delta") or {})
            if delta.get("content") is not None:
                yield ProviderEvent("text_delta", {"delta": _text(delta["content"])})
            if delta.get("function_call"):
                raise _protocol("模型使用了旧版 function_call；请启用当前 tool_calls 协议。")
            for part in delta.get("tool_calls") or []:
                index = _index(part.get("index"))
                call = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if part.get("id"):
                    if call["id"] and call["id"] != part["id"]:
                        raise _protocol()
                    call["id"] = _text(part["id"])
                function = _object(part.get("function") or {})
                if function.get("name") is not None:
                    call["name"] = _append(call["name"], function["name"], 512)
                if function.get("arguments") is not None:
                    call["arguments"] = _append(call["arguments"], function["arguments"])
            if choice.get("finish_reason"):
                finish = _text(choice["finish_reason"])
    raise _protocol("Chat 流在 [DONE] 终止标记前断开。")


async def _responses(events, allowed: dict) -> AsyncIterator[ProviderEvent]:
    items, arguments = {}, {}
    async for event, raw in events:
        if raw == "[DONE]":
            raise _protocol("Responses 流缺少 response.completed。")
        value = _object(_load(raw))
        kind = _event_type(event, value)
        if kind in ("error", "response.failed"):
            raise _remote_error(400, value.get("response", value))
        if kind == "response.incomplete":
            raise _error("provider_incomplete", "模型响应未完整完成；请检查输出预算或服务限制。")
        if kind == "response.output_text.delta":
            yield ProviderEvent("text_delta", {"delta": _text(value.get("delta"))})
        elif kind == "response.output_item.done":
            index = _index(value.get("output_index"))
            items[index] = _object(value.get("item"))
        elif kind == "response.function_call_arguments.delta":
            index = _index(value.get("output_index"))
            arguments[index] = _append(arguments.get(index, ""), value.get("delta"))
        elif kind == "response.completed":
            response = _object(value.get("response"))
            if response.get("status") != "completed":
                raise _protocol("Responses 终止事件没有 completed 状态。")
            output = response.get("output")
            if not isinstance(output, list) or len(output) > MAX_ITEMS:
                raise _protocol("Responses 终止事件缺少完整输出项目。")
            # Replay only finalized items. Never retain encrypted fragments from item.added.
            for index, item in enumerate(output):
                _object(item)
                previous = items.get(index)
                if previous is not None:
                    if previous.get("id") != item.get("id"):
                        raise _protocol("Responses 最终输出与已完成内容块不一致。")
                    output[index] = {**item, **previous}
                if output[index].get("type") == "reasoning" and not output[index].get(
                    "encrypted_content"
                ):
                    raise _error(
                        "provider_context_unavailable", "推理响应缺少可回传的 opaque 数据。"
                    )
            if any(index >= len(output) for index in items):
                raise _protocol("Responses 最终输出丢失已完成内容块。")
            _bounded(output, MAX_STREAM_BYTES)
            complete = _complete_calls(
                [
                    {
                        "id": item.get("call_id"),
                        "name": item.get("name"),
                        "arguments": item.get("arguments"),
                    }
                    for item in output
                    if item.get("type") == "function_call"
                ],
                allowed,
            )
            yield ProviderEvent(
                "provider_output", {"provider_type": "openai_responses", "output": output}
            )
            if response.get("usage") is not None:
                yield _usage("openai_responses", _object(response["usage"]))
            for call in complete:
                yield call
            yield ProviderEvent("done", {"finish_reason": "tool_calls" if complete else "stop"})
            return
    raise _protocol("Responses 流在 response.completed 前断开。")


async def _anthropic(events, allowed: dict) -> AsyncIterator[ProviderEvent]:
    blocks, arguments, open_blocks = {}, {}, set()
    usage, finish, started = {}, None, False
    async for event, raw in events:
        value = _object(_load(raw))
        kind = _event_type(event, value)
        if kind == "error":
            raise _remote_error(400, value)
        if kind == "message_start":
            if started:
                raise _protocol()
            started = True
            usage.update(_object(value.get("message", {}).get("usage") or {}))
        elif kind == "content_block_start":
            index = _index(value.get("index"))
            if not started or index in blocks:
                raise _protocol()
            block = _object(value.get("content_block"))
            if block.get("type") not in ("text", "tool_use", "thinking", "redacted_thinking"):
                raise _protocol("当前适配器不能重建该 Anthropic 内容块类型。")
            blocks[index], open_blocks = dict(block), open_blocks | {index}
            if block.get("type") == "text" and block.get("text"):
                yield ProviderEvent("text_delta", {"delta": _text(block["text"])})
        elif kind == "content_block_delta":
            index = _index(value.get("index"))
            if index not in open_blocks:
                raise _protocol()
            block, delta = blocks[index], _object(value.get("delta"))
            delta_type = delta.get("type")
            if delta_type == "text_delta" and block.get("type") == "text":
                block["text"] = _append(block.get("text", ""), delta.get("text"), MAX_STREAM_BYTES)
                yield ProviderEvent("text_delta", {"delta": _text(delta["text"])})
            elif delta_type == "input_json_delta" and block.get("type") == "tool_use":
                arguments[index] = _append(arguments.get(index, ""), delta.get("partial_json"))
            elif (
                delta_type in ("thinking_delta", "signature_delta")
                and block.get("type") == "thinking"
            ):
                key = "thinking" if delta_type == "thinking_delta" else "signature"
                block[key] = _append(block.get(key, ""), delta.get(key), MAX_STREAM_BYTES)
            else:
                raise _protocol("无法安全重建 Anthropic 内容增量。")
        elif kind == "content_block_stop":
            index = _index(value.get("index"))
            if index not in open_blocks:
                raise _protocol()
            open_blocks.remove(index)
            if blocks[index].get("type") == "tool_use":
                blocks[index]["input"] = _arguments(
                    arguments.pop(index, blocks[index].get("input"))
                )
        elif kind == "message_delta":
            if not started:
                raise _protocol()
            delta = _object(value.get("delta") or {})
            if delta.get("stop_reason"):
                finish = _text(delta["stop_reason"])
            # Anthropic message_delta usage is cumulative, never sum it with message_start.
            usage.update(_object(value.get("usage") or {}))
        elif kind == "message_stop":
            if not started or not finish or open_blocks:
                raise _protocol("Anthropic 终止时仍有未完成内容块或缺少结束原因。")
            output = [block for _, block in sorted(blocks.items())]
            complete = _complete_calls(
                [
                    {
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "arguments": block.get("input"),
                    }
                    for block in output
                    if block.get("type") == "tool_use"
                ],
                allowed,
            )
            if bool(complete) != (finish == "tool_use"):
                raise _protocol("Anthropic 工具调用没有以 tool_use 正常结束。")
            yield ProviderEvent("provider_output", {"provider_type": "anthropic", "output": output})
            if usage:
                yield _usage("anthropic", usage)
            for call in complete:
                yield call
            yield ProviderEvent("done", {"finish_reason": finish})
            return
    raise _protocol("Anthropic 流在 message_stop 前断开。")


async def stream_model(
    config: ProviderConfig, messages: list[dict], tools: list[dict]
) -> AsyncIterator[ProviderEvent]:
    """One provider attempt. No retries/fallback; incomplete streams raise sanitized AppError.

    Tool events contain complete JSON objects and whitelisted names. The executor must
    still validate each tool's own schema and current authorization before execution.
    Persist provider_output on the corresponding assistant message for exact replay.
    """
    try:
        provider, url, headers, payload, allowed = _request(config, messages, tools)
        handlers = {
            "openai_compatible": _chat,
            "openai_responses": _responses,
            "anthropic": _anthropic,
        }
        async with asyncio.timeout(config.timeout_seconds):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(config.timeout_seconds),
                transport=httpx.AsyncHTTPTransport(retries=0),
                follow_redirects=False,
            ) as client:
                async with client.stream("POST", url, headers=headers, content=payload) as response:
                    await _check_http(response)
                    events = (
                        _chat(_sse(response), allowed, config.cache_observation)
                        if provider == "openai_compatible"
                        else handlers[provider](_sse(response), allowed)
                    )
                    async for event in events:
                        yield event
    except AppError:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise _error(
            "provider_timeout",
            "模型服务请求超时；部分输出保持未完成，本次未自动重试。",
            retryable=True,
        ) from None
    except httpx.InvalidURL:
        raise _error("provider_invalid_endpoint", "模型服务 URL 无效。") from None
    except httpx.RequestError:
        raise _error(
            "provider_network", "模型服务连接或流传输失败；本次未自动重试。", retryable=True
        ) from None
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        raise _protocol() from None
