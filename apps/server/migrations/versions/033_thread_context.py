"""Persist active context windows and idempotent manual compaction requests."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "033_thread_context"
down_revision = "032_visible_reasoning"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "thread_contexts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.String(36), nullable=False, unique=True),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("covered_messages", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("summary_sources", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("transfer_scope", sa.String()),
        sa.Column("memory_fingerprint", sa.String()),
        sa.Column("state_artifact_id", sa.String()),
        sa.Column("summary_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model_connection_id", sa.String()),
        sa.Column("context_window", sa.Integer()),
        sa.Column("used_tokens", sa.Integer()),
        sa.Column("source", sa.String(), nullable=False, server_default="unavailable"),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("last_compaction_at", sa.DateTime(timezone=True)),
        sa.Column("before_tokens", sa.Integer()),
        sa.Column("after_tokens", sa.Integer()),
        sa.Column("request_id", sa.String()),
        sa.Column("request_run_id", sa.String()),
        sa.Column("compaction_status", sa.String(), nullable=False, server_default="idle"),
        sa.Column("compaction_error", sa.String()),
    )


def downgrade():
    op.drop_table("thread_contexts")

