"""Bind accepted input to the successful model attempt that actually received it."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d4f2a7c19b60"
down_revision = "c9e85d1b473a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "attempts",
        sa.Column("input_message_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column("agent_messages", sa.Column("delivered_attempt_id", sa.String(36)))
    op.create_foreign_key(
        "fk_agent_messages_delivered_attempt",
        "agent_messages",
        "attempts",
        ["delivered_attempt_id"],
        ["id"],
    )


def downgrade():
    op.drop_constraint("fk_agent_messages_delivered_attempt", "agent_messages", type_="foreignkey")
    op.drop_column("agent_messages", "delivered_attempt_id")
    op.drop_column("attempts", "input_message_ids")
