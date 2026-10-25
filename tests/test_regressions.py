import io
import os
import unittest
import threading

import httpx
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

# Unit tests use a non-production secret and mock membership/database access.
os.environ.setdefault("TRAMITIA_JWT_SECRET", "only-for-tests-never-use-this-value-in-production-123")
from app.auth import Principal, issue_token
from app.main import app

AUTH_HEADERS = {"Authorization": "Bearer " + issue_token(Principal(11, 21, "owner"))}
from app.core.validators import validate_basic


def make_pdf(encrypted=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
    })
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 250 Td (Solicitud de prueba. Contacto 612345678. Firma.) Tj ET")
    page[NameObject("/Contents")] = stream
    if encrypted:
        writer.encrypt("test-password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.client.headers.update(AUTH_HEADERS)
        member = patch("app.auth.get_membership_role", return_value="owner")
        member.start()
        self.addCleanup(member.stop)
        # Request-limit tests use a separate PostgreSQL integration suite.
        quota = patch("app.main.consume_analysis_quota")
        quota.start()
        self.addCleanup(quota.stop)

    def test_invalid_uploads_return_400_without_saving(self):
        for data in (b"", b"not a PDF", b"%PDF-1.7\ntruncated", make_pdf(True)):
            with self.subTest(data=data[:20]), patch("app.main.save_analysis") as save:
                response = self.client.post(
                    "/analyze", files={"file": ("document.pdf", data, "application/pdf")}
                )
                self.assertEqual(response.status_code, 400, response.text)
                self.assertTrue(response.json()["detail"])
                save.assert_not_called()

    def test_valid_pdf_extracts_text_and_saves(self):
        with patch("app.main.save_analysis", return_value=42) as save:
            response = self.client.post(
                "/analyze", files={"file": ("document.pdf", make_pdf(), "application/pdf")}
            )
        self.assertEqual(response.status_code, 200, response.text)
        save.assert_called_once()
        report = save.call_args.args[0]
        self.assertIn("Contacto 612345678", report["extracted_text"])
        self.assertEqual(report["pages"], 1)
        self.assertNotIn("MISSING_PHONE", [f["code"] for f in report["findings"]])
        self.assertNotIn("extracted_text", response.json())

    def test_upload_size_boundary(self):
        data = make_pdf()
        with patch("app.main.MAX_UPLOAD_BYTES", len(data)), patch("app.main.save_analysis", return_value=1) as save:
            response = self.client.post(
                "/analyze", files={"file": ("ok.pdf", data, "application/pdf")}
            )
            self.assertEqual(response.status_code, 200)
            save.assert_called_once()
        with patch("app.main.MAX_UPLOAD_BYTES", len(data) - 1), patch("app.main.save_analysis") as save, patch("app.main.parse_pdf_document") as reader:
            response = self.client.post(
                "/analyze", files={"file": ("large.pdf", data, "application/pdf")}
            )
            self.assertEqual(response.status_code, 413)
            self.assertIn("20 MB", response.json()["detail"])
            reader.assert_not_called()
            save.assert_not_called()

    def test_reject_non_pdf_signature_before_parser(self):
        # The client-provided filename and MIME must never prove a file is a PDF.
        with patch("app.main.save_analysis") as save, patch("app.main.parse_pdf_document") as reader:
            response = self.client.post(
                "/analyze",
                files={"file": ("fake.pdf", b"not a real PDF", "application/pdf")},
            )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("cabecera PDF", response.json()["detail"])
        reader.assert_not_called()
        save.assert_not_called()

    def test_zero_page_pdf_is_not_saved(self):
        out = io.BytesIO()
        PdfWriter().write(out)
        with patch("app.main.save_analysis") as save:
            response = self.client.post(
                "/analyze",
                files={"file": ("empty.pdf", out.getvalue(), "application/pdf")},
            )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("no contiene páginas", response.json()["detail"])
        save.assert_not_called()

    def test_page_ceiling_rejects_before_text_extraction(self):
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        writer.add_blank_page(width=300, height=300)
        out = io.BytesIO()
        writer.write(out)
        with patch("app.main.MAX_PDF_PAGES", 1), patch("app.main.save_analysis") as save:
            response = self.client.post(
                "/analyze",
                files={"file": ("too-many.pdf", out.getvalue(), "application/pdf")},
            )
        self.assertEqual(response.status_code, 413, response.text)
        self.assertIn("1 páginas", response.json()["detail"])
        save.assert_not_called()

    def test_extracted_text_ceiling_rejects_without_saving(self):
        with patch("app.main.MAX_EXTRACTED_TEXT_CHARS", 10), patch("app.main.save_analysis") as save:
            response = self.client.post(
                "/analyze",
                files={"file": ("much-text.pdf", make_pdf(), "application/pdf")},
            )
        self.assertEqual(response.status_code, 413, response.text)
        self.assertIn("10 caracteres", response.json()["detail"])
        save.assert_not_called()

    def test_filename_is_stored_as_basename_not_local_path(self):
        with patch("app.main.save_analysis", return_value=42) as save:
            response = self.client.post(
                "/analyze",
                files={"file": (r"C:\\users\\owner\\financial.pdf", make_pdf(), "application/pdf")},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(save.call_args.args[0]["filename"], "financial.pdf")

    def test_filename_ceiling_rejects_without_saving(self):
        with patch("app.main.save_analysis") as save:
            response = self.client.post(
                "/analyze",
                files={"file": ("x" * 210 + ".pdf", make_pdf(), "application/pdf")},
            )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("nombre del archivo", response.json()["detail"])
        save.assert_not_called()

    def test_database_errors_remain_server_errors(self):
        with patch("app.main.save_analysis", side_effect=RuntimeError("database unavailable")):
            response = self.client.post(
                "/analyze", files={"file": ("document.pdf", make_pdf(), "application/pdf")}
            )
        self.assertEqual(response.status_code, 500)


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocking_work_runs_outside_event_loop(self):
        event_loop_thread = threading.get_ident()
        worker_threads = []

        def save(report, organization_id):
            self.assertEqual(organization_id, 21)
            worker_threads.append(threading.get_ident())
            return 1

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=AUTH_HEADERS) as client:
            with patch("app.auth.get_membership_role", return_value="owner"), patch("app.main.consume_analysis_quota"), patch("app.main.save_analysis", side_effect=save):
                response = await client.post(
                    "/analyze", files={"file": ("ok.pdf", make_pdf(), "application/pdf")}
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(worker_threads), 1)
        self.assertNotEqual(worker_threads[0], event_loop_thread)


class PhoneTests(unittest.TestCase):
    def findings(self, contact):
        return {f.code for f in validate_basic(
            "Solicitud de ayuda con datos suficientes para revisar. Contacto "
            + contact + " Firma del solicitante."
        )}

    def test_phone_formats_surrounded_by_words(self):
        for phone in ("612345678", "612 345 678", "612-345-678",
                      "+34612345678", "+34 612 345 678", "912345678",
                      "612\u00a0345\u00a0678"):
            with self.subTest(phone=phone):
                self.assertNotIn("MISSING_PHONE", self.findings(phone))

    def test_missing_invalid_and_embedded_numbers(self):
        for value in ("sin telefono", "61234567", "6123456789",
                      "1612345678", "abc612345678", "612345678abc"):
            with self.subTest(value=value):
                self.assertIn("MISSING_PHONE", self.findings(value))


if __name__ == "__main__":
    unittest.main()


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_anonymous_requests_are_rejected(self):
        for method, path in (
            ("post", "/analyze"), ("get", "/analyses"),
            ("get", "/analyses/1"), ("get", "/analyses/1/export"),
            ("get", "/auth/me"),
        ):
            with self.subTest(path=path), patch("app.main.save_analysis") as save:
                if method == "post":
                    response = self.client.post(path, files={"file": ("sample.pdf", b"x", "application/pdf")})
                else:
                    response = self.client.get(path)
                self.assertEqual(response.status_code, 401, response.text)
                save.assert_not_called()

    def test_invalid_token_is_rejected(self):
        response = self.client.get("/analyses", headers={"Authorization": "Bearer tampered"})
        self.assertEqual(response.status_code, 401)

    def test_revoked_membership_is_rejected(self):
        with patch("app.auth.get_membership_role", return_value=None):
            response = self.client.get("/analyses", headers=AUTH_HEADERS)
        self.assertEqual(response.status_code, 401)

    def test_tenant_id_is_taken_from_principal_for_all_routes(self):
        with patch("app.auth.get_membership_role", return_value="employee"):
            with patch("app.main.list_analyses", return_value={"total": 0, "items": [], "limit": 20, "offset": 0}) as listing:
                response = self.client.get("/analyses", headers=AUTH_HEADERS)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(listing.call_args.kwargs["organization_id"], 21)

            with patch("app.main.get_analysis_by_id", return_value=None) as detail:
                response = self.client.get("/analyses/999", headers=AUTH_HEADERS)
                self.assertEqual(response.status_code, 404)
                detail.assert_called_once_with(999, 21)

            with patch("app.main.get_analysis_export", return_value=None) as export:
                response = self.client.get("/analyses/999/export", headers=AUTH_HEADERS)
                self.assertEqual(response.status_code, 404)
                export.assert_called_once_with(999, 21)

    def test_wrong_secret_rejects_token(self):
        with patch.dict(os.environ, {"TRAMITIA_JWT_SECRET": "another-valid-secret-with-more-than-32-chars"}):
            response = self.client.get("/analyses", headers=AUTH_HEADERS)
        self.assertEqual(response.status_code, 401)
