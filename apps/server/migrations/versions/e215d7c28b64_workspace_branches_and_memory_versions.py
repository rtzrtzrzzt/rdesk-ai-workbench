"""Workspace lifecycle, explicit conversation branches and confirmed MD revisions."""

import sqlalchemy as sa
from alembic import op

revision = "e215d7c28b64"
down_revision = "8b71c5dc2b01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workspaces", sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("threads", sa.Column("parent_thread_id", sa.String(36), nullable=True))
    op.add_column("threads", sa.Column("fork_message_id", sa.String(36), nullable=True))
    op.create_foreign_key("fk_threads_parent", "threads", "threads", ["parent_thread_id"], ["id"])
    op.create_foreign_key(
        "fk_threads_fork_message", "threads", "messages", ["fork_message_id"], ["id"]
    )
    op.create_index("ix_threads_parent_thread_id", "threads", ["parent_thread_id"])
    op.create_table(
        "memory_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=True),
        sa.Column("scope_id", sa.String(36), sa.ForeignKey("memory_scopes.id"), nullable=False),
        sa.Column(
            "document_id", sa.String(36), sa.ForeignKey("memory_documents.id"), nullable=False
        ),
        sa.Column("scope_generation", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.String(), nullable=False, unique=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("origin", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "journal_id", sa.String(36), sa.ForeignKey("memory_write_journal.id"), nullable=False
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "scope_generation", "content_hash"),
    )
    for column in ("user_id", "scope_id", "document_id"):
        op.create_index(f"ix_memory_revisions_{column}", "memory_revisions", [column])


def downgrade():
    op.drop_table("memory_revisions")
    op.drop_index("ix_threads_parent_thread_id", table_name="threads")
    op.drop_constraint("fk_threads_fork_message", "threads", type_="foreignkey")
    op.drop_constraint("fk_threads_parent", "threads", type_="foreignkey")
    op.drop_column("threads", "fork_message_id")
    op.drop_column("threads", "parent_thread_id")
    op.drop_column("workspaces", "removed_at")
