import hashlib
import io
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from pypdf import PdfReader

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


@app.post("/analyze", response_model=Report)
async def analyze(file: UploadFile = File(...)):
    data = await file.read()
    reader = PdfReader(io.BytesIO(data))

    text = ""
    for page in reader.pages:
        text += (page.extract_text() or "") + "\n"

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

    new_id = save_analysis(report)

    # Response al cliente (no hace falta mandar extracted_text entero)
    report_out = dict(report)
    report_out.pop("extracted_text", None)
    report_out["id"] = new_id

    return report_out


@app.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: int):
    row = get_analysis_by_id(analysis_id)
    if not row:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return row


@app.get("/analyses/{analysis_id}/export")
def export_analysis(analysis_id: int):
    row = get_analysis_export(analysis_id)
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
):
    return list_analyses(
        limit=limit,
        offset=offset,
        filename=filename,
        min_score=min_score,
        status=status,
        document_type=document_type,
    )
