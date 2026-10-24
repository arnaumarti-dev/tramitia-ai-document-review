import hashlib
import io
import re
from typing import Optional, Literal

from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pydantic import BaseModel, Field

from app.auth import Principal, authenticate, current_principal, issue_token, hash_password, TOKEN_MINUTES
from app.staff import (StaffForbidden, StaffConflict, StaffNotFound,
                       list_staff as staff_list, create_staff as staff_create,
                       disable_staff as staff_disable)

from app.schemas.report import Report
from app.core.validators import validate_subsidy_application
from app.repository import (
    save_analysis,
    list_analyses,
    get_analysis_by_id,
    get_analysis_export,
)

app = FastAPI(title="TramitIA MVP")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = PROJECT_ROOT / "frontend"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")


@app.get("/", include_in_schema=False)
def serve_frontend():
    return FileResponse(FRONTEND_DIR / "index.html")

# CORS para frontend local (python http.server, live server, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8080",
        "http://localhost:8080",
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)
    organization: str = Field(min_length=1, max_length=80)


@app.post("/auth/login")
def login(request: LoginRequest):
    principal = authenticate(request.email, request.password, request.organization)
    return {
        "access_token": issue_token(principal),
        "token_type": "bearer",
        "expires_in": TOKEN_MINUTES * 60,
        "role": principal.role,
    }


@app.get("/auth/me")
def whoami(principal: Principal = Depends(current_principal)):
    return {"user_id": principal.user_id, "organization_id": principal.organization_id,
            "role": principal.role}


def require_staff_manager(principal: Principal = Depends(current_principal)) -> Principal:
    if principal.role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="No tienes permisos de administración.")
    return principal


class CreateStaffRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    role: Literal["employee", "admin"] = "employee"
    password: str = Field(min_length=12, max_length=128)


def _staff_error(exc: Exception) -> HTTPException:
    if isinstance(exc, StaffForbidden):
        return HTTPException(status_code=403, detail="No tienes permisos para esta operación.")
    if isinstance(exc, StaffNotFound):
        return HTTPException(status_code=404, detail="Empleado no encontrado.")
    return HTTPException(status_code=409, detail="No se puede crear la cuenta indicada.")


@app.get("/staff")
def get_staff(principal: Principal = Depends(require_staff_manager)):
    try:
        return {"items": staff_list(principal.organization_id, principal.user_id)}
    except StaffForbidden as exc:
        raise _staff_error(exc) from exc


@app.post("/staff", status_code=201)
def add_staff_member(request: CreateStaffRequest,
                     principal: Principal = Depends(require_staff_manager)):
    email = request.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=422, detail="Correo electrónico no válido.")
    if request.role == "admin" and principal.role != "owner":
        raise HTTPException(status_code=403, detail="Solo el propietario puede crear administradores.")
    try:
        # The service rechecks the actor's role inside the transaction.
        return staff_create(
            principal.organization_id, principal.user_id,
            email, request.role, hash_password(request.password),
        )
    except (StaffForbidden, StaffConflict) as exc:
        raise _staff_error(exc) from exc


@app.post("/staff/{user_id}/disable")
def disable_staff_member(user_id: int,
                         principal: Principal = Depends(require_staff_manager)):
    if user_id <= 0:
        raise HTTPException(status_code=404, detail="Empleado no encontrado.")
    try:
        staff_disable(principal.organization_id, principal.user_id, user_id)
    except (StaffForbidden, StaffNotFound) as exc:
        raise _staff_error(exc) from exc
    return {"status": "disabled"}


@app.post("/analyze", response_model=Report)
def analyze(file: UploadFile = File(...), principal: Principal = Depends(current_principal)):
    # FastAPI executes this blocking parser/database work in its worker pool.
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="El PDF supera el límite de 20 MB.")
    if not data:
        raise HTTPException(status_code=400, detail="El archivo está vacío.")

    # Solo los errores de lectura del documento son errores del cliente.
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise HTTPException(
                status_code=400,
                detail="El PDF está protegido. Sube una copia sin contraseña.",
            )
        text = ""
        for page in reader.pages:
            text += (page.extract_text() or "") + "\n"
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail="No se puede leer el PDF. Comprueba que sea válido y no esté dañado.",
        ) from exc

    # hash del texto extraído (auditoría)
    text_sha256 = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()

    findings, score, status, summary = validate_subsidy_application(text)

    report = {
        "filename": file.filename,
        "pages": len(reader.pages),
        "chars": len(text),
        "document_type": "subsidy_application",
        "score": score,
        "status": status,
        "summary": summary,
        "findings": [f.model_dump() for f in findings],
        "extracted_text": text,         # (si quieres, luego lo podemos recortar)
        "text_sha256": text_sha256,
    }

    new_id = save_analysis(report, principal.organization_id)

    # Response al cliente (no hace falta mandar extracted_text entero)
    report_out = dict(report)
    report_out.pop("extracted_text", None)
    report_out["id"] = new_id

    return report_out


@app.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: int, principal: Principal = Depends(current_principal)):
    row = get_analysis_by_id(analysis_id, principal.organization_id)
    if not row:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return row


@app.get("/analyses/{analysis_id}/export")
def export_analysis(analysis_id: int, principal: Principal = Depends(current_principal)):
    row = get_analysis_export(analysis_id, principal.organization_id)
    if not row:
        raise HTTPException(status_code=404, detail="Analysis not found")

    return JSONResponse(
        content=jsonable_encoder(row),
        headers={
            "Content-Disposition": f'attachment; filename="analysis_{analysis_id}.json"'
        },
    )


@app.get("/analyses")
def get_analyses(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),

    filename: Optional[str] = Query(None, description="Filtra por nombre de archivo (contiene)"),
    min_score: Optional[int] = Query(None, ge=0, le=100, description="Score mínimo (0-100)"),
    status: Optional[str] = Query(None, description="OK / REVISAR / RIESGO"),
    document_type: Optional[str] = Query(None, description="Ej: subsidy_application"),
    principal: Principal = Depends(current_principal),
):
    return list_analyses(
        organization_id=principal.organization_id,
        limit=limit,
        offset=offset,
        filename=filename,
        min_score=min_score,
        status=status,
        document_type=document_type,
    )
