"""Regression tests for explicit textual claims that a signature is absent.

Text-only detections cannot establish whether a PDF contains a valid signature.
"""
import unittest

from app.core.validators import validate_subsidy_application
from scripts.evaluate_validation_corpus import evaluate, load_corpus


def fictional_text(signature_phrase: str) -> str:
    return (
        "Solicitud de subvención ficticia para probar alertas textuales, "
        "sin documentos de clientes. Declaro datos inventados: "
        "DNI 00000000T, fecha 07/11/2026, "
        "correo demo@example.invalid, Calle Inventada 1. "
        + signature_phrase
    )


class SignatureNegationRegressionTests(unittest.TestCase):
    def test_explicit_absence_variants_require_review(self):
        for phrase in (
            "No consta la firma.",
            "No consta firma.",
            "No se ha firmado.",
            "Sin datos de firma.",
            "No consta firma, aunque se menciona la palabra firma.",
        ):
            with self.subTest(phrase=phrase):
                findings, _score, status, summary = validate_subsidy_application(
                    fictional_text(phrase)
                )
                codes = {finding.code for finding in findings}
                self.assertIn("SIGNATURE_REQUIRED", codes)
                self.assertNotIn("SIGNATURE_UNVERIFIED", codes)
                self.assertNotEqual(status, "OK")
                self.assertIn("validez legal", summary)

    def test_plain_mentions_are_not_misread_as_explicit_absence(self):
        for phrase in (
            "El formulario figura firmado.",
            "La firma es opcional.",
            "Firma de prueba ficticia.",
        ):
            with self.subTest(phrase=phrase):
                findings, _score, status, _summary = validate_subsidy_application(
                    fictional_text(phrase)
                )
                codes = {finding.code for finding in findings}
                self.assertNotIn("SIGNATURE_REQUIRED", codes)
                self.assertIn("SIGNATURE_UNVERIFIED", codes)
                self.assertNotEqual(status, "OK")

    def test_known_synthetic_false_negatives_are_resolved(self):
        result = evaluate(load_corpus())
        missed = set(
            result["per_code"]["SIGNATURE_REQUIRED"]["false_negative_case_ids"]
        )
        for case_id in (
            "signature_no_consta",
            "signature_no_se_ha_firmado",
            "signature_sin_datos",
            "combined_bad_id_bad_date_unsigned",
        ):
            with self.subTest(case_id=case_id):
                self.assertNotIn(case_id, missed)
        self.assertEqual(result["unsafe_status_case_ids"], [])


if __name__ == "__main__":
    unittest.main()
