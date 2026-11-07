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
import socket
import socketserver
import stat
import struct
import sys
import threading
import time

from app.pdf_processing import PdfProcessingError, parse_pdf_document

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_BYTES = 3_000_000
MAX_REQUEST_RECEIVE_SECONDS = 10
MAX_CONCURRENT_BROKER_REQUESTS = 1
_SOCKET_DEFAULT = "/run/tramitia/pdf-broker.sock"


def _configured_peer_uid() -> int | None:
    """Require a distinct trusted web UID for the daemonless broker.

    SO_PEERCRED is supplied by Linux for the connected socket, not by the
    requesting process. Other execution modes may opt in to the same check.
    """
    raw = os.getenv("TRAMITIA_BROKER_ALLOWED_PEER_UID", "").strip()
    daemonless = os.getenv("TRAMITIA_PDF_BROKER_DAEMONLESS", "0") == "1"
    if not raw:
        if daemonless:
            raise RuntimeError("Daemonless broker requires an allowed web peer UID")
        return None
    if sys.platform != "linux" or not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeError("Broker peer UID checks require Linux SO_PEERCRED")
    if not raw.isascii() or not raw.isdecimal() or int(raw) == 0:
        raise RuntimeError("Invalid allowed broker web peer UID")
    uid = int(raw)
    if uid >= (1 << 32) or uid == os.geteuid():
        raise RuntimeError("Broker web peer UID must differ from the broker UID")
    return uid


def _read_exact(sock, length: int, *, deadline: float) -> bytes:
    """Bound total time to deliver the request, not just time between bytes."""
    chunks = []
    while length:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("PDF broker request exceeded receive deadline")
        sock.settimeout(remaining)
        part = sock.recv(min(length, 65536))
        if not part:
            raise ConnectionError("Truncated broker message")
        chunks.append(part)
        length -= len(part)
    return b"".join(chunks)


def _send_payload(sock, payload: dict) -> None:
    content = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")
    if len(content) > MAX_RESPONSE_BYTES:
        content = b'{"broker_error":"failed"}'
    sock.sendall(struct.pack("!I", len(content)) + content)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        # A peer cannot hold the broker slot forever by sending one byte at
        # a time. This absolute receive deadline is separate from PDF CPU
        # and wall-clock limits.
        deadline = time.monotonic() + MAX_REQUEST_RECEIVE_SECONDS
        try:
            header = _read_exact(self.request, 12, deadline=deadline)
            length, pages, chars = struct.unpack("!III", header)
            if not (0 < length <= MAX_DOCUMENT_BYTES
                    and 1 <= pages <= 50 and 1 <= chars <= 200_000):
                return
            # Never read more than the validated PDF byte count.
            data = _read_exact(self.request, length, deadline=deadline)
            if self.server._one_shot:
                # A mere socket connection, empty request, or malformed
                # header is NOT a completed PDF request. Those must not
                # consume the one-shot parser's processing budget.
                self.server._accepted_requests += 1
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
            self.request.settimeout(3)
            _send_payload(self.request, payload)
        except (ConnectionError, BrokenPipeError, OSError, TimeoutError,
                ValueError, struct.error):
            # A malformed client request is discarded with no document echo.
            return


class PdfBrokerServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """One active socket job; promptly reject overload without launching Docker.

    The broker-level semaphore is shared by all FastAPI worker processes
    connecting to this socket. Never start a thread for rejected work.
    """
    allow_reuse_address = False
    daemon_threads = True
    block_on_close = False
    request_queue_size = 4

    def __init__(self, server_address, request_handler_class, *,
                 allowed_peer_uid=None, one_shot=False):
        self._request_slots = threading.BoundedSemaphore(MAX_CONCURRENT_BROKER_REQUESTS)
        self._allowed_peer_uid = allowed_peer_uid
        # Opt-in parser mode handles one authenticated request before exit.
        self._one_shot = one_shot
        self._accepted_requests = 0
        super().__init__(server_address, request_handler_class)

    def process_request(self, request, client_address):
        if self._allowed_peer_uid is not None:
            # Reject before reserving capacity or accepting PDF bytes.
            # Never rely on a UID supplied inside the request protocol.
            try:
                credentials = request.getsockopt(
                    socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
                )
                _pid, peer_uid, _gid = struct.unpack("3i", credentials)
                if peer_uid != self._allowed_peer_uid:
                    self.shutdown_request(request)
                    return
            except (OSError, struct.error):
                self.shutdown_request(request)
                return
        if (self._one_shot and self._accepted_requests >= 1) or not self._request_slots.acquire(blocking=False):
            try:
                request.settimeout(1)
                _send_payload(request, {"broker_error": "busy"})
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()
            if self._one_shot and self._accepted_requests:
                # shutdown() requires a thread distinct from serve_forever.
                # An operator-managed container restart initializes the next
                # service process; no runtime Docker privileges are needed.
                self.shutdown()


def create_server(socket_path: str) -> PdfBrokerServer:
    if not hasattr(socketserver, "UnixStreamServer") or sys.platform == "win32":
        raise RuntimeError("The PDF broker requires a POSIX Unix-domain socket")
    path = Path(socket_path)
    if not path.is_absolute():
        raise RuntimeError("Broker socket must use an absolute path")
    directory = path.parent
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeError("Broker socket directory must exist and not be a symlink")
    mode = directory.stat().st_mode
    # A group-writable directory lets a compromised web process unlink or
    # replace the socket and impersonate the trusted PDF broker.
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeError("Broker directory cannot be writable by group or others")
    if path.exists() or path.is_symlink():
        raise RuntimeError("Broker socket already exists; check stale service")
    allowed_peer_uid = _configured_peer_uid()
    one_shot = os.getenv("TRAMITIA_PARSER_ONE_SHOT", "0")
    if one_shot not in ("0", "1"):
        raise RuntimeError("Invalid one-shot parser setting")
    if one_shot == "1":
        if (os.getenv("TRAMITIA_PDF_BROKER_DAEMONLESS") != "1"
                or os.getenv("TRAMITIA_PDF_WORKER_BACKEND") != "subprocess"
                or allowed_peer_uid is None):
            raise RuntimeError("One-shot mode requires a daemonless parser service")
    server = PdfBrokerServer(
        str(path), _Handler, allowed_peer_uid=allowed_peer_uid,
        one_shot=(one_shot == "1"),
    )
    try:
        group = os.getenv("TRAMITIA_BROKER_SOCKET_GROUP", "").strip()
        numeric_gid = os.getenv("TRAMITIA_BROKER_SOCKET_GID", "").strip()
        if group and numeric_gid:
            raise RuntimeError("Configure either socket GROUP or GID, not both")
        if group:
            # grp is POSIX-only; keep Windows development imports working.
            import grp
            if not group.replace("-", "").replace("_", "").isalnum():
                raise RuntimeError("Invalid broker socket group name")
            gid = grp.getgrnam(group).gr_gid
        elif numeric_gid:
            if not numeric_gid.isascii() or not numeric_gid.isdigit():
                raise RuntimeError("Invalid broker socket group ID")
            gid = int(numeric_gid)
        else:
            gid = None
        if gid is not None:
            if path.stat().st_gid != gid:
                os.chown(path, -1, gid)
            os.chmod(path, 0o660)
        else:
            os.chmod(path, 0o600)
    except Exception:
        server.server_close()
        path.unlink(missing_ok=True)
        raise
    return server


def validate_broker_runtime() -> None:
    """Require deliberate selection of one of two isolated execution modes.

    In daemonless mode an outer, separately provisioned container provides
    kernel isolation; the broker must not control a container runtime.
    """
    backend = os.environ.get("TRAMITIA_PDF_WORKER_BACKEND")
    daemonless = os.environ.get("TRAMITIA_PDF_BROKER_DAEMONLESS", "0")
    if daemonless == "0":
        if backend != "container":
            raise RuntimeError("Host broker must launch fixed-function containers")
        return
    if daemonless != "1":
        raise RuntimeError("Invalid daemonless broker mode")
    if sys.platform != "linux" or not hasattr(os, "geteuid"):
        raise RuntimeError("Daemonless broker requires a Linux sandbox")
    if os.geteuid() == 0 or backend not in ("subprocess", "parser_socket"):
        raise RuntimeError("Daemonless broker must be non-root with a restricted parser")
    if any(name in os.environ for name in (
        "TRAMITIA_JWT_SECRET", "POSTGRES_PASSWORD",
        "DATABASE_URL", "DOCKER_HOST", "DOCKER_CONTEXT",
    )):
        raise RuntimeError("Daemonless broker must have no application or Docker secrets")
    if Path("/var/run/docker.sock").exists():
        raise RuntimeError("Daemonless broker must not expose Docker socket")
    _configured_peer_uid()
    one_shot = os.environ.get("TRAMITIA_PARSER_ONE_SHOT", "0")
    if one_shot not in ("0", "1"):
        raise RuntimeError("Invalid one-shot parser setting")
    if one_shot == "1" and backend != "subprocess":
        raise RuntimeError("Only the isolated subprocess parser can be one-shot")
    if backend == "parser_socket":
        parser_path = os.environ.get("TRAMITIA_PARSER_SOCKET", "")
        broker_path = os.environ.get("TRAMITIA_BROKER_SOCKET", _SOCKET_DEFAULT)
        raw_uid = os.environ.get("TRAMITIA_PARSER_ALLOWED_PEER_UID", "")
        if not parser_path or not Path(parser_path).is_absolute():
            raise RuntimeError("Isolated parser socket must be an absolute path")
        if parser_path == broker_path:
            raise RuntimeError("Parser and broker must use different socket paths")
        if (not raw_uid.isascii() or not raw_uid.isdecimal()
                or int(raw_uid) in (0, os.geteuid())
                or int(raw_uid) >= (1 << 32)):
            raise RuntimeError("Isolated parser requires a distinct peer UID")


def _harden_daemonless_broker_process() -> None:
    """Prevent same-UID parser children from inspecting broker memory.

    Linux PR_SET_DUMPABLE=0 blocks ordinary same-UID ptrace and /proc/pid/mem
    access, reducing the impact of a compromised parser. This is NOT a UID
    boundary and cannot stop the parser from replacing a writable socket path.
    Fail closed if the kernel cannot enforce or verify the setting.
    """
    if sys.platform != "linux":
        raise RuntimeError("Broker memory protection requires Linux")
    import ctypes

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
        prctl.argtypes = (
            ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_ulong, ctypes.c_ulong,
        )
        prctl.restype = ctypes.c_int
        # PR_SET_DUMPABLE=4, PR_GET_DUMPABLE=3.
        if prctl(4, 0, 0, 0, 0) != 0:
            raise RuntimeError("Cannot disable broker process dumpability")
        if prctl(3, 0, 0, 0, 0) != 0:
            raise RuntimeError("Cannot verify broker process dumpability")
    except (AttributeError, OSError) as exc:
        raise RuntimeError("Broker memory protection is unavailable") from exc


def main() -> None:
    validate_broker_runtime()
    if os.environ.get("TRAMITIA_PDF_BROKER_DAEMONLESS", "0") == "1":
        _harden_daemonless_broker_process()
    socket_path = os.environ.get("TRAMITIA_BROKER_SOCKET", _SOCKET_DEFAULT)
    server = create_server(socket_path)
    try:
        with server:
            server.serve_forever(poll_interval=0.1)
    finally:
        Path(socket_path).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
