"""Change password with an atomic user-row lock and session invalidation."""
from psycopg2.extras import RealDictCursor

from app.auth import password_hasher, hash_password
from app.db import get_conn


class InvalidPassword(Exception):
    pass


def change_password(user_id: int, organization_id: int, session_version: int,
                    current: str, new: str) -> None:
    if current == new:
        raise InvalidPassword()
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT u.password_hash
                  FROM users u JOIN memberships m ON m.user_id = u.id
                  JOIN organizations o ON o.id = m.organization_id
                 WHERE u.id = %s AND m.organization_id = %s
                   AND u.session_version = %s
                   AND u.is_active AND m.is_active AND o.is_active
                 FOR UPDATE OF u, m
                """,
                (user_id, organization_id, session_version),
            )
            row = cursor.fetchone()
            if not row or not password_hasher.verify(current, row["password_hash"]):
                raise InvalidPassword()
            cursor.execute(
                """
                UPDATE users SET password_hash = %s,
                    session_version = session_version + 1
                 WHERE id = %s
                """,
                (hash_password(new), user_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
