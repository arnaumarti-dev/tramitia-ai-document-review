"""Authentication is deliberately fail-closed: no secret or membership means no access."""
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash

from app.repository import get_membership_role, get_user_for_login
from app.login_rate_limit import reject_if_limited, record_failure, clear_failures

ISSUER = "tramitia"
AUDIENCE = "tramitia-api"
TOKEN_MINUTES = 30
password_hasher = PasswordHash.recommended()
bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user_id: int
    organization_id: int
    role: str
    session_version: int = 0


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Credenciales inválidas o sesión caducada.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _secret() -> str:
    secret = os.getenv("TRAMITIA_JWT_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("Configure TRAMITIA_JWT_SECRET (32+ caracteres aleatorios).")
    return secret


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


# Always verify a hash, including for unknown users, to reduce account enumeration.
DUMMY_HASH = hash_password("tramitia-dummy-password-not-for-login")


def authenticate(email: str, password: str, organization_slug: str) -> Principal:
    email = email.strip().lower()
    organization_slug = organization_slug.strip().lower()
    reject_if_limited(email, organization_slug)
    record = get_user_for_login(email, organization_slug)
    valid = password_hasher.verify(password, record["password_hash"] if record else DUMMY_HASH)
    if not valid or not record:
        record_failure(email, organization_slug)
        raise _unauthorized()
    clear_failures(email, organization_slug)
    return Principal(
        user_id=int(record["user_id"]),
        organization_id=int(record["organization_id"]),
        role=str(record["role"]),
        session_version=int(record["session_version"]),
    )


def issue_token(principal: Principal) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(principal.user_id),
            "org": principal.organization_id,
            "ver": principal.session_version,
            "iat": now,
            "exp": now + timedelta(minutes=TOKEN_MINUTES),
        },
        _secret(),
        algorithm="HS256",
    )


def current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> Principal:
    if not credentials:
        raise _unauthorized()
    try:
        payload = jwt.decode(
            credentials.credentials,
            _secret(),
            algorithms=["HS256"],
            issuer=ISSUER,
            audience=AUDIENCE,
            options={"require": ["sub", "org", "ver", "exp", "iat", "iss", "aud"]},
        )
        user_id = int(payload["sub"])
        organization_id = int(payload["org"])
        session_version = int(payload["ver"])
        if user_id <= 0 or organization_id <= 0 or session_version < 0:
            raise ValueError("invalid identifiers")
    except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise _unauthorized() from exc

    # A signed token is not enough: revoking an active membership takes effect now.
    role = get_membership_role(user_id, organization_id, session_version)
    if not role:
        raise _unauthorized()
    return Principal(user_id=user_id, organization_id=organization_id, role=role,
                     session_version=session_version)
