"""Fixed-function PDF broker. The web API never invokes Docker directly.

Run this service under a separately managed OS identity with access to the
local Docker daemon. Only expose its restricted Unix socket to the API.
The socket protocol is: big-endian uint32 PDF bytes, pages, characters,
followed by PDF bytes; reply uint32 JSON length followed by JSON bytes.
No arbitrary commands, image names or paths are accepted from the client.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socketserver
import stat
import struct
import sys

from app.pdf_processing import PdfProcessingError, parse_pdf_document

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_BYTES = 3_000_000
_SOCKET_DEFAULT = "/run/tramitia/pdf-broker.sock"


def _read_exact(sock, length: int) -> bytes:
    chunks = []
    while length:
        part = sock.recv(min(length, 65536))
        if not part:
            raise ConnectionError("Truncated broker message")
        chunks.append(part)
        length -= len(part)
    return b"".join(chunks)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(19)
        try:
            header = _read_exact(self.request, 12)
            length, pages, chars = struct.unpack("!III", header)
            if not (0 < length <= MAX_DOCUMENT_BYTES
                    and 1 <= pages <= 50 and 1 <= chars <= 200_000):
                return
            # Never read more than the validated PDF byte count.
            data = _read_exact(self.request, length)
            try:
                text, count = parse_pdf_document(
                    data, max_pages=pages, max_chars=chars
                )
                payload = {"pages": count, "text": text}
            except PdfProcessingError as exc:
                # Fixed error vocabulary prevents leaks from the privileged broker.
                kind = {
                    400: "invalid", 413: "too_large",
                    422: "failed", 503: "unavailable",
                }.get(exc.status_code, "failed")
                payload = {"broker_error": kind}
            content = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")
            if len(content) > MAX_RESPONSE_BYTES:
                content = b'{"broker_error":"failed"}'
            self.request.sendall(struct.pack("!I", len(content)) + content)
        except (ConnectionError, BrokenPipeError, OSError, TimeoutError,
                ValueError, struct.error):
            # A malformed client request is discarded with no document echo.
            return


class PdfBrokerServer(socketserver.UnixStreamServer):
    allow_reuse_address = False


def create_server(socket_path: str) -> PdfBrokerServer:
    if not hasattr(socketserver, "UnixStreamServer") or sys.platform == "win32":
        raise RuntimeError("The PDF broker requires a POSIX Unix-domain socket")
    path = Path(socket_path)
    if not path.is_absolute():
        raise RuntimeError("Broker socket must use an absolute path")
    directory = path.parent
    if not directory.is_dir():
        raise RuntimeError("Broker socket directory must exist")
    mode = directory.stat().st_mode
    if mode & stat.S_IWOTH:
        raise RuntimeError("Broker directory cannot be world-writable")
    if path.exists() or path.is_symlink():
        raise RuntimeError("Broker socket already exists; check stale service")
    server = PdfBrokerServer(str(path), _Handler)
    try:
        group = os.getenv("TRAMITIA_BROKER_SOCKET_GID")
        if group is not None:
            os.chown(path, -1, int(group))
            os.chmod(path, 0o660)
        else:
            os.chmod(path, 0o600)
    except Exception:
        server.server_close()
        path.unlink(missing_ok=True)
        raise
    return server


def main() -> None:
    if os.environ.get("TRAMITIA_PDF_WORKER_BACKEND") != "container":
        raise SystemExit("Broker must launch fixed-function container workers")
    socket_path = os.environ.get("TRAMITIA_BROKER_SOCKET", _SOCKET_DEFAULT)
    server = create_server(socket_path)
    try:
        with server:
            server.serve_forever(poll_interval=0.1)
    finally:
        Path(socket_path).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
