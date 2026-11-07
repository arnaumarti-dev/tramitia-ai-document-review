"""Unix-socket protocol tests; the API client must never invoke Docker."""
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app.pdf_broker import _read_exact, create_server, validate_broker_runtime
from app.pdf_processing import (
    PdfProcessingError, _broker_request, parse_pdf_document,
)
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

    def test_rejects_group_writable_or_symlinked_socket_directory(self):
        with tempfile.TemporaryDirectory(prefix="tramitia-dir-policy-") as folder:
            directory = Path(folder)
            path = directory / "new-broker.sock"
            directory.chmod(0o770)
            with self.assertRaisesRegex(RuntimeError, "writable by group"):
                create_server(str(path))
            self.assertFalse(path.exists())

            directory.chmod(0o750)
            server = create_server(str(path))
            try:
                self.assertTrue(path.is_socket())
            finally:
                server.server_close()
                path.unlink(missing_ok=True)

            alias = directory / "socket-dir-link"
            real = directory / "socket-dir"
            real.mkdir()
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                create_server(str(alias / "broker.sock"))

    @unittest.skipUnless(
        hasattr(socket, "SO_PEERCRED") and os.name == "posix",
        "Linux SO_PEERCRED required",
    )
    def test_wrong_peer_uid_rejected_before_pdf_processing(self):
        with tempfile.TemporaryDirectory(prefix="tramitia-peer-") as folder:
            path = str(Path(folder) / "peer.sock")
            with patch.dict(os.environ, {
                "TRAMITIA_PDF_BROKER_DAEMONLESS": "0",
                "TRAMITIA_BROKER_ALLOWED_PEER_UID": str(os.getuid() + 1),
            }):
                server = create_server(path)
            thread = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.01}, daemon=True,
            )
            thread.start()
            try:
                with patch("app.pdf_broker.parse_pdf_document") as parser:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                        sock.settimeout(3)
                        sock.connect(path)
                        self.assertEqual(sock.recv(1), b"")
                    parser.assert_not_called()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                Path(path).unlink(missing_ok=True)

    def test_good_pdf_protocol_returns_text_without_docker_in_api(self):
        sample = make_demo_pdf()
        with patch("app.pdf_broker.parse_pdf_document", return_value=("Texto ficticio", 1)) as parser, patch(
            "app.pdf_processing._run_bounded_worker"
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

    def test_busy_broker_rejects_second_client_and_recovers(self):
        """Global broker slot works even when different APIs connect."""
        entered = threading.Event()
        release = threading.Event()
        first = {}
        sample = make_demo_pdf()

        def slow_worker(data, *, max_pages, max_chars):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("Test worker release timed out")
            return ("PDF ficticio", 1)

        def first_client():
            try:
                first["reply"] = json.loads(
                    _broker_request(sample, 50, 200_000)
                )
            except Exception as exc:
                first["exception"] = exc

        with patch("app.pdf_broker.parse_pdf_document", side_effect=slow_worker) as parser:
            job = threading.Thread(target=first_client, daemon=True)
            job.start()
            try:
                self.assertTrue(entered.wait(timeout=3))
                started = time.monotonic()
                with self.assertRaises(PdfProcessingError) as ctx:
                    parse_pdf_document(sample)
                self.assertEqual(ctx.exception.status_code, 503)
                self.assertIn("ocupado", ctx.exception.message)
                self.assertLess(time.monotonic() - started, 2.0)
                parser.assert_called_once()
            finally:
                release.set()
                job.join(timeout=5)
            self.assertFalse(job.is_alive(), "Worker socket did not return")
            self.assertNotIn("exception", first, first.get("exception"))
            self.assertEqual(first["reply"], {"text": "PDF ficticio", "pages": 1})
            self.assertEqual(
                parse_pdf_document(sample), ("PDF ficticio", 1)
            )
            self.assertEqual(parser.call_count, 2)

    def test_receive_deadline_expires_without_waiting_for_more_pdf_bytes(self):
        class Stream:
            def recv(self, count):
                raise AssertionError("Expired request should not receive data")

            def settimeout(self, timeout):
                raise AssertionError("Expired request should not reset timeout")

        with self.assertRaises(TimeoutError):
            _read_exact(Stream(), 1, deadline=time.monotonic() - 0.01)

    def test_missing_broker_fails_closed_not_fallback_docker(self):
        with patch.dict(os.environ, {
            "TRAMITIA_PDF_WORKER_BACKEND": "broker",
            "TRAMITIA_BROKER_SOCKET": self.path + ".missing",
        }), patch("app.pdf_processing._run_bounded_worker") as spawn:
            with self.assertRaises(PdfProcessingError) as ctx:
                parse_pdf_document(make_demo_pdf())
        self.assertEqual(ctx.exception.status_code, 503)
        spawn.assert_not_called()


class DaemonlessBrokerRuntimeTests(unittest.TestCase):
    def test_existing_host_broker_keeps_fixed_container_backend(self):
        with patch.dict(os.environ, {
            "TRAMITIA_PDF_BROKER_DAEMONLESS": "0",
            "TRAMITIA_PDF_WORKER_BACKEND": "container",
        }):
            validate_broker_runtime()
        with patch.dict(os.environ, {
            "TRAMITIA_PDF_BROKER_DAEMONLESS": "0",
            "TRAMITIA_PDF_WORKER_BACKEND": "subprocess",
        }):
            with self.assertRaisesRegex(RuntimeError, "fixed-function"):
                validate_broker_runtime()

    def test_daemonless_broker_requires_nonroot_linux_and_no_docker(self):
        env = {
            "TRAMITIA_PDF_BROKER_DAEMONLESS": "1",
            "TRAMITIA_PDF_WORKER_BACKEND": "subprocess",
            "TRAMITIA_BROKER_ALLOWED_PEER_UID": "12345",
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "app.pdf_broker.os.geteuid", return_value=65534
        ), patch("app.pdf_broker.Path.exists", return_value=False):
            validate_broker_runtime()
        with patch.dict(os.environ, env, clear=True), patch(
            "app.pdf_broker.os.geteuid", return_value=0
        ):
            with self.assertRaisesRegex(RuntimeError, "non-root"):
                validate_broker_runtime()
        with patch.dict(os.environ, env, clear=True), patch(
            "app.pdf_broker.os.geteuid", return_value=65534
        ), patch("app.pdf_broker.Path.exists", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "Docker socket"):
                validate_broker_runtime()

    def test_daemonless_rejects_docker_backend_or_inherited_secrets(self):
        for extra in (
            {"TRAMITIA_PDF_WORKER_BACKEND": "container"},
            {"TRAMITIA_JWT_SECRET": "never-in-broker"},
            {"POSTGRES_PASSWORD": "never-in-broker"},
            {"DOCKER_HOST": "unix:///var/run/docker.sock"},
        ):
            env = {
                "TRAMITIA_PDF_BROKER_DAEMONLESS": "1",
                "TRAMITIA_PDF_WORKER_BACKEND": "subprocess",
                "TRAMITIA_BROKER_ALLOWED_PEER_UID": "12345",
                **extra,
            }
            with self.subTest(extra=extra), patch.dict(
                os.environ, env, clear=True
            ), patch("app.pdf_broker.os.geteuid", return_value=65534), patch(
                "app.pdf_broker.Path.exists", return_value=False
            ):
                with self.assertRaises(RuntimeError):
                    validate_broker_runtime()

    def test_daemonless_requires_distinct_explicit_web_identity(self):
        env = {
            "TRAMITIA_PDF_BROKER_DAEMONLESS": "1",
            "TRAMITIA_PDF_WORKER_BACKEND": "subprocess",
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "app.pdf_broker.os.geteuid", return_value=65534
        ), patch("app.pdf_broker.Path.exists", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "allowed web peer UID"):
                validate_broker_runtime()

        with patch.dict(os.environ, {
            **env, "TRAMITIA_BROKER_ALLOWED_PEER_UID": "65534"
        }, clear=True), patch(
            "app.pdf_broker.os.geteuid", return_value=65534
        ), patch("app.pdf_broker.Path.exists", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "must differ"):
                validate_broker_runtime()

    @unittest.skipUnless(os.name == "posix", "Linux-specific process protection")
    def test_daemonless_hardening_sets_and_verifies_nondumpable_state(self):
        with patch("ctypes.CDLL") as libc:
            prctl = libc.return_value.prctl
            prctl.side_effect = [0, 0]
            from app.pdf_broker import _harden_daemonless_broker_process
            _harden_daemonless_broker_process()
        self.assertEqual(prctl.call_count, 2)
        self.assertEqual(prctl.call_args_list[0].args, (4, 0, 0, 0, 0))
        self.assertEqual(prctl.call_args_list[1].args, (3, 0, 0, 0, 0))

    @unittest.skipUnless(os.name == "posix", "Linux-specific process protection")
    def test_daemonless_hardening_refuses_missing_kernel_protection(self):
        from app.pdf_broker import _harden_daemonless_broker_process
        for results in ([-1], [0, 1]):
            with self.subTest(results=results), patch("ctypes.CDLL") as libc:
                libc.return_value.prctl.side_effect = results
                with self.assertRaisesRegex(RuntimeError, "dumpability"):
                    _harden_daemonless_broker_process()
        with patch("ctypes.CDLL", side_effect=OSError("blocked")):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                _harden_daemonless_broker_process()

    def test_main_hardens_before_binding_daemonless_socket(self):
        from app.pdf_broker import main
        with patch.dict(os.environ, {
            "TRAMITIA_PDF_BROKER_DAEMONLESS": "1",
            "TRAMITIA_BROKER_SOCKET": "/tmp/test-broker-not-created.sock",
        }), patch("app.pdf_broker.validate_broker_runtime"), patch(
            "app.pdf_broker._harden_daemonless_broker_process"
        ) as protect, patch(
            "app.pdf_broker.create_server"
        ) as create, patch("app.pdf_broker.Path.unlink"):
            main()
            protect.assert_called_once_with()
            create.assert_called_once()
            # A failed hardening step must abort before opening the socket.
        with patch.dict(os.environ, {
            "TRAMITIA_PDF_BROKER_DAEMONLESS": "1"
        }), patch("app.pdf_broker.validate_broker_runtime"), patch(
            "app.pdf_broker._harden_daemonless_broker_process",
            side_effect=RuntimeError("not protected")
        ), patch("app.pdf_broker.create_server") as create:
            with self.assertRaisesRegex(RuntimeError, "not protected"):
                main()
            create.assert_not_called()

    def test_daemonless_broker_validates_separate_parser_socket_identity(self):
        original = {
            "TRAMITIA_PDF_BROKER_DAEMONLESS": "1",
            "TRAMITIA_PDF_WORKER_BACKEND": "parser_socket",
            "TRAMITIA_BROKER_ALLOWED_PEER_UID": "12345",
            "TRAMITIA_BROKER_SOCKET": "/run/tramitia/pdf-broker.sock",
            "TRAMITIA_PARSER_SOCKET": "/run/tramitia-parser/parser.sock",
            "TRAMITIA_PARSER_ALLOWED_PEER_UID": "65533",
        }
        with patch.dict(os.environ, original, clear=True), patch(
            "app.pdf_broker.os.geteuid", return_value=65534
        ), patch("app.pdf_broker.Path.exists", return_value=False):
            validate_broker_runtime()

        variants = [
            {"TRAMITIA_PARSER_SOCKET": ""},
            {"TRAMITIA_PARSER_SOCKET": "relative.sock"},
            {"TRAMITIA_PARSER_SOCKET": "/run/tramitia/pdf-broker.sock"},
            {"TRAMITIA_PARSER_ALLOWED_PEER_UID": "65534"},
            {"TRAMITIA_PARSER_ALLOWED_PEER_UID": "0"},
            {"TRAMITIA_PARSER_ALLOWED_PEER_UID": ""},
        ]
        for change in variants:
            with self.subTest(change=change), patch.dict(
                os.environ, {**original, **change}, clear=True
            ), patch("app.pdf_broker.os.geteuid", return_value=65534), patch(
                "app.pdf_broker.Path.exists", return_value=False
            ):
                with self.assertRaises(RuntimeError):
                    validate_broker_runtime()

    def test_invalid_daemonless_selector_fails_closed(self):
        with patch.dict(os.environ, {
            "TRAMITIA_PDF_BROKER_DAEMONLESS": "yes",
            "TRAMITIA_PDF_WORKER_BACKEND": "container",
        }):
            with self.assertRaisesRegex(RuntimeError, "Invalid"):
                validate_broker_runtime()


if __name__ == "__main__":
    unittest.main()
