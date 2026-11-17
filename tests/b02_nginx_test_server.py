"""Disposable localhost-only FastAPI fixture for the real NGINX upload probe.

This fixture uses the actual TramitIA ASGI multipart upload stack, but mocks
only authentication, database quota/persistence, and PDF parsing to avoid
requiring customer records or manipulating a real database. NEVER deploy it.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

if os.getenv("TRAMITIA_NGINX_UPLOAD_SMOKE") != "1":
    raise RuntimeError("Refusing to start the isolated NGINX upload test fixture")

from app import main as backend
from app.upload_temporary_storage import verify_private_upload_tmpfs

# Verify the actual worker's tempfile resolution and RAM-backed mount.
verify_private_upload_tmpfs()

backend.app.app.dependency_overrides[backend.current_principal] = lambda: SimpleNamespace(
    user_id=1, organization_id=1, role="owner",
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
