"""Disposable localhost-only FastAPI fixture for the real NGINX upload probe.

This fixture uses the actual TramitIA ASGI multipart upload stack, but mocks
only authentication, database quota/persistence, and PDF parsing to avoid
requiring customer records or manipulating a real database. NEVER deploy it.
"""
from __future__ import annotations

import os

if os.getenv("TRAMITIA_NGINX_UPLOAD_SMOKE") != "1":
    raise RuntimeError("Refusing to start the isolated NGINX upload test fixture")

from app import main as backend
from app import auth as test_auth
from app.upload_temporary_storage import verify_private_upload_tmpfs

# Verify actual worker spooling in private tmpfs before any request.
verify_private_upload_tmpfs()

# Only this explicitly gated, localhost-only test fixture accepts synthetic
# identity (1, 1); production authentication still uses live DB membership.
test_auth.get_membership_role = lambda user, org, ver: (
    "owner" if (user, org, ver) == (1, 1, 0) else None
)
backend._consume_peer_budget = lambda *_args, **_kwargs: None
backend.consume_analysis_quota = lambda *_args, **_kwargs: None
backend.save_analysis = lambda _report, _organization_id: 9001
backend.parse_pdf_document = lambda _data, **_kwargs: (
    "Solicitud de subvención ficticia para probar los temporales sin documentos reales. "
    "Declaro un correo demo@example.invalid y una fecha de prueba 07/11/2026. Firma de prueba.",
    1,
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        backend.app, host="127.0.0.1", port=18081,
        proxy_headers=False, access_log=False, log_level="warning",
    )
