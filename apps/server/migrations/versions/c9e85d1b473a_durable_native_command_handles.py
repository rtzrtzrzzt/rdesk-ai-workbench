"""Durable long-command ownership and immutable log artifact references."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c9e85d1b473a"
down_revision = "b46d1ca93f85"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "command_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column(
            "operation_id",
            sa.String(36),
            sa.ForeignKey("operations.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("nonce", sa.String(), nullable=False),
        sa.Column("command", sa.String(), nullable=False),
        sa.Column("sandbox_profile", sa.String(), nullable=False),
        sa.Column("argv", postgresql.JSONB(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_output_bytes", sa.Integer(), nullable=False),
        sa.Column("output_path", sa.String(), nullable=False, unique=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("native_identity", postgresql.JSONB()),
        sa.Column("receipt", postgresql.JSONB()),
        sa.Column("stop_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "resources_closed", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("log_artifacts", postgresql.JSONB(), nullable=False),
        sa.Column("error", postgresql.JSONB()),
    )
    for column in ("user_id", "workspace_id", "run_id", "status"):
        op.create_index(f"ix_command_executions_{column}", "command_executions", [column])


def downgrade():
    op.drop_table("command_executions")
