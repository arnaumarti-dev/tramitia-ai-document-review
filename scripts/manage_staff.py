"""Trusted local operator tool for adding and revoking staff memberships.

There is no public signup/invitation route. Use only from a secured server.
Existing emails cannot be silently enrolled into a different organization.
"""
import argparse
import getpass
from typing import Optional

from app.auth import hash_password
from app.db import get_conn


def add_staff(slug: str, email: str, role: str, password: str) -> int:
    """Provision a new member in a single transaction; reject existing users."""
    if role not in {"admin", "employee"}:
        raise ValueError("Choose 'admin' or 'employee'.")
    if len(password) < 12:
        raise ValueError("The password must be at least 12 characters.")
    slug, email = slug.strip().lower(), email.strip().lower()
    if not slug or not email or "@" not in email:
        raise ValueError("Invalid slug or email.")
    encoded = hash_password(password)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Serializes this organization's account operations.
            cur.execute(
                "SELECT id FROM organizations WHERE slug = %s AND is_active FOR UPDATE",
                (slug,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Organization not found or inactive.")
            org_id = row[0]
            cur.execute("SELECT 1 FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                raise ValueError("That email is already registered; invitation support is not available.")
            cur.execute(
                "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
                (email, encoded),
            )
            user_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO memberships (user_id, organization_id, role) VALUES (%s,%s,%s)",
                (user_id, org_id, role),
            )
        conn.commit()
        return int(user_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def disable_staff(slug: str, email: str) -> None:
    """Revoke all outstanding bearer tokens by deactivating membership."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM organizations WHERE slug = %s AND is_active FOR UPDATE",
                (slug.strip().lower(),),
            )
            organization = cur.fetchone()
            if not organization:
                raise ValueError("Organization not found or inactive.")
            org_id = organization[0]
            cur.execute(
                """SELECT m.role, m.is_active
                     FROM memberships m JOIN users u ON u.id = m.user_id
                    WHERE m.organization_id = %s AND u.email = %s FOR UPDATE OF m""",
                (org_id, email.strip().lower()),
            )
            member = cur.fetchone()
            if not member:
                raise ValueError("Membership not found.")
            if member[0] == "owner" and member[1]:
                cur.execute(
                    "SELECT COUNT(*) FROM memberships WHERE organization_id = %s AND role = 'owner' AND is_active",
                    (org_id,),
                )
                if cur.fetchone()[0] <= 1:
                    raise ValueError("Cannot disable the last active owner.")
            cur.execute(
                """UPDATE memberships SET is_active = FALSE
                    WHERE organization_id = %s
                      AND user_id = (SELECT id FROM users WHERE email = %s)""",
                (org_id, email.strip().lower()),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_staff(slug: str) -> list[tuple[str, str, bool]]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT u.email, m.role, m.is_active
                   FROM organizations o
                   JOIN memberships m ON m.organization_id = o.id
                   JOIN users u ON u.id = m.user_id
                   WHERE o.slug = %s ORDER BY u.email""",
                (slug.strip().lower(),),
            )
            return cur.fetchall()
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="TramitIA local staff administration")
    parser.add_argument("--slug", required=True, help="Target organization identifier")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="Create a new employee/admin account")
    add.add_argument("--email", required=True)
    add.add_argument("--role", choices=("admin", "employee"), default="employee")
    disable = sub.add_parser("disable", help="Revoke a member's organization access")
    disable.add_argument("--email", required=True)
    sub.add_parser("list", help="List organization members")
    args = parser.parse_args()
    if args.command == "add":
        password = getpass.getpass("Contraseña inicial (12+ caracteres): ")
        if password != getpass.getpass("Confirmar contraseña: "):
            parser.error("Passwords do not match.")
        user_id = add_staff(args.slug, args.email, args.role, password)
        print(f"Usuario {user_id} incorporado a {args.slug}.")
    elif args.command == "disable":
        disable_staff(args.slug, args.email)
        print("Acceso revocado. La API invalida sus tokens en la siguiente petición.")
    else:
        for email, role, active in list_staff(args.slug):
            print(f"{email}\t{role}\t{'activo' if active else 'desactivado'}")


if __name__ == "__main__":
    main()
