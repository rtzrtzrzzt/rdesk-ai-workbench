"""Durable reply anchors, file effects and published Agent API authority."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "040_agent_api"
down_revision = "039_agent_conversations"
branch_labels = None
depends_on = None


def upgrade():

    op.create_table(
        "agent_services",
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("current_revision_id", sa.String(length=36), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_agent_services_owner_user_id"), "agent_services", ["owner_user_id"], unique=False
    )
    op.create_table(
        "agent_service_revisions",
        sa.Column("service_id", sa.String(length=36), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["agent_services.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_id", "number"),
    )
    op.create_index(
        op.f("ix_agent_service_revisions_service_id"),
        "agent_service_revisions",
        ["service_id"],
        unique=False,
    )
    op.create_table(
        "api_clients",
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("requests_per_minute", sa.Integer(), nullable=False),
        sa.Column("max_active_runs", sa.Integer(), nullable=False),
        sa.Column("request_window", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_api_clients_owner_user_id"), "api_clients", ["owner_user_id"], unique=False
    )
    op.create_table(
        "api_keys",
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["api_clients.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_digest"),
    )
    op.create_index(op.f("ix_api_keys_client_id"), "api_keys", ["client_id"], unique=False)
    op.create_table(
        "api_grants",
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("service_id", sa.String(length=36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("allowed_tools", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["api_clients.id"],
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["agent_services.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "service_id"),
    )
    op.create_index(op.f("ix_api_grants_client_id"), "api_grants", ["client_id"], unique=False)
    op.create_index(op.f("ix_api_grants_service_id"), "api_grants", ["service_id"], unique=False)
    op.create_table(
        "external_namespaces",
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("service_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["api_clients.id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["agent_services.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "service_id"),
        sa.UniqueConstraint("workspace_id"),
    )
    op.create_index(
        op.f("ix_external_namespaces_client_id"), "external_namespaces", ["client_id"], unique=False
    )
    op.create_index(
        op.f("ix_external_namespaces_owner_user_id"),
        "external_namespaces",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_external_namespaces_service_id"),
        "external_namespaces",
        ["service_id"],
        unique=False,
    )
    op.create_table(
        "execution_bindings",
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("origin", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("service_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["api_clients.id"],
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["external_namespaces.id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["agent_service_revisions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["agent_services.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_execution_bindings_client_id"), "execution_bindings", ["client_id"], unique=False
    )
    op.create_index(
        op.f("ix_execution_bindings_namespace_id"),
        "execution_bindings",
        ["namespace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_execution_bindings_owner_user_id"),
        "execution_bindings",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_execution_bindings_service_id"), "execution_bindings", ["service_id"], unique=False
    )
    op.create_table(
        "agent_session_bindings",
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["external_namespaces.id"],
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["agent_service_revisions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["threads.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id"),
    )
    op.create_index(
        op.f("ix_agent_session_bindings_namespace_id"),
        "agent_session_bindings",
        ["namespace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_session_bindings_revision_id"),
        "agent_session_bindings",
        ["revision_id"],
        unique=False,
    )
    op.create_table(
        "change_records",
        sa.Column("operation_id", sa.String(length=36), nullable=False),
        sa.Column("item_index", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("root_run_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("execution_binding_id", sa.String(length=36), nullable=True),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("before_version", sa.String(), nullable=True),
        sa.Column("after_version", sa.String(), nullable=True),
        sa.Column("before_exists", sa.Boolean(), nullable=False),
        sa.Column("after_exists", sa.Boolean(), nullable=False),
        sa.Column("before_artifact_id", sa.String(length=36), nullable=True),
        sa.Column("after_artifact_id", sa.String(length=36), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("attribution", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("gaps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["after_artifact_id"],
            ["artifacts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["before_artifact_id"],
            ["artifacts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["execution_binding_id"],
            ["execution_bindings.id"],
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["operations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["root_run_id"],
            ["runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", "item_index"),
    )
    op.create_index(
        op.f("ix_change_records_operation_id"), "change_records", ["operation_id"], unique=False
    )
    op.create_index(
        op.f("ix_change_records_root_run_id"), "change_records", ["root_run_id"], unique=False
    )
    op.create_index(op.f("ix_change_records_run_id"), "change_records", ["run_id"], unique=False)
    op.create_index(
        op.f("ix_change_records_workspace_id"), "change_records", ["workspace_id"], unique=False
    )
    op.create_table(
        "command_change_captures",
        sa.Column("operation_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("root_path", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("baseline", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("gaps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["operations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id"),
    )
    op.create_index(
        op.f("ix_command_change_captures_run_id"),
        "command_change_captures",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_command_change_captures_workspace_id"),
        "command_change_captures",
        ["workspace_id"],
        unique=False,
    )
    op.add_column(
        "runs", sa.Column("execution_origin", sa.String(), server_default="owner", nullable=False)
    )
    op.add_column("runs", sa.Column("execution_binding_id", sa.String(length=36), nullable=True))
    op.add_column("runs", sa.Column("final_message_id", sa.String(length=36), nullable=True))
    op.add_column("runs", sa.Column("final_attempt_id", sa.String(length=36), nullable=True))
    op.add_column("runs", sa.Column("finalized_event_seq", sa.Integer(), nullable=True))
    op.create_index("ix_runs_execution_binding_id", "runs", ["execution_binding_id"], unique=False)
    op.create_foreign_key(
        "fk_runs_execution_binding", "runs", "execution_bindings", ["execution_binding_id"], ["id"]
    )
    op.create_foreign_key("fk_run_final_message", "runs", "messages", ["final_message_id"], ["id"])
    op.create_foreign_key("fk_run_final_attempt", "runs", "attempts", ["final_attempt_id"], ["id"])
    op.create_foreign_key(
        "fk_agent_current_revision",
        "agent_services",
        "agent_service_revisions",
        ["current_revision_id"],
        ["id"],
    )

    op.create_check_constraint(
        "ck_run_execution_origin",
        "runs",
        "(execution_origin = 'owner' AND execution_binding_id IS NULL) OR "
        "(execution_origin = 'api' AND execution_binding_id IS NOT NULL)",
    )


def downgrade():
    op.drop_constraint("ck_run_execution_origin", "runs", type_="check")
    op.drop_constraint("fk_run_final_attempt", "runs", type_="foreignkey")
    op.drop_constraint("fk_run_final_message", "runs", type_="foreignkey")
    op.drop_constraint("fk_runs_execution_binding", "runs", type_="foreignkey")
    op.drop_index("ix_runs_execution_binding_id", table_name="runs")
    for column in (
        "finalized_event_seq",
        "final_attempt_id",
        "final_message_id",
        "execution_binding_id",
        "execution_origin",
    ):
        op.drop_column("runs", column)
    op.drop_constraint("fk_agent_current_revision", "agent_services", type_="foreignkey")
    op.drop_table("command_change_captures")
    op.drop_table("change_records")
    op.drop_table("agent_session_bindings")
    op.drop_table("execution_bindings")
    op.drop_table("external_namespaces")
    op.drop_table("api_grants")
    op.drop_table("api_keys")
    op.drop_table("api_clients")
    op.drop_table("agent_service_revisions")
    op.drop_table("agent_services")
