"""Scanned/image-only and blank PDFs must not receive fabricated risk scores."""
import io
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter

os.environ.setdefault(
    "TRAMITIA_JWT_SECRET",
    "fictional-only-test-secret-that-must-not-be-used-in-production",
)
from app.auth import Principal, issue_token
from app.main import app
from scripts.generate_demo_pdf import make_demo_pdf


class NoExtractableTextTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.client.headers.update({
            "Authorization": "Bearer " + issue_token(Principal(11, 21, "owner"))
        })
        for target, value in (
            ("app.auth.get_membership_role", "owner"),
            ("app.main._consume_peer_budget", None),
            ("app.main.consume_analysis_quota", None),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def blank_pdf(page_count=1):
        writer = PdfWriter()
        for _ in range(page_count):
            writer.add_blank_page(width=300, height=300)
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    def test_blank_pages_return_ocr_guidance_and_do_not_save(self):
        for pages in (1, 2):
            with self.subTest(pages=pages), patch("app.main.save_analysis") as save:
                result = self.client.post("/analyze", files={
                    "file": ("only-images-or-empty.pdf", self.blank_pdf(pages), "application/pdf")
                })
                self.assertEqual(result.status_code, 422, result.text)
                detail = result.json()["detail"]
                self.assertIn("texto extraíble", detail)
                self.assertIn("OCR", detail)
                save.assert_not_called()

    def test_whitespace_only_parser_output_does_not_save(self):
        with patch("app.main.parse_pdf_document", return_value=("\n  \t", 2)), patch(
            "app.main.save_analysis"
        ) as save:
            result = self.client.post("/analyze", files={
                "file": ("fictional.pdf", self.blank_pdf(), "application/pdf")
            })
        self.assertEqual(result.status_code, 422, result.text)
        save.assert_not_called()

    def test_searchable_fictional_pdf_still_returns_a_report(self):
        with patch("app.main.save_analysis", return_value=123) as save:
            result = self.client.post("/analyze", files={
                "file": ("fictional-searchable.pdf", make_demo_pdf(), "application/pdf")
            })
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["id"], 123)
        save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
