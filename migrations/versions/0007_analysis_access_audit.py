"""Audit successful report views/exports with minimal tenant-scoped metadata."""
from alembic import op
import sqlalchemy as sa

revision = "0007_analysis_access_audit"
down_revision = "0006_analysis_deletion_audit"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "analysis_access_audit",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("organization_id", sa.BigInteger(),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("analysis_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(
            "action IN ('analysis.viewed', 'analysis.exported')",
            name="analysis_access_audit_allowed_actions",
        ),
        sa.CheckConstraint(
            "actor_user_id > 0 AND analysis_id > 0",
            name="analysis_access_audit_positive_ids",
        ),
    )
    op.create_index(
        "ix_analysis_access_audit_org_time",
        "analysis_access_audit", ["organization_id", "occurred_at", "id"],
    )


def downgrade():
    # Downgrading removes evidence of successful access and exports.
    # Review retention and evidence-preservation obligations before using.
    op.drop_index(
        "ix_analysis_access_audit_org_time", table_name="analysis_access_audit"
    )
    op.drop_table("analysis_access_audit")
