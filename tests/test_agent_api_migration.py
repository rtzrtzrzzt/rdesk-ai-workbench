"""039↔040 schema/data checks in a fresh temporary PostgreSQL, not product acceptance."""

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, inspect, select
from sqlalchemy.exc import IntegrityError
from test_api_authorization import isolated_postgres_engine  # noqa: F401 - pytest fixture
from workbench.infrastructure.database import Base, new_id, now
from workbench.modules.agent_services.domain import AgentService, AgentServiceRevision

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS = "039_agent_conversations"
CURRENT = "040_agent_api"


def legacy_rows(connection, workspace_root):
    metadata = MetaData()
    metadata.reflect(connection)
    source = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0]

    def insert(table, **values):
        row = {"id": new_id(), "created_at": now(), "version": 1, **values}
        connection.execute(metadata.tables[table].insert().values(**row))
        return row

    owner = insert("users", username="migration-preserved-owner", owner_slot=1)
    workspace = insert(
        "workspaces",
        user_id=owner["id"],
        name="Temporary migration workspace",
        root_path=str(workspace_root),
        archived=False,
    )
    thread = insert("threads", user_id=owner["id"], workspace_id=workspace["id"], title=source)
    run = insert(
        "runs",
        user_id=owner["id"],
        workspace_id=workspace["id"],
        thread_id=thread["id"],
        status="succeeded",
        input=source,
        last_event_seq=1,
        graph_version="1",
        config_snapshot={"migration_source": "README.md", "context_paths": []},
    )
    message = insert(
        "messages",
        thread_id=thread["id"],
        run_id=run["id"],
        role="assistant",
        content=source,
        status="completed",
    )
    step = insert(
        "run_steps",
        run_id=run["id"],
        logical_key="migration-final",
        kind="model",
        status="succeeded",
    )
    attempt = insert(
        "attempts",
        run_id=run["id"],
        step_id=step["id"],
        number=1,
        status="succeeded",
        message_id=message["id"],
        finished_at=now(),
    )
    insert(
        "run_events",
        run_id=run["id"],
        seq=1,
        type="message.completed",
        payload={"message_id": message["id"]},
    )
    names = (
        "users",
        "workspaces",
        "threads",
        "runs",
        "messages",
        "run_steps",
        "attempts",
        "run_events",
    )
    existing = {name: metadata.tables[name] for name in names}
    snapshot = read_existing(connection, existing)
    return owner, run, message, attempt, existing, snapshot


def read_existing(connection, existing):
    return {
        name: [dict(row) for row in connection.execute(select(table)).mappings()]
        for name, table in existing.items()
    }


def assert_current_schema(connection):
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    assert compare_metadata(context, Base.metadata) == []
    inspector = inspect(connection)
    for table, column, target in (
        ("agent_services", "current_revision_id", "agent_service_revisions"),
        ("runs", "final_message_id", "messages"),
        ("runs", "final_attempt_id", "attempts"),
    ):
        constraints = [
            foreign
            for foreign in inspector.get_foreign_keys(table)
            if foreign["constrained_columns"] == [column]
        ]
        assert len(constraints) == 1
        assert constraints[0]["referred_table"] == target


def test_039_data_survives_upgrade_and_fk_cycle_downgrade_reupgrade(
    isolated_postgres_engine, tmp_path  # noqa: F811 - imported pytest fixture
):
    engine = isolated_postgres_engine
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "apps/server/migrations"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, PREVIOUS)
        owner, legacy_run, message, attempt, existing, snapshot = legacy_rows(
            connection, workspace_root
        )
        command.upgrade(config, CURRENT)
        assert_current_schema(connection)
        assert read_existing(connection, existing) == snapshot
        metadata = MetaData()
        metadata.reflect(connection)
        runs = metadata.tables["runs"]
        upgraded = (
            connection.execute(select(runs).where(runs.c.id == legacy_run["id"])).mappings().one()
        )
        assert upgraded["execution_origin"] == "owner"
        assert upgraded["execution_binding_id"] is None
        assert upgraded["final_message_id"] is None
        connection.execute(
            runs.update()
            .where(runs.c.id == legacy_run["id"])
            .values(
                final_message_id=message["id"],
                final_attempt_id=attempt["id"],
                finalized_event_seq=1,
            )
        )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                runs.update()
                .where(runs.c.id == legacy_run["id"])
                .values(
                    final_message_id=new_id(),
                )
            )
        # Exercise the real cyclic reference before downgrade, not just empty DDL.
        service_id, revision_id = new_id(), new_id()
        connection.execute(
            AgentService.__table__.insert().values(
                id=service_id,
                owner_user_id=owner["id"],
                name="Migration cycle check",
            )
        )
        connection.execute(
            AgentServiceRevision.__table__.insert().values(
                id=revision_id,
                service_id=service_id,
                number=1,
                definition={},
            )
        )
        connection.execute(
            AgentService.__table__.update()
            .where(
                AgentService.__table__.c.id == service_id,
            )
            .values(current_revision_id=revision_id)
        )
        # One round trip specifically checks use_alter creation/drop ordering and duplicates.
        command.downgrade(config, PREVIOUS)
        assert read_existing(connection, existing) == snapshot
        assert "agent_services" not in inspect(connection).get_table_names()
        command.upgrade(config, CURRENT)
        assert_current_schema(connection)
        assert read_existing(connection, existing) == snapshot
