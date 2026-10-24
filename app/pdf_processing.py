"""Extract PDFs in a separate short-lived, resource-capped Python process.

The child is deliberately not given JWT / DB secrets. This is defense in depth,
not a true security sandbox; production needs OS/container separation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading

MAX_WORKER_SECONDS = 15
MAX_WORKER_STDOUT_BYTES = 1_500_000
_WORKER = Path(__file__).with_name("pdf_worker.py")
# Prevent simultaneous PDF jobs from multiplying the memory budget.
# This is per web-server process, not a fleet-wide queue.
_WORKER_SLOT = threading.BoundedSemaphore(1)


class PdfProcessingError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


_MESSAGES = {
    "encrypted": (400, "El PDF está protegido. Sube una copia sin contraseña."),
    "empty": (400, "El PDF no contiene páginas."),
    "page_limit": (413, "El PDF supera el límite de {pages} páginas."),
    "text_limit": (413, "El PDF supera el límite de {chars:,} caracteres de texto."),
    "invalid": (400, "No se puede leer el PDF. Comprueba que sea válido y no esté dañado."),
}


def _minimal_environment() -> dict[str, str]:
    # The PDF parser cannot read credentials from the server's environment.
    allow = ("PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "TMP", "TEMP")
    return {key: os.environ[key] for key in allow if key in os.environ}


def _parse_pdf_document_unlocked(
    data: bytes,
    *,
    max_pages: int = 50,
    max_chars: int = 200_000,
) -> tuple[str, int]:
    if not (1 <= max_pages <= 50 and 1 <= max_chars <= 200_000):
        raise ValueError("Invalid worker resource limits")

    try:
        completed = subprocess.run(
            [sys.executable, "-I", str(_WORKER), str(max_pages), str(max_chars)],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=MAX_WORKER_SECONDS,
            env=_minimal_environment(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PdfProcessingError(
            422, "El análisis del PDF superó el tiempo máximo permitido."
        ) from exc
    except OSError as exc:
        raise PdfProcessingError(
            503, "El servicio de análisis de PDF no está disponible."
        ) from exc

    if completed.returncode != 0 or len(completed.stdout) > MAX_WORKER_STDOUT_BYTES:
        raise PdfProcessingError(
            422, "No se pudo procesar el PDF dentro de los límites de seguridad."
        )
    try:
        result = json.loads(completed.stdout)
        if not isinstance(result, dict):
            raise ValueError("bad worker response")
        if "error" in result:
            code = result["error"]
            if code not in _MESSAGES:
                raise ValueError("bad worker error")
            status, template = _MESSAGES[code]
            raise PdfProcessingError(
                status, template.format(pages=max_pages, chars=max_chars)
            )
        text = result["text"]
        pages = result["pages"]
        if not isinstance(text, str) or not isinstance(pages, int):
            raise ValueError("bad worker result")
        if pages < 1 or pages > max_pages or len(text) > max_chars:
            raise ValueError("worker exceeded limits")
        return text, pages
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise PdfProcessingError(
            422, "El analizador devolvió una respuesta no válida."
        ) from exc


def parse_pdf_document(
    data: bytes,
    *,
    max_pages: int = 50,
    max_chars: int = 200_000,
) -> tuple[str, int]:
    """At most one PDF subprocess per web-server process; fail fast on overload."""
    if not _WORKER_SLOT.acquire(blocking=False):
        raise PdfProcessingError(
            503, "El analizador está ocupado. Vuelve a intentarlo en unos momentos."
        )
    try:
        return _parse_pdf_document_unlocked(
            data, max_pages=max_pages, max_chars=max_chars
        )
    finally:
        _WORKER_SLOT.release()
