"""Account preferences and versioned developer-tool settings."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "038_configuration_states"
down_revision = "037_conversation_naming"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "configuration_states",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("section", sa.String(), nullable=False),
        sa.Column("scope_key", sa.String(), nullable=False),
        sa.Column("values", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("user_id", "section", "scope_key"),
    )
    op.create_index("ix_configuration_states_user_id", "configuration_states", ["user_id"])


def downgrade():
    op.drop_index("ix_configuration_states_user_id", table_name="configuration_states")
    op.drop_table("configuration_states")
