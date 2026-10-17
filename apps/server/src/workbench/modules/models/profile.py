"""Connection-specific capability declarations, independent of provider wire schemas."""

from datetime import datetime
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Direction = Literal["input", "output", "input_output", "behavior"]
CAPABILITY_DIRECTIONS = MappingProxyType(
    {
        "text": "input_output",
        "image": "input",
        "audio": "input",
        "video": "input",
        "native_document": "input",
        "image_output": "output",
        "audio_output": "output",
        "structured_output": "output",
        "tool_calling": "behavior",
        "stream": "behavior",
        "prefix_cache": "behavior",
    }
)
CAPABILITIES = frozenset(CAPABILITY_DIRECTIONS)


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: Direction = Field(default="behavior", frozen=True)
    support: Literal["supported", "unsupported", "unknown"] = "unknown"
    enabled: bool = True
    evidence_status: (
        Literal["declared", "manually_asserted", "verified", "verification_failed", "stale"] | None
    ) = None
    evidence_source: str | None = None
    checked_at: datetime | None = None
    deployment_revision: str | None = None


class Declaration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    support: Literal["supported", "unsupported", "unknown"]
    enabled: bool = True
    note: str = Field(default="", max_length=2000)


class Generation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_output_tokens: int = Field(default=2048, ge=64, le=131072, strict=True)
    timeout_seconds: int = Field(default=180, ge=5, le=1800, strict=True)
    temperature: float | None = Field(default=None, ge=0, le=2)
    cache_observation: Literal["standard", "sglang"] = "standard"


def normalized(values: dict) -> dict:
    return {
        # Stored legacy profiles have no direction; any stored value is advisory
        # only. Direction belongs to this catalog, never a user declaration.
        name: Capability.model_validate(
            {
                **(values.get(name) or {}),
                "direction": CAPABILITY_DIRECTIONS[name],
            }
        ).model_dump(mode="json")
        for name in sorted(CAPABILITIES)
    }


def permits(values: dict, name: str) -> bool:
    item = values.get(name) or {}
    return item.get("support") == "supported" and item.get("enabled", True)


def adapter_route_matrix(provider_type: str) -> dict:
    """Implementation routes, never a connected model's verified capabilities.

    A route describes this application's wire implementation, not the connected
    model's actual support or an end-to-end model verification.
    """
    aliases = {
        "openai_compatible": "openai_compatible",
        "openai_chat": "openai_compatible",
        "openai_responses": "openai_responses",
        "anthropic": "anthropic",
        "anthropic_messages": "anthropic",
    }
    provider = aliases.get(provider_type) if isinstance(provider_type, str) else None
    result = {
        name: {
            "direction": CAPABILITY_DIRECTIONS[name],
            "status": "unavailable",
            "reason": "当前适配器未实现此方向的模型协议。",
            "formats": [],
            "verification": "not_evaluated",
        }
        for name in sorted(CAPABILITIES)
    }
    if provider is None:
        for item in result.values():
            item["reason"] = "未知模型协议类型，没有可用适配路线。"
        return result
    for name in ("text", "image", "tool_calling", "stream"):
        result[name].update(status="available", reason="已有协议适配；连接模型仍须独立核对。")
    result["image"]["formats"] = ["image/png", "image/jpeg", "image/webp"]
    result["prefix_cache"].update(
        status="available",
        reason="仅按真实usage观察缓存计数；不保证服务器启用或命中缓存，不提供flush。",
    )
    result["native_document"].update(
        status="available",
        formats=["application/pdf"],
        reason="已接入原生PDF输入计划与协议编码；连接模型仍须独立核对。",
    )
    if provider == "openai_compatible":
        result["audio"].update(
            status="available",
            formats=["audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav"],
            reason="已实现Chat input_audio的MP3/WAV纯编码；音频模型和端点仍须独立核对。",
        )
    else:
        result["audio"]["reason"] = "当前适配路线不支持原生音频消息；不得把转录文本当作原生音频。"
    result["video"]["reason"] = "没有已实现且已核对的原生视频wire；不猜兼容服务扩展。"
    for name in ("image_output", "audio_output", "structured_output"):
        result[name]["reason"] = "当前请求/响应适配未实现此输出；用户声明不能开启协议路线。"
    return result
