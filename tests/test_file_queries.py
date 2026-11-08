"""Workspace queries over public README bytes and disposable files, with real rg."""

import asyncio
import base64
import json
import os
import re
from types import SimpleNamespace

import pytest
from workbench.config import PROJECT_ROOT
from workbench.contracts.errors import AppError
from workbench.modules.tools import file_queries as queries
from workbench.modules.tools import filesystem

README = (PROJECT_ROOT / "README.md").read_text()
TITLE = README.splitlines()[0]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(filesystem, "get_settings", lambda: SimpleNamespace(data_dir=root / "data"))
    return root


def write(root, path, content=README):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


def test_globs_are_relative_to_search_directory_and_pages_do_not_repeat(workspace):
    for name in ("README.md", "notes.txt", "docs/guide.md", "docs/sub/REFERENCE.MD"):
        write(workspace, name)
    assert queries.find_files(workspace, {"pattern": "*.md"})["files"] == ["README.md"]
    assert queries.find_files(workspace, {"pattern": "**/*.md"})["files"] == [
        "README.md",
        "docs/guide.md",
    ]
    assert queries.find_files(
        workspace,
        {
            "path": "docs",
            "pattern": "**/*.md",
            "case_sensitive": False,
        },
    )["files"] == ["docs/guide.md", "docs/sub/REFERENCE.MD"]
    seen, values, cursors = [], {"limit": 1}, set()
    while True:
        result = queries.find_files(workspace, values)
        seen.extend(result["files"])
        if not result["next_cursor"]:
            assert not result["truncated"]
            break
        assert result["next_cursor"] not in cursors
        cursors.add(result["next_cursor"])
        values["cursor"] = result["next_cursor"]
    assert seen == ["README.md", "docs/guide.md", "docs/sub/REFERENCE.MD", "notes.txt"]
    assert queries.find_files(workspace, {"path": "README.md", "limit": 1})["next_cursor"] is None


def test_protected_paths_and_symlinks_are_never_traversed(workspace):
    write(workspace, "public/README.md")
    for name in (".env", ".env.local", ".git/config", "data/readme.txt"):
        write(workspace, name)
    (workspace / "alias").symlink_to(workspace / "public", target_is_directory=True)
    (workspace / "outside").symlink_to(PROJECT_ROOT / "README.md")
    result = queries.find_files(workspace, {})
    assert result["files"] == ["public/README.md"]
    for path in (".env", ".git", "data", "alias", "alias/README.md", "outside", "../README.md"):
        with pytest.raises(AppError) as denied:
            queries.find_files(workspace, {"path": path})
        assert denied.value.status == 403
    (workspace / ".env").unlink()
    (workspace / ".env").symlink_to(workspace / "public/README.md")
    with pytest.raises(AppError, match="受保护"):
        filesystem.read_file(workspace, {"path": ".env"})


def test_invalid_glob_and_cursor_fail_even_for_empty_directory(workspace):
    for pattern in ("../*", "/tmp/*"):
        with pytest.raises(AppError):
            queries.find_files(workspace, {"pattern": pattern})
    write(workspace, "a.md")
    write(workspace, "b.md")
    page = queries.find_files(workspace, {"limit": 1})
    with pytest.raises(AppError):
        queries.find_files(workspace, {"cursor": page["next_cursor"], "pattern": "*.md"})
    other = workspace.parent / "other"
    other.mkdir()
    with pytest.raises(AppError):
        queries.find_files(other, {"cursor": page["next_cursor"]})
    malformed = base64.urlsafe_b64encode(json.dumps([]).encode()).decode()
    with pytest.raises(AppError):
        queries.find_files(workspace, {"cursor": malformed})


def test_runtime_discovery_defaults_are_separate_from_path_authority(workspace):
    for name in (
        "README.md", ".github/README.md", ".cache/README.md", ".config/README.md",
        ".config/__pycache__/README.md", ".git/README.md", "data/README.md",
    ):
        write(workspace, name)
    result = filesystem.list_files(workspace, ".")
    assert result["files"] == [".github/README.md", "README.md"]
    assert not result["truncated"] and "include_runtime" in result["discovery_note"]
    included = queries.find_files(workspace, {"include_runtime": True})
    assert ".cache/README.md" in included["files"]
    assert ".config/__pycache__/README.md" in included["files"]
    assert ".git/README.md" not in included["files"] and "data/README.md" not in included["files"]
    assert queries.find_files(workspace, {"path": ".config"})["files"] == [
        ".config/README.md", ".config/__pycache__/README.md"
    ]
    assert queries.find_files(workspace, {"path": ".cache/README.md"})["files"] == [
        ".cache/README.md"
    ]
    assert filesystem.read_file(workspace, {"path": ".cache/README.md"})["content"].startswith(
        "1: " + TITLE
    )
    with pytest.raises(AppError):
        queries.find_files(workspace, {"path": ".git", "include_runtime": True})


def test_runtime_search_is_opt_in_and_legacy_cursor_remains_bound_to_scope(workspace):
    write(workspace, "a.md")
    write(workspace, "b.md")
    write(workspace, ".cache/README.md")
    values = queries.FindFiles(limit=1).model_dump()
    values["_root"] = str(workspace.resolve())
    legacy = {key: value for key, value in values.items() if key != "include_runtime"}
    assert queries._key(legacy) == queries._key(values)
    cursor = queries._cursor(legacy, "a.md")
    assert queries.find_files(workspace, {"cursor": cursor})["files"] == ["b.md"]
    with pytest.raises(AppError):
        queries.find_files(workspace, {"cursor": cursor, "include_runtime": True})

    async def check():
        default = await queries.search_files(workspace, {"query": TITLE})
        assert all(not row["path"].startswith(".cache/") for row in default["matches"])
        explicit = await queries.search_files(workspace, {"query": TITLE, "path": ".cache"})
        included = await queries.search_files(workspace, {"query": TITLE, "include_runtime": True})
        assert explicit["matches"][0]["path"] == ".cache/README.md"
        assert any(row["path"] == ".cache/README.md" for row in included["matches"])

    asyncio.run(check())


def test_filename_byte_budget_pages_without_omitting_the_next_match(workspace):
    names = [f"{'d' * 120}/file-{index:03}-{'n' * 120}.md" for index in range(110)]
    for name in names:
        write(workspace, name, TITLE)
    seen, values = [], {"limit": 500}
    while True:
        result = queries.find_files(workspace, values)
        assert len(json.dumps(result, ensure_ascii=False).encode()) < filesystem.MAX_OUTPUT_BYTES
        seen.extend(result["files"])
        if not result["next_cursor"]:
            assert not result["truncated"]
            break
        assert result["files"] and result["truncated"]
        values["cursor"] = result["next_cursor"]
    assert seen == names


def test_real_rg_over_twenty_matches_page_with_complete_adjacent_context(workspace):
    write(workspace, "README.md", (TITLE + "\n") * 65)

    async def verify():
        seen, values, cursors = [], {"query": TITLE, "limit": 23, "before": 1, "after": 2}, set()
        while True:
            result = await queries.search_files(workspace, values)
            for hit in result["matches"]:
                number = hit["line"]
                assert [row["line"] for row in hit["before"]] == (
                    [number - 1] if number > 1 else []
                )
                assert [row["line"] for row in hit["after"]] == list(
                    range(number + 1, min(number + 2, 65) + 1)
                )
            seen.extend(hit["line"] for hit in result["matches"])
            if not result["next_cursor"]:
                assert not result["truncated"]
                break
            assert result["truncated"] and result["next_cursor"] not in cursors
            cursors.add(result["next_cursor"])
            values["cursor"] = result["next_cursor"]
        assert seen == list(range(1, 66))

    asyncio.run(verify())


def test_real_rg_literal_regex_case_and_file_relative_glob(workspace):
    write(workspace, "docs/README.md", TITLE + "\n" + TITLE.swapcase() + "\n")
    write(workspace, "docs/sub/README.md", TITLE + "\n")

    async def verify():
        values = {"query": TITLE, "path": "docs", "pattern": "*.md"}
        literal = await queries.search_files(workspace, values)
        assert [(row["path"], row["line"]) for row in literal["matches"]] == [("docs/README.md", 1)]
        insensitive = await queries.search_files(workspace, {**values, "case_sensitive": False})
        assert [row["line"] for row in insensitive["matches"]] == [1, 2]
        expression = await queries.search_files(
            workspace, {**values, "query": "^" + re.escape(TITLE) + "$", "regex": True}
        )
        assert [row["line"] for row in expression["matches"]] == [1]
        with pytest.raises(AppError) as invalid:
            await queries.search_files(workspace, {**values, "query": "(", "regex": True})
        assert invalid.value.code == "search_failed"

    asyncio.run(verify())


def test_context_and_repeated_stdout_stay_within_response_budget(workspace):
    long_line = TITLE + " " + README.replace("\n", " ")[:3500]
    write(workspace, "README.md", (long_line + "\n") * 35)

    async def verify():
        result = await queries.search_files(workspace, {"query": TITLE, "before": 10, "after": 10})
        assert len(json.dumps(result, ensure_ascii=False).encode()) <= filesystem.MAX_OUTPUT_BYTES
        assert result["truncated"] and result["next_cursor"]
        assert result["matches"] and any(row["context_truncated"] for row in result["matches"])

    asyncio.run(verify())


def test_long_single_line_continues_without_skips_and_detects_version_change(workspace):
    long_line = README.replace("\n", " ")[:6000]
    target = write(workspace, "README.md", long_line)
    values, fragments, positions = {"path": "README.md", "max_chars": 128}, [], set()
    first = filesystem.read_file(workspace, values)
    while True:
        result = filesystem.read_file(workspace, values)
        assert len(result["content"]) <= 128
        fragments.append(re.sub(r"^1(?:（续）)?: ", "", result["content"]))
        if result["next"] is None:
            break
        position = (result["next"]["start_line"], result["next"]["start_character"])
        assert position not in positions
        positions.add(position)
        values = result["next"]
    assert "".join(fragments) == long_line and len(positions) > 20
    target.write_text(long_line + " changed")
    with pytest.raises(AppError) as conflict:
        filesystem.read_file(workspace, first["next"])
    assert conflict.value.code == "file_version_conflict"


def test_batch_edit_preflight_rejects_later_invalid_file_before_writes(workspace):
    first = write(workspace, "first.md")
    second = write(workspace, "second.md")
    _, version = filesystem.read_text(first)
    edits = [{"old_text": TITLE, "new_text": TITLE + " updated"}]
    values = {
        "files": [
            {"path": "first.md", "expected_version": version, "edits": edits},
            {"path": "second.md", "expected_version": "0" * 64, "edits": edits},
        ]
    }
    with pytest.raises(AppError) as failed:
        queries.prepare_edits(workspace, values)
    assert failed.value.code == "file_version_conflict"
    assert first.read_text() == README and second.read_text() == README
    values["files"][1]["expected_version"] = version
    second.unlink()
    second.symlink_to(first)
    with pytest.raises(AppError):
        queries.prepare_edits(workspace, values)
    assert first.read_text() == README


@pytest.mark.skipif(os.name == "nt", reason="Requires POSIX FIFO and process-group cleanup")
@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_and_cancel_reap_blocked_real_rg(workspace, monkeypatch, cancel):
    # Inject a FIFO only to make an actual rg block deterministically. Production
    # traversal excludes FIFOs; this tests cleanup, not FIFO search support.
    fifo = workspace / "blocked"
    os.mkfifo(fifo)
    monkeypatch.setattr(queries, "_paths", lambda *_args, **_kwargs: iter([(fifo, "blocked")]))
    monkeypatch.setattr(queries, "SEARCH_SECONDS", 0.12)
    created = []
    create = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        process = await create(*args, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(queries.asyncio, "create_subprocess_exec", capture)

    async def verify():
        task = asyncio.create_task(queries.search_files(workspace, {"query": TITLE}))
        while not created:
            await asyncio.sleep(0.002)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await task
            assert result["timed_out"] and result["truncated"]
            assert result["next_cursor"] is None
        assert all(process.returncode is not None for process in created)
        for process in created:
            with pytest.raises(ProcessLookupError):
                os.kill(process.pid, 0)

    asyncio.run(asyncio.wait_for(verify(), timeout=4))
