"""Persistent account controls and per-workspace inherited runtime defaults."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b46d1ca93f85"
down_revision = "e215d7c28b64"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "auth_sessions", sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "login_throttles",
        sa.Column("key_digest", sa.String(64), primary_key=True),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_login_throttles_window_started_at", "login_throttles", ["window_started_at"]
    )
    op.create_table(
        "account_command_receipts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "session_id", sa.String(36), sa.ForeignKey("auth_sessions.id", ondelete="SET NULL")
        ),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("request_digest", sa.String(64)),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.UniqueConstraint("user_id", "request_id"),
    )
    op.create_index("ix_account_command_receipts_user_id", "account_command_receipts", ["user_id"])
    op.add_column("runtime_defaults", sa.Column("model_connection_id", sa.String(36)))
    op.create_foreign_key(
        "fk_runtime_defaults_model",
        "runtime_defaults",
        "model_connections",
        ["model_connection_id"],
        ["id"],
    )
    op.create_table(
        "workspace_runtime_defaults",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("options", postgresql.JSONB(), nullable=False),
        sa.Column("model_connection_id", sa.String(36), sa.ForeignKey("model_connections.id")),
    )
    op.create_index(
        "ix_workspace_runtime_defaults_user_id", "workspace_runtime_defaults", ["user_id"]
    )


def downgrade():
    op.drop_table("workspace_runtime_defaults")
    op.drop_constraint("fk_runtime_defaults_model", "runtime_defaults", type_="foreignkey")
    op.drop_column("runtime_defaults", "model_connection_id")
    op.drop_table("account_command_receipts")
    op.drop_table("login_throttles")
    op.drop_column("auth_sessions", "last_active_at")
