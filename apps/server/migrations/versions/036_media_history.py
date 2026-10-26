"""Deferred bulk history removal; generated artifacts retain their ownership."""

import sqlalchemy as sa
from alembic import op

revision = "036_media_history"
down_revision = "035_extensions_media"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("media_tasks", sa.Column("deletion_requested_at", sa.DateTime(timezone=True)))
    op.add_column("media_tasks", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_media_tasks_history",
        "media_tasks",
        ["created_at", "id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade():
    op.drop_index("ix_media_tasks_history", table_name="media_tasks")
    op.drop_column("media_tasks", "deleted_at")
    op.drop_column("media_tasks", "deletion_requested_at")
