"""Bounded, resumable workspace queries over the existing path authority."""

import asyncio
import base64
import fnmatch
import hashlib
import json
import os
import time
from collections import deque
from functools import cache
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from workbench.contracts.errors import AppError
from workbench.modules.tools import filesystem

SEARCH_SECONDS = 10
SEARCH_OUTPUT_BYTES = 24_000
RUNTIME_DIRECTORIES = frozenset(
    {".cache", ".config", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
)
DISCOVERY_NOTE = (
    "默认跳过运行缓存目录；需要时指定该目录或设置include_runtime=true。此选项不放开受保护路径。"
)


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindFiles(Query):
    path: str = "."
    pattern: str = Field(default="**/*", min_length=1, max_length=500)
    case_sensitive: bool = True
    limit: int = Field(default=100, ge=1, le=500)
    cursor: str | None = Field(default=None, max_length=2048)
    include_runtime: bool = False


class SearchFiles(Query):
    query: str = Field(min_length=1, max_length=500)
    path: str = "."
    pattern: str = Field(default="**/*", min_length=1, max_length=500)
    regex: bool = False
    case_sensitive: bool = True
    before: int = Field(default=0, ge=0, le=10)
    after: int = Field(default=0, ge=0, le=10)
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=2048)
    include_runtime: bool = False


class FileRead(Query):
    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=200, ge=1)
    start_character: int = Field(default=0, ge=0)
    max_chars: int = Field(default=32768, ge=128, le=32768)
    expected_version: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ReadFiles(Query):
    files: list[FileRead] = Field(min_length=1, max_length=12)


class TextEdit(Query):
    old_text: str = Field(min_length=1, max_length=500_000)
    new_text: str = Field(max_length=500_000)


class EditFile(Query):
    path: str
    expected_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    edits: list[TextEdit] = Field(min_length=1, max_length=40)


class EditFiles(Query):
    files: list[EditFile] = Field(min_length=1, max_length=12)


def matches(path: str, pattern: str, *, case_sensitive=True) -> bool:
    """Match glob segments; ** includes zero directories, * never crosses /."""
    if pattern.startswith("/") or ".." in PurePosixPath(pattern).parts:
        raise AppError("invalid_file_pattern", "文件模式须相对搜索目录，不能包含上级路径。", 422)
    parts, patterns = PurePosixPath(path).parts, PurePosixPath(pattern).parts
    if not case_sensitive:
        parts, patterns = tuple(p.casefold() for p in parts), tuple(p.casefold() for p in patterns)

    @cache
    def match(i, j):
        while j < len(patterns):
            if patterns[j] == "**":
                return any(match(k, j + 1) for k in range(i, len(parts) + 1))
            if i == len(parts) or not fnmatch.fnmatchcase(parts[i], patterns[j]):
                return False
            i, j = i + 1, j + 1
        return i == len(parts)

    return match(0, 0)


def _key(values):
    return hashlib.sha256(
        json.dumps(
            {
                k: v
                for k, v in values.items()
                if k not in {"cursor", "limit"} and not (k == "include_runtime" and v is False)
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:24]


def _decode(values):
    if not values.get("cursor"):
        return "", -1
    try:
        item = json.loads(base64.urlsafe_b64decode(values["cursor"]))
        if (
            item["key"] != _key(values)
            or type(item["line"]) is not int
            or item["line"] < -1
            or not isinstance(item["path"], str)
        ):
            raise ValueError
        path = str(PurePosixPath(item["path"]))
        if path.startswith("/") or ".." in PurePosixPath(path).parts:
            raise ValueError
        return path, item["line"]
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise AppError(
            "query_cursor_invalid", "续读游标无效或搜索条件已变化，请重新查询。", 422
        ) from None


def _cursor(values, path, line=-1):
    return base64.urlsafe_b64encode(
        json.dumps({"key": _key(values), "path": path, "line": line}).encode()
    ).decode()


def _includes_runtime(root, path, requested):
    return requested or any(
        part in RUNTIME_DIRECTORIES
        for part in PurePosixPath(filesystem.display_path(root, path)).parts
    )


def _paths(root: Path, path: Path, after="", *, include_after=False, include_runtime=False):
    after_parts = PurePosixPath(after).parts if after else ()
    include_runtime = _includes_runtime(root, path, include_runtime)
    cursor_root = root if path.is_relative_to(root) else (path.parent if path.is_file() else path)

    def walk(directory, depth=0):
        if depth > 64:
            raise AppError("query_depth_limit", "目录超过64层，请缩小搜索目录。", 422)
        with os.scandir(directory) as entries:
            ordered = sorted(entries, key=lambda entry: entry.name)
        for entry in ordered:
            candidate = Path(entry.path)
            if not filesystem._visible_path(root, candidate):
                continue
            relative = candidate.relative_to(cursor_root).as_posix()
            parts = PurePosixPath(relative).parts
            if entry.is_dir(follow_symlinks=False):
                # Discovery defaults are not access policy. An explicit starting
                # path or include_runtime can inspect these directories normally.
                if not include_runtime and entry.name in RUNTIME_DIRECTORIES:
                    continue
                if after_parts and parts < after_parts and after_parts[: len(parts)] != parts:
                    continue
                yield from walk(candidate, depth + 1)
            elif entry.is_file(follow_symlinks=False) and (
                not after_parts or parts > after_parts or (include_after and parts == after_parts)
            ):
                yield candidate, relative

    if path.is_file():
        relative = path.relative_to(cursor_root).as_posix()
        parts = PurePosixPath(relative).parts
        if not after_parts or parts > after_parts or (include_after and parts == after_parts):
            yield path, relative
    else:
        yield from walk(path)


def _query_path(root, value):
    # Traversal never follows symlinks; preserve that boundary for an explicitly
    # supplied starting file/directory too, before authorized_path resolves it.
    authorized = filesystem.authorized_path(root, value)
    # Preserve '..' until every traversed component has been checked: normalizing
    # first would hide a symlink followed by '..' from the no-follow boundary.
    unresolved = root / value
    start = root if unresolved.is_relative_to(root) else Path(unresolved.anchor)
    current = start
    for part in unresolved.relative_to(start).parts:
        current = current / part
        if current.is_symlink():
            raise AppError(
                "query_symlink_denied",
                "请直接指定真实路径，查询不跟随符号链接。",
                403,
                "policy",
            )
    return authorized


def _next_cursor(values, last, original):
    # A deadline before the first resumable position is not a new page. The
    # caller reports an incomplete query with no cursor and a narrowing hint.
    return _cursor(values, *last) if last != original and last[0] else None


def find_files(root, values):
    values = FindFiles.model_validate(values).model_dump()
    root = root.resolve()
    values["_root"] = str(root)
    matches("", values["pattern"])
    path = _query_path(root, values["path"])
    if not path.exists():
        raise AppError("file_not_found", "搜索目录不存在。", 404)
    after, _ = _decode(values)
    files, last, started = [], after, time.monotonic()
    notice = (
        {}
        if _includes_runtime(root, path, values["include_runtime"])
        else {"discovery_note": DISCOVERY_NOTE}
    )
    output_bytes = 2
    for scanned, (candidate, relative) in enumerate(
        _paths(root, path, after, include_runtime=values["include_runtime"])
    ):
        if scanned >= 20_000 or time.monotonic() - started > 5:
            return {
                **notice,
                "files": files,
                "truncated": True,
                "next_cursor": _next_cursor(values, (last, -1), (after, -1)),
                "note": "遍历达到预算；没有续读游标时请缩小目录。",
            }
        if matches(
            candidate.relative_to(path).as_posix() if path.is_dir() else candidate.name,
            values["pattern"],
            case_sensitive=values["case_sensitive"],
        ):
            visible = filesystem.display_path(root, candidate)
            item_bytes = len(json.dumps(visible, ensure_ascii=False).encode()) + 2
            if item_bytes + 2 > SEARCH_OUTPUT_BYTES:
                raise AppError(
                    "query_path_too_long", "文件路径超出查询输出预算，请缩小搜索范围。", 422
                )
            if len(files) == values["limit"] or output_bytes + item_bytes > SEARCH_OUTPUT_BYTES:
                return {
                    **notice,
                    "files": files,
                    "truncated": True,
                    "next_cursor": _cursor(values, last),
                }
            files.append(visible)
            output_bytes += item_bytes
        last = relative
    return {**notice, "files": files, "truncated": False, "next_cursor": None}


def _stdout(results):
    return "\n".join(f"{row['path']}:{row['line']}:{row['text']}" for row in results)


def _output_size(results):
    # Count UTF-8/JSON overhead, context and the legacy stdout representation;
    # summing just match characters misses most of a context-heavy response.
    return len(
        json.dumps({"matches": results, "stdout": _stdout(results)}, ensure_ascii=False).encode()
    )


def _excerpt(line, text):
    encoded = text.encode()
    selected = encoded[:4000].decode("utf-8", errors="ignore")
    return {"line": line, "text": selected, "text_truncated": len(encoded) > 4000}


async def _discard(stream):
    while await stream.read(65536):
        pass


async def search_files(root, values):
    values = SearchFiles.model_validate(values).model_dump()
    root = root.resolve()
    values["_root"] = str(root)
    matches("", values["pattern"])
    path = _query_path(root, values["path"])
    if not path.exists():
        raise AppError("file_not_found", "搜索路径不存在。", 404)
    after_path, after_line = _decode(values)
    original = after_path, after_line
    results, last, started = [], original, time.monotonic()
    more, timed_out = False, False
    for scanned, (candidate, relative) in enumerate(
        _paths(
            root,
            path,
            after_path,
            include_after=after_line >= 0,
            include_runtime=values["include_runtime"],
        )
    ):
        if scanned >= 10_000 or time.monotonic() - started > SEARCH_SECONDS:
            more, timed_out = True, time.monotonic() - started > SEARCH_SECONDS
            break
        if not matches(
            candidate.relative_to(path).as_posix() if path.is_dir() else candidate.name,
            values["pattern"],
            case_sensitive=values["case_sensitive"],
        ):
            last = relative, -1
            continue
        arguments = ["rg", "--no-config", "--json", "--color", "never"]
        if not values["regex"]:
            arguments.append("--fixed-strings")
        if not values["case_sensitive"]:
            arguments.append("--ignore-case")
        arguments += [
            "--before-context",
            str(values["before"]),
            "--after-context",
            str(values["after"]),
        ]
        arguments += ["--", values["query"], str(candidate)]
        environment = {
            key: os.environ[key]
            for key in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP")
            if key in os.environ
        }
        process = await asyncio.create_subprocess_exec(
            *arguments,
            cwd=root,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=os.name != "nt",
            limit=4 * 1024**2,
        )
        previous, active = deque(maxlen=values["before"]), []
        previous_line, page_full = None, False
        try:
            async with asyncio.timeout(max(0.001, SEARCH_SECONDS - (time.monotonic() - started))):
                async for raw in process.stdout:
                    event = json.loads(raw)
                    if event.get("type") not in {"match", "context"}:
                        continue
                    data = event["data"]
                    line = data["line_number"]
                    text = data["lines"].get("text")
                    if text is None:
                        text = "[非UTF-8内容，请使用文件处理工具]"
                    excerpt = _excerpt(line, text.rstrip("\r\n"))
                    if previous_line != line - 1:
                        previous.clear()
                    previous_line = line
                    active = [item for item in active if line <= item["line"] + values["after"]]
                    for item in active:
                        item["after"].append(excerpt)
                        if _output_size(results) > SEARCH_OUTPUT_BYTES:
                            item["after"].pop()
                            item["context_truncated"] = True
                    if (
                        event["type"] == "match"
                        and not page_full
                        and not (relative == after_path and line <= after_line)
                    ):
                        item = {
                            "path": filesystem.display_path(root, candidate),
                            **excerpt,
                            "before": list(previous),
                            "after": [],
                            "context_truncated": False,
                        }
                        results.append(item)
                        while item["before"] and _output_size(results) > SEARCH_OUTPUT_BYTES:
                            item["before"].pop(0)
                            item["context_truncated"] = True
                        if (
                            len(results) > values["limit"]
                            or _output_size(results) > SEARCH_OUTPUT_BYTES
                        ):
                            results.pop()
                            more, page_full = True, True
                        else:
                            active.append(item)
                            last = relative, line
                    previous.append(excerpt)
                    # Even an excluded next hit is real after-context for the
                    # accepted page. Finish that context before stopping rg.
                    if page_full and not active:
                        break
                if not more:
                    await process.wait()
        except TimeoutError:
            more, timed_out = True, True
        except (ValueError, asyncio.LimitOverrunError):
            raise AppError(
                "search_line_budget", "搜索命中行过长，请缩小文件或改用分段读取。", 422
            ) from None
        finally:
            # Cancellation and timeout both wait for the actual process exit.
            # Stderr is never echoed (rg diagnostics may contain file content).
            drain = asyncio.create_task(_discard(process.stdout))
            try:
                if process.returncode is None:
                    await filesystem.stop_process(process)
                else:
                    await process.wait()
            finally:
                await drain
        if process.returncode not in {0, 1} and not more:
            raise AppError("search_failed", "搜索表达式无效或文件不可读取。请检查正则和路径。", 422)
        if more:
            break
        last = relative, -1
    cursor = _next_cursor(values, last, original) if more else None
    return {
        **(
            {}
            if _includes_runtime(root, path, values["include_runtime"])
            else {"discovery_note": DISCOVERY_NOTE}
        ),
        "matches": results,
        "stdout": _stdout(results),
        "stderr": "",
        "exit_code": 0 if results else 1,
        "truncated": more,
        "timed_out": timed_out,
        "next_cursor": cursor,
        "note": (
            "搜索达到预算且未建立续读位置，请缩小目录或文件。"
            if more and not cursor
            else "结果按当前内容分页，截短片段有标记；续读仍须核对文件版本。"
        ),
    }


def read_files(root, values):
    results = []
    allowance = 32768 // len(values["files"])
    for item in values["files"]:
        try:
            results.append(
                filesystem.read_file(
                    root, {**item, "max_chars": min(item.get("max_chars", 32768), allowance)}
                )
            )
        except AppError as error:
            results.append(
                {"path": item["path"], "error": {"code": error.code, "message": error.message}}
            )
    return {"files": results}


def prepare_edits(root, values):
    """Validate all versions before writing; caller owns ordered file locks."""
    values = EditFiles.model_validate(values).model_dump()
    root = root.resolve()
    prepared, seen = [], set()
    for item in values["files"]:
        path = _query_path(root, item["path"])
        if path in seen:
            raise AppError("duplicate_edit_path", "每个文件只能在编辑批次中出现一次。", 422)
        seen.add(path)
        content, version = filesystem.read_text(path)
        if version != item["expected_version"]:
            raise AppError("file_version_conflict", "文件已变化，整批尚未写入，请重读。", 409)
        for edit in item["edits"]:
            if content.count(edit["old_text"]) != 1:
                raise AppError(
                    "patch_ambiguous", "每段原文须在前一步结果中恰好出现一次，整批尚未写入。", 409
                )
            content = content.replace(edit["old_text"], edit["new_text"], 1)
        if len(content.encode()) > filesystem.MAX_FILE_BYTES:
            raise AppError("file_too_large", "编辑结果超过文件预算，整批尚未写入。", 422)
        prepared.append({"path": item["path"], "expected_version": version, "content": content})
    return prepared
