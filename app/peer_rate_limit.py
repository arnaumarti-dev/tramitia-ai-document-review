"""Atomic per-peer request throttles shared across all FastAPI instances.

Do NOT trust X-Forwarded-For or user-supplied headers. The caller passes
the ASGI connection address, which must be set by a correctly configured
server. Run Uvicorn with --no-proxy-headers, otherwise it may have already
rewritten ASGI scope['client'] based on forwarded headers. A production
reverse proxy must apply stronger client-IP limiting at the edge.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import os

import psycopg2

from app.db import get_conn


class PeerRateLimited(Exception):
    def __init__(self, category: str, retry_after: int):
        super().__init__("Request budget exhausted")
        self.category = category
        self.retry_after = retry_after


class PeerRateLimitUnavailable(Exception):
    """The shared quota database failed: deny instead of bypassing."""


# Modest per-origin budgets to avoid locking out legitimate shared-IP NATs.
# These are defense-in-depth controls, not DDoS mitigation.
LIMITS = {
    "login": (60, 15),       # 60 login requests / 15 minutes / peer
    "invitation": (30, 15),  # 30 invitation acceptances / 15 minutes
    "analyze": (60, 60),     # 60 PDF uploads / hour / peer
}
_EPOCH = "2000-01-01 00:00:00+00"


def _canonical_peer(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise PeerRateLimitUnavailable()
    # All network deployments should provide an IP literal in ASGI
    # scope['client']; the test harness may use the synthetic 'testclient'
    # label, which is a single fixed peer and never a user-provided header.
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        if value != "testclient":
            raise PeerRateLimitUnavailable() from None
        return value


def _hashed_key(peer: str, category: str) -> str:
    secret = os.environ.get("TRAMITIA_JWT_SECRET", "")
    if len(secret) < 32:
        raise PeerRateLimitUnavailable()
    return hmac.new(
        secret.encode("utf-8"),
        b"peer-rate-v1\0" + category.encode("ascii") + b"\0" + peer.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _retry_after(window_minutes: int) -> int:
    now = datetime.now(timezone.utc)
    secs = window_minutes * 60
    elapsed = now.minute * 60 + now.second
    return max(1, secs - (elapsed % secs))


def enforce_peer_rate_limit(peer: str | None, category: str) -> None:
    """Consume one budget atomically, or raise a controlled 429/503 condition.

    An accepted attempt counts even if downstream authentication or PDF
    parsing fails. Failing requests never trigger the expensive operation.
    """
    if category not in LIMITS:
        raise ValueError("Invalid fixed peer rate limit category")
    limit, window_minutes = LIMITS[category]
    normalized_peer = _canonical_peer(peer)
    key = _hashed_key(normalized_peer, category)
    interval = f"{window_minutes} minutes"
    connection = None
    try:
        connection = get_conn()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ip_rate_limits(key_hash, request_count, window_started_at)
                VALUES (
                    %s, 1,
                    date_bin(%s::interval, CURRENT_TIMESTAMP, %s::timestamptz)
                )
                ON CONFLICT (key_hash) DO UPDATE SET
                    window_started_at = EXCLUDED.window_started_at,
                    request_count = CASE
                        WHEN ip_rate_limits.window_started_at < EXCLUDED.window_started_at
                        THEN 1 ELSE ip_rate_limits.request_count + 1
                    END
                WHERE ip_rate_limits.window_started_at < EXCLUDED.window_started_at
                    OR ip_rate_limits.request_count < %s
                RETURNING request_count
                """,
                (key, interval, _EPOCH, limit),
            )
            if cursor.fetchone() is None:
                raise PeerRateLimited(category, _retry_after(window_minutes))
        connection.commit()
    except PeerRateLimited:
        if connection is not None:
            connection.rollback()
        raise
    except (psycopg2.Error, OSError) as exc:
        if connection is not None:
            connection.rollback()
        raise PeerRateLimitUnavailable() from exc
    finally:
        if connection is not None:
            connection.close()


def delete_expired_peer_counters() -> int:
    """Idempotent cleanup; run daily from a trusted job, never per request."""
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM ip_rate_limits WHERE window_started_at < NOW() - INTERVAL '48 hours'"
            )
            deleted = cursor.rowcount
        conn.commit()
        return deleted
    except (psycopg2.Error, OSError) as exc:
        if conn is not None:
            conn.rollback()
        raise PeerRateLimitUnavailable() from exc
    finally:
        if conn is not None:
            conn.close()
