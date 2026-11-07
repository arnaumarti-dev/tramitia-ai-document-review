"""Audit successful deletions without document contents; retention is not yet automated."""
from alembic import op
import sqlalchemy as sa

revision = "0006_analysis_deletion_audit"
down_revision = "0005_ip_rate_limits"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "analysis_deletion_audit",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("organization_id", sa.BigInteger(),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("analysis_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False,
                  server_default="analysis.deleted"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("action = 'analysis.deleted'",
                           name="analysis_deletion_audit_action"),
        sa.CheckConstraint("actor_user_id > 0 AND analysis_id > 0",
                           name="analysis_deletion_audit_positive_ids"),
    )
    op.create_index("ix_analysis_deletion_audit_org_time",
                    "analysis_deletion_audit", ["organization_id", "occurred_at", "id"])


def downgrade():
    # Dropping this table destroys audit events: review before downgrading.
    op.drop_index("ix_analysis_deletion_audit_org_time", table_name="analysis_deletion_audit")
    op.drop_table("analysis_deletion_audit")
