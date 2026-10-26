"""Persist the immediate user-wide computer control switch."""

import sqlalchemy as sa
from alembic import op

revision = "034_computer_control_switch"
down_revision = "033_thread_context"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "automation_settings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )


def downgrade():
    op.drop_table("automation_settings")
