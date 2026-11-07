"""CI-only PostgreSQL backup/restore drill with synthetic tenant records.

This is NOT a production backup program. It tests whether a logical dump from
the fully migrated app can be restored to a *new disposable database* without
losing organization, membership, report or minimal audit records.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import tempfile

import psycopg2
from psycopg2 import sql


SOURCE_DB = "tramitia_test"
RESTORED_DB = "tramitia_restore_verify_ci"
ORG_SLUG = "premerge-restore-fictitious"
EMAIL = "premerge-restore-owner@example.invalid"
FILENAME = "only-fictional-pdf-report.pdf"
REPORT_HASH = "a" * 64
HEAD = "0007_analysis_access_audit"


def _assert_disposable_ci() -> None:
    if (os.environ.get("CI") != "true"
            or os.environ.get("TRAMITIA_TEST_POSTGRES") != "1"
            or os.environ.get("POSTGRES_DB") != SOURCE_DB
            or os.environ.get("POSTGRES_HOST") not in ("localhost", "127.0.0.1")
            or os.environ.get("POSTGRES_PORT") != "5432"
            or os.environ.get("POSTGRES_USER") != "tramitia"):
        raise RuntimeError("Backup/restore probe requires disposable local CI PostgreSQL")
    if not os.environ.get("POSTGRES_PASSWORD"):
        raise RuntimeError("No test database password configured")


def _conn(db: str):
    return psycopg2.connect(
        host="127.0.0.1",
        port=5432,
        dbname=db,
        user="tramitia",
        password=os.environ["POSTGRES_PASSWORD"],
        connect_timeout=5,
        application_name="tramitia_ci_restore_probe",
    )


def _fixture(conn):
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            if cur.fetchone()[0] != HEAD:
                raise RuntimeError("Cannot back up database at unexpected revision")
            cur.execute(
                "INSERT INTO organizations (slug, name) VALUES (%s,%s) RETURNING id",
                (ORG_SLUG, "Fictitious Restore Trial"),
            )
            org_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO users (email,password_hash) VALUES (%s,%s) RETURNING id",
                (EMAIL, "not-a-real-login-hash"),
            )
            user_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO memberships (organization_id,user_id,role) VALUES (%s,%s,'owner')",
                (org_id, user_id),
            )
            cur.execute(
                """INSERT INTO analyses (organization_id,filename,pages,chars,
                   document_type,score,status,summary,findings_json,text_sha256)
                   VALUES (%s,%s,1,77,'subsidy_application',85,'REVISAR',
                           'Fictitious report','[]'::jsonb,%s) RETURNING id""",
                (org_id, FILENAME, REPORT_HASH),
            )
            report_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO analysis_access_audit
                   (organization_id,actor_user_id,analysis_id,action)
                   VALUES (%s,%s,%s,'analysis.viewed')""",
                (org_id, user_id, report_id),
            )
            cur.execute(
                """INSERT INTO analysis_deletion_audit
                   (organization_id,actor_user_id,analysis_id)
                   VALUES (%s,%s,%s)""",
                (org_id, user_id, report_id),
            )
        conn.commit()
        return (org_id, user_id, report_id)
    except Exception:
        conn.rollback()
        raise


def _verify(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        if cur.fetchone()[0] != HEAD:
            raise RuntimeError("Wrong revision after restore")
        cur.execute(
            """SELECT o.slug,u.email,m.role,a.filename,a.score,a.text_sha256
               FROM analyses a JOIN organizations o ON o.id=a.organization_id
               JOIN memberships m ON m.organization_id=o.id
               JOIN users u ON u.id=m.user_id
               WHERE o.slug=%s AND u.email=%s AND a.filename=%s""",
            (ORG_SLUG, EMAIL, FILENAME),
        )
        expected = (ORG_SLUG, EMAIL, "owner", FILENAME, 85, REPORT_HASH)
        if cur.fetchall() != [expected]:
            raise RuntimeError("Restored tenant membership/report differ from backup")
        for table, action in (
            ("analysis_access_audit", "analysis.viewed"),
            ("analysis_deletion_audit", "analysis.deleted"),
        ):
            cur.execute(
                sql.SQL(
                    """SELECT COUNT(*) FROM {} a JOIN organizations o
                       ON o.id=a.organization_id
                       WHERE o.slug=%s AND a.action=%s"""
                ).format(sql.Identifier(table)),
                (ORG_SLUG, action),
            )
            if cur.fetchone()[0] != 1:
                raise RuntimeError("Restored minimal audit evidence missing: " + table)


def _docker_postgres(*arguments: str, directory: str, password: str):
    env = dict(os.environ, PGPASSWORD=password)
    command = [
        "docker", "run", "--rm", "--network=host",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-e", "PGPASSWORD",
        "--mount", f"type=bind,src={directory},dst=/backup",
        "postgres:16", *arguments,
    ]
    subprocess.run(command, check=True, timeout=150, env=env)


def main():
    _assert_disposable_ci()
    conn = _conn(SOURCE_DB)
    admin = None
    restored_created = False
    ids = None
    try:
        # Reserve a database name only after refusing any pre-existing target.
        admin = _conn("postgres")
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (RESTORED_DB,))
            if cur.fetchone():
                raise RuntimeError("Refusing to overwrite existing restore database")

        ids = _fixture(conn)
        with tempfile.TemporaryDirectory(prefix="tramitia-restore-ci-") as tmp:
            path = Path(tmp) / "synthetic.backup"
            _docker_postgres(
                "pg_dump", "--host=127.0.0.1", "--port=5432",
                "--username=tramitia", f"--dbname={SOURCE_DB}", "--format=custom",
                "--file=/backup/synthetic.backup",
                directory=tmp, password=os.environ["POSTGRES_PASSWORD"],
            )
            if path.stat().st_size < 1024 or (stat.S_IMODE(path.stat().st_mode) & 0o077):
                raise RuntimeError("Backup missing or too broadly readable")
            with admin.cursor() as cur:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(RESTORED_DB)))
                restored_created = True

            _docker_postgres(
                "pg_restore", "--host=127.0.0.1", "--port=5432",
                "--username=tramitia", f"--dbname={RESTORED_DB}",
                "--exit-on-error", "--no-owner", "--no-acl",
                "/backup/synthetic.backup",
                directory=tmp, password=os.environ["POSTGRES_PASSWORD"],
            )
            restored_conn = _conn(RESTORED_DB)
            try:
                _verify(restored_conn)
                restored_conn.rollback()
            finally:
                restored_conn.close()
        print("CI: PostgreSQL custom dump/restore preserved fictional tenant and audit data")
    finally:
        if admin is not None:
            try:
                if restored_created:
                    with admin.cursor() as cur:
                        cur.execute(
                            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                                sql.Identifier(RESTORED_DB)
                            )
                        )
            finally:
                admin.close()
        if ids is not None:
            org_id, user_id, _ = ids
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM analyses WHERE organization_id=%s", (org_id,))
                    cur.execute("DELETE FROM organizations WHERE id=%s", (org_id,))
                    cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        conn.close()


if __name__ == "__main__":
    main()
