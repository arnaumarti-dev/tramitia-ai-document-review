"""Atomic per-peer request throttles shared across all FastAPI instances.

Do NOT trust X-Forwarded-For or user-supplied headers. With no
TRAMITIA_TRUSTED_PROXY_IPS allowlist, only the direct ASGI peer is counted.
When a trusted peer is explicitly configured, accept exactly one overwritten
X-Real-IP literal from that peer, never from an untrusted direct client.
Uvicorn MUST run with --no-proxy-headers in both cases. The reverse proxy
must apply stronger limits at the edge and must be the only component that
can connect to the application when proxy trust is enabled.
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


def resolve_request_peer(request) -> str:
    """Select the real client only when the direct peer is explicitly trusted.

    Never use X-Forwarded-For or Forwarded. A trusted ingress MUST replace
    all incoming X-Real-IP values, not append or forward client input.
    With no proxy allowlist, all forwarded headers are ignored.
    """
    direct = _canonical_peer(
        request.client.host if request.client is not None else None
    )
    configured = os.environ.get("TRAMITIA_TRUSTED_PROXY_IPS", "").strip()
    if not configured:
        return direct

    # Exact IPs only: CIDRs and wildcards make it too easy to trust public
    # sources. Invalid configuration is an error, not an implicit bypass.
    raw = [item.strip() for item in configured.split(",")]
    if (len(raw) > 8 or any(not item for item in raw)):
        raise PeerRateLimitUnavailable()
    try:
        trusted = {ipaddress.ip_address(item).compressed for item in raw}
    except ValueError as exc:
        raise PeerRateLimitUnavailable() from exc

    if direct not in trusted:
        return direct

    # Inspect raw ASGI headers so duplicate case-insensitive fields cannot
    # be hidden by a framework's dictionary normalization.
    values = [
        value for name, value in request.scope.get("headers", [])
        if name.lower() == b"x-real-ip"
    ]
    if len(values) != 1 or len(values[0]) > 45:
        raise PeerRateLimitUnavailable()
    try:
        ip = values[0].decode("ascii", "strict")
        if ip != ip.strip() or "," in ip:
            raise PeerRateLimitUnavailable()
        return ipaddress.ip_address(ip).compressed
    except (UnicodeDecodeError, ValueError) as exc:
        raise PeerRateLimitUnavailable() from exc


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
