"""Canonical native inputs to official wire objects; no I/O or document execution.

This module proves encoding only, not endpoint/model support or remote consent.
"""

from __future__ import annotations

import base64
import binascii
import unicodedata
from collections.abc import Mapping

from workbench.contracts.errors import AppError

# The aggregate provider request has the same 16 MiB bound. The caller must apply
# its stricter per-artifact/aggregate budget before reading or base64-encoding.
MAX_BASE64_CHARS = 16 * 1024 * 1024
PROVIDERS = {
    "openai_compatible": "openai_compatible",
    "openai_chat": "openai_compatible",
    "openai_responses": "openai_responses",
    "anthropic": "anthropic",
    "anthropic_messages": "anthropic",
}
AUDIO_FORMATS = {
    "audio/mpeg": ("mp3", ".mp3"),
    "audio/mp3": ("mp3", ".mp3"),
    "audio/wav": ("wav", ".wav"),
    "audio/x-wav": ("wav", ".wav"),
}


def _invalid(message: str) -> AppError:
    # No filename, data, path, key, or original decoder exception in diagnostics.
    return AppError("provider_native_input", message, status=422)


def _unsupported(message: str) -> AppError:
    return AppError("provider_unsupported_content", message, status=422, kind="capability")


def _payload(data) -> bytes:
    if not isinstance(data, str) or not data or len(data) > MAX_BASE64_CHARS:
        raise _invalid("原生输入须提供预算内的非空标准base64；不接受URL或data URI。")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise _invalid("原生输入不是有效标准base64。") from None
    if not raw or base64.b64encode(raw).decode("ascii") != data:
        raise _invalid("原生输入须使用规范标准base64编码。")
    return raw


def _signature(raw: bytes, format_: str) -> bool:
    # Lightweight consistency checks only, never a complete safe-file validator.
    # Full parse/consent/mime/size/duration checks belong to the input plan.
    if format_ == "pdf":
        return raw.startswith(b"%PDF-")
    if format_ == "wav":
        return len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"
    if format_ == "mp3":
        return raw.startswith(b"ID3") or (
            len(raw) >= 4
            and raw[0] == 0xFF
            and raw[1] & 0xE0 == 0xE0
            and raw[1] & 0x18 != 0x08  # reserved MPEG version
            and raw[1] & 0x06 == 0x02  # MPEG Layer III, not Layer I/II or ADTS AAC
            and raw[2] & 0xF0 != 0xF0
            and raw[2] & 0x0C != 0x0C
        )
    return False


def encode_extra(block: Mapping, provider_type: str) -> dict:
    """Encode one trusted user-content block, returning a fresh wire dictionary.

    Input keys are exactly type/mime_type/filename/data. PDF is supported for all
    three protocols; native audio is Chat MP3/WAV only. Native video always fails
    explicitly. The function never reads files, uploads, fetches URLs, executes a
    document, mutates its input, or converts unsupported input to extracted text.
    """
    provider = PROVIDERS.get(provider_type) if isinstance(provider_type, str) else None
    if provider is None:
        raise _unsupported("未知模型协议类型；原生输入没有可用路线。")
    if not isinstance(block, Mapping) or set(block) != {"type", "mime_type", "filename", "data"}:
        raise _invalid("原生输入仅接受type、mime_type、filename和data四个字段。")
    kind, mime, filename = block["type"], block["mime_type"], block["filename"]
    if not isinstance(kind, str) or kind not in {"native_document", "native_audio", "native_video"}:
        raise _invalid("未知原生输入类型。")
    if kind == "native_video":
        raise _unsupported("当前三种适配路线均未实现已核对的原生视频协议。")
    if kind == "native_audio" and provider != "openai_compatible":
        raise _unsupported("此协议未实现原生音频消息；请显式选择转录路线或支持的协议。")
    if (
        not isinstance(filename, str)
        or not 1 <= len(filename) <= 255
        or any(unicodedata.category(c) in {"Cc", "Cs"} or c in "/\\:" for c in filename)
        or filename in {".", ".."}
    ):
        raise _invalid("filename须为无控制字符的普通文件名，不接受路径或URL。")
    if not isinstance(mime, str):
        raise _invalid("mime_type须为白名单MIME字符串。")
    if kind == "native_document":
        if mime != "application/pdf" or not filename.lower().endswith(".pdf"):
            raise _unsupported("原生文档纯编码当前仅支持application/pdf及.pdf文件。")
        format_ = "pdf"
    else:
        selected = AUDIO_FORMATS.get(mime)
        if selected is None or not filename.lower().endswith(selected[1]):
            raise _unsupported("Chat原生音频纯编码仅支持匹配MIME/后缀的MP3或WAV。")
        format_ = selected[0]
    data = block["data"]
    raw = _payload(data)
    if not _signature(raw, format_):
        raise _invalid("原生输入的内容头与声明格式不一致。")
    if kind == "native_audio":
        return {"type": "input_audio", "input_audio": {"data": data, "format": format_}}
    data_uri = f"data:application/pdf;base64,{data}"
    if provider == "openai_compatible":
        return {"type": "file", "file": {"filename": filename, "file_data": data_uri}}
    if provider == "openai_responses":
        return {"type": "input_file", "filename": filename, "file_data": data_uri}
    return {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf", "data": data},
    }
