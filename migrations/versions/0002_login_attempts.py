"""Persist login failure rate limits across workers.

Revision ID: 0002_login_attempts
Revises: 0001_tenant_foundation
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_login_attempts"
down_revision = "0001_tenant_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "login_attempts",
        sa.Column("key_hash", sa.String(length=64), primary_key=True),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_table("login_attempts")
