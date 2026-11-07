"""Persist short-lived, pseudonymous per-peer request limits.

This is only an application-layer safeguard; front-door reverse proxies
must also rate-limit, bound connections, and correctly handle client IPs.
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_ip_rate_limits"
down_revision = "0004_analysis_quotas"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ip_rate_limits",
        sa.Column("key_hash", sa.String(length=64), primary_key=True),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("request_count >= 1", name="ip_rate_count_positive"),
    )
    op.create_index(
        "ix_ip_rate_limits_window_started_at", "ip_rate_limits",
        ["window_started_at"],
    )


def downgrade():
    op.drop_index("ix_ip_rate_limits_window_started_at", table_name="ip_rate_limits")
    op.drop_table("ip_rate_limits")
