from pydantic import BaseModel
from typing import List, Literal, Optional
from datetime import datetime

Severity = Literal["info", "warning", "error"]
DocType = Literal["generic", "subsidy_application"]

class AnalysisItem(BaseModel):
    id: int
    created_at: datetime
    filename: str
    pages: int | None = None
    chars: int | None = None
    document_type: str | None = None
    score: int | None = None
    status: str | None = None
    summary: str | None = None

class Finding(BaseModel):
    code: str
    severity: Severity
    message: str
    hint: Optional[str] = None


class Report(BaseModel):
    id: Optional[int] = None
    filename: str
    pages: int
    chars: int
    document_type: DocType
    score: int
    status: Literal["OK", "REVISAR", "RIESGO"]
    summary: str
    findings: List[Finding]
    text_sha256: Optional[str] = None
