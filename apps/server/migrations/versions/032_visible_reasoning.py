"""Persist provider-visible reasoning separately from private protocol state."""

import sqlalchemy as sa
from alembic import op

revision = "032_visible_reasoning"
down_revision = "031_workflow_safety"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("reasoning", sa.Text(), nullable=False, server_default=""))
    # Raise former product defaults for new tasks; custom values and frozen
    # running-task snapshots retain their explicit settings.
    for table in ("runtime_defaults", "workspace_runtime_defaults"):
        op.execute(
            f"UPDATE {table} SET options=options || "
            "CASE WHEN (options->>'max_model_rounds')::int = 1024 "
            "THEN jsonb_build_object('max_model_rounds',65536) ELSE '{}'::jsonb END || "
            "CASE WHEN (options->>'root_model_attempt_limit')::int = 8192 "
            "THEN jsonb_build_object('root_model_attempt_limit',524288) ELSE '{}'::jsonb END"
        )


def downgrade():
    op.drop_column("messages", "reasoning")
