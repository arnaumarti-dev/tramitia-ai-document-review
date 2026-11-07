"""Persist tenant and user PDF-analysis quotas across API workers.

Revision ID: 0004_analysis_quotas
Revises: 0003_invites_password_revision
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_analysis_quotas"
down_revision = "0003_invites_password_revision"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "analysis_user_quotas",
        sa.Column("user_id", sa.BigInteger(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.CheckConstraint("used >= 1", name="analysis_user_quota_positive"),
    )
    op.create_table(
        "analysis_org_quotas",
        sa.Column("organization_id", sa.BigInteger(),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.CheckConstraint("used >= 1", name="analysis_org_quota_positive"),
    )


def downgrade():
    op.drop_table("analysis_org_quotas")
    op.drop_table("analysis_user_quotas")
