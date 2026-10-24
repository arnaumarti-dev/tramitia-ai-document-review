"""Secure tenant-scoped invitations (token digest only; never store raw token)."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from psycopg2.extras import RealDictCursor

from app.db import get_conn
from app.auth import hash_password
from app.staff import _actor_role, StaffForbidden, StaffConflict

INVITE_HOURS = 24


class InvalidInvitation(Exception):
    pass


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_invitation(organization_id: int, actor_id: int, email: str, role: str) -> str:
    if role not in ("employee", "admin"):
        raise StaffForbidden()
    token = secrets.token_urlsafe(32)
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            actor_role = _actor_role(cursor, organization_id, actor_id)
            if role == "admin" and actor_role != "owner":
                raise StaffForbidden()
            cursor.execute("SELECT 1 FROM users WHERE email = %s", (email,))
            if cursor.fetchone():
                # Explicit cross-organization onboarding requires a future consent flow.
                raise StaffConflict()
            # Supersede previous unaccepted invitations to the same address.
            cursor.execute(
                """
                UPDATE staff_invitations SET revoked_at = NOW()
                WHERE organization_id = %s AND email = %s
                  AND consumed_at IS NULL AND revoked_at IS NULL
                """,
                (organization_id, email),
            )
            cursor.execute(
                """
                INSERT INTO staff_invitations
                    (organization_id, email, role, invited_by, token_hash, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (organization_id, email, role, actor_id, _digest(token),
                 datetime.now(timezone.utc) + timedelta(hours=INVITE_HOURS)),
            )
        conn.commit()
        return token
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def accept_invitation(token: str, password: str) -> None:
    # Do not process oversized or malformed attacker input.
    if not token or len(token) > 200:
        raise InvalidInvitation()
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT i.id, i.email, i.role, i.organization_id
                FROM staff_invitations i
                JOIN organizations o ON o.id = i.organization_id
                JOIN memberships inviter ON inviter.organization_id = i.organization_id
                    AND inviter.user_id = i.invited_by
                JOIN users u ON u.id = i.invited_by
                WHERE i.token_hash = %s AND i.consumed_at IS NULL
                  AND i.revoked_at IS NULL AND i.expires_at > NOW()
                  AND o.is_active AND u.is_active AND inviter.is_active
                  AND inviter.role IN ('owner','admin')
                  AND (i.role = 'employee' OR inviter.role = 'owner')
                FOR UPDATE OF i
                """,
                (_digest(token),),
            )
            invitation = cursor.fetchone()
            if not invitation:
                raise InvalidInvitation()
            cursor.execute(
                """
                INSERT INTO users (email, password_hash)
                VALUES (%s, %s) ON CONFLICT (email) DO NOTHING RETURNING id
                """,
                (invitation["email"], hash_password(password)),
            )
            user = cursor.fetchone()
            if not user:
                raise InvalidInvitation()
            cursor.execute(
                """
                INSERT INTO memberships (user_id, organization_id, role)
                VALUES (%s, %s, %s)
                """,
                (user["id"], invitation["organization_id"], invitation["role"]),
            )
            cursor.execute(
                "UPDATE staff_invitations SET consumed_at = NOW() WHERE id = %s",
                (invitation["id"],),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
