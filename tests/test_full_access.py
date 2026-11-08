"""Full-access contracts over disposable files/PG; no product DB or model calls."""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from test_run_presentation import (  # noqa: F401 - existing isolated Unix-socket PG fixtures
    authorization_engine,
    isolated_postgres_engine,
    run_facts,
)
from workbench.config import PROJECT_ROOT
from workbench.contracts.errors import AppError
from workbench.contracts.execution import ExecutionScope, current_scope
from workbench.contracts.requests import RunOptionOverrides, RunOptions
from workbench.infrastructure.database import new_id
from workbench.modules.access import service as access
from workbench.modules.agent_services.dto import Definition, public_runtime_options_schema
from workbench.modules.interactions import Approval
from workbench.modules.preferences.service import DefaultsSave
from workbench.modules.project_configuration.dto import ProjectConfiguration
from workbench.modules.runs import safety
from workbench.modules.tools import authority, file_approval, file_queries, filesystem, review
from workbench.modules.tools import service as tools
from workbench.modules.tools.domain import Operation

README = (PROJECT_ROOT / "README.md").read_text()
TITLE = README.splitlines()[0]


@contextmanager
def file_authority(root, *, host_access=True, allowed_paths=None):
    token = authority.current_file_authority.set(
        authority.FileAuthority(root.resolve(), host_access, allowed_paths)
    )
    try:
        yield
    finally:
        authority.current_file_authority.reset(token)


@pytest.fixture
def host_files(tmp_path, monkeypatch):
    root, outside = tmp_path / "workspace", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    monkeypatch.setattr(
        filesystem, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path / "private")
    )
    return root, outside


def test_host_reads_writes_and_version_conflicts_require_local_authority(host_files):
    root, outside = host_files
    target = outside / "README.md"
    target.write_text(README)
    with pytest.raises(AppError) as denied:
        filesystem.read_file(root, {"path": str(target)})
    assert denied.value.code == "path_not_allowed"
    with file_authority(root):
        read = filesystem.read_file(root, {"path": str(target)})
        assert read["path"] == str(target) and TITLE in read["content"]
        result = filesystem.change_file(
            root,
            {"path": str(target), "expected_version": read["version"], "content": TITLE},
            patch=False,
        )
        assert result["path"] == str(target) and target.read_text() == TITLE
        with pytest.raises(AppError) as stale:
            filesystem.change_file(
                root,
                {"path": str(target), "expected_version": read["version"], "content": README},
                patch=False,
            )
        assert stale.value.code == "file_version_conflict"
        assert target.read_text() == TITLE
    assert authority.current_file_authority.get() is None


def test_local_manual_mode_requires_approval_for_read_steps(run_facts, monkeypatch):  # noqa: F811
    db, run, _, _ = run_facts
    monkeypatch.setattr(tools, "engine", lambda: db.get_bind())
    for mode, required in (("manual", True), ("smart", False), ("full_access", False)):
        run.config_snapshot = {"runtime_options": {"approval_mode": mode}}
        db.commit()
        assert tools.needs_approval(run.id, "read_file", {"path": "README.md"}) is required
        assert tools.needs_approval(run.id, "find_files", {"path": "."}) is required
        assert tools.needs_approval(run.id, "write_file", {"path": "README.md"}) is True
    assert set(public_runtime_options_schema()["properties"]["approval_mode"]["enum"]) == {
        "smart",
        "manual",
    }


def test_host_list_and_real_search_paginate_without_repeating_paths(host_files):
    root, outside = host_files
    expected = []
    for name in ("a.md", "b.md", "c.md"):
        target = outside / name
        target.write_text(TITLE + "\n")
        expected.append(str(target))
    with file_authority(root):
        values, seen, cursors = {"path": str(outside), "limit": 1}, [], set()
        while True:
            page = filesystem.list_files(
                root, **{"value": values["path"], "limit": 1, "cursor": values.get("cursor")}
            )
            seen.extend(page["files"])
            if not page["next_cursor"]:
                break
            assert page["next_cursor"] not in cursors
            cursors.add(page["next_cursor"])
            values["cursor"] = page["next_cursor"]
        assert seen == expected

        async def search_pages():
            values = {"path": str(outside), "query": TITLE, "limit": 1}
            seen, cursors = [], set()
            while True:
                page = await file_queries.search_files(root, values)
                seen.extend(row["path"] for row in page["matches"])
                if not page["next_cursor"]:
                    return seen
                assert page["next_cursor"] not in cursors
                cursors.add(page["next_cursor"])
                values["cursor"] = page["next_cursor"]

        assert asyncio.run(search_pages()) == expected


def test_host_access_keeps_credential_paths_and_query_symlinks_protected(host_files):
    root, outside = host_files
    regular = outside / "README.md"
    regular.write_text(README)
    protected = [outside / ".env", outside / ".ssh" / "config", outside.parent / "private" / "x"]
    for target in protected:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(TITLE)
    alias = outside / "alias"
    alias.symlink_to(regular)
    credential_alias = outside / "credential-alias"
    credential_alias.symlink_to(protected[0])
    with file_authority(root):
        for target in [*protected, credential_alias, PROJECT_ROOT / "models"]:
            with pytest.raises(AppError) as denied:
                filesystem.authorized_path(root, str(target))
            assert denied.value.code == "protected_path"
        with pytest.raises(AppError) as query_alias:
            file_queries.find_files(root, {"path": str(alias)})
        assert query_alias.value.code == "query_symlink_denied"
        directory_alias = root / "directory-alias"
        nested = outside / "nested"
        nested.mkdir()
        directory_alias.symlink_to(nested, target_is_directory=True)
        with pytest.raises(AppError) as parent_query:
            file_queries.find_files(root, {"path": "directory-alias/.."})
        assert parent_query.value.code == "query_symlink_denied"
        with pytest.raises(AppError) as write_alias:
            filesystem.prepare_file_change(
                root,
                {
                    "path": str(alias),
                    "expected_version": filesystem.read_text(regular)[1],
                    "content": TITLE,
                },
                patch=False,
            )
        assert write_alias.value.code == "symlink_write_denied"
        assert file_queries.find_files(root, {"path": str(outside)})["files"] == [str(regular)]


def test_thread_propagation_public_scope_and_delegated_paths_keep_their_boundaries(host_files):
    root, outside = host_files
    allowed, sibling = outside / "allowed.md", outside / "sibling.md"
    allowed.write_text(README)
    sibling.write_text(TITLE)
    with file_authority(root, allowed_paths=(allowed,)):
        assert (
            asyncio.run(asyncio.to_thread(filesystem.authorized_path, root, str(allowed)))
            == allowed
        )
        with pytest.raises(AppError) as denied:
            filesystem.authorized_path(root, str(sibling))
        assert denied.value.code == "agent_path_scope"
        scope = ExecutionScope(
            "client",
            "namespace",
            "service",
            "revision",
            "workspace",
            frozenset({"read_file"}),
            frozenset(),
            "",
            (1, 1, 1),
        )
        token = current_scope.set(scope)
        try:
            with pytest.raises(AppError) as public:
                filesystem.authorized_path(root, str(allowed))
            assert public.value.code == "path_not_allowed"
        finally:
            current_scope.reset(token)
    with file_authority(outside):
        with pytest.raises(AppError):
            filesystem.authorized_path(root, str(allowed))
    assert authority.current_file_authority.get() is None and current_scope.get() is None


def test_full_access_cannot_be_published_or_inherited_from_configuration():
    options = RunOptions(approval_mode="full_access")
    with pytest.raises(ValidationError):
        DefaultsSave(expected_version=0, options=options)
    with pytest.raises(ValidationError):
        RunOptionOverrides(approval_mode="full_access")
    with pytest.raises(ValidationError):
        ProjectConfiguration.model_validate(
            {"version": 1, "defaults": {"approval_mode": "full_access"}}
        )
    with pytest.raises(ValidationError):
        Definition(model_connection_id=uuid4(), runtime_options=options)
    binding = SimpleNamespace(
        snapshot={"runtime_options": options.model_dump(), "model_connection_id": str(uuid4())}
    )
    with pytest.raises(AppError) as public:
        access.defaults_for_binding(binding)
    assert public.value.code == "api_full_access_denied"
    for origin, identifier in (("api", str(uuid4())), ("owner", str(uuid4()))):
        run = SimpleNamespace(
            execution_origin=origin,
            execution_binding_id=identifier,
            config_snapshot={"runtime_options": options.model_dump()},
        )
        assert not authority.full_access_enabled(run)


@pytest.fixture
def full_run(run_facts, monkeypatch):  # noqa: F811
    db, run, _, root = run_facts
    run.config_snapshot = {"runtime_options": RunOptions(approval_mode="full_access").model_dump()}
    db.commit()
    for module in (tools, file_approval, safety):
        monkeypatch.setattr(module, "engine", lambda: db.get_bind())
    return db, run, root


def test_full_access_approval_is_durable_and_bound_to_exact_arguments(full_run):
    db, run, _ = full_run
    values = {"path": "README.md", "expected_version": "missing", "content": TITLE}
    op = Operation(run_id=run.id, tool_call_id=new_id(), tool_name="write_file", arguments=values)
    db.add(op)
    db.commit()
    approval = tools.prepare_approval(run.id, op.id, "write_file", values)
    assert approval["status"] == "approved" and approval["review"]["source"] == "full_access"
    assert tools.approved_result(approval["id"], values)
    with pytest.raises(AppError) as changed:
        tools.approved_result(approval["id"], {**values, "content": README})
    assert changed.value.code == "approval_parameters_changed"


def test_real_operation_resets_authority_after_read_and_preflight_failure(full_run, monkeypatch):
    db, run, root = full_run
    target = root.parent / "outside.md"
    target.write_text(README)

    async def no_model(*_):
        pytest.fail("Full access and deterministic file conflicts must not invoke a model")

    monkeypatch.setattr(review, "review", no_model)
    read = asyncio.run(
        tools.execute_one(
            run.id,
            {
                "id": new_id(),
                "name": "read_file",
                "arguments": {"path": str(target)},
            },
        )
    )
    assert read["path"] == str(target) and TITLE in read["content"]
    assert authority.current_file_authority.get() is None
    result = asyncio.run(
        tools.execute_one(
            run.id,
            {
                "id": new_id(),
                "name": "write_file",
                "arguments": {"path": str(target), "expected_version": "missing", "content": TITLE},
            },
        )
    )
    assert result["error"]["code"] == "file_version_conflict"
    assert target.read_text() == README
    assert authority.current_file_authority.get() is None and current_scope.get() is None
    db.expire_all()
    assert (
        db.scalar(select(func.count()).select_from(Approval).where(Approval.run_id == run.id)) == 0
    )
    assert authority.for_run(db, run).host_access
    run.parent_run_id = run.id
    run.config_snapshot = {**run.config_snapshot, "allowed_paths": []}
    delegated = authority.for_run(db, run)
    assert delegated.allowed_paths == (root,)
