"""Keep owner data, remove application login passwords and revoke old sessions."""

from alembic import op

revision = "030_local_identity"
down_revision = "d4f2a7c19b60"
branch_labels = None
depends_on = None


def upgrade():
    # Owner IDs and every ownership FK remain unchanged. Vault key material and
    # encrypted credentials are independent and are deliberately untouched.
    op.execute("UPDATE auth_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE revoked_at IS NULL")
    op.execute("DELETE FROM login_throttles")
    op.execute("UPDATE account_command_receipts SET request_digest=NULL")
    op.drop_column("users", "password_hash")


def downgrade():
    raise RuntimeError("应用登录密码已移除；无法从迁移还原已删除的密码hash。")
