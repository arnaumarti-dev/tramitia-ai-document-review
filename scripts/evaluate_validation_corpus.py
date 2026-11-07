"""Synthetic signal classification benchmark; NOT a real-world accuracy test."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Callable

from app.core.validators import validate_subsidy_application

CORPUS_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "validation_signals_v1.json"
SUPPORTED_CODES = {
    "INVALID_ID_CONTROL", "MISSING_ID", "INVALID_DATE",
    "MISSING_DATE", "SIGNATURE_REQUIRED", "SIGNATURE_UNVERIFIED",
}


def load_corpus(path: Path = CORPUS_PATH) -> dict:
    corpus = json.loads(path.read_text(encoding="utf-8"))
    if corpus.get("schema_version") != 1:
        raise ValueError("Unsupported corpus version")
    codes = corpus.get("tracked_codes")
    cases = corpus.get("cases")
    if not isinstance(codes, list) or set(codes) != SUPPORTED_CODES or len(codes) != len(SUPPORTED_CODES):
        raise ValueError("Corpus must declare each tracked code exactly once")
    if not isinstance(cases, list) or len(cases) < 20:
        raise ValueError("At least 20 manually annotated synthetic cases required")
    ids = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"]:
            raise ValueError("Every case needs a stable ID")
        if case["id"] in ids:
            raise ValueError("Duplicate corpus case ID")
        ids.add(case["id"])
        if not isinstance(case.get("text"), str) or len(case["text"].strip()) < 50:
            raise ValueError("Each test case needs substantive synthetic text")
        alerts = case.get("expected_alerts")
        if (not isinstance(alerts, list) or len(set(alerts)) != len(alerts)
                or not set(alerts).issubset(SUPPORTED_CODES)):
            raise ValueError("Invalid independent annotation")
    for code in codes:
        positives = sum(code in case["expected_alerts"] for case in cases)
        if positives < 2 or len(cases) - positives < 2:
            raise ValueError(f"Insufficient positive/negative cases for {code}")
    return corpus


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def evaluate(corpus: dict, validate: Callable = validate_subsidy_application) -> dict:
    codes = corpus["tracked_codes"]
    per_code = {
        code: {
            "tp": 0, "fp": 0, "fn": 0, "tn": 0,
            "false_positive_case_ids": [], "false_negative_case_ids": [],
        } for code in codes
    }
    status_counts: Counter = Counter()
    unsafe_status_case_ids = []
    for case in corpus["cases"]:
        findings, _score, status, _summary = validate(case["text"])
        actual = {finding.code for finding in findings}
        expected = set(case["expected_alerts"])
        status_counts[status] += 1
        if status == "OK":
            unsafe_status_case_ids.append(case["id"])
        for code in codes:
            wanted, predicted = code in expected, code in actual
            row = per_code[code]
            if wanted and predicted:
                row["tp"] += 1
            elif not wanted and predicted:
                row["fp"] += 1
                row["false_positive_case_ids"].append(case["id"])
            elif wanted and not predicted:
                row["fn"] += 1
                row["false_negative_case_ids"].append(case["id"])
            else:
                row["tn"] += 1

    for row in per_code.values():
        tp, fp, fn, tn = (row[key] for key in ("tp", "fp", "fn", "tn"))
        row["precision"] = _ratio(tp, tp + fp)
        row["recall"] = _ratio(tp, tp + fn)
        row["f1"] = _ratio(2 * tp, 2 * tp + fp + fn)
        row["false_positive_rate"] = _ratio(fp, fp + tn)
        row["false_negative_rate"] = _ratio(fn, fn + tp)
        row["support_positive"] = tp + fn
        row["support_negative"] = fp + tn
    totals = {key: sum(row[key] for row in per_code.values()) for key in ("tp", "fp", "fn", "tn")}
    return {
        "scope": "synthetic_textual_signal_detection_only",
        "not_valid_for": [
            "real_world_accuracy", "legal_validity", "signature_authenticity",
            "identity_verification", "PDF_OCR_performance",
        ],
        "case_count": len(corpus["cases"]),
        "tracked_codes": list(codes),
        "per_code": per_code,
        "micro": {
            **totals,
            "precision": _ratio(totals["tp"], totals["tp"] + totals["fp"]),
            "recall": _ratio(totals["tp"], totals["tp"] + totals["fn"]),
            "f1": _ratio(2 * totals["tp"], 2 * totals["tp"] + totals["fp"] + totals["fn"]),
        },
        "status_counts": dict(sorted(status_counts.items())),
        "unsafe_status_case_ids": unsafe_status_case_ids,
    }


def markdown_report(result: dict) -> str:
    lines = [
        "## TramitIA — benchmark de señales textuales sintéticas",
        "",
        "**NO es precisión real, probabilidad de aprobación, validación jurídica, "
        "identidad ni firma digital.** Casos de prueba no representativos.",
        "",
        f"Casos: **{result['case_count']}**. Códigos: **{len(result['tracked_codes'])}**.",
        "",
        "| Señal | TP | FP | FN | TN | Precisión | Sensibilidad | F1 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    def fmt(value: float | None) -> str:
        return "N/D" if value is None else f"{value:.1%}"
    for code, row in result["per_code"].items():
        lines.append(
            f"| {code} | {row['tp']} | {row['fp']} | {row['fn']} | {row['tn']} | "
            f"{fmt(row['precision'])} | {fmt(row['recall'])} | {fmt(row['f1'])} |"
        )
    lines.extend(["", "### Casos con errores observados", ""])
    for code, row in result["per_code"].items():
        if row["fp"] or row["fn"]:
            lines.append(
                f"- **{code}**: FP={', '.join(row['false_positive_case_ids']) or 'ninguno'}; "
                f"FN={', '.join(row['false_negative_case_ids']) or 'ninguno'}"
            )
    lines.extend([
        "",
        f"Estados: {json.dumps(result['status_counts'], ensure_ascii=False)}",
        f"Estados OK inseguros: {len(result['unsafe_status_case_ids'])}.",
        "",
        "**Uso:** localizar límites de reglas. No anunciar métricas como precisión comercial.",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=CORPUS_PATH)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args()
    result = evaluate(load_corpus(args.corpus))
    print(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if args.format == "json" else markdown_report(result)
    )
    # Only mandatory safety gate: text-only rules must never certify OK.
    # FP/FN are intentionally visible, not silently converted into a pass.
    return 2 if result["unsafe_status_case_ids"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
