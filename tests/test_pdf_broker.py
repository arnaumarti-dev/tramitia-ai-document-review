"""Unix-socket protocol tests; the API client must never invoke Docker."""
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.pdf_broker import create_server
from app.pdf_processing import PdfProcessingError, parse_pdf_document
from scripts.generate_demo_pdf import make_demo_pdf


@unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Unix domain sockets are POSIX-only")
class BrokerProtocolTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="tramitia-broker-")
        self.path = str(Path(self.folder.name) / "broker.sock")
        self.server = create_server(self.path)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()
        self.patch_env = patch.dict(os.environ, {
            "TRAMITIA_PDF_WORKER_BACKEND": "broker",
            "TRAMITIA_BROKER_SOCKET": self.path,
        })
        self.patch_env.start()

    def tearDown(self):
        self.patch_env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        Path(self.path).unlink(missing_ok=True)
        self.folder.cleanup()

    def test_socket_is_private_and_rejects_duplicate_owner(self):
        self.assertEqual(Path(self.path).stat().st_mode & 0o777, 0o600)
        with self.assertRaises(RuntimeError):
            create_server(self.path)

    def test_good_pdf_protocol_returns_text_without_docker_in_api(self):
        sample = make_demo_pdf()
        with patch("app.pdf_broker.parse_pdf_document", return_value=("Texto ficticio", 1)) as parser, patch(
            "app.pdf_processing.subprocess.run"
        ) as forbidden:
            self.assertEqual(parse_pdf_document(sample), ("Texto ficticio", 1))
        parser.assert_called_once_with(sample, max_pages=50, max_chars=200_000)
        forbidden.assert_not_called()

    def test_broker_errors_are_fixed_code_not_debug_tracebacks(self):
        with patch("app.pdf_broker.parse_pdf_document",
                   side_effect=PdfProcessingError(413, "raw secret / private error")):
            with self.assertRaises(PdfProcessingError) as ctx:
                parse_pdf_document(make_demo_pdf())
        self.assertEqual(ctx.exception.status_code, 413)
        self.assertNotIn("secret", ctx.exception.message)

    def test_malformed_client_request_does_not_invoke_parser(self):
        with patch("app.pdf_broker.parse_pdf_document") as forbidden:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(2)
                sock.connect(self.path)
                sock.sendall(struct.pack("!III", 20 * 1024 * 1024 + 1, 50, 200_000))
                sock.shutdown(socket.SHUT_WR)
                self.assertEqual(sock.recv(1), b"")
            forbidden.assert_not_called()

    def test_missing_broker_fails_closed_not_fallback_docker(self):
        with patch.dict(os.environ, {
            "TRAMITIA_PDF_WORKER_BACKEND": "broker",
            "TRAMITIA_BROKER_SOCKET": self.path + ".missing",
        }), patch("app.pdf_processing.subprocess.run") as spawn:
            with self.assertRaises(PdfProcessingError) as ctx:
                parse_pdf_document(make_demo_pdf())
        self.assertEqual(ctx.exception.status_code, 503)
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
