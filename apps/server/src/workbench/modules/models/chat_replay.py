"""Replay a verified local model's current tool chain through live context filters."""

import hashlib
import json

from workbench.modules.models.managed import SGLANG_CONTRACT, matching_deployment


def enabled(config):
    return (
        config.local_contract == SGLANG_CONTRACT
        and matching_deployment(config) is not None
        and config.model_identifier.lower().rsplit("/", 1)[-1].startswith("qwen3.8")
        and config.reasoning_adapter == "auto"
        and config.thinking_mode != "disabled"
        and bool(config.run_id)
    )


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def restore(config, sources, wire, tools, options):
    """Only restore reasoning when every preceding model-visible input still matches.

    Hash the already filtered/compacted wire input, including earlier replayed
    reasoning. A forgotten source, changed rule, new tool set or summary therefore
    invalidates dependent reasoning without querying old text after filtering.
    """
    if not enabled(config):
        return wire, None
    digest = hashlib.sha256(
        _encoded(
            {
                "endpoint": config.endpoint,
                "model": config.model_identifier,
                "tools": tools,
                "options": options,
            }
        )
    )
    last_user = max(
        (
            i
            for i, (source, item) in enumerate(zip(sources, wire, strict=True))
            if item.get("role") == "user"
            and not (
                source.get("source_kind") == "runtime_budget"
                and source.get("_run_id") == config.run_id
                and isinstance(item.get("content"), str)
                and item["content"].startswith("<tool_response>")
                and item["content"].endswith("</tool_response>")
            )
        ),
        default=-1,
    )
    restored = []
    for index, (source, item) in enumerate(zip(sources, wire, strict=True)):
        item = dict(item)
        saved = source.get("_native_replay")
        if (
            index > last_user
            and item.get("role") == "assistant"
            and item.get("tool_calls")
            and source.get("_run_id") == config.run_id
            and isinstance(saved, dict)
            and saved.get("context") == digest.hexdigest()
            and isinstance(saved.get("reasoning"), str)
            and saved["reasoning"]
        ):
            item["reasoning_content"] = saved["reasoning"]
        restored.append(item)
        digest.update(b"\n")
        digest.update(_encoded(item))
    return restored, digest.hexdigest()
