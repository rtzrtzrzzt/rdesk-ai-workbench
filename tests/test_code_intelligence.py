"""Temporary source files with real installed TS/Ruff; no app or persistent server."""

import asyncio
import json
import os
import sys

import pytest
from workbench.contracts.errors import AppError
from workbench.modules.tools import code_intelligence as intelligence


def execute(root, operation, path, **kwargs):
    return asyncio.run(intelligence.execute(root, {"operation": operation, "path": path, **kwargs}))


@pytest.mark.skipif(intelligence._typescript() is None, reason="Local TypeScript/Node unavailable")
def test_real_typescript_cross_file_navigation_symbols_and_diagnostics(tmp_path):
    (tmp_path / "library.ts").write_text(
        "export function double(value: number) { return value * 2; }\n"
    )
    (tmp_path / "main.ts").write_text(
        'import { double } from "./library";\nconst answer: number = double(21);\n'
    )
    result = execute(tmp_path, "definition", "main.ts", line=2, column=24)
    assert result["available"] and result["backend"] == "typescript"
    assert any(item["path"] == "library.ts" for item in result["items"])
    references = execute(tmp_path, "references", "main.ts", line=2, column=24)
    assert len(references["items"]) >= 2
    symbols = execute(tmp_path, "symbols", "library.ts")
    assert any(item["name"] == "double" for item in symbols["items"])
    (tmp_path / "main.ts").write_text('const answer: number = "wrong";\n')
    diagnostics = execute(tmp_path, "diagnostics", "main.ts")
    assert any(item["code"] == 2322 for item in diagnostics["items"])


@pytest.mark.skipif(intelligence._typescript() is None, reason="Local TypeScript/Node unavailable")
def test_typescript_excludes_external_and_protected_definitions(tmp_path):
    protected = tmp_path / ".local"
    protected.mkdir()
    (protected / "secret.ts").write_text("export const hidden = 1;\n")
    (tmp_path / "main.ts").write_text('import { hidden } from "./.local/secret";\nhidden;\n')
    result = execute(tmp_path, "definition", "main.ts", line=2, column=2)
    assert all(not item["path"].startswith(".local") for item in result["items"])
    with pytest.raises(AppError) as denied:
        execute(tmp_path, "symbols", ".local/secret.ts")
    assert denied.value.code == "protected_path"


@pytest.mark.skipif(intelligence._executable("ruff") is None, reason="Local Ruff unavailable")
def test_real_ruff_short_lived_lsp_diagnostics_and_honest_capabilities(tmp_path):
    (tmp_path / "main.py").write_text("import os\n")
    servers = [{"language": "python", "command": [intelligence._executable("ruff"), "server"]}]
    capabilities = asyncio.run(
        intelligence.execute(
            tmp_path, {"operation": "capabilities", "path": "main.py"}, servers=servers
        )
    )
    assert capabilities["available"] and capabilities["capabilities"]["definition"] is False
    result = asyncio.run(
        intelligence.execute(
            tmp_path, {"operation": "diagnostics", "path": "main.py"}, servers=servers
        )
    )
    assert result["available"] and any(item.get("code") == "F401" for item in result["items"])
    unsupported = asyncio.run(
        intelligence.execute(
            tmp_path, {"operation": "definition", "path": "main.py"}, servers=servers
        )
    )
    assert unsupported["available"] is False
    assert unsupported["reason"] == "unsupported_capability"


def test_unavailable_position_and_file_authority(tmp_path, monkeypatch):
    (tmp_path / "main.py").write_text("value = 1\n")
    monkeypatch.setattr(intelligence, "_executable", lambda _: None)
    result = execute(tmp_path, "definition", "main.py")
    assert not result["available"] and result["reason"] == "language_server_unavailable"
    with pytest.raises(AppError) as invalid:
        execute(tmp_path, "definition", "main.py", column=200)
    assert invalid.value.code == "invalid_code_position"
    with pytest.raises(AppError) as escaped:
        execute(tmp_path, "symbols", "../main.py")
    assert escaped.value.code == "path_not_allowed"


def test_lsp_location_results_filter_outside_and_protected_paths(tmp_path):
    options = intelligence.CodeIntelligence(operation="definition", path="main.py")
    span = {"start": {"line": 0, "character": 2}, "end": {"line": 0, "character": 4}}
    data = [
        {"uri": (tmp_path / "main.py").as_uri(), "range": span},
        {"uri": (tmp_path / ".local/secret.py").as_uri(), "range": span},
        {"uri": (tmp_path.parent / "outside.py").as_uri(), "range": span},
    ]
    result = intelligence._normalize(tmp_path, tmp_path / "main.py", options, data)
    assert len(result["items"]) == 1 and result["items"][0]["range"]["start"]["column"] == 3


def test_lsp_message_reader_checks_length_and_frame():
    async def check():
        stream = asyncio.StreamReader()
        data = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode()
        stream.feed_data(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        stream.feed_eof()
        process = type("Process", (), {"stdout": stream})()
        assert (await intelligence._Client(process, None, {}).receive())["id"] == 1
        stream = asyncio.StreamReader()
        stream.feed_data(b"Content-Length: 99999999\r\n\r\n")
        process.stdout = stream
        with pytest.raises(AppError) as oversized:
            await intelligence._Client(process, None, {}).receive()
        assert oversized.value.code == "lsp_message_too_large"

    asyncio.run(check())


@pytest.mark.skipif(os.name == "nt", reason="POSIX owned-process termination check")
@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_and_cancel_reap_owned_process(tmp_path, cancel):
    # A deliberately silent short-lived child tests cleanup independently of any
    # language server behavior. No application service or model is launched.
    (tmp_path / "main.py").write_text("pass\n")
    marker = tmp_path / "owned.pid"
    program = (
        "import os,pathlib,time; "
        f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(30)"
    )

    async def run():
        task = asyncio.create_task(
            intelligence.execute(
                tmp_path,
                {
                    "operation": "definition",
                    "path": "main.py",
                    "timeout_seconds": 1,
                },
                servers=[{"language": "python", "command": [sys.executable, "-c", program]}],
            )
        )
        async with asyncio.timeout(2):
            while not marker.exists():
                await asyncio.sleep(0.01)
        pid = int(marker.read_text())
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await task)["reason"] == "timeout"
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    asyncio.run(run())
