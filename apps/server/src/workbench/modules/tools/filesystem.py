import asyncio
import hashlib
import os
import signal
import tempfile
import time
from pathlib import Path

from workbench.config import PROJECT_ROOT, get_settings
from workbench.contracts.errors import AppError

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 32 * 1024
EXCLUDED = frozenset({".git", ".venv", "node_modules", ".local", ".runtime", ".tools"})


def authorized_path(root: Path, value: str) -> Path:
    candidate = (root / value).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise AppError("path_not_allowed", "目标位于当前工作区之外。", 403, "policy")
    relative = candidate.relative_to(root.resolve())
    if any(
        part in EXCLUDED or part == ".env" or part.startswith(".env.") for part in relative.parts
    ):
        raise AppError(
            "protected_path", "该路径包含受保护的运行数据、凭据或版本管理内容。", 403, "policy"
        )
    for protected in (get_settings().data_dir.resolve(), (PROJECT_ROOT / "models").resolve()):
        if candidate == protected or candidate.is_relative_to(protected):
            raise AppError(
                "protected_path", "不能通过任务工具访问工作台凭据或模型权重。", 403, "policy"
            )
    return candidate


def read_text(path: Path) -> tuple[str, str]:
    if not path.is_file():
        raise AppError("file_not_found", "指定文件不存在。", 404)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise AppError("file_too_large", "文件超出基础文本工具限制，请缩小处理范围。")
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES or b"\x00" in data:
        raise AppError("unsupported_file", "请使用相应文件解析器处理二进制或过大的文件。")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        raise AppError("unsupported_encoding", "基础文本工具仅支持 UTF-8 文件。") from None
    return content, hashlib.sha256(data).hexdigest()


def _visible_path(root: Path, candidate: Path) -> bool:
    if candidate.is_symlink():
        return False
    try:
        authorized_path(root, str(candidate))
        return True
    except AppError as error:
        if error.kind == "policy":
            return False
        raise


def _walk_files(root: Path, path: Path, *, limit: int) -> tuple[list[Path], bool]:
    # Traversal and direct reads share one authority check, including a custom
    # non-hidden data directory below the workspace. Ignore files are not policy.
    found = []
    started = time.monotonic()
    for directory, children, files in os.walk(path, followlinks=False):
        children[:] = sorted(
            child for child in children if _visible_path(root, Path(directory) / child)
        )
        if time.monotonic() - started > 5:
            return found, True
        for name in sorted(files):
            candidate = Path(directory) / name
            if not _visible_path(root, candidate):
                continue
            found.append(candidate)
            if len(found) >= limit or time.monotonic() - started > 5:
                return found, True
    return found, False


def list_files(root: Path, value: str) -> dict:
    path = authorized_path(root, value)
    if not path.is_dir():
        raise AppError("directory_not_found", "指定目录不存在。", 404)
    found, truncated = _walk_files(root, path, limit=500)
    return {"files": [str(item.relative_to(root)) for item in found], "truncated": truncated}


async def search_files(root: Path, values: dict) -> dict:
    path = authorized_path(root, values["path"])
    if path.is_dir():
        files, truncated = await asyncio.to_thread(_walk_files, root, path, limit=10_000)
    elif path.is_file():
        files, truncated = [path], False
    else:
        raise AppError("file_not_found", "搜索路径不存在。", 404)
    output = {"stdout": "", "stderr": "", "truncated": truncated, "exit_code": 1}
    started = time.monotonic()
    while files:
        batch, size = [], 0
        while files and len(batch) < 128 and size < 12_000:
            candidate = files.pop()
            if _visible_path(root, candidate):
                value = str(candidate)
                batch.append(value)
                size += len(value.encode()) + 1
        if not batch:
            continue
        result = await run_process(
            [
                "rg",
                "--no-config",
                "--fixed-strings",
                "--with-filename",
                "--line-number",
                "--max-count",
                "20",
                "--",
                values["query"],
                *batch,
            ],
            root,
            max(1, round(10 - (time.monotonic() - started))),
            stop_at_output_limit=True,
        )
        if result["exit_code"] not in {0, 1} and not result["truncated"]:
            raise AppError("search_failed", "文本搜索未完成，请检查路径或搜索工具。")
        if result["exit_code"] == 0:
            output["exit_code"] = 0
        for stream in ("stdout", "stderr"):
            combined = (output[stream] + result[stream]).encode()
            output[stream] = combined[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            output["truncated"] |= len(combined) > MAX_OUTPUT_BYTES
        output["truncated"] |= result["truncated"]
        if (
            output["truncated"]
            and len(output["stdout"].encode()) >= MAX_OUTPUT_BYTES
            or result.get("timed_out")
            or time.monotonic() - started >= 10
        ):
            output["truncated"] |= bool(files) or bool(result.get("timed_out"))
            break
    return output


def read_file(root: Path, values: dict) -> dict:
    path = authorized_path(root, values["path"])
    content, version = read_text(path)
    start, end = values["start_line"], values["end_line"]
    if end < start or end - start > 999:
        raise AppError("invalid_line_range", "每次读取不超过 1000 行，结束行须大于等于起始行。")
    lines = content.splitlines()
    selected = "\n".join(f"{i + 1}: {lines[i]}" for i in range(start - 1, min(end, len(lines))))
    return {
        "path": str(path.relative_to(root)),
        "content": selected[:MAX_OUTPUT_BYTES],
        "version": version,
        "total_lines": len(lines),
        "truncated": len(selected) > MAX_OUTPUT_BYTES or end < len(lines),
    }


def change_file(root: Path, values: dict, *, patch: bool) -> dict:
    path = authorized_path(root, values["path"])
    if (root / values["path"]).is_symlink():
        raise AppError(
            "symlink_write_denied", "请直接指定真实文件，不能修改符号链接。", 403, "policy"
        )
    if path.exists():
        current, version = read_text(path)
    else:
        current, version = "", "missing"
    if version != values["expected_version"]:
        raise AppError("file_version_conflict", "文件已改变，尚未覆盖；请重读并比较候选差异。", 409)
    if patch:
        if current.count(values["old_text"]) != 1:
            raise AppError("patch_ambiguous", "原文须在当前版本中恰好出现一次。", 409)
        content = current.replace(values["old_text"], values["new_text"], 1)
    else:
        content = values["content"]
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise AppError("file_too_large", "新文件超过基础文本工具容量。")
    if not path.parent.is_dir():
        raise AppError("parent_not_found", "父目录不存在，请先选择已有目录。", 404)
    # Only cooperating writers share the application lock. External editor races remain possible.
    descriptor, temporary = tempfile.mkstemp(prefix=".workbench-write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {
        "path": str(path.relative_to(root)),
        "previous_version": version,
        "version": hashlib.sha256(encoded).hexdigest(),
        "bytes": len(encoded),
    }


async def stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        stopper = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await stopper.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    await process.wait()


async def run_process(
    arguments: list[str], root: Path, timeout: int, *, stop_at_output_limit: bool = False
) -> dict:
    # Model/DB/session secrets are never inherited through the application's environment.
    environment = {
        name: os.environ[name]
        for name in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP")
        if name in os.environ
    }
    process = await asyncio.create_subprocess_exec(
        *arguments,
        cwd=root,
        env=environment,
        start_new_session=os.name != "nt",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    result = {"stdout": "", "stderr": "", "truncated": False}

    async def collect(stream, name):
        chunks = bytearray()
        while chunk := await stream.read(4096):
            remaining = MAX_OUTPUT_BYTES - len(chunks)
            if remaining > 0:
                chunks.extend(chunk[:remaining])
            if len(chunk) > remaining:
                result["truncated"] = True
                if stop_at_output_limit:
                    await stop_process(process)
                    break
        result[name] = chunks.decode("utf-8", errors="replace")

    readers = [
        asyncio.create_task(collect(process.stdout, "stdout")),
        asyncio.create_task(collect(process.stderr, "stderr")),
    ]
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except TimeoutError:
        result["timed_out"] = True
        await stop_process(process)
    except asyncio.CancelledError:
        await stop_process(process)
        raise
    finally:
        await asyncio.gather(*readers)
    result["exit_code"] = process.returncode
    return result


async def execute(root: Path, name: str, values: dict) -> dict:
    if name == "list_files":
        return list_files(root, values["path"])
    if name == "read_file":
        return read_file(root, values)
    if name == "search_files":
        return await search_files(root, values)
    if name in {"write_file", "apply_patch"}:
        return change_file(root, values, patch=name == "apply_patch")
    if name == "exec_command":
        command = (
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", values["command"]]
            if os.name == "nt"
            else ["/bin/sh", "-c", values["command"]]
        )
        return await run_process(command, root, values["timeout_seconds"])
    raise AppError("unknown_tool", "此工具尚未注册。", 400, "policy")
