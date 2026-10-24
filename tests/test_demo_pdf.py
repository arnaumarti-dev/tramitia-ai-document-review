"""The local demo fixture must remain parseable and fictional."""
import io
import unittest

from pypdf import PdfReader

from scripts.generate_demo_pdf import make_demo_pdf


class DemoPdfTests(unittest.TestCase):
    def test_generated_document_is_searchable_and_fictional(self):
        pdf = make_demo_pdf()
        self.assertTrue(pdf.startswith(b"%PDF"))
        reader = PdfReader(io.BytesIO(pdf))
        self.assertEqual(len(reader.pages), 1)
        text = reader.pages[0].extract_text()
        self.assertIn("Solicitud ficticia", text)
        self.assertIn("demo@example.invalid", text)
        self.assertIn("Calle Inventada 1", text)


if __name__ == "__main__":
    unittest.main()
