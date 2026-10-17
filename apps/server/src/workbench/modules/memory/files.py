"""Restricted UTF-8 files and atomic publication; external editors do not share our lock."""

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from workbench.config import get_settings
from workbench.contracts.errors import AppError

MAX_BYTES = 2 * 1024 * 1024
PREFIX = "<!-- workbench-memory "


class MemoryMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    document_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    scope: Literal["user", "workspace"]
    generation: int = Field(ge=1, strict=True)
    source_refs: list[str] = Field(max_length=32)
    updated_at: datetime | None = None


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def private_path(relative: str) -> Path:
    base = get_settings().data_dir.resolve()
    value = Path(relative)
    if (
        value.is_absolute()
        or ".." in value.parts
        or not value.parts
        or value.parts[0] not in {"users", "workspaces"}
    ):
        raise AppError("memory_path_denied", "记忆路径超出私有数据范围。", 403, "policy")
    probe = base
    for part in value.parts:
        probe = probe / part
        if probe.is_symlink():
            raise AppError("memory_symlink_denied", "记忆存储路径不能经过符号链接。", 403, "policy")
    if not probe.resolve().is_relative_to(base):
        raise AppError("memory_path_denied", "记忆路径超出私有数据范围。", 403, "policy")
    return probe


def read(relative: str) -> tuple[bytes | None, str]:
    path = private_path(relative)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None, "missing"
    with os.fdopen(descriptor, "rb") as stream:
        if os.fstat(stream.fileno()).st_size > MAX_BYTES:
            raise AppError("memory_file_too_large", "记忆或规则文件超过 2 MiB 限制。", 413)
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or b"\0" in data:
        raise AppError("invalid_memory_file", "记忆必须是有界 UTF-8 文本。")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise AppError("memory_encoding", "记忆与规则文件必须使用 UTF-8。") from None
    return data, digest(data)


def encode(
    document_id: str, title: str, scope: str, generation: int, refs: list[str], body: str
) -> bytes:
    if "\0" in body or "\0" in title:
        raise AppError("invalid_memory_text", "Markdown 不能包含 NUL 字符。")
    metadata = {
        "schema_version": 1,
        "document_id": document_id,
        "title": title,
        "scope": scope,
        "generation": generation,
        "source_refs": refs,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    data = (
        PREFIX + json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + " -->\n\n" + body
    ).encode()
    if len(data) > MAX_BYTES:
        raise AppError("memory_file_too_large", "记忆文件超过 2 MiB 限制。", 413)
    return data


def decode(data: bytes, document_id: str) -> tuple[dict, str]:
    text = data.decode("utf-8")
    header, separator, remainder = text.partition("\n")
    try:
        if not separator or not header.startswith(PREFIX) or not header.endswith(" -->"):
            raise ValueError
        metadata = MemoryMetadata.model_validate(json.loads(header[len(PREFIX) : -4])).model_dump(
            mode="json"
        )
        if metadata["document_id"] != document_id:
            raise ValueError
    except (ValueError, TypeError, ValidationError):
        raise AppError(
            "memory_metadata_conflict",
            "Markdown 元信息已改变，请保留当前文件并人工核对。",
            409,
            "conflict",
        ) from None
    return metadata, remainder.removeprefix("\n")


def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def snapshot(relative: str, data: bytes) -> None:
    """Create a journal-owned immutable candidate/base file, never replace an existing file."""
    path = private_path(relative)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(path.parent)


def stage(
    root: str,
    journal_id: str,
    number: int,
    target: str,
    new: bytes | None,
    *,
    expected_version: str = "missing",
) -> dict:
    old, before = read(target)
    if before != expected_version:
        raise AppError(
            "memory_version_conflict", "暂存前文件已改变，未覆盖当前内容。", 409, "conflict"
        )
    directory = f"{root}/.writes/{journal_id}"
    base = f"{directory}/{number}.base.md" if old is not None else None
    candidate = f"{directory}/{number}.candidate.md" if new is not None else None
    if old is not None:
        snapshot(base, old)
    if new is not None:
        snapshot(candidate, new)
    return {
        "path": target,
        "before": before,
        "after": digest(new) if new is not None else "missing",
        "base": base,
        "candidate": candidate,
    }


def publish(plan: dict) -> None:
    files = plan.get("files", [])
    for entry in files:
        current, version = read(entry["path"])
        if version != entry["before"]:
            if current is not None:
                saved = entry.get("candidate") or entry.get("base")
                if saved:
                    conflict = str(Path(saved).parent / f"{Path(entry['path']).name}.external.md")
                    try:
                        snapshot(conflict, current)
                    except FileExistsError:
                        pass
            raise AppError(
                "memory_file_conflict",
                "检测到外部修改；原文件未被本次覆盖，候选已保留。",
                409,
                "conflict",
            )
    if plan.get("blocked_history"):
        raise AppError(
            "memory_history_cleanup_conflict",
            "历史快照已改变；遗忘屏障已保存，文件清理仍待核对。",
            409,
            "outcome_unknown",
        )
    if plan.get("blocked_index"):
        raise AppError(
            "memory_index_conflict",
            "索引已被外部编辑；遗忘屏障已保存，文件清理仍待核对。",
            409,
            "outcome_unknown",
        )
    for entry in files:
        path = private_path(entry["path"])
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Re-read immediately before each replacement; external-editor races still exist.
        if read(entry["path"])[1] != entry["before"]:
            raise AppError(
                "memory_file_conflict",
                "发布期间检测到外部修改，结果需要核对。",
                409,
                "outcome_unknown",
            )
        if entry["after"] == "missing":
            path.unlink(missing_ok=True)
            _sync_directory(path.parent)
            continue
        data, version = read(entry["candidate"])
        if data is None or version != entry["after"]:
            raise AppError(
                "memory_candidate_missing",
                "已登记的候选文件缺失或改变，需要核对。",
                409,
                "outcome_unknown",
            )
        descriptor, temporary = tempfile.mkstemp(prefix=".memory-publish-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            _sync_directory(path.parent)
        finally:
            Path(temporary).unlink(missing_ok=True)
