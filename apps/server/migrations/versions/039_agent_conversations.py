"""Stable agent conversations, execution turns and typed bidirectional mail."""

import sqlalchemy as sa
from alembic import op

revision = "039_agent_conversations"
down_revision = "038_configuration_states"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_turns",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agent_slots.id"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False, unique=True),
    )
    op.create_index("ix_agent_turns_agent_id", "agent_turns", ["agent_id"])
    op.execute(
        "INSERT INTO agent_turns (id, created_at, version, agent_id, run_id) "
        "SELECT run_id, created_at, 1, id, run_id FROM agent_slots"
    )
    op.add_column(
        "agent_messages", sa.Column("kind", sa.String(), nullable=False, server_default="note")
    )
    op.add_column("agent_messages", sa.Column("reply_to_id", sa.String(), nullable=True))


def downgrade():
    op.drop_column("agent_messages", "reply_to_id")
    op.drop_column("agent_messages", "kind")
    op.drop_index("ix_agent_turns_agent_id", table_name="agent_turns")
    op.drop_table("agent_turns")
