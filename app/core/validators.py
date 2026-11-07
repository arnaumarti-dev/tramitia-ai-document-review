import re
from datetime import date
from typing import List, Tuple
from app.schemas.report import Finding


# These are *textual indicators*, not proof of identity, authorship or
# acceptance of an administrative application.
_ID_CANDIDATES = re.compile(r"\b(?:\d{8}|[XYZ]\d{7})[A-Z]\b", re.IGNORECASE)
_ID_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
_DATE_DMY = re.compile(r"(?<!\d)(\d{1,2})([./-])(\d{1,2})\2(\d{4})(?!\d)")
_DATE_YMD = re.compile(r"(?<!\d)(\d{4})([./-])(\d{1,2})\2(\d{1,2})(?!\d)")
_SIGNATURE_WORDS = re.compile(r"\b(?:firma|firmad[oa])\b", re.IGNORECASE)
_EXPLICITLY_UNSIGNED = re.compile(
    r"\b(?:sin\s+firma|falta\s+(?:la\s+)?firma|firma\s+(?:pendiente|ausente)"
    r"|pendiente\s+de\s+firma|no\s+(?:est[aá]\s+)?firmad[oa]"
    r"|no\s+consta\s+(?:la\s+)?firma|no\s+se\s+ha\s+firmad[oa]"
    r"|sin\s+datos\s+de\s+firma)\b",
    re.IGNORECASE,
)


def _valid_nif_nie_control_letter(candidate: str) -> bool:
    """Check Spain's DNI/NIE letter; never claim holder identity is verified."""
    value = candidate.upper()
    numeric = (str("XYZ".index(value[0])) + value[1:8]
               if value[0] in "XYZ" else value[:8])
    return _ID_LETTERS[int(numeric) % 23] == value[-1]


def _date_signals(text: str) -> tuple[bool, bool]:
    """Return (calendar-valid date found, impossible date found).

    Only unambiguous four-digit years are accepted. This checks calendar
    possibility, not that a document was actually signed/submitted that day.
    """
    has_valid = False
    has_impossible = False
    for regex, iso_first in ((_DATE_DMY, False), (_DATE_YMD, True)):
        for match in regex.finditer(text):
            if iso_first:
                year, month, day = map(int, (match[1], match[3], match[4]))
            else:
                day, month, year = map(int, (match[1], match[3], match[4]))
            try:
                date(year, month, day)
                has_valid = True
            except ValueError:
                has_impossible = True
    return has_valid, has_impossible



def validate_basic(text: str) -> List[Finding]:
    findings: List[Finding] = []
    normalized = " ".join(text.split())

    if len(normalized) < 50:
        findings.append(Finding(
            code="DOC_TOO_SHORT",
            severity="error",
            message="El documento tiene muy poco texto o no se ha podido extraer.",
            hint="Si es un PDF escaneado, habrá que usar OCR."
        ))
        return findings

    candidates = _ID_CANDIDATES.findall(normalized)
    if not candidates:
        findings.append(Finding(
            code="MISSING_ID",
            severity="warning",
            message="No se detecta un posible DNI o NIE en el texto.",
            hint="Comprueba la identificación manualmente si el trámite la exige."
        ))
    elif any(not _valid_nif_nie_control_letter(item) for item in candidates):
        findings.append(Finding(
            code="INVALID_ID_CONTROL",
            severity="warning",
            message="Algún posible DNI/NIE presenta una letra de control incorrecta.",
            hint="Revisa el dato con el documento original. Una letra correcta no acredita identidad."
        ))

    if not re.search(r"\b[\w\.-]+@[\w\.-]+\.\w{2,}\b", normalized):
        findings.append(Finding(
            code="MISSING_EMAIL",
            severity="warning",
            message="No se detecta un email en el documento.",
            hint="Añade un correo electrónico de contacto."
        ))

    # Conserva los límites entre palabras y admite separadores del teléfono.
    if not re.search(r"(?<!\w)(?:\+34[ -]*)?[6-9](?:[ -]*[0-9]){8}(?!\w)", normalized):
        findings.append(Finding(
            code="MISSING_PHONE",
            severity="info",
            message="No se detecta un número de teléfono.",
            hint="Si no es obligatorio, puedes ignorar este aviso."
        ))

    if not _SIGNATURE_WORDS.search(normalized):
        findings.append(Finding(
            code="MISSING_SIGNATURE",
            severity="info",
            message="No se menciona una firma en el texto extraído.",
            hint="Una firma visible o electrónica no puede verificarse con una búsqueda de palabras."
        ))

    return findings


def _has_date(normalized: str) -> bool:
    """Calendar-valid, four-digit-year date indication (not date authenticity)."""
    return _date_signals(normalized)[0]


def validate_subsidy_application(text: str) -> Tuple[List[Finding], int, str, str]:
    """
    Devuelve:
      findings, score(0-100), status(OK/REVISAR/RIESGO), summary
    """
    normalized = " ".join(text.split())
    findings = validate_basic(text)

    # ---- Checks específicos de ayudas/subvenciones ----

    # A) Fecha: detecting a pattern is not validating a real calendar date.
    has_valid_date, has_impossible_date = _date_signals(normalized)
    if has_impossible_date:
        findings.append(Finding(
            code="INVALID_DATE",
            severity="warning",
            message="Se detecta al menos una fecha de calendario imposible.",
            hint="Comprueba las fechas con el documento original (por ejemplo, 31/02/2026 no existe)."
        ))
    if not has_valid_date and not has_impossible_date:
        findings.append(Finding(
            code="MISSING_DATE",
            severity="warning",
            message="No se identifica una fecha completa con año de cuatro cifras.",
            hint="Comprueba manualmente la fecha exigida; una coincidencia no verifica autenticidad."
        ))

    # B) Dirección (muy heurístico)
    if not re.search(r"\b(calle|avda\.?|avenida|plaza|paseo|c\/|cp|c\.p\.|código postal)\b", normalized, re.IGNORECASE):
        findings.append(Finding(
            code="MISSING_ADDRESS",
            severity="info",
            message="No se detecta claramente una dirección/código postal.",
            hint="En solicitudes suele pedirse domicilio y CP."
        ))

    # C) “Solicito / Solicitud”
    if not re.search(r"\bsolicit[oa]\b|\bsolicitud\b", normalized, re.IGNORECASE):
        findings.append(Finding(
            code="MISSING_REQUEST_WORDING",
            severity="info",
            message="No se detecta lenguaje típico de solicitud (p.ej., 'solicito', 'solicitud').",
            hint="Si es un escrito de solicitud, revisa que quede claro qué pides."
        ))

    # D) “Declaro / Declaración responsable”
    if not re.search(r"\bdeclaro\b|\bdeclaración responsable\b|\bmanifiesto\b", normalized, re.IGNORECASE):
        findings.append(Finding(
            code="MISSING_DECLARATION",
            severity="info",
            message="No se detecta una declaración responsable ('declaro', 'manifiesto', etc.).",
            hint="Muchas ayudas exigen una declaración responsable en el texto."
        ))

    # E) Firma: a word such as "firmado" is NOT a signature verification.
    has_signature_word = bool(_SIGNATURE_WORDS.search(normalized))
    if not has_signature_word or _EXPLICITLY_UNSIGNED.search(normalized):
        findings.append(Finding(
            code="SIGNATURE_REQUIRED",
            severity="warning",
            message="No hay evidencia textual suficiente de una firma; puede indicarse que falta.",
            hint="Verifica la firma en el PDF original mediante una herramienta de firma digital, si procede."
        ))
    else:
        findings.append(Finding(
            code="SIGNATURE_UNVERIFIED",
            severity="info",
            message="El texto menciona una firma, pero no se ha verificado su existencia ni validez.",
            hint="Solo se han analizado palabras; una firma electrónica requiere validación específica."
        ))

    # ---- Score & status ----
    errors = sum(1 for f in findings if f.severity == "error")
    warnings = sum(1 for f in findings if f.severity == "warning")
    infos = sum(1 for f in findings if f.severity == "info")

    # Penalización simple (ajustable)
    score = 100
    score -= errors * 35
    score -= warnings * 15
    score -= infos * 5
    score = max(0, min(100, score))

    if errors >= 1 or warnings >= 3 or score < 55:
        status = "RIESGO"
        summary = (
            "Se han detectado varias alertas textuales que requieren revisión prioritaria. "
            "No se ha comprobado la validez legal ni la existencia de una firma."
        )
    else:
        # A text-only rule engine can never certify a document as correct.
        # Preserve the old schema/status choices, but do not emit a false OK.
        status = "REVISAR"
        summary = (
            "Resultado orientativo de reglas textuales. No verifica identidad, "
            "firma electrónica, requisitos de la convocatoria ni validez legal. "
            "Se necesita revisión humana."
        )

    return findings, score, status, summary
