"""Authorization/persistence checks on a disposable PostgreSQL cluster, never UI acceptance.

Only native binaries are reused. No product DSN, database, model endpoint, or
running service is opened; every row and filesystem effect lives under pytest tmp.
"""

import hashlib
import subprocess
from dataclasses import dataclass
from datetime import timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from workbench.composition import tables  # noqa: F401 - register actual FK targets
from workbench.config import Settings
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import Base, new_id, now
from workbench.modules.access import dto as access_dto
from workbench.modules.access import service as access
from workbench.modules.access.domain import (
    ApiClient,
    ApiKey,
    ExecutionBinding,
    ExternalNamespace,
)
from workbench.modules.agent_services import dto as agent_dto
from workbench.modules.agent_services import public
from workbench.modules.agent_services import service as agents
from workbench.modules.agent_services.domain import AgentSessionBinding
from workbench.modules.context.compiler import compile_context
from workbench.modules.conversations import Thread
from workbench.modules.identity import User
from workbench.modules.memory import files as memory_files
from workbench.modules.memory import service as memory
from workbench.modules.models.domain import ModelConnection, ModelRevision
from workbench.modules.runs.domain import Run
from workbench.modules.workspaces import Workspace


@pytest.fixture(scope="module")
def isolated_postgres_engine(tmp_path_factory):
    source = Path(__file__).resolve().parents[1] / "scripts/dev/postgres.py"
    spec = spec_from_file_location("authorization_test_postgres", source)
    assert spec is not None and spec.loader is not None
    postgres = module_from_spec(spec)
    spec.loader.exec_module(postgres)
    bindir = postgres.binaries()
    if bindir is None:
        pytest.skip(
            "Native PostgreSQL 16 binaries are required; no download or product DB fallback"
        )
    root = tmp_path_factory.mktemp("api-authorization-pg")
    root.chmod(0o700)
    data = root / "data"
    socket_dir = root / "socket"
    socket_dir.mkdir(mode=0o700)
    environment = postgres.environment()

    def pg(command):
        return subprocess.run(
            [str(part) for part in command],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=40,
        )

    pg(
        [
            bindir / "initdb",
            "-D",
            data,
            "-U",
            "authorization_test",
            "--auth=trust",
            "--encoding=UTF8",
            "--locale=C.UTF-8",
            "--no-instructions",
        ]
    )
    # A unique private Unix socket directory avoids TCP and any product port.
    pg(
        [
            bindir / "pg_ctl",
            "-D",
            data,
            "-l",
            root / "postgres.log",
            "-w",
            "-t",
            "30",
            "-o",
            f"-h '' -k {socket_dir} -p 55499 -c jit=off -c fsync=off",
            "start",
        ]
    )
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username="authorization_test",
            database="postgres",
            query={"host": str(socket_dir), "port": "55499"},
        )
    )
    try:
        yield engine
    finally:
        engine.dispose()
        pg([bindir / "pg_ctl", "-D", data, "-m", "fast", "-w", "-t", "30", "stop"])


@pytest.fixture(scope="module")
def authorization_engine(isolated_postgres_engine):
    Base.metadata.create_all(isolated_postgres_engine)
    return isolated_postgres_engine


@pytest.fixture
def authorization_db(authorization_engine, tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "private")
    monkeypatch.setattr(access, "get_settings", lambda: settings)
    monkeypatch.setattr(memory_files, "get_settings", lambda: settings)
    with authorization_engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
            yield db, settings.data_dir, tmp_path
        transaction.rollback()


@dataclass
class Publication:
    owner: User
    model: ModelConnection
    revision: ModelRevision
    service: dict
    client: ApiClient
    namespace: ExternalNamespace


def publication(db, *, owner_slot=1):
    owner = User(id=new_id(), username=f"authorization-owner-{owner_slot}", owner_slot=owner_slot)
    db.add(owner)
    db.flush()
    model = ModelConnection(
        id=new_id(),
        user_id=owner.id,
        name="Protocol-only connection; never invoked",
        provider_type="openai_compatible",
        endpoint="http://127.0.0.1:1/v1",
        model_identifier="authorization-protocol-only",
    )
    db.add(model)
    db.flush()
    revision = ModelRevision(
        id=new_id(),
        connection_id=model.id,
        connection_version=model.version,
        configuration={"generation": {"prompt_template": {"mode": "native"}}},
    )
    db.add(revision)
    db.flush()
    service = agents.publish(
        db,
        owner.id,
        agent_dto.PublishAgent(
            name="Authorization protocol service",
            definition=agent_dto.Definition(
                model_connection_id=UUID(model.id),
                allowed_tools=["read_file", "write_file"],
                instructions="只做明确要求或完成目标必需的工作。",
            ),
        ),
    )
    receipt = access.create_client(
        db,
        owner.id,
        access_dto.ClientCreate(
            request_id=UUID(new_id()),
            name="Isolated protocol client",
            max_active_runs=3,
        ),
    )
    client = db.get(ApiClient, receipt["id"])
    access.save_grant(
        db,
        owner.id,
        client.id,
        service["id"],
        access_dto.GrantSave(
            actions=["invoke", "read", "write", "files"],
            allowed_tools=None,
        ),
    )
    namespace = access.get_namespace(db, client.id, service["id"])
    db.flush()
    return Publication(owner, model, revision, service, client, namespace)


def bound_run(db, published, *, status="queued", parent=None):
    binding = access.create_execution_binding(
        db,
        published.client,
        published.service["id"],
        published.service["revision_id"],
        published.namespace.id,
        {"allowed_tools": ["exec_command"]},
    )
    thread = Thread(
        id=new_id(),
        user_id=published.owner.id,
        workspace_id=published.namespace.workspace_id,
    )
    db.add(thread)
    db.flush()
    db.add(
        AgentSessionBinding(
            thread_id=thread.id,
            namespace_id=published.namespace.id,
            revision_id=published.service["revision_id"],
        )
    )
    run = Run(
        id=new_id(),
        user_id=published.owner.id,
        workspace_id=published.namespace.workspace_id,
        thread_id=thread.id,
        model_connection_id=published.model.id,
        status=status,
        input="检查明确授权的工具和资料范围。",
        execution_origin="api",
        execution_binding_id=binding.id,
        parent_run_id=parent.id if parent else None,
        root_run_id=parent.id if parent else None,
        config_snapshot={"model_revision_id": published.revision.id},
    )
    db.add(run)
    db.flush()
    return run, binding


def test_key_digest_rotation_and_revocation_keep_client_execution_identity(authorization_db):
    db, _, _ = authorization_db
    published = publication(db)
    command = access_dto.KeyIssue(request_id=UUID(new_id()))
    first = access.issue_key(db, published.owner.id, published.client.id, command)
    second = access.issue_key(
        db, published.owner.id, published.client.id, access_dto.KeyIssue(request_id=UUID(new_id()))
    )
    db.commit()
    first_row = db.get(ApiKey, first["id"])
    assert first_row.token_digest == hashlib.sha256(first["key"].encode()).hexdigest()
    assert first_row.token_digest != first["key"]
    assert access.issue_key(db, published.owner.id, published.client.id, command)["key"] is None
    assert access.authenticate(db, first["key"]).id == published.client.id
    assert access.authenticate(db, second["key"]).id == published.client.id
    run, binding = bound_run(db, published)
    first_row.revoked_at = now()
    db.commit()
    with pytest.raises(AppError) as revoked:
        access.authenticate(db, first["key"])
    assert (revoked.value.code, revoked.value.status) == ("api_key_invalid", 401)
    rotated_client = access.authenticate(db, second["key"])
    assert access.require_run(db, rotated_client.id, run).id == binding.id
    assert public.get_run(db, rotated_client, run.id)["id"] == run.id


@pytest.mark.parametrize("restriction", ["expired", "client_disabled"])
def test_authentication_rechecks_current_key_and_client_state(authorization_db, restriction):
    db, _, _ = authorization_db
    published = publication(db)
    key = access.issue_key(
        db, published.owner.id, published.client.id, access_dto.KeyIssue(request_id=UUID(new_id()))
    )
    if restriction == "expired":
        db.get(ApiKey, key["id"]).expires_at = now() - timedelta(seconds=1)
    else:
        published.client.enabled = False
    db.commit()
    with pytest.raises(AppError) as denied:
        access.authenticate(db, key["key"])
    assert denied.value.status in {401, 403}


def test_revoked_grant_and_revised_service_reduce_existing_run_scope(authorization_db):
    db, _, _ = authorization_db
    published = publication(db)
    run, binding = bound_run(db, published)
    assert binding.snapshot["allowed_tools"] == ["read_file", "write_file"]
    assert access.scope_for_run(db, run).allowed_tools == {"read_file", "write_file"}
    grant = access.save_grant(
        db,
        published.owner.id,
        published.client.id,
        published.service["id"],
        access_dto.GrantSave(expected_version=1, allowed_tools=["read_file"]),
    )
    db.commit()
    assert access.scope_for_run(db, run).allowed_tools == {"read_file"}
    with pytest.raises(AppError) as tool_denied:
        access.authorize_tool(db, run, "write_file", {})
    assert tool_denied.value.code == "api_tool_denied"
    revised = agents.revise(
        db,
        published.owner.id,
        published.service["id"],
        agent_dto.ReviseAgent(
            expected_version=published.service["version"],
            name=published.service["name"],
            definition=agent_dto.Definition(
                model_connection_id=published.model.id, allowed_tools=["write_file"]
            ),
        ),
    )
    db.commit()
    assert revised["revision_id"] != binding.revision_id
    assert access.scope_for_run(db, run).allowed_tools == frozenset()
    access.save_grant(
        db,
        published.owner.id,
        published.client.id,
        published.service["id"],
        access_dto.GrantSave(expected_version=grant["version"], enabled=False),
    )
    db.commit()
    with pytest.raises(AppError) as revoked:
        access.scope_for_run(db, run)
    assert revoked.value.status == 403


def test_public_origin_without_binding_fails_in_scope_and_postgres_constraint(authorization_db):
    db, _, _ = authorization_db
    published = publication(db)
    unbound = Run(
        id=new_id(),
        user_id=published.owner.id,
        workspace_id=published.namespace.workspace_id,
        input="检查缺失授权的拒绝路径。",
        execution_origin="api",
    )
    with pytest.raises(AppError) as denied:
        access.scope_for_run(db, unbound)
    assert denied.value.code == "execution_binding_invalid"
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(unbound)
        db.flush()


def test_other_client_namespace_and_reads_are_rejected(authorization_db):
    db, _, _ = authorization_db
    published = publication(db)
    run, _ = bound_run(db, published)
    other_receipt = access.create_client(
        db,
        published.owner.id,
        access_dto.ClientCreate(
            request_id=UUID(new_id()),
            name="Second isolated protocol client",
        ),
    )
    other = db.get(ApiClient, other_receipt["id"])
    access.save_grant(
        db, published.owner.id, other.id, published.service["id"], access_dto.GrantSave()
    )
    other_namespace = access.get_namespace(db, other.id, published.service["id"])
    with pytest.raises(AppError) as mismatch:
        access.create_execution_binding(
            db,
            published.client,
            published.service["id"],
            published.service["revision_id"],
            other_namespace.id,
            {},
        )
    assert mismatch.value.status == 403
    with pytest.raises(AppError) as hidden:
        public.get_run(db, other, run.id)
    assert (hidden.value.code, hidden.value.status) == ("run_not_found", 404)
    with pytest.raises(AppError) as hidden:
        access.require_run(db, other.id, run)
    assert hidden.value.status == 404
    run.workspace_id = other_namespace.workspace_id
    db.flush()
    with pytest.raises(AppError) as mismatch:
        access.scope_for_run(db, run)
    assert mismatch.value.code == "execution_binding_invalid"


def test_binding_survives_parent_and_root_relation_rewrite(authorization_db):
    db, _, _ = authorization_db
    published = publication(db)
    root, _ = bound_run(db, published, status="succeeded")
    child, binding = bound_run(db, published, parent=root)
    child.root_run_id = None
    child.parent_run_id = None
    db.commit()
    db.expire_all()
    recovered = db.get(Run, child.id)
    assert access.binding_for_run(db, recovered).id == binding.id
    assert access.scope_for_run(db, recovered).client_id == published.client.id
    assert public.get_run(db, published.client, recovered.id)["id"] == child.id


@pytest.mark.parametrize("field", ["owner_user_id", "client_id", "service_id"])
def test_persisted_namespace_identity_mismatch_fails_closed(authorization_db, field):
    db, _, _ = authorization_db
    published = publication(db, owner_slot=1)
    other = publication(db, owner_slot=2)
    run, _ = bound_run(db, published)
    setattr(published.namespace, field, getattr(other.namespace, field))
    db.commit()
    with pytest.raises(AppError) as mismatch:
        access.scope_for_run(db, run)
    assert (mismatch.value.code, mismatch.value.status) == ("execution_binding_invalid", 403)


def test_public_compiler_excludes_both_owners_real_private_rule_files(authorization_db):
    db, private, _ = authorization_db
    publications = [publication(db, owner_slot=slot) for slot in (1, 2)]
    # Exact fragments supplied in the user's AGENTS instructions, stored as real files.
    private_rules = ["不得覆盖或回退无关改动。", "下载依赖统一使用默认源。"]
    for published, rule in zip(publications, private_rules, strict=True):
        path = private / "users" / published.owner.id / "AGENTS.md"
        path.parent.mkdir(parents=True)
        path.write_text(rule, encoding="utf-8")
        loaded = memory.get_global_rules(db, published.owner.id, published.namespace.workspace_id)
        assert loaded.exists and loaded.body == rule
    for published in publications:
        run, _ = bound_run(db, published)
        compiled = compile_context(db, run, [{"role": "user", "content": run.input}], [])
        db.commit()
        system = compiled[0]["content"]
        assert published.service["definition"]["instructions"] in system
        assert "其它对话当前读取范围：disabled" in system
        assert run.config_snapshot["context_manifest"]["rules"] == []
        for rule in private_rules:
            assert rule not in system


@pytest.mark.parametrize("status", ["queued", "running", "waiting_user"])
def test_namespace_is_idempotent_and_queued_or_active_capacity_is_bounded(authorization_db, status):
    db, private, _ = authorization_db
    published = publication(db)
    published.client.max_active_runs = 1
    db.commit()
    namespaces_before = db.scalar(select(func.count()).select_from(ExternalNamespace))
    workspaces_before = db.scalar(select(func.count()).select_from(Workspace))
    for _ in range(3):
        assert (
            access.get_namespace(db, published.client.id, published.service["id"]).id
            == published.namespace.id
        )
    assert db.scalar(select(func.count()).select_from(ExternalNamespace)) == namespaces_before
    assert db.scalar(select(func.count()).select_from(Workspace)) == workspaces_before
    workspace_root = Path(db.get(Workspace, published.namespace.workspace_id).root_path)
    assert workspace_root.is_dir() and workspace_root.is_relative_to(private.parent)
    assert len(list(workspace_root.parent.iterdir())) == 1
    run, _ = bound_run(db, published, status=status)
    bindings_before = db.scalar(select(func.count()).select_from(ExecutionBinding))
    with pytest.raises(AppError) as capacity:
        bound_run(db, published)
    assert (capacity.value.code, capacity.value.status, capacity.value.retryable) == (
        "api_capacity",
        429,
        True,
    )
    assert db.scalar(select(func.count()).select_from(ExecutionBinding)) == bindings_before
    run.status = "succeeded"
    db.commit()
    replacement, _ = bound_run(db, published)
    assert replacement.status == "queued"
