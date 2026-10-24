import io
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.main import app
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

    def test_database_errors_remain_server_errors(self):
        with patch("app.main.save_analysis", side_effect=RuntimeError("database unavailable")):
            response = self.client.post(
                "/analyze", files={"file": ("document.pdf", make_pdf(), "application/pdf")}
            )
        self.assertEqual(response.status_code, 500)


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
