"""Interactive initial organization owner creation (development bootstrap).

Usage: python -m scripts.bootstrap_admin --slug mi-gestoria --name "Mi Gestoría" --email owner@example.com
Never pass passwords on command line or store them in the repository.
"""
import argparse
import getpass

from app.auth import hash_password
from app.db import get_conn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    slug, email = args.slug.strip().lower(), args.email.strip().lower()
    if not slug or not email or "@" not in email:
        parser.error("Proporciona un slug y email válidos.")
    password = getpass.getpass("Nueva contraseña (12 caracteres mínimo): ")
    if len(password) < 12:
        parser.error("La contraseña debe tener al menos 12 caracteres.")
    if password != getpass.getpass("Confirma contraseña: "):
        parser.error("Las contraseñas no coinciden.")
    hashed = hash_password(password)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO organizations (slug, name) VALUES (%s,%s) RETURNING id",
                (slug, args.name.strip()),
            )
            org_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO users (email,password_hash) VALUES (%s,%s) RETURNING id",
                (email, hashed),
            )
            user_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO memberships (user_id,organization_id,role) VALUES (%s,%s,'owner')",
                (user_id, org_id),
            )
        conn.commit()
        print(f"Organización '{slug}' creada con propietario '{email}'.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
