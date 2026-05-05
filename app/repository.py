from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from psycopg2.extras import RealDictCursor

from app.db import get_conn


def save_analysis(report: dict) -> int:
    """
    Guarda y devuelve el ID creado.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO analyses (
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
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (
                    report["filename"],
                    report["pages"],
                    report["chars"],
                    report.get("document_type"),
                    report.get("score"),
                    report.get("status"),
                    report.get("summary"),
                    json.dumps(report.get("findings", [])),
                    report.get("extracted_text"),
                    report.get("text_sha256"),
                ),
            )
            new_id = cur.fetchone()[0]
            conn.commit()
            return int(new_id)
    finally:
        conn.close()


def _build_filters(
    filename: Optional[str],
    min_score: Optional[int],
    status: Optional[str],
    document_type: Optional[str],
) -> Tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []

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

    if not clauses:
        return "", params

    return "WHERE " + " AND ".join(clauses), params


def list_analyses(
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
    where_sql, params = _build_filters(filename, min_score, status, document_type)

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


def get_analysis_by_id(analysis_id: int) -> Optional[Dict[str, Any]]:
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
                WHERE id = %s;
                """,
                (analysis_id,),
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


def get_analysis_export(analysis_id: int) -> Optional[Dict[str, Any]]:
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
                WHERE id = %s;
                """,
                (analysis_id,),
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
