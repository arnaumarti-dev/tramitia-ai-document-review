"""ASGI authorization gate for PDF uploads before multipart parsing.

FastAPI resolves UploadFile before endpoint dependencies, meaning an anonymous
request could otherwise force disk/RAM spooling before receiving HTTP 401.
This narrow gate checks the SAME signed JWT and current membership as the
endpoint, without ever invoking receive(). The route checks again afterward
to prevent a revoked membership from being used during a long upload.

It does not reserve user quotas or replace ingress body/time/rate limits.
"""
from __future__ import annotations

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app.auth import current_principal


class UploadAuthenticationPreflight:
    """Refuse unauthenticated POST /analyze before reading request bytes."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope.get("method") != "POST"
                or scope.get("path") != "/analyze"):
            return await self.app(scope, receive, send)

        raw = [
            v for key, v in scope.get("headers", [])
            if key.lower() == b"authorization"
        ]
        token = None
        if len(raw) == 1 and len(raw[0]) <= 4200:
            try:
                value = raw[0].decode("ascii", "strict")
                scheme, separator, secret = value.partition(" ")
                if (separator and scheme.lower() == "bearer"
                        and 0 < len(secret) <= 4096
                        and secret.isascii() and secret.isprintable()
                        and not any(ch.isspace() for ch in secret)):
                    token = secret
            except UnicodeDecodeError:
                pass

        # No DB lookup for absent, malformed or duplicated credentials.
        if not token:
            return await self._deny(401, scope, receive, send)

        try:
            # current_principal() performs signature/expiry verification AND
            # checks live organization membership in PostgreSQL. Run off-loop.
            await run_in_threadpool(
                current_principal,
                HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
            )
        except HTTPException as exc:
            if exc.status_code == 401:
                return await self._deny(401, scope, receive, send)
            return await self._deny(503, scope, receive, send)
        except Exception:
            # Fail closed when verification cannot complete (including DB
            # outages). Never leak exception text, token or customer content.
            return await self._deny(503, scope, receive, send)

        return await self.app(scope, receive, send)

    @staticmethod
    async def _deny(status, scope, receive, send):
        response = JSONResponse(
            {"detail": ("Credenciales inválidas o sesión caducada."
                        if status == 401 else
                        "No se puede verificar la sesión en este momento.")},
            status_code=status,
            headers={
                "Cache-Control": "no-store",
                **({"WWW-Authenticate": "Bearer"} if status == 401 else {}),
            },
        )
        return await response(scope, receive, send)
