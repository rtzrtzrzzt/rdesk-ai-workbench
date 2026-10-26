"""Persist opt-in naming state for newly created ordinary conversations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "037_conversation_naming"
down_revision = "036_media_history"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "threads", sa.Column("title_source", sa.String(), nullable=False, server_default="legacy")
    )
    op.add_column(
        "threads",
        sa.Column("title_naming", postgresql.JSONB(), nullable=False, server_default="{}"),
    )


def downgrade():
    op.drop_column("threads", "title_naming")
    op.drop_column("threads", "title_source")
