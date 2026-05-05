import re
from typing import List, Tuple
from app.schemas.report import Finding


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

    if not re.search(r"\b(\d{8}[A-Z]|[XYZ]\d{7}[A-Z])\b", normalized):
        findings.append(Finding(
            code="MISSING_ID",
            severity="warning",
            message="No se detecta un DNI o NIE en el documento.",
            hint="Revisa que el documento incluya identificación."
        ))

    if not re.search(r"\b[\w\.-]+@[\w\.-]+\.\w{2,}\b", normalized):
        findings.append(Finding(
            code="MISSING_EMAIL",
            severity="warning",
            message="No se detecta un email en el documento.",
            hint="Añade un correo electrónico de contacto."
        ))

    if not re.search(r"\b(\+34)?[6-9]\d{8}\b", normalized.replace(" ", "")):
        findings.append(Finding(
            code="MISSING_PHONE",
            severity="info",
            message="No se detecta un número de teléfono.",
            hint="Si no es obligatorio, puedes ignorar este aviso."
        ))

    if not re.search(r"\bfirma\b|\bfirmado\b|\bfirmada\b", normalized, re.IGNORECASE):
        findings.append(Finding(
            code="MISSING_SIGNATURE",
            severity="info",
            message="No se detectan indicios de firma.",
            hint="Si es una solicitud o contrato, revisa si falta firmar."
        ))

    return findings


def _has_date(normalized: str) -> bool:
    # Fechas típicas: 28/01/2026, 28-01-2026, 28.01.2026, 2026-01-28
    return bool(re.search(r"\b(\d{1,2}[\/\-.]\d{1,2}[\/\-.]\d{2,4}|\d{4}[\/\-.]\d{1,2}[\/\-.]\d{1,2})\b", normalized))


def validate_subsidy_application(text: str) -> Tuple[List[Finding], int, str, str]:
    """
    Devuelve:
      findings, score(0-100), status(OK/REVISAR/RIESGO), summary
    """
    normalized = " ".join(text.split())
    findings = validate_basic(text)

    # ---- Checks específicos de ayudas/subvenciones ----

    # A) Fecha
    if not _has_date(normalized):
        findings.append(Finding(
            code="MISSING_DATE",
            severity="warning",
            message="No se detecta una fecha en la solicitud.",
            hint="Incluye la fecha (dd/mm/aaaa) si el documento es una solicitud."
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

    # E) Firma: aquí la tratamos como más seria para solicitud
    # Si en basic era info, la reforzamos (pero sin duplicar)
    has_signature = re.search(r"\bfirma\b|\bfirmado\b|\bfirmada\b", normalized, re.IGNORECASE)
    if not has_signature:
        # ya añade basic MISSING_SIGNATURE (info). Aquí añadimos “crítico” para ayudas.
        findings.append(Finding(
            code="SIGNATURE_REQUIRED",
            severity="warning",
            message="En solicitudes suele ser obligatoria la firma. No se detecta.",
            hint="Revisa que el documento esté firmado o tenga sección de firma."
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
        summary = "La solicitud tiene carencias importantes y podría ser rechazada por errores o faltas de datos."
    elif warnings >= 1 or infos >= 3 or score < 75:
        status = "REVISAR"
        summary = "La solicitud parece válida, pero conviene revisar algunos puntos para reducir riesgos."
    else:
        status = "OK"
        summary = "La solicitud parece correcta según las validaciones básicas."

    return findings, score, status, summary
