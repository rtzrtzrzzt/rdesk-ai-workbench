"""Durable follow-ups, conversation lifecycle and independent approval reviews."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "031_workflow_safety"
down_revision = "030_local_identity"
branch_labels = None
depends_on = None


def entity_columns():
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    ]


def upgrade():
    for name in ("archived", "deleting", "queue_paused"):
        op.add_column(
            "threads", sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.add_column("threads", sa.Column("queue_resume_run_id", sa.String(), nullable=True))
    op.add_column(
        "threads",
        sa.Column("fork_include_anchor", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("approvals", sa.Column("review", postgresql.JSONB(), nullable=True))
    op.create_table(
        "queued_inputs",
        *entity_columns(),
        sa.Column("thread_id", sa.String(36), sa.ForeignKey("threads.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("error", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_queued_inputs_thread_id", "queued_inputs", ["thread_id"])
    op.create_index("ix_queued_inputs_user_id", "queued_inputs", ["user_id"])
    op.create_table(
        "conversation_deletions",
        *entity_columns(),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("target_thread_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("intent", postgresql.JSONB(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
    )
    for name in ("user_id", "target_thread_id", "status"):
        op.create_index(f"ix_conversation_deletions_{name}", "conversation_deletions", [name])
    # Existing Run snapshots are immutable. Only saved defaults for NEW tasks
    # move away from the former small system ceilings.
    for table in ("runtime_defaults", "workspace_runtime_defaults"):
        op.execute(
            f"UPDATE {table} SET options=options || "
            "CASE WHEN (options->>'max_model_rounds')::int <= 64 "
            "THEN jsonb_build_object('max_model_rounds',1024) ELSE '{}'::jsonb END || "
            "CASE WHEN (options->>'root_model_attempt_limit')::int <= 200 "
            "THEN jsonb_build_object('root_model_attempt_limit',8192) ELSE '{}'::jsonb END"
        )


def downgrade():
    op.drop_table("conversation_deletions")
    op.drop_table("queued_inputs")
    op.drop_column("approvals", "review")
    for name in (
        "fork_include_anchor",
        "queue_resume_run_id",
        "queue_paused",
        "deleting",
        "archived",
    ):
        op.drop_column("threads", name)
