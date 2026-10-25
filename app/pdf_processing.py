"""Extract PDFs in a separate short-lived, resource-capped Python process.

The child is deliberately not given JWT / DB secrets. This is defense in depth,
not a true security sandbox; production needs OS/container separation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

MAX_WORKER_SECONDS = 15
MAX_WORKER_STDOUT_BYTES = 3_000_000
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


def _worker_launch(max_pages: int, max_chars: int) -> tuple[list[str], dict[str, str]]:
    """Select either the local test worker or a disposable container.

    Container mode is opt-in, requires a pre-built local image and a trusted
    Docker daemon. Never mount the web app filesystem or inherit its secrets.
    """
    backend = os.environ.get("TRAMITIA_PDF_WORKER_BACKEND", "subprocess")
    if backend == "subprocess":
        return (
            [sys.executable, "-I", str(_WORKER), str(max_pages), str(max_chars)],
            _minimal_environment(),
        )
    if backend != "container":
        raise PdfProcessingError(503, "Configuración del analizador PDF no válida.")

    image = os.environ.get("TRAMITIA_PDF_WORKER_IMAGE", "tramitia-pdf-worker:local")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._:/-]{0,150}", image):
        raise PdfProcessingError(503, "La imagen del analizador PDF no es válida.")

    # Explicitly bind to the LOCAL engine. Do not honor the web process's
    # DOCKER_HOST/DOCKER_CONTEXT (which could transmit client PDFs remotely).
    docker_host = ("npipe:////./pipe/docker_engine" if sys.platform == "win32"
                   else "unix:///var/run/docker.sock")
    command = [
        "docker", "--host", docker_host, "run", "--rm", "--interactive",
        "--pull=never",
        "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=32",
        "--memory=768m", "--memory-swap=768m", "--cpus=1",
        "--user=65534:65534",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=8m",
        "--log-driver=none",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        image, str(max_pages), str(max_chars),
    ]
    # Keep Docker CLI startup environment minimal; no application credentials.
    return command, _minimal_environment()


_BROKER_ERRORS = {
    "invalid": (400, "El documento PDF no es válido o está protegido."),
    "too_large": (413, "El PDF supera el límite de páginas o de texto."),
    "failed": (422, "El analizador de PDF no pudo completar la operación."),
    "unavailable": (503, "El servicio de análisis de PDF no está disponible."),
    "busy": (503, "El servicio de análisis de PDF está ocupado. Inténtalo de nuevo."),
}


def _read_broker_exact(connection: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = connection.recv(min(65536, length - len(data)))
        if not chunk:
            raise ConnectionError("Broker connection was closed")
        data.extend(chunk)
    return bytes(data)


def _broker_request(data: bytes, max_pages: int, max_chars: int) -> bytes:
    """Send only validated PDF bytes/limits to a trusted local Unix socket.

    No Docker CLI, Docker socket or arbitrary launch command is used here.
    """
    if not hasattr(socket, "AF_UNIX"):
        raise PdfProcessingError(503, "El broker PDF no es compatible con este sistema.")
    target = os.getenv("TRAMITIA_BROKER_SOCKET", "/run/tramitia/pdf-broker.sock")
    if not Path(target).is_absolute():
        raise PdfProcessingError(503, "Ruta del broker PDF no válida.")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(MAX_WORKER_SECONDS + 5)
            connection.connect(target)
            connection.sendall(struct.pack("!III", len(data), max_pages, max_chars) + data)
            size = struct.unpack("!I", _read_broker_exact(connection, 4))[0]
            if size > MAX_WORKER_STDOUT_BYTES or size == 0:
                raise ValueError("Oversized broker output")
            return _read_broker_exact(connection, size)
    except (OSError, ConnectionError, TimeoutError) as exc:
        raise PdfProcessingError(
            503, "No se pudo contactar con el servicio aislado de PDF."
        ) from exc
    except (ValueError, struct.error) as exc:
        raise PdfProcessingError(422, "Respuesta del broker PDF no válida.") from exc


def _run_bounded_worker(
    command: list[str], data: bytes, environment: dict[str, str],
) -> subprocess.CompletedProcess:
    """Read at most MAX_WORKER_STDOUT_BYTES + 1 bytes, even for a hostile child.

    A feeder and a bounded reader run concurrently so a child that writes
    stdout before consuming stdin cannot deadlock the caller. The deadline
    covers writing stdin, reading stdout and process exit, not merely CPU.
    """
    try:
        child = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        raise PdfProcessingError(
            503, "El servicio de análisis de PDF no está disponible."
        ) from exc

    deadline = time.monotonic() + MAX_WORKER_SECONDS
    chunks: list[bytes] = []
    read_errors: list[Exception] = []
    write_errors: list[Exception] = []

    def _feed() -> None:
        try:
            child.stdin.write(data)
            child.stdin.flush()
        except (OSError, ValueError) as exc:
            write_errors.append(exc)
        finally:
            try:
                child.stdin.close()
            except OSError:
                pass

    def _read_capped() -> None:
        try:
            # Buffered read(n) returns at most n bytes or earlier at EOF.
            # Stop as soon as the child crosses the permitted output size.
            chunks.append(child.stdout.read(MAX_WORKER_STDOUT_BYTES + 1))
        except (OSError, ValueError) as exc:
            read_errors.append(exc)

    feeder = threading.Thread(target=_feed, daemon=True)
    reader = threading.Thread(target=_read_capped, daemon=True)
    try:
        feeder.start()
        reader.start()
        reader.join(timeout=max(0, deadline - time.monotonic()))
        if reader.is_alive():
            raise PdfProcessingError(
                422, "El análisis del PDF superó el tiempo máximo permitido."
            )
        if read_errors or not chunks:
            raise PdfProcessingError(
                422, "No se pudo leer la respuesta del analizador PDF."
            )
        if len(chunks[0]) > MAX_WORKER_STDOUT_BYTES:
            raise PdfProcessingError(
                422, "El analizador PDF superó el límite de salida permitido."
            )

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, MAX_WORKER_SECONDS)
        code = child.wait(timeout=remaining)
        feeder.join(timeout=max(0, deadline - time.monotonic()))
        if feeder.is_alive():
            raise subprocess.TimeoutExpired(command, MAX_WORKER_SECONDS)
        if write_errors:
            raise PdfProcessingError(
                422, "No se pudo transmitir el PDF al analizador."
            )
        return subprocess.CompletedProcess(command, code, stdout=chunks[0])
    except subprocess.TimeoutExpired as exc:
        raise PdfProcessingError(
            422, "El análisis del PDF superó el tiempo máximo permitido."
        ) from exc
    finally:
        # Kill and reap on overflow, timeout or protocol errors. Killing the
        # private process group also stops local descendants where supported.
        if child.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(child.pid, signal.SIGKILL)
                else:
                    child.kill()
            except ProcessLookupError:
                pass
            except OSError:
                child.kill()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=2)
        feeder.join(timeout=1)
        reader.join(timeout=1)
        child.stdout.close()
        if child.stdin and not feeder.is_alive() and not child.stdin.closed:
            child.stdin.close()


def _parse_pdf_document_unlocked(
    data: bytes,
    *,
    max_pages: int = 50,
    max_chars: int = 200_000,
) -> tuple[str, int]:
    if not (1 <= max_pages <= 50 and 1 <= max_chars <= 200_000):
        raise ValueError("Invalid worker resource limits")

    if os.getenv("TRAMITIA_PDF_WORKER_BACKEND", "subprocess") == "broker":
        response = _broker_request(data, max_pages, max_chars)
    else:
        command, environment = _worker_launch(max_pages, max_chars)
        completed = _run_bounded_worker(command, data, environment)

        if completed.returncode != 0 or len(completed.stdout) > MAX_WORKER_STDOUT_BYTES:
            raise PdfProcessingError(
                422, "No se pudo procesar el PDF dentro de los límites de seguridad."
            )
        response = completed.stdout
    try:
        result = json.loads(response)
        if not isinstance(result, dict):
            raise ValueError("bad worker response")
        if "broker_error" in result:
            code = result["broker_error"]
            if code not in _BROKER_ERRORS:
                raise ValueError("bad broker error")
            status, message = _BROKER_ERRORS[code]
            raise PdfProcessingError(status, message)
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
