from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from psycopg2.extras import RealDictCursor

from app.db import get_conn


def save_analysis(report: dict, organization_id: int) -> int:
    """
    Guarda y devuelve el ID creado.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO analyses (
                    organization_id,
                    filename,
                    pages,
                    chars,
                    document_type,
                    score,
                    status,
                    summary,
                    findings_json,
                    extracted_text,
                    text_sha256
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (
                    organization_id,
                    report["filename"],
                    report["pages"],
                    report["chars"],
                    report.get("document_type"),
                    report.get("score"),
                    report.get("status"),
                    report.get("summary"),
                    json.dumps(report.get("findings", [])),
                    None,  # Do not retain personal document text by default.
                    report.get("text_sha256"),
                ),
            )
            new_id = cur.fetchone()[0]
            conn.commit()
            return int(new_id)
    finally:
        conn.close()


def _build_filters(
    organization_id: int,
    filename: Optional[str],
    min_score: Optional[int],
    status: Optional[str],
    document_type: Optional[str],
) -> Tuple[str, List[Any]]:
    clauses: List[str] = ["organization_id = %s"]
    params: List[Any] = [organization_id]

    if filename:
        clauses.append("filename ILIKE %s")
        params.append(f"%{filename}%")

    if min_score is not None:
        clauses.append("score >= %s")
        params.append(min_score)

    if status:
        clauses.append("status = %s")
        params.append(status)

    if document_type:
        clauses.append("document_type = %s")
        params.append(document_type)

    return "WHERE " + " AND ".join(clauses), params


def list_analyses(
    organization_id: int,
    limit: int = 20,
    offset: int = 0,
    filename: Optional[str] = None,
    min_score: Optional[int] = None,
    status: Optional[str] = None,
    document_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    {
      "total": int,
      "items": [...],
      "limit": int,
      "offset": int
    }
    """
    where_sql, params = _build_filters(organization_id, filename, min_score, status, document_type)

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # total
            cur.execute(f"SELECT COUNT(*) AS total FROM analyses {where_sql};", params)
            total = int(cur.fetchone()["total"])

            # items
            cur.execute(
                f"""
                SELECT
                    id,
                    created_at,
                    filename,
                    pages,
                    chars,
                    document_type,
                    score,
                    status,
                    summary
                FROM analyses
                {where_sql}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s;
                """,
                params + [limit, offset],
            )
            items = cur.fetchall()
            return {"total": total, "items": items, "limit": limit, "offset": offset}
    finally:
        conn.close()


def get_analysis_by_id(analysis_id: int, organization_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    id,
                    created_at,
                    filename,
                    pages,
                    chars,
                    document_type,
                    score,
                    status,
                    summary,
                    findings_json
                FROM analyses
                WHERE id = %s AND organization_id = %s;
                """,
                (analysis_id, organization_id),
            )
            row = cur.fetchone()
            if not row:
                return None

            findings_raw = row.get("findings_json")
            if isinstance(findings_raw, str):
                try:
                    row["findings"] = json.loads(findings_raw)
                except Exception:
                    row["findings"] = []
            else:
                row["findings"] = findings_raw or []

            row.pop("findings_json", None)
            return row
    finally:
        conn.close()


def get_analysis_export(analysis_id: int, organization_id: int) -> Optional[Dict[str, Any]]:
    """
    Export completo: incluye findings + extracted_text + hash (si existen).
    """
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    id,
                    created_at,
                    filename,
                    pages,
                    chars,
                    document_type,
                    score,
                    status,
                    summary,
                    findings_json,
                    extracted_text,
                    text_sha256
                FROM analyses
                WHERE id = %s AND organization_id = %s;
                """,
                (analysis_id, organization_id),
            )
            row = cur.fetchone()
            if not row:
                return None

            findings_raw = row.get("findings_json")
            if isinstance(findings_raw, str):
                try:
                    row["findings"] = json.loads(findings_raw)
                except Exception:
                    row["findings"] = []
            else:
                row["findings"] = findings_raw or []

            row.pop("findings_json", None)
            return row
    finally:
        conn.close()


def get_user_for_login(email: str, organization_slug: str) -> Optional[Dict[str, Any]]:
    """Only active users and organizations with active memberships may log in."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT u.id AS user_id, o.id AS organization_id,
                       u.password_hash, m.role
                  FROM users u
                  JOIN memberships m ON m.user_id = u.id
                  JOIN organizations o ON o.id = m.organization_id
                 WHERE u.email = %s AND o.slug = %s
                   AND u.is_active AND o.is_active AND m.is_active;
                """,
                (email, organization_slug),
            )
            return cur.fetchone()
    finally:
        conn.close()


def get_membership_role(user_id: int, organization_id: int) -> Optional[str]:
    """Re-check the membership on every request, including exports."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.role FROM memberships m
                  JOIN users u ON u.id = m.user_id
                  JOIN organizations o ON o.id = m.organization_id
                 WHERE m.user_id = %s AND m.organization_id = %s
                   AND m.is_active AND u.is_active AND o.is_active;
                """,
                (user_id, organization_id),
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()
