"""Create organizations and protect existing analysis data.

Revision ID: 0001_tenant_foundation
Revises:
This migration preserves legacy analysis rows with NULL organization_id.
Those rows are intentionally invisible to all authenticated users and must
never be automatically attributed to the first created organization.
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_tenant_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "organizations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{1,78}[a-z0-9]$'", name="organization_slug_format"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint("email = lower(email)", name="user_email_lowercase"),
    )
    op.create_table(
        "memberships",
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("organization_id", sa.BigInteger(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'admin', 'employee')", name="membership_allowed_role"),
    )
    # A historic installation may have created analyses manually using README.
    op.execute("""
        CREATE TABLE IF NOT EXISTS analyses (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            filename TEXT NOT NULL,
            pages INTEGER,
            chars INTEGER,
            document_type TEXT,
            score INTEGER,
            status TEXT,
            summary TEXT,
            findings_json JSONB,
            extracted_text TEXT,
            text_sha256 TEXT
        );
    """)
    op.add_column("analyses", sa.Column("organization_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_analysis_organization", "analyses", "organizations",
        ["organization_id"], ["id"], ondelete="RESTRICT",
    )
    # PostgreSQL validates new/updated rows without rejecting old NULL legacy rows.
    op.execute("""
        ALTER TABLE analyses ADD CONSTRAINT ck_new_analysis_has_organization
        CHECK (organization_id IS NOT NULL) NOT VALID;
    """)
    op.create_index("ix_analyses_tenant_created", "analyses", ["organization_id", "created_at"])


def downgrade():
    op.drop_index("ix_analyses_tenant_created", table_name="analyses")
    op.execute("ALTER TABLE analyses DROP CONSTRAINT ck_new_analysis_has_organization")
    op.drop_constraint("fk_analysis_organization", "analyses", type_="foreignkey")
    op.drop_column("analyses", "organization_id")
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("organizations")
    # Preserve the original analyses table and old records.
