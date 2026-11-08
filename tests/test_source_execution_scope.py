"""Real published source snapshots and tool calls on disposable PostgreSQL only."""

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.orm import Session
from test_api_authorization import (  # noqa: F401 - shared private PostgreSQL fixtures
    authorization_engine,
    bound_run,
    isolated_postgres_engine,
    publication,
)
from workbench.config import Settings
from workbench.contracts.errors import AppError
from workbench.contracts.execution import current_scope
from workbench.infrastructure.database import new_id
from workbench.modules.access import service as access
from workbench.modules.agent_services import dto, sources
from workbench.modules.agent_services import service as agents
from workbench.modules.tools import filesystem
from workbench.modules.tools import service as tools
from workbench.modules.workspaces import Workspace

PUBLISHED_TOOLS = [
    "read_file",
    "read_files",
    "find_files",
    "list_files",
    "search_files",
    "write_file",
    "apply_patch",
    "edit_files",
]


@dataclass
class SourceFacts:
    db: Session
    published: object
    source: Workspace
    source_root: Path
    run: object
    binding: object
    root: Path
    revision: str


def revise(facts, paths):
    published = facts.published
    published.service = agents.revise(
        facts.db,
        published.owner.id,
        published.service["id"],
        dto.ReviseAgent(
            expected_version=published.service["version"],
            name="Explicit source scope",
            definition=dto.Definition(
                model_connection_id=UUID(published.model.id),
                allowed_tools=PUBLISHED_TOOLS,
                source_workspace_id=UUID(facts.source.id),
                source_paths=paths,
            ),
        ),
    )
    facts.db.commit()


@pytest.fixture
def source_facts(authorization_engine, tmp_path, monkeypatch):  # noqa: F811
    settings = Settings(data_dir=tmp_path / "private")
    for module in (access, sources, filesystem):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    with authorization_engine.connect() as connection:
        transaction = connection.begin()
        monkeypatch.setattr(tools, "engine", lambda: connection)
        with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
            published = publication(db)
            source_root = tmp_path / "owner-project"
            (source_root / "docs" / "nested").mkdir(parents=True)
            (source_root / "docs" / "nested" / "allowed.md").write_text("scope-needle allowed\n")
            (source_root / "docs" / "unshared.md").write_text("scope-needle private sibling\n")
            (source_root / "owner-only.md").write_text("owner ordinary file\n")
            (source_root / "docs" / "notebook.ipynb").write_text(
                json.dumps(
                    {
                        "nbformat": 4,
                        "nbformat_minor": 5,
                        "metadata": {},
                        "cells": [],
                    }
                )
            )
            source = Workspace(
                id=new_id(),
                user_id=published.owner.id,
                name="Explicit source directory",
                root_path=str(source_root),
            )
            db.add(source)
            db.commit()
            facts = SourceFacts(db, published, source, source_root, None, None, Path(), "")
            revise(facts, ["docs"])
            run, binding = bound_run(db, published, status="running")
            db.commit()
            facts.run, facts.binding = run, binding
            facts.root = Path(db.get(Workspace, run.workspace_id).root_path)
            facts.revision = binding.snapshot["source_revision"]
            yield facts
        transaction.rollback()


def selected(facts, relative="docs/nested/allowed.md"):
    return f"_sources/{facts.revision}/{relative}"


async def invoke(facts, name, values):
    return await tools.invoke(facts.run.id, new_id(), facts.root, name, values)


def test_owner_normal_reads_remain_available_and_public_scope_reads_published_path(source_facts):
    facts = source_facts
    assert current_scope.get() is None
    ordinary = filesystem.read_file(facts.source_root, {"path": "owner-only.md"})
    assert "owner ordinary file" in ordinary["content"]
    result = asyncio.run(invoke(facts, "read_file", {"path": selected(facts)}))
    assert "scope-needle allowed" in result["content"]
    assert current_scope.get() is None


def test_current_directory_to_file_grant_restriction_hides_siblings_and_other_revision(
    source_facts,
):
    facts = source_facts
    before = access.scope_for_run(facts.db, facts.run)
    assert before.source_paths == ("docs",)
    sibling = selected(facts, "docs/unshared.md")
    assert (
        "private sibling" in asyncio.run(invoke(facts, "read_file", {"path": sibling}))["content"]
    )
    revise(facts, ["docs/nested/allowed.md"])
    after = access.scope_for_run(facts.db, facts.run)
    assert after.source_paths == ("docs/nested/allowed.md",)
    assert after.source_revision == facts.revision
    newer = access.create_execution_binding(
        facts.db,
        facts.published.client,
        facts.published.service["id"],
        facts.published.service["revision_id"],
        facts.published.namespace.id,
        {},
    )
    facts.db.commit()
    other_revision_path = f"_sources/{newer.snapshot['source_revision']}/docs/nested/allowed.md"
    assert (facts.root / other_revision_path).is_file()

    async def inspect():
        for path in (sibling, other_revision_path):
            with pytest.raises(AppError) as error:
                await invoke(facts, "read_file", {"path": path})
            assert error.value.code == "source_not_allowed"
            assert current_scope.get() is None
        found = await invoke(facts, "find_files", {"path": "."})
        listed = await invoke(facts, "list_files", {"path": "."})
        searched = await invoke(facts, "search_files", {"path": ".", "query": "scope-needle"})
        expected = {selected(facts)}
        assert set(found["files"]) == set(listed["files"]) == expected
        assert {hit["path"] for hit in searched["matches"]} == expected
        assert "private sibling" not in searched["stdout"]
        assert newer.snapshot["source_revision"] not in json.dumps([found, listed, searched])

    asyncio.run(inspect())


def test_scope_survives_to_thread_and_isolated_tasks_do_not_inherit_finished_scope(source_facts):
    facts = source_facts
    revise(facts, ["docs/nested/allowed.md"])
    scope = access.scope_for_run(facts.db, facts.run)
    denied_path = selected(facts, "docs/unshared.md")

    async def inspect():
        entered, release = asyncio.Event(), asyncio.Event()

        async def restricted():
            token = current_scope.set(scope)
            try:
                allowed = await asyncio.to_thread(
                    filesystem.read_file, facts.root, {"path": selected(facts)}
                )
                assert "allowed" in allowed["content"]
                with pytest.raises(AppError) as error:
                    await asyncio.to_thread(filesystem.read_file, facts.root, {"path": denied_path})
                assert error.value.code == "source_not_allowed"
                entered.set()
                await release.wait()
            finally:
                current_scope.reset(token)
            assert current_scope.get() is None

        async def independent_owner():
            await entered.wait()
            assert current_scope.get() is None
            visible = await asyncio.to_thread(
                filesystem.read_file, facts.root, {"path": denied_path}
            )
            assert "private sibling" in visible["content"]
            release.set()

        await asyncio.gather(
            asyncio.create_task(restricted()), asyncio.create_task(independent_owner())
        )
        assert current_scope.get() is None
        await asyncio.create_task(independent_after())

    async def independent_after():
        assert current_scope.get() is None
        assert (
            "private sibling"
            in (await asyncio.to_thread(filesystem.read_file, facts.root, {"path": denied_path}))[
                "content"
            ]
        )

    asyncio.run(inspect())
    assert current_scope.get() is None


@pytest.mark.parametrize("name", ["write_file", "apply_patch", "edit_files", "edit_notebook"])
def test_source_readonly_gate_rejects_real_tool_entrypoints_without_mutating_snapshot(
    source_facts, name
):
    facts = source_facts
    relative = "docs/notebook.ipynb" if name == "edit_notebook" else "docs/nested/allowed.md"
    path = selected(facts, relative)
    target = facts.root / path
    original = target.read_bytes()
    version = filesystem.read_text(target)[1]
    values = {
        "write_file": {"path": path, "expected_version": version, "content": "replacement"},
        "apply_patch": {
            "path": path,
            "expected_version": version,
            "old_text": "scope-needle",
            "new_text": "replacement",
        },
        "edit_files": {
            "files": [
                {
                    "path": path,
                    "expected_version": version,
                    "edits": [{"old_text": "scope-needle", "new_text": "replacement"}],
                }
            ]
        },
        "edit_notebook": {
            "path": path,
            "expected_version": version,
            "operations": [{"action": "insert", "index": 0, "source": "replacement"}],
        },
    }[name]
    with pytest.raises(AppError) as error:
        asyncio.run(invoke(facts, name, values))
    assert error.value.status == 403
    assert error.value.code == (
        "api_tool_denied" if name == "edit_notebook" else "source_read_only"
    )
    assert target.read_bytes() == original and current_scope.get() is None
