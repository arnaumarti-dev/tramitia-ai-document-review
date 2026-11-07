"""CI-only regression: prove existing fictional tenant records survive PR migrations.

This intentionally works with the actual Alembic main-base revision and a
separate throwaway PostgreSQL CI database. Never run against production.
"""
from __future__ import annotations

import os
import sys

from app.db import get_conn


ORG_SLUG = "premerge-fictional-org"
USER_EMAIL = "premerge-fictional-owner@example.invalid"
REPORT_NAME = "fictional-application-before-upgrade.pdf"
REPORT_HASH = "1" * 64
EXPECTED_REVISION = "0007_analysis_access_audit"


def _assert_ci_only() -> None:
    db = os.getenv("POSTGRES_DB", "")
    if os.getenv("TRAMITIA_TEST_POSTGRES") != "1" or db != "tramitia_test":
        raise RuntimeError("Migration probe is only allowed on the exact disposable CI database")
    if os.getenv("POSTGRES_HOST") not in ("localhost", "127.0.0.1"):
        raise RuntimeError("Migration probe refuses remote database hosts")


def seed() -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            assert cur.fetchone()[0] == "0003_invites_password_revision"
            cur.execute(
                "INSERT INTO organizations (slug, name) VALUES (%s, %s) RETURNING id",
                (ORG_SLUG, "Gestoría Ficticia de Migraciones"),
            )
            organization_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
                (USER_EMAIL, "fictional-non-login-password-hash"),
            )
            user_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO memberships (organization_id, user_id, role)
                   VALUES (%s, %s, 'owner')""",
                (organization_id, user_id),
            )
            cur.execute(
                """INSERT INTO analyses (organization_id, filename, pages, chars,
                       document_type, score, status, summary, findings_json, text_sha256)
                   VALUES (%s, %s, 1, 120, 'subsidy_application', 74,
                           'REVISAR', %s, '[]'::jsonb, %s)""",
                (organization_id, REPORT_NAME, "Solo datos sintéticos", REPORT_HASH),
            )
        conn.commit()
        print("Fictional records seeded at main database revision")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def verify() -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            assert cur.fetchone()[0] == EXPECTED_REVISION
            cur.execute(
                """SELECT o.id, u.id, m.role, a.filename, a.score, a.text_sha256
                   FROM analyses a
                   JOIN organizations o ON a.organization_id = o.id
                   JOIN memberships m ON m.organization_id = o.id
                   JOIN users u ON m.user_id = u.id
                   WHERE o.slug = %s AND u.email = %s AND a.filename = %s""",
                (ORG_SLUG, USER_EMAIL, REPORT_NAME),
            )
            rows = cur.fetchall()
            assert len(rows) == 1, "Pre-merge migration lost or duplicated a report"
            organization_id, user_id, role, filename, score, digest = rows[0]
            assert role == "owner" and filename == REPORT_NAME and score == 74
            assert digest == REPORT_HASH
            for table in ("analysis_user_quotas", "analysis_org_quotas",
                          "ip_rate_limits", "analysis_deletion_audit",
                          "analysis_access_audit"):
                cur.execute("SELECT to_regclass(%s)", (table,))
                assert cur.fetchone()[0] is not None, f"Missing post-upgrade table: {table}"
            # Remove test fixture so the rest of the test suite begins clean.
            cur.execute("DELETE FROM analyses WHERE organization_id = %s", (organization_id,))
            cur.execute("DELETE FROM memberships WHERE organization_id = %s", (organization_id,))
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            cur.execute("DELETE FROM organizations WHERE id = %s", (organization_id,))
        conn.commit()
        print("In-place migration preserved fictitious tenant data; fixture cleaned")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    _assert_ci_only()
    if sys.argv[1:] == ["seed"]:
        seed()
    elif sys.argv[1:] == ["verify"]:
        verify()
    else:
        raise SystemExit("Usage: python -m scripts.premerge_migration_probe seed|verify")
