"""One-time onboarding invitations and global session invalidation.

Revision ID: 0003_invites_password_revision
Revises: 0002_login_attempts
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_invites_password_revision"
down_revision = "0002_login_attempts"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column(
        "session_version", sa.BigInteger(), nullable=False, server_default="0"
    ))
    op.create_table(
        "staff_invitations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("organization_id", sa.BigInteger(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("invited_by", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('employee', 'admin')", name="invite_allowed_role"),
    )
    op.create_index("ix_staff_invitations_org_email", "staff_invitations",
                    ["organization_id", "email"])


def downgrade():
    op.drop_index("ix_staff_invitations_org_email", table_name="staff_invitations")
    op.drop_table("staff_invitations")
    op.drop_column("users", "session_version")
