"""Reject oversized PDF uploads before multipart parsing can buffer them.

The PDF parser already rejects files larger than 20 MiB; this separate
ASGI receive-layer guard caps the ENTIRE multipart HTTP body at 21 MiB.
A reverse proxy must also enforce upload limits in production.
"""
from __future__ import annotations

from starlette.responses import JSONResponse

MAX_MULTIPART_BODY_BYTES = 21 * 1024 * 1024
_TOO_LARGE_MESSAGE = "La petición de subida supera el límite permitido."


class _BodyTooLarge(Exception):
    pass


class UploadBodyLimit:
    """ASGI receive guard for POST /analyze (including chunked HTTP requests)."""

    def __init__(self, app, max_body_bytes: int = MAX_MULTIPART_BODY_BYTES):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope.get("path") != "/analyze"
                or scope.get("method") != "POST"):
            return await self.app(scope, receive, send)

        # Reject a declared oversized body without waiting for the multipart
        # parser; also reject conflicting or invalid Content-Length headers.
        lengths = [
            value.strip()
            for name, value in scope.get("headers", [])
            if name.lower() == b"content-length"
        ]
        if lengths:
            value = lengths[0]
            if (len(lengths) != 1 or not value.isascii()
                    or not value.isdigit() or len(value) > 16):
                return await JSONResponse(
                    {"detail": "Cabecera Content-Length no válida."},
                    status_code=400,
                )(scope, receive, send)
            if int(value) > self.max_body_bytes:
                return await JSONResponse(
                    {"detail": _TOO_LARGE_MESSAGE},
                    status_code=413,
                )(scope, receive, send)

        received = 0
        oversized = False
        pending_start = None
        response_started = False

        async def limited_receive():
            nonlocal received, oversized
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    oversized = True
                    raise _BodyTooLarge()
            return message

        async def protected_send(message):
            nonlocal pending_start, response_started
            if oversized:
                # Starlette may attempt to send a generic 500/400 response
                # when limited_receive raises. Discard it in favor of 413.
                return
            if message["type"] == "http.response.start":
                pending_start = message
            elif message["type"] == "http.response.body":
                if pending_start is not None:
                    response_started = True
                    await send(pending_start)
                    pending_start = None
                await send(message)
            else:
                await send(message)

        try:
            await self.app(scope, limited_receive, protected_send)
        except _BodyTooLarge:
            pass

        if oversized and not response_started:
            return await JSONResponse(
                {"detail": _TOO_LARGE_MESSAGE},
                status_code=413,
            )(scope, receive, send)

        if pending_start is not None:
            await send(pending_start)
            await send({"type": "http.response.body", "body": b"", "more_body": False})
