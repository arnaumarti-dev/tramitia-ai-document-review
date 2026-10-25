"""PDF subprocess tests, including failures that never touch PostgreSQL."""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.pdf_processing import (
    PdfProcessingError, parse_pdf_document, _minimal_environment,
    _run_bounded_worker,
)
from app.main import app
from scripts.generate_demo_pdf import make_demo_pdf

# Unit route checks use the same dummy JWT approach as the regression suite.
os.environ.setdefault(
    "TRAMITIA_JWT_SECRET", "only-for-tests-never-use-this-value-in-production-123"
)
from app.auth import Principal, issue_token
AUTH_HEADERS = {
    "Authorization": "Bearer " + issue_token(Principal(11, 21, "owner"))
}


class RealPDFWorkerTests(unittest.TestCase):
    def test_can_parse_fictional_pdf_using_real_child(self):
        text, pages = parse_pdf_document(make_demo_pdf())
        self.assertEqual(pages, 1)
        self.assertIn("Solicitud ficticia", text)

    def test_rejects_more_pages_before_extracting(self):
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        writer.add_blank_page(width=300, height=300)
        out = io.BytesIO()
        writer.write(out)
        with self.assertRaises(PdfProcessingError) as ctx:
            parse_pdf_document(out.getvalue(), max_pages=1)
        self.assertEqual(ctx.exception.status_code, 413)
        self.assertIn("1 páginas", ctx.exception.message)

    def test_rejects_extracted_text_over_limit(self):
        with self.assertRaises(PdfProcessingError) as ctx:
            parse_pdf_document(make_demo_pdf(), max_chars=10)
        self.assertEqual(ctx.exception.status_code, 413)
        self.assertIn("10 caracteres", ctx.exception.message)

    def test_invalid_and_encrypted_input(self):
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        writer.encrypt("fictional-password")
        out = io.BytesIO()
        writer.write(out)
        for data, code in [(b"%PDF-1.7\\ninvalid", 400), (out.getvalue(), 400)]:
            with self.subTest(data=data[:10]):
                with self.assertRaises(PdfProcessingError) as ctx:
                    parse_pdf_document(data)
                self.assertEqual(ctx.exception.status_code, code)


class PDFWorkerFailureTests(unittest.TestCase):
    def test_worker_environment_excludes_application_credentials(self):
        with patch.dict(os.environ, {
            "TRAMITIA_JWT_SECRET": "not-available-to-worker",
            "POSTGRES_PASSWORD": "not-available-to-worker",
            "AWS_SECRET_ACCESS_KEY": "not-available-to-worker",
        }):
            environment = _minimal_environment()
            self.assertNotIn("TRAMITIA_JWT_SECRET", environment)
            self.assertNotIn("POSTGRES_PASSWORD", environment)
            self.assertNotIn("AWS_SECRET_ACCESS_KEY", environment)

    def test_subprocess_is_always_isolated_mode_with_timeout(self):
        def fake_run(argv, data, environment):
            self.assertEqual(argv[1], "-I")
            self.assertTrue(argv[2].endswith("pdf_worker.py"))
            self.assertEqual(argv[3:], ["50", "200000"])
            self.assertEqual(data, b"%PDF-1.7")
            self.assertNotIn("TRAMITIA_JWT_SECRET", environment)
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"pages": 1, "text": "fictitious"}).encode(),
            )

        with patch("app.pdf_processing._run_bounded_worker", side_effect=fake_run):
            self.assertEqual(parse_pdf_document(b"%PDF-1.7"), ("fictitious", 1))

    def test_timeout_returns_controlled_failure(self):
        with patch("app.pdf_processing._run_bounded_worker",
                   side_effect=PdfProcessingError(422, "El análisis del PDF superó el tiempo máximo permitido.")):
            with self.assertRaises(PdfProcessingError) as ctx:
                parse_pdf_document(b"%PDF-1.7")
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("tiempo máximo", ctx.exception.message)

    def test_killed_worker_and_invalid_json_are_rejected(self):
        for code, output in [(-9, b""), (0, b"not-json"),
                             (0, json.dumps({"pages": 1, "text": "A" * 200001}).encode())]:
            with self.subTest(code=code):
                with patch("app.pdf_processing._run_bounded_worker", return_value=SimpleNamespace(
                    returncode=code, stdout=output,
                )):
                    with self.assertRaises(PdfProcessingError) as ctx:
                        parse_pdf_document(b"%PDF-1.7")
                    self.assertEqual(ctx.exception.status_code, 422)

    def test_overloaded_worker_is_rejected_without_spawning(self):
        with patch("app.pdf_processing._WORKER_SLOT") as slot, patch(
            "app.pdf_processing._run_bounded_worker"
        ) as spawn:
            slot.acquire.return_value = False
            with self.assertRaises(PdfProcessingError) as ctx:
                parse_pdf_document(b"%PDF-1.7")
            self.assertEqual(ctx.exception.status_code, 503)
            self.assertIn("ocupado", ctx.exception.message)
            spawn.assert_not_called()
            slot.release.assert_not_called()

    def test_worker_slot_is_released_after_crash(self):
        with patch("app.pdf_processing._WORKER_SLOT") as slot, patch(
            "app.pdf_processing._run_bounded_worker",
            return_value=SimpleNamespace(returncode=-9, stdout=b""),
        ):
            slot.acquire.return_value = True
            with self.assertRaises(PdfProcessingError):
                parse_pdf_document(b"%PDF-1.7")
            slot.release.assert_called_once()

    def test_worker_launch_failure_is_503(self):
        with patch("app.pdf_processing._run_bounded_worker", side_effect=PdfProcessingError(503, "El servicio de análisis de PDF no está disponible.")):
            with self.assertRaises(PdfProcessingError) as ctx:
                parse_pdf_document(b"%PDF-1.7")
        self.assertEqual(ctx.exception.status_code, 503)


class BoundedStreamingWorkerTests(unittest.TestCase):
    def test_real_child_output_is_rejected_while_it_is_still_writing(self):
        script = (
            "import sys,time;"
            "sys.stdin.buffer.read();"
            "sys.stdout.buffer.write(b'x'*200000);"
            "sys.stdout.buffer.flush();"
            "time.sleep(20)"
        )
        started = time.monotonic()
        with patch("app.pdf_processing.MAX_WORKER_STDOUT_BYTES", 4096):
            with self.assertRaises(PdfProcessingError) as ctx:
                _run_bounded_worker(
                    [sys.executable, "-I", "-c", script], b"tiny",
                    _minimal_environment(),
                )
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("límite de salida", ctx.exception.message)
        self.assertLess(time.monotonic() - started, 5)

    def test_real_child_hanging_after_input_is_killed_and_slot_recovers(self):
        script = "import sys,time;sys.stdin.buffer.read();time.sleep(20)"
        started = time.monotonic()
        with patch("app.pdf_processing.MAX_WORKER_SECONDS", 0.4):
            with self.assertRaises(PdfProcessingError) as ctx:
                _run_bounded_worker(
                    [sys.executable, "-I", "-c", script], b"tiny",
                    _minimal_environment(),
                )
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("tiempo máximo", ctx.exception.message)
        self.assertLess(time.monotonic() - started, 5)
        # The parent no longer retains a blocked reader / worker slot.
        self.assertEqual(parse_pdf_document(make_demo_pdf())[1], 1)

    def test_simultaneous_stdin_and_stdout_never_deadlock(self):
        script = (
            "import sys;"
            "sys.stdout.buffer.write(b'{\"pages\":1,\"text\":\"ok\"}');"
            "sys.stdout.buffer.flush();"
            "sys.stdin.buffer.read()"
        )
        result = _run_bounded_worker(
            [sys.executable, "-I", "-c", script], b"y" * 65536,
            _minimal_environment(),
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["text"], "ok")

    @unittest.skipUnless(os.name == "posix", "Process groups are POSIX-only")
    def test_orphaned_child_cannot_survive_after_worker_leader_exits(self):
        """An orphan inheriting stdout must not evade wall-clock termination."""
        with tempfile.TemporaryDirectory(prefix="tramitia-pdf-children-") as folder:
            marker = str(Path(folder) / "orphan-lived.txt")
            orphan_code = (
                "import pathlib,sys,time;"
                "time.sleep(1.2);"
                "pathlib.Path(sys.argv[1]).write_text('escaped')"
            )
            leader_code = (
                "import subprocess,sys;"
                "subprocess.Popen("
                f"[sys.executable,'-I','-c',{orphan_code!r},{marker!r}],"
                "stdin=subprocess.DEVNULL,stdout=sys.stdout,"
                "stderr=subprocess.DEVNULL);"
                "sys.exit(0)"
            )
            with patch("app.pdf_processing.MAX_WORKER_SECONDS", 0.3):
                with self.assertRaises(PdfProcessingError) as ctx:
                    _run_bounded_worker(
                        [sys.executable, "-I", "-c", leader_code],
                        b"fictional", _minimal_environment(),
                    )
            self.assertEqual(ctx.exception.status_code, 422)
            time.sleep(1.4)
            self.assertFalse(
                Path(marker).exists(),
                "An orphaned PDF worker child survived its supervisor",
            )

    def test_nonzero_exit_produces_a_controlled_pdf_error(self):
        with patch("app.pdf_processing._worker_launch", return_value=(
            [sys.executable, "-I", "-c",
             "import sys;sys.stdin.buffer.read();sys.exit(9)"],
            _minimal_environment(),
        )):
            with self.assertRaises(PdfProcessingError) as ctx:
                parse_pdf_document(b"%PDF-1.7")
        self.assertEqual(ctx.exception.status_code, 422)


class PDFWorkerRouteFailures(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.client.headers.update(AUTH_HEADERS)
        role = patch("app.auth.get_membership_role", return_value="owner")
        role.start()
        self.addCleanup(role.stop)
        quota = patch("app.main.consume_analysis_quota")
        quota.start()
        self.addCleanup(quota.stop)
        peer = patch("app.main._consume_peer_budget")
        peer.start()
        self.addCleanup(peer.stop)

    def test_worker_timeout_or_crash_never_saves_analysis(self):
        cases = [(422, "tiempo"), (503, "no está disponible")]
        for code, message in cases:
            with self.subTest(code=code):
                with patch("app.main.parse_pdf_document",
                           side_effect=PdfProcessingError(code, message)), patch(
                    "app.main.save_analysis"
                ) as saved:
                    result = self.client.post(
                        "/analyze",
                        files={"file": ("demo.pdf", make_demo_pdf(), "application/pdf")},
                    )
                self.assertEqual(result.status_code, code, result.text)
                saved.assert_not_called()


if __name__ == "__main__":
    unittest.main()
