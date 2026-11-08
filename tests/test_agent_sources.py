"""Source snapshot tests use real isolated temporary files, never project/private data."""

import json
import stat
from types import SimpleNamespace
from uuid import uuid4

import pytest
from workbench.contracts.errors import AppError
from workbench.modules.agent_services import dto, sources
from workbench.modules.tools import filesystem


@pytest.fixture
def environment(tmp_path, monkeypatch):
    source_root, data_root = tmp_path / "selected-project", tmp_path / "private-data"
    source_root.mkdir(mode=0o700)
    data_root.mkdir(mode=0o700)
    owner_id, source_id = str(uuid4()), str(uuid4())
    workspaces = {source_id: SimpleNamespace(root_path=str(source_root))}
    settings = SimpleNamespace(data_dir=data_root, ensure_data_dir=lambda: data_root)
    monkeypatch.setattr(sources, "get_settings", lambda: settings)
    monkeypatch.setattr(filesystem, "get_settings", lambda: settings)

    def workspace(_db, requested_owner, workspace_id):
        assert requested_owner == owner_id
        return workspaces[workspace_id]

    monkeypatch.setattr(sources.workspace_service, "get", workspace)

    def namespace():
        workspace_id = str(uuid4())
        root = tmp_path / workspace_id
        root.mkdir(mode=0o700)
        workspaces[workspace_id] = SimpleNamespace(root_path=str(root))
        return SimpleNamespace(owner_user_id=owner_id, workspace_id=workspace_id), root

    def definition(paths, **extra):
        return dto.Definition(
            model_connection_id=uuid4(), source_workspace_id=source_id, source_paths=paths, **extra
        )

    return SimpleNamespace(
        source=source_root,
        data=data_root,
        owner=owner_id,
        source_id=source_id,
        definition=definition,
        namespace=namespace,
    )


def target(root, revision):
    return root / "_sources" / revision


def test_sources_require_explicit_relative_selection_and_normalize_without_defaulting_workspace(
    environment,
):
    env = environment
    (env.source / "code.py").write_text("print('selected')\n")
    result = sources.validate(None, env.owner, env.definition(["./code.py", "code.py"]))
    assert result["source_paths"] == ["code.py"]
    for paths in ([], ["."], ["../outside"], ["/absolute"], ["code/../code.py"], ["C:\\secret"]):
        with pytest.raises(AppError):
            sources.validate(None, env.owner, env.definition(paths))
    with pytest.raises(AppError):
        sources.validate(
            None, env.owner, dto.Definition(model_connection_id=uuid4(), source_paths=["code.py"])
        )


def test_publication_freezes_one_canonical_version_for_all_namespaces_and_new_revision_updates(
    environment,
):
    env = environment
    (env.source / "src").mkdir()
    (env.source / "src" / "main.py").write_text("VERSION = 'published'\n", encoding="utf-8")
    (env.source / "README.md").write_text("明确共享的说明\n", encoding="utf-8")
    definition = env.definition(["src", "README.md"])
    revision = str(uuid4())
    sources.prepare_revision(None, env.owner, revision, definition)
    # A change after publication cannot change this revision for a later client.
    (env.source / "src" / "main.py").write_text("VERSION = 'later'\n", encoding="utf-8")
    first, first_root = env.namespace()
    second, second_root = env.namespace()
    for namespace, root in ((first, first_root), (second, second_root)):
        scope = sources.materialize(None, namespace, revision, definition)
        assert scope == {"source_revision": revision, "source_paths": ["README.md", "src"]}
        snapshot = target(root, revision)
        assert (snapshot / "src" / "main.py").read_text() == "VERSION = 'published'\n"
        assert (snapshot / "README.md").read_text() == "明确共享的说明\n"
        assert stat.S_IMODE(snapshot.stat().st_mode) == 0o500
        assert stat.S_IMODE((snapshot / "src").stat().st_mode) == 0o500
        assert stat.S_IMODE((snapshot / "src" / "main.py").stat().st_mode) == 0o400
        marker = (snapshot / sources.MARKER).read_text()
        assert str(env.source) not in marker
        assert json.loads(marker)["file_count"] == 2
    updated = str(uuid4())
    sources.prepare_revision(None, env.owner, updated, definition)
    sources.materialize(None, first, updated, definition)
    assert (target(first_root, updated) / "src" / "main.py").read_text() == "VERSION = 'later'\n"


def test_retry_only_checks_immutable_marker_and_does_not_read_or_hash_payload(
    environment, monkeypatch
):
    env = environment
    (env.source / "text.md").write_text("published\n")
    definition, revision = env.definition(["text.md"]), str(uuid4())
    sources.prepare_revision(None, env.owner, revision, definition)
    namespace, _ = env.namespace()
    sources.materialize(None, namespace, revision, definition)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("unchanged snapshot payload must not be scanned or reread")

    monkeypatch.setattr(sources, "_scan", unexpected)
    monkeypatch.setattr(sources, "_read", unexpected)
    sources.prepare_revision(None, env.owner, revision, definition)
    sources.materialize(None, namespace, revision, definition)


@pytest.mark.parametrize(
    "bad_name,content",
    [(".env", b"credential"), ("binary.bin", b"text\0binary"), ("legacy.txt", b"\xff\xfe")],
)
def test_protected_or_non_text_entries_fail_whole_directory_without_partial_snapshot(
    environment, bad_name, content
):
    env = environment
    (env.source / "chosen").mkdir()
    (env.source / "chosen" / "good.py").write_text("ok = True\n")
    (env.source / "chosen" / bad_name).write_bytes(content)
    revision = str(uuid4())
    with pytest.raises(AppError):
        sources.prepare_revision(None, env.owner, revision, env.definition(["chosen"]))
    canonical = env.data / "agent-api" / "source-revisions"
    assert not (canonical / revision).exists()
    assert not list(canonical.glob(".source-stage-*"))


def test_symlink_source_and_symlink_target_are_rejected_without_touching_outside(environment):
    env = environment
    (env.source / "real.txt").write_text("original")
    (env.source / "alias.txt").symlink_to("real.txt")
    with pytest.raises(AppError):
        sources.validate(None, env.owner, env.definition(["alias.txt"]))
    revision, definition = str(uuid4()), env.definition(["real.txt"])
    sources.prepare_revision(None, env.owner, revision, definition)
    namespace, root = env.namespace()
    (root / "_sources").mkdir(mode=0o700)
    target(root, revision).symlink_to(env.source, target_is_directory=True)
    with pytest.raises(AppError):
        sources.materialize(None, namespace, revision, definition)
    assert (env.source / "real.txt").read_text() == "original"


@pytest.mark.parametrize(
    "limit,value",
    [
        ("MAX_FILE_BYTES", 3),
        ("MAX_TOTAL_BYTES", 4),
        ("MAX_FILES", 1),
        ("MAX_ENTRIES", 1),
        ("MAX_SECONDS", 0),
    ],
)
def test_scanning_and_copy_budgets_reject_entire_publication(
    environment, monkeypatch, limit, value
):
    env = environment
    (env.source / "src").mkdir()
    (env.source / "src" / "a.py").write_text("a = 1\n")
    (env.source / "src" / "b.py").write_text("b = 2\n")
    monkeypatch.setattr(sources, limit, value)
    revision = str(uuid4())
    with pytest.raises(AppError) as denied:
        sources.prepare_revision(None, env.owner, revision, env.definition(["src"]))
    assert denied.value.code == "agent_source_budget"
    assert not (env.data / "agent-api" / "source-revisions" / revision).exists()


def test_materialize_never_reopens_original_project_after_publication(environment, monkeypatch):
    env = environment
    (env.source / "file.txt").write_text("published")
    revision, definition = str(uuid4()), env.definition(["file.txt"])
    sources.prepare_revision(None, env.owner, revision, definition)
    (env.source / "file.txt").unlink()
    namespace, root = env.namespace()
    lookup = sources.workspace_service.get

    def workspace(db, owner_id, workspace_id):
        assert workspace_id != env.source_id
        return lookup(db, owner_id, workspace_id)

    monkeypatch.setattr(sources.workspace_service, "get", workspace)
    sources.materialize(None, namespace, revision, definition)
    assert (target(root, revision) / "file.txt").read_text() == "published"


def test_source_directory_change_during_freeze_rejects_the_whole_revision(environment, monkeypatch):
    env = environment
    (env.source / "src").mkdir()
    (env.source / "src" / "first.py").write_text("before = True\n")
    original = sources._read

    def changing_read(*args, **kwargs):
        result = original(*args, **kwargs)
        (env.source / "src" / "arrived.py").write_text("changed = True\n")
        return result

    monkeypatch.setattr(sources, "_read", changing_read)
    revision = str(uuid4())
    with pytest.raises(AppError) as changed:
        sources.prepare_revision(None, env.owner, revision, env.definition(["src"]))
    assert changed.value.code == "agent_source_changed"
    assert not (env.data / "agent-api" / "source-revisions" / revision).exists()


def test_missing_or_changed_snapshot_marker_is_not_treated_as_a_valid_revision(environment):
    env = environment
    (env.source / "file.txt").write_text("published")
    revision, definition = str(uuid4()), env.definition(["file.txt"])
    namespace, root = env.namespace()
    with pytest.raises(AppError):
        sources.materialize(None, namespace, revision, definition)
    sources.prepare_revision(None, env.owner, revision, definition)
    sources.materialize(None, namespace, revision, definition)
    marker = target(root, revision) / sources.MARKER
    marker.chmod(0o600)
    with pytest.raises(AppError) as denied:
        sources.materialize(None, namespace, revision, definition)
    assert denied.value.code == "agent_source_snapshot_invalid"


def test_empty_directory_is_an_explicit_read_only_snapshot_and_no_source_is_a_noop(environment):
    env = environment
    (env.source / "empty").mkdir()
    revision, definition = str(uuid4()), env.definition(["empty"])
    sources.prepare_revision(None, env.owner, revision, definition)
    namespace, root = env.namespace()
    sources.materialize(None, namespace, revision, definition)
    assert (target(root, revision) / "empty").is_dir()
    assert sources.materialize(
        None, namespace, str(uuid4()), dto.Definition(model_connection_id=uuid4())
    ) == {"source_revision": None, "source_paths": []}
