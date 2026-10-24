"""Persistent per-organization/account failure throttling across API workers.

This is one layer only. Add IP-aware perimeter limits, monitoring and MFA
before deployment. An attacker can still intentionally lock out an account.
"""
import hashlib
import hmac
import os

from fastapi import HTTPException

from app.db import get_conn

# Avoid storing raw submitted email or organization names in the login ledger.
MAX_FAILURES = 5


def _digest(email: str, organization: str) -> str:
    secret = os.getenv("TRAMITIA_JWT_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("Configure a random TRAMITIA_JWT_SECRET of at least 32 characters.")
    identifier = (organization.strip().lower() + "\0" + email.strip().lower()).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), b"login-limit-v1\0" + identifier, hashlib.sha256).hexdigest()


def reject_if_limited(email: str, organization: str) -> None:
    digest = _digest(email, organization)
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT blocked_until > NOW() FROM login_attempts WHERE key_hash = %s",
                (digest,),
            )
            row = cursor.fetchone()
            if row and row[0]:
                raise HTTPException(
                    status_code=429,
                    detail="Demasiados intentos. Vuelve a intentarlo en unos minutos.",
                    headers={"Retry-After": "900"},
                )
    finally:
        conn.close()


def record_failure(email: str, organization: str) -> None:
    digest = _digest(email, organization)
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO login_attempts (key_hash, failures, window_started_at, blocked_until)
                VALUES (%s, 1, NOW(), NULL)
                ON CONFLICT (key_hash) DO UPDATE SET
                  failures = CASE WHEN login_attempts.window_started_at < NOW() - INTERVAL '15 minutes'
                                  THEN 1 ELSE login_attempts.failures + 1 END,
                  window_started_at = CASE WHEN login_attempts.window_started_at < NOW() - INTERVAL '15 minutes'
                                           THEN NOW() ELSE login_attempts.window_started_at END,
                  blocked_until = CASE
                    WHEN login_attempts.window_started_at < NOW() - INTERVAL '15 minutes' THEN NULL
                    WHEN login_attempts.failures + 1 >= %s THEN NOW() + INTERVAL '15 minutes'
                    ELSE login_attempts.blocked_until
                  END
                """,
                (digest, MAX_FAILURES),
            )
        conn.commit()
    finally:
        conn.close()


def clear_failures(email: str, organization: str) -> None:
    digest = _digest(email, organization)
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM login_attempts WHERE key_hash = %s", (digest,))
        conn.commit()
    finally:
        conn.close()
