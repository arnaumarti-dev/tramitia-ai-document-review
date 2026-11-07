"""Adversarial fictional rule-engine cases: no legal validity from regex alone."""
import unittest

from app.core.validators import (
    _has_date,
    _valid_nif_nie_control_letter,
    validate_basic,
    validate_subsidy_application,
)


def text_with(*signals: str) -> str:
    # Long, fully synthetic text; no customer or personal documents.
    return (
        "Solicitud de ayuda ficticia para comprobar reglas y posibles "
        "incidencias en la revisión de un documento de prueba. "
        "Declaro los siguientes datos: "
        + " ".join(signals)
    )


def codes(text: str):
    return {finding.code for finding in validate_subsidy_application(text)[0]}


class ValidatorConfidenceTests(unittest.TestCase):
    def test_dni_control_letter_is_checked_without_claiming_identity(self):
        self.assertTrue(_valid_nif_nie_control_letter("00000000T"))
        self.assertFalse(_valid_nif_nie_control_letter("00000000A"))
        self.assertNotIn("INVALID_ID_CONTROL", codes(text_with("DNI 00000000T")))
        self.assertIn("INVALID_ID_CONTROL", codes(text_with("DNI 00000000A")))
        self.assertIn("INVALID_ID_CONTROL", codes(
            text_with("DNI 00000000T y otro identificador 00000000A")
        ))

    def test_nie_control_letter_and_case(self):
        self.assertTrue(_valid_nif_nie_control_letter("x0000000t"))
        self.assertFalse(_valid_nif_nie_control_letter("X0000000Z"))
        self.assertNotIn("INVALID_ID_CONTROL", codes(text_with("NIE x0000000t")))
        self.assertIn("INVALID_ID_CONTROL", codes(text_with("NIE X0000000Z")))

    def test_missing_identifier_is_not_mistaken_for_invalid_identifier(self):
        result = {finding.code for finding in validate_basic(text_with("SIN DNI"))}
        self.assertIn("MISSING_ID", result)
        self.assertNotIn("INVALID_ID_CONTROL", result)

    def test_real_dates_and_leap_years(self):
        for value in ("29/02/2024", "2026-11-07", "28.01.2026"):
            with self.subTest(date=value):
                self.assertTrue(_has_date(value))
                self.assertNotIn("INVALID_DATE", codes(text_with(value)))
                self.assertNotIn("MISSING_DATE", codes(text_with(value)))

    def test_impossible_dates_are_not_counted_as_valid(self):
        for value in ("31/02/2026", "2026-13-01", "29/02/2025", "00/11/2026"):
            with self.subTest(date=value):
                self.assertFalse(_has_date(value))
                self.assertIn("INVALID_DATE", codes(text_with(value)))

    def test_short_two_digit_year_is_not_accepted_as_verified_date(self):
        self.assertFalse(_has_date("01/02/26"))
        self.assertIn("MISSING_DATE", codes(text_with("01/02/26")))

    def test_signature_mentions_are_not_verified_signatures(self):
        for phrase in ("Firma de prueba", "Está firmado", "firmada"):
            with self.subTest(phrase=phrase):
                findings, score, status, summary = validate_subsidy_application(
                    text_with("00000000T", "07/11/2026", phrase)
                )
                self.assertIn("SIGNATURE_UNVERIFIED", {item.code for item in findings})
                self.assertNotEqual(status, "OK")
                self.assertIn("revisión humana", summary if status == "REVISAR" else "revisión humana")

    def test_explicitly_missing_signature_is_not_taken_as_signed(self):
        for phrase in ("falta la firma", "sin firma", "pendiente de firma", "no está firmado"):
            with self.subTest(phrase=phrase):
                self.assertIn("SIGNATURE_REQUIRED", codes(
                    text_with("00000000T", "07/11/2026", phrase)
                ))
                self.assertNotIn("SIGNATURE_UNVERIFIED", codes(
                    text_with("00000000T", "07/11/2026", phrase)
                ))

    def test_text_only_document_never_claims_legal_validity(self):
        findings, score, status, summary = validate_subsidy_application(
            text_with("00000000T", "07/11/2026",
                      "correo ficticio@example.invalid",
                      "Calle Inventada 1", "612 345 678",
                      "Firmado. Solicito esta ayuda.")
        )
        self.assertIn(status, ("RIESGO", "REVISAR"))
        self.assertNotEqual(status, "OK")
        self.assertIn("validez legal", summary)
        self.assertTrue(0 <= score <= 100)
        self.assertNotIn("INVALID_ID_CONTROL", {x.code for x in findings})


if __name__ == "__main__":
    unittest.main()
