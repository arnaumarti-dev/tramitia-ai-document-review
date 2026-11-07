"""Integrity checks for synthetic corpus and benchmark confusion counts."""
import json
import unittest
from types import SimpleNamespace

from scripts.evaluate_validation_corpus import (
    SUPPORTED_CODES, load_corpus, evaluate, markdown_report,
)


class ValidationBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = load_corpus()
        cls.result = evaluate(cls.corpus)

    def test_manual_annotations_include_positives_and_negatives(self):
        self.assertGreaterEqual(self.result["case_count"], 20)
        self.assertEqual(set(self.result["tracked_codes"]), SUPPORTED_CODES)
        for code, row in self.result["per_code"].items():
            with self.subTest(code=code):
                self.assertGreaterEqual(row["support_positive"], 2)
                self.assertGreaterEqual(row["support_negative"], 2)
                self.assertEqual(
                    row["tp"] + row["fp"] + row["fn"] + row["tn"],
                    self.result["case_count"],
                )

    def test_no_heuristic_output_can_be_legal_ok(self):
        self.assertEqual(self.result["unsafe_status_case_ids"], [])
        self.assertNotIn("OK", self.result["status_counts"])

    def test_report_is_serializable_and_does_not_claim_real_accuracy(self):
        self.assertEqual(self.result["scope"], "synthetic_textual_signal_detection_only")
        self.assertIn("real_world_accuracy", self.result["not_valid_for"])
        self.assertIn("NO es precisión real", markdown_report(self.result))
        self.assertIn("F1", markdown_report(self.result))
        json.dumps(self.result, allow_nan=False)
        for row in self.result["per_code"].values():
            for metric in ("precision", "recall", "f1", "false_positive_rate", "false_negative_rate"):
                value = row[metric]
                self.assertTrue(value is None or 0 <= value <= 1)

    def test_evaluator_counts_all_four_confusion_outcomes(self):
        corpus = {
            "tracked_codes": ["INVALID_DATE"],
            "cases": [
                {"id": "a", "text": "one", "expected_alerts": ["INVALID_DATE"]},
                {"id": "b", "text": "two", "expected_alerts": ["INVALID_DATE"]},
                {"id": "c", "text": "three", "expected_alerts": []},
                {"id": "d", "text": "four", "expected_alerts": []},
            ],
        }
        actual = {"one": True, "two": False, "three": True, "four": False}
        def fake_validator(value):
            findings = [SimpleNamespace(code="INVALID_DATE")] if actual[value] else []
            return findings, 50, "REVISAR", "Only fictional text."
        result = evaluate(corpus, validate=fake_validator)
        row = result["per_code"]["INVALID_DATE"]
        self.assertEqual((row["tp"], row["fp"], row["fn"], row["tn"]), (1, 1, 1, 1))
        self.assertEqual(row["false_positive_case_ids"], ["c"])
        self.assertEqual(row["false_negative_case_ids"], ["b"])
        self.assertEqual(row["precision"], 0.5)
        self.assertEqual(row["recall"], 0.5)
        self.assertEqual(row["f1"], 0.5)

    def test_difficult_signature_cases_are_kept_even_if_rule_improves(self):
        annotated_ids = {case["id"] for case in self.corpus["cases"]}
        difficult = {"signature_no_consta", "signature_no_se_ha_firmado", "signature_sin_datos"}
        self.assertTrue(difficult.issubset(annotated_ids))
        for code, row in self.result["per_code"].items():
            with self.subTest(code=code):
                self.assertTrue(set(row["false_positive_case_ids"]).issubset(annotated_ids))
                self.assertTrue(set(row["false_negative_case_ids"]).issubset(annotated_ids))


if __name__ == "__main__":
    unittest.main()
