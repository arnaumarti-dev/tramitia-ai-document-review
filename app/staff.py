"""Organization-scoped staff administration.

Authorization is rechecked under a database row lock inside each write
transaction. UI visibility is never a security boundary.
"""
from __future__ import annotations

from typing import Any

from psycopg2.extras import RealDictCursor

from app.db import get_conn


class StaffForbidden(Exception):
    pass


class StaffNotFound(Exception):
    pass


class StaffConflict(Exception):
    pass


def _actor_role(cursor: Any, organization_id: int, actor_id: int) -> str:
    cursor.execute(
        """
        SELECT m.role
          FROM memberships m
          JOIN users u ON u.id = m.user_id
          JOIN organizations o ON o.id = m.organization_id
         WHERE m.organization_id = %s AND m.user_id = %s
           AND m.is_active AND u.is_active AND o.is_active
         FOR UPDATE OF m
        """,
        (organization_id, actor_id),
    )
    row = cursor.fetchone()
    if not row or row["role"] not in ("owner", "admin"):
        raise StaffForbidden()
    return row["role"]


def list_staff(organization_id: int, actor_id: int) -> list[dict]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # A manager's own membership must remain valid, even mid-session.
            cursor.execute(
                """
                SELECT m.role FROM memberships m
                JOIN organizations o ON o.id = m.organization_id
                JOIN users u ON u.id = m.user_id
                WHERE m.organization_id = %s AND m.user_id = %s
                  AND m.is_active AND o.is_active AND u.is_active
                """,
                (organization_id, actor_id),
            )
            row = cursor.fetchone()
            if not row or row["role"] not in ("owner", "admin"):
                raise StaffForbidden()
            cursor.execute(
                """
                SELECT u.id AS user_id, u.email, m.role, m.is_active
                FROM memberships m JOIN users u ON u.id = m.user_id
                WHERE m.organization_id = %s
                ORDER BY u.email
                LIMIT 200
                """,
                (organization_id,),
            )
            return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def create_staff(organization_id: int, actor_id: int, email: str,
                 role: str, password_hash: str) -> dict:
    if role not in ("employee", "admin"):
        raise StaffForbidden()
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            actor_role = _actor_role(cursor, organization_id, actor_id)
            if role == "admin" and actor_role != "owner":
                raise StaffForbidden()
            cursor.execute(
                """
                INSERT INTO users (email, password_hash)
                VALUES (%s, %s)
                ON CONFLICT (email) DO NOTHING
                RETURNING id
                """,
                (email, password_hash),
            )
            new_user = cursor.fetchone()
            if not new_user:
                raise StaffConflict()
            user_id = new_user["id"]
            cursor.execute(
                """
                INSERT INTO memberships (user_id, organization_id, role)
                VALUES (%s, %s, %s)
                """,
                (user_id, organization_id, role),
            )
        conn.commit()
        return {"user_id": user_id, "email": email, "role": role, "is_active": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def disable_staff(organization_id: int, actor_id: int, target_user_id: int) -> None:
    if actor_id == target_user_id:
        raise StaffForbidden()
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            actor_role = _actor_role(cursor, organization_id, actor_id)
            cursor.execute(
                """
                SELECT role, is_active FROM memberships
                WHERE organization_id = %s AND user_id = %s
                FOR UPDATE
                """,
                (organization_id, target_user_id),
            )
            target = cursor.fetchone()
            if not target:
                raise StaffNotFound()
            if target["role"] == "owner":
                # Ownership transfers require a separately audited process.
                raise StaffForbidden()
            if target["role"] == "admin" and actor_role != "owner":
                raise StaffForbidden()
            cursor.execute(
                """
                UPDATE memberships SET is_active = FALSE
                WHERE organization_id = %s AND user_id = %s
                """,
                (organization_id, target_user_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
