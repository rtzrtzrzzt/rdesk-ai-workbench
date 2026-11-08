"""Short lived code navigation backends, sharing the task filesystem authority.

No command is accepted from tool arguments. Optional LSP commands belong to trusted
operator configuration. Every request owns and closes its stdio process group.
"""

import asyncio
import importlib.util
import json
import os
import shutil
import signal
import sys
from contextlib import suppress
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from urllib.request import url2pathname

from pydantic import BaseModel, ConfigDict, Field

from workbench.config import PROJECT_ROOT, get_settings
from workbench.contracts.errors import AppError
from workbench.modules.tools import filesystem

LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".go": "go",
}
METHODS = {
    "definition": ("textDocument/definition", "definitionProvider"),
    "references": ("textDocument/references", "referencesProvider"),
    "symbols": ("textDocument/documentSymbol", "documentSymbolProvider"),
    "diagnostics": ("textDocument/diagnostic", "diagnosticProvider"),
}
MAX_MESSAGE_BYTES = 4 * 1024 * 1024


class CodeIntelligence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["capabilities", "definition", "references", "symbols", "diagnostics"]
    path: str = Field(min_length=1, max_length=4096)
    line: int = Field(default=1, ge=1)
    column: int = Field(default=1, ge=1)
    timeout_seconds: int = Field(default=15, ge=1, le=60)
    max_results: int = Field(default=100, ge=1, le=500)


def _executable(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    candidate = Path(sys.executable).parent / (name + (".exe" if os.name == "nt" else ""))
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None


def _typescript() -> tuple[str, Path, Path] | None:
    library = PROJECT_ROOT / "apps/web/node_modules/typescript/lib/typescript.js"
    helper = PROJECT_ROOT / "scripts/tools/typescript_intelligence.cjs"
    node = _executable("node")
    if not node:
        spec = importlib.util.find_spec("playwright")
        if spec and spec.origin:
            candidate = (
                Path(spec.origin).parent / "driver" / ("node.exe" if os.name == "nt" else "node")
            )
            if candidate.is_file() and os.access(candidate, os.X_OK):
                node = str(candidate)
    return (node, library, helper) if node and library.is_file() and helper.is_file() else None


def _servers(language: str, configured: list[dict] | None) -> list[dict]:
    if configured is not None:
        # This configuration is supplied by the executor, never by the model.
        result = []
        if len(configured) > 8:
            raise AppError("invalid_lsp_configuration", "最多配置 8 个语言服务。")
        for server in configured:
            if server.get("language") != language:
                continue
            command = server.get("command")
            if (
                not isinstance(command, list)
                or not command
                or not all(
                    isinstance(part, str) and part and "\x00" not in part for part in command
                )
            ):
                raise AppError("invalid_lsp_configuration", "语言服务命令配置不正确。")
            executable = _executable(command[0])
            if executable:
                result.append({**server, "command": [executable, *command[1:]]})
        return result
    choices = {
        "python": [
            ("basedpyright-langserver", "--stdio"),
            ("pyright-langserver", "--stdio"),
            ("pylsp",),
            ("ruff", "server"),
        ],
        "typescript": [("typescript-language-server", "--stdio")],
        "typescriptreact": [("typescript-language-server", "--stdio")],
        "javascript": [("typescript-language-server", "--stdio")],
        "javascriptreact": [("typescript-language-server", "--stdio")],
        "rust": [("rust-analyzer",)],
        "c": [("clangd",)],
        "cpp": [("clangd",)],
        "go": [("gopls",)],
    }
    return [
        {"language": language, "command": [executable, *command[1:]]}
        for command in choices.get(language, [])
        if (executable := _executable(command[0]))
    ]


class _Client:
    def __init__(self, process, root: Path, settings: dict):
        self.process = process
        self.root = root
        self.settings = settings
        self.serial = 0
        self.diagnostics = {}

    async def send(self, value: dict):
        data = json.dumps({"jsonrpc": "2.0", **value}, ensure_ascii=False).encode()
        if len(data) > MAX_MESSAGE_BYTES:
            raise AppError("lsp_message_too_large", "语言服务消息超出容量。")
        self.process.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        await self.process.stdin.drain()

    async def receive(self) -> dict:
        header = await self.process.stdout.readuntil(b"\r\n\r\n")
        if len(header) > 8192:
            raise AppError("lsp_protocol_error", "语言服务响应头超出容量。")
        lengths = [
            line.split(b":", 1)[1].strip()
            for line in header.split(b"\r\n")
            if line.lower().startswith(b"content-length:")
        ]
        if len(lengths) != 1 or not lengths[0].isdigit():
            raise AppError("lsp_protocol_error", "语言服务缺少有效的消息长度。")
        length = int(lengths[0])
        if length > MAX_MESSAGE_BYTES:
            raise AppError("lsp_message_too_large", "语言服务响应超出容量。")
        value = json.loads(await self.process.stdout.readexactly(length))
        if not isinstance(value, dict):
            raise AppError("lsp_protocol_error", "语言服务响应格式不正确。")
        return value

    async def handle(self, value: dict):
        method = value.get("method")
        params = value.get("params") or {}
        if method == "textDocument/publishDiagnostics":
            self.diagnostics[params.get("uri")] = params.get("diagnostics", [])
        if "id" not in value or not method:
            return
        if method == "workspace/configuration":
            result = []
            for item in params.get("items", []):
                setting = self.settings
                for part in (item.get("section") or "").split("."):
                    if part:
                        setting = setting.get(part) if isinstance(setting, dict) else None
                result.append(setting)
        elif method == "workspace/workspaceFolders":
            result = [{"uri": self.root.as_uri(), "name": self.root.name}]
        else:
            # In particular, never honor server-initiated workspace/applyEdit.
            await self.send(
                {
                    "id": value["id"],
                    "error": {"code": -32601, "message": "Client request is not supported"},
                }
            )
            return
        await self.send({"id": value["id"], "result": result})

    async def request(self, method: str, params: dict | None):
        self.serial += 1
        serial = self.serial
        await self.send({"id": serial, "method": method, "params": params})
        while True:
            value = await self.receive()
            if value.get("id") == serial and "method" not in value:
                if "error" in value:
                    raise AppError("lsp_request_failed", "语言服务未能完成该请求。")
                return value.get("result")
            await self.handle(value)


async def _discard(stream):
    while await stream.read(8192):
        pass


async def _close(process):
    # Always signal the owned group, even if the leader already exited, so a
    # server cannot leave descendant stdio processes behind on failure/cancel.
    if os.name == "nt":
        await filesystem.stop_process(process)
        return
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    with suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), 0.3)
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    await process.wait()


async def _lsp(root: Path, path: Path, content: str, language: str, options, server) -> dict:
    environment = {
        name: os.environ[name]
        for name in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP")
        if name in os.environ
    }
    process = await asyncio.create_subprocess_exec(
        *server["command"],
        cwd=root,
        env=environment,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name != "nt",
        limit=MAX_MESSAGE_BYTES + 8192,
    )
    stderr = asyncio.create_task(_discard(process.stderr))
    client = _Client(process, root, server.get("settings") or {})
    try:
        initialized = await client.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": root.as_uri(),
                "workspaceFolders": [{"uri": root.as_uri(), "name": root.name}],
                "capabilities": {
                    "general": {"positionEncodings": ["utf-16"]},
                    "workspace": {"configuration": True, "workspaceFolders": True},
                    "textDocument": {"diagnostic": {"dynamicRegistration": False}},
                },
                "initializationOptions": server.get("initialization_options"),
            },
        )
        capabilities = initialized.get("capabilities", {})
        if capabilities.get("positionEncoding", "utf-16") != "utf-16":
            raise AppError("lsp_position_encoding", "语言服务未采用支持的 UTF-16 位置编码。")
        await client.send({"method": "initialized", "params": {}})
        await client.send(
            {
                "method": "workspace/didChangeConfiguration",
                "params": {"settings": server.get("settings") or {}},
            }
        )
        supported = {
            operation: capabilities.get(capability) is not None
            and capabilities.get(capability) is not False
            for operation, (_, capability) in METHODS.items()
        }
        if options.operation == "capabilities":
            return {
                "available": True,
                "backend": Path(server["command"][0]).name,
                "capabilities": supported,
                "diagnostics_may_publish": True,
            }
        method, _ = METHODS[options.operation]
        if options.operation != "diagnostics" and not supported[options.operation]:
            return {
                "available": False,
                "reason": "unsupported_capability",
                "capabilities": supported,
            }
        uri = path.as_uri()
        await client.send(
            {
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": uri,
                        "languageId": language,
                        "version": 1,
                        "text": content,
                    }
                },
            }
        )
        if options.operation == "diagnostics" and not supported["diagnostics"]:
            while uri not in client.diagnostics:
                await client.handle(await client.receive())
            data = client.diagnostics[uri]
        else:
            params = {"textDocument": {"uri": uri}}
            if options.operation in {"definition", "references"}:
                params["position"] = {"line": options.line - 1, "character": options.column - 1}
            if options.operation == "references":
                params["context"] = {"includeDeclaration": True}
            data = await client.request(method, params)
            if options.operation == "diagnostics":
                data = data.get("items", [])
        result = _normalize(root, path, options, data)
        return {"available": True, "backend": Path(server["command"][0]).name, **result}
    finally:
        # A broken shutdown request must not consume the caller's request deadline.
        with suppress(Exception):
            async with asyncio.timeout(0.3):
                await client.request("shutdown", None)
                await client.send({"method": "exit"})
        await _close(process)
        stderr.cancel()
        await asyncio.gather(stderr, return_exceptions=True)


def _location(root: Path, uri: str, span: dict) -> dict | None:
    parsed = urlsplit(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        return None
    try:
        path = filesystem.authorized_path(root, url2pathname(parsed.path))
    except AppError:
        return None
    return {
        "path": filesystem.display_path(root, path),
        "range": {
            part: {"line": span[part]["line"] + 1, "column": span[part]["character"] + 1}
            for part in ("start", "end")
        },
    }


def _normalize(root: Path, path: Path, options, data) -> dict:
    items = []

    def visit(values):
        for value in values or []:
            if options.operation == "diagnostics":
                item = _location(root, path.as_uri(), value["range"])
                item.update(
                    {
                        key: value[key]
                        for key in ("message", "severity", "code", "source")
                        if key in value and isinstance(value[key], (str, int))
                    }
                )
            elif options.operation == "symbols":
                location = value.get("location", {})
                item = _location(
                    root,
                    location.get("uri", path.as_uri()),
                    location.get("range") or value.get("selectionRange") or value.get("range"),
                )
                if item:
                    item.update(name=value.get("name", ""), kind=value.get("kind"))
            else:
                item = _location(
                    root,
                    value.get("targetUri") or value.get("uri", ""),
                    value.get("targetSelectionRange") or value.get("range"),
                )
            if item:
                items.append(item)
            if options.operation == "symbols" and value.get("children"):
                visit(value["children"])

    visit([data] if isinstance(data, dict) else data)
    result = {"items": [], "truncated": False, "position_encoding": "utf-16"}
    for item in items:
        if (
            len(result["items"]) >= options.max_results
            or len(json.dumps({**result, "items": [*result["items"], item]}).encode())
            > filesystem.MAX_OUTPUT_BYTES - 512
        ):
            result["truncated"] = True
            break
        result["items"].append(item)
    return result


async def execute(root: Path, values: dict, *, servers: list[dict] | None = None) -> dict:
    options = CodeIntelligence.model_validate(values)
    root = root.resolve()
    path = filesystem.authorized_path(root, options.path)
    content, version = filesystem.read_text(path)
    language = LANGUAGES.get(path.suffix.lower())
    if not language:
        return {"available": False, "reason": "unsupported_language"}
    lines = content.splitlines() or [""]
    if (
        options.line > len(lines)
        or options.column > len(lines[options.line - 1].encode("utf-16-le")) // 2 + 1
    ):
        raise AppError(
            "invalid_code_position", "行列位置超出文件范围；位置从 1 开始，列采用 UTF-16。"
        )
    typescript = _typescript() if language.startswith(("typescript", "javascript")) else None
    if typescript and servers is None:
        if options.operation == "capabilities":
            return {
                "available": True,
                "backend": "typescript",
                "version": version,
                "capabilities": dict.fromkeys(METHODS, True),
                "position_encoding": "utf-16",
            }
        node, library, helper = typescript
        request = {
            **options.model_dump(),
            "path": str(path),
            "root": str(root),
            "excluded": sorted(filesystem.EXCLUDED),
            "protected": [
                str(get_settings().data_dir.resolve()),
                str((PROJECT_ROOT / "models").resolve()),
            ],
            "maxFileBytes": filesystem.MAX_FILE_BYTES,
        }
        result = await filesystem.run_process(
            [node, str(helper), str(library), json.dumps(request)],
            root,
            options.timeout_seconds,
            stop_at_output_limit=True,
        )
        if result.get("timed_out"):
            return {"available": False, "reason": "timeout", "version": version}
        if result["exit_code"] or result["truncated"]:
            raise AppError("code_intelligence_failed", "TypeScript 代码分析未完成或超出预算。")
        if filesystem.read_text(path)[1] != version:
            raise AppError("file_version_conflict", "分析期间源文件已改变，请重试。", 409)
        try:
            return {**json.loads(result["stdout"]), "version": version}
        except ValueError:
            raise AppError("code_intelligence_failed", "TypeScript 分析结果无效。") from None
    candidates = _servers(language, servers)
    if not candidates:
        return {
            "available": False,
            "reason": "language_server_unavailable",
            "language": language,
            "required_configuration": "安装本地语言服务或配置受信 language/command 服务器。",
        }
    try:
        async with asyncio.timeout(options.timeout_seconds):
            for server in candidates:
                result = await _lsp(root, path, content, language, options, server)
                if result["available"]:
                    return {**result, "version": version}
            return {**result, "version": version}
    except TimeoutError:
        return {"available": False, "reason": "timeout", "version": version}
    except (
        OSError,
        ValueError,
        asyncio.IncompleteReadError,
        asyncio.LimitOverrunError,
        KeyError,
        TypeError,
        RecursionError,
    ):
        raise AppError("lsp_protocol_error", "语言服务未完成有效的协议交换。") from None
