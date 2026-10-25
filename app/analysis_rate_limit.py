"""Transactional PostgreSQL quotas for authenticated PDF analysis requests.

Each counter has one row per user or organization and resets at the start
of the next UTC hour. Both limits are reserved atomically: if either one
is exhausted, the whole transaction rolls back. The counters work across
multiple FastAPI processes and instances sharing the same database.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math

import psycopg2

from app.db import get_conn

MAX_ANALYSES_PER_USER_HOUR = 20
MAX_ANALYSES_PER_ORG_HOUR = 100

# Only static, trusted table/column identifiers are interpolated into SQL.
# Request data is always passed separately as bound psycopg2 parameters.
_QUOTAS = (
    ("analysis_user_quotas", "user_id"),
    ("analysis_org_quotas", "organization_id"),
)


class AnalysisQuotaExceeded(Exception):
    def __init__(self, scope: str, retry_after: int):
        self.scope = scope
        self.retry_after = retry_after
        super().__init__(f"PDF analysis quota exceeded for {scope}")


class AnalysisQuotaUnavailable(Exception):
    """The DB quota guard could not confirm permission; never fail open."""


def _retry_after_seconds() -> int:
    now = datetime.now(timezone.utc)
    return max(1, 3600 - now.minute * 60 - now.second)


def _reserve_counter(cursor, table: str, identifier: str, subject_id: int, limit: int) -> bool:
    # Concurrency-safe in PostgreSQL: ON CONFLICT locks the existing row
    # and the conditional UPDATE cannot push usage beyond the limit.
    # Using current_timestamp on the DB gives all API instances the same
    # window irrespective of their operating-system clocks.
    cursor.execute(
        f"""
        INSERT INTO {table} ({identifier}, window_start, used)
        VALUES (%s, date_trunc('hour', CURRENT_TIMESTAMP), 1)
        ON CONFLICT ({identifier}) DO UPDATE SET
            window_start = EXCLUDED.window_start,
            used = CASE
                WHEN {table}.window_start < EXCLUDED.window_start THEN 1
                ELSE {table}.used + 1
            END
        WHERE {table}.window_start < EXCLUDED.window_start
           OR {table}.used < %s
        RETURNING used
        """,
        (subject_id, limit),
    )
    return cursor.fetchone() is not None


def consume_analysis_quota(user_id: int, organization_id: int) -> None:
    """Reserve a request before parsing; rejected requests consume no quota.

    Intentionally counts accepted *attempts*, even when the submitted PDF
    later proves invalid, to prevent repeated hostile documents from
    exhausting worker capacity. Never accept user/org IDs from request body.
    """
    if user_id <= 0 or organization_id <= 0:
        raise ValueError("Authenticated account and organization IDs required")
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cursor:
            # Uniform order prevents deadlocks with competing reservations.
            if not _reserve_counter(
                cursor, _QUOTAS[0][0], _QUOTAS[0][1],
                user_id, MAX_ANALYSES_PER_USER_HOUR,
            ):
                raise AnalysisQuotaExceeded("usuario", _retry_after_seconds())
            if not _reserve_counter(
                cursor, _QUOTAS[1][0], _QUOTAS[1][1],
                organization_id, MAX_ANALYSES_PER_ORG_HOUR,
            ):
                raise AnalysisQuotaExceeded("gestoría", _retry_after_seconds())
        conn.commit()
    except AnalysisQuotaExceeded:
        if conn is not None:
            conn.rollback()
        raise
    except (psycopg2.Error, OSError) as exc:
        if conn is not None:
            conn.rollback()
        raise AnalysisQuotaUnavailable() from exc
    finally:
        if conn is not None:
            conn.close()
