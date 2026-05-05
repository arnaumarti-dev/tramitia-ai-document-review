# TramitIA

TramitIA is a **local MVP** for reviewing administrative PDF documents, focused on subsidy application use cases. It allows users to upload a PDF, extracts text, applies predefined validation rules, generates a risk score, stores results in PostgreSQL, and lets users review historical analyses and export reports as JSON.

> This project is intended as a learning/portfolio MVP and is **not** a production-ready legal or administrative system.

## Features

- Upload a PDF document from a web dashboard.
- Extract text from PDFs with `pypdf`.
- Run rule-based validations for subsidy application documents.
- Generate a risk score (`0-100`) and status (`OK`, `REVISAR`, `RIESGO`).
- Store analysis metadata and findings in PostgreSQL.
- List and filter past analyses (filename, minimum score, status, document type).
- View analysis details and download a full JSON export per analysis.

## Tech Stack

- **Backend:** FastAPI, Uvicorn
- **Database:** PostgreSQL 16
- **PDF processing:** pypdf
- **Data validation/models:** Pydantic
- **Frontend:** HTML, CSS, Vanilla JavaScript
- **Containerization:** Docker Compose (PostgreSQL service)

## Architecture

High-level flow:

1. User uploads a PDF from the frontend dashboard.
2. FastAPI backend reads the file and extracts text with `pypdf`.
3. Validation rules are applied to the extracted text (IDs, email, phone, signature/date/address/request/declaration signals, etc.).
4. The app calculates a score and risk status.
5. Analysis is persisted in PostgreSQL (`analyses` table).
6. Frontend can fetch:
   - current analysis result,
   - paginated history,
   - detail by ID,
   - full JSON export for download.

## Project Structure

```text
tramitia-ai-document-review/
├── app/
│   ├── core/
│   │   └── validators.py        # Rule-based validation + scoring
│   ├── schemas/
│   │   └── report.py            # Pydantic models for API responses
│   ├── db.py                    # PostgreSQL connection helper
│   ├── main.py                  # FastAPI app and routes
│   └── repository.py            # DB queries and persistence
├── frontend/
│   ├── index.html               # Dashboard UI
│   ├── app.js                   # Frontend logic and API calls
│   └── styles.css               # UI styling
├── docker-compose.yml           # PostgreSQL service for local dev
└── requirements.txt             # Python dependencies
```

## Local Setup (Backend + Frontend)

### 1) Prerequisites

- Python 3.10+
- PostgreSQL 16 (or use Docker Compose section below)
- `pip`

### 2) Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3) Configure environment variables

The backend reads these variables (with defaults shown):

- `POSTGRES_HOST` (default: `localhost`)
- `POSTGRES_PORT` (default: `5432`)
- `POSTGRES_DB` (default: `tramitia`)
- `POSTGRES_USER` (default: `tramitia`)
- `POSTGRES_PASSWORD` (default: `tramitia`)

Example:

```bash
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
export POSTGRES_DB=tramitia
export POSTGRES_USER=tramitia
export POSTGRES_PASSWORD=tramitia
```

### 4) Create database table (manual step)

This MVP expects an `analyses` table. If it does not exist, create it manually in your local database before running tests.

### 5) Run the API

```bash
uvicorn app.main:app --reload
```

Backend default URL:
- `http://127.0.0.1:8000`

### 6) Open the dashboard

- Open `http://127.0.0.1:8000/` (served by FastAPI)
- Or access static assets under `/frontend`

## Docker / PostgreSQL Instructions

This repository includes a Docker Compose setup for PostgreSQL only.

### Start PostgreSQL

```bash
docker compose up -d
```

### Stop PostgreSQL

```bash
docker compose down
```

### Reset database volume (optional)

```bash
docker compose down -v
```

Default container/database settings from `docker-compose.yml`:
- Container: `tramitia-postgres`
- User: `tramitia`
- Password: `tramitia`
- Database: `tramitia`
- Port mapping: `5432:5432`

## API Endpoints

Base URL: `http://127.0.0.1:8000`

- `GET /health`
  - Health check.

- `POST /analyze`
  - Multipart upload (`file`) of a PDF.
  - Returns summarized analysis result.

- `GET /analyses`
  - Paginated list of analyses.
  - Query params:
    - `limit` (1-100, default `20`)
    - `offset` (default `0`)
    - `filename` (contains filter)
    - `min_score` (`0-100`)
    - `status` (`OK`, `REVISAR`, `RIESGO`)
    - `document_type` (e.g. `subsidy_application`)

- `GET /analyses/{analysis_id}`
  - Detailed analysis by ID (includes findings).

- `GET /analyses/{analysis_id}/export`
  - Full JSON export for download (includes extracted text and hash).

## Example Response (`POST /analyze`)

```json
{
  "id": 12,
  "filename": "solicitud_subvencion.pdf",
  "pages": 3,
  "chars": 2480,
  "document_type": "subsidy_application",
  "score": 70,
  "status": "REVISAR",
  "summary": "La solicitud parece válida, pero conviene revisar algunos puntos para reducir riesgos.",
  "findings": [
    {
      "code": "MISSING_SIGNATURE",
      "severity": "info",
      "message": "No se detectan indicios de firma.",
      "hint": "Si es una solicitud o contrato, revisa si falta firmar."
    },
    {
      "code": "SIGNATURE_REQUIRED",
      "severity": "warning",
      "message": "En solicitudes suele ser obligatoria la firma. No se detecta.",
      "hint": "Revisa que el documento esté firmado o tenga sección de firma."
    }
  ],
  "text_sha256": "a3d4f7..."
}
```

## Current Limitations

- Uses **heuristic, rule-based** checks (regex/text signals), not advanced legal NLP.
- No OCR pipeline for scanned/image-only PDFs.
- No authentication, authorization, or multi-tenant support.
- No background queue/job system for large batch workloads.
- No migration framework included in this repository.
- CORS and setup are tuned for local development.
- Validation logic is currently tailored to subsidy application patterns and Spanish-language wording.

## Roadmap

- Add OCR support for scanned PDFs.
- Add configurable rule sets by procedure/document type.
- Add migration tooling and seed scripts.
- Add automated tests (unit + integration).
- Add user authentication and role-based access.
- Improve explainability of scoring and findings.
- Add containerized backend service for one-command local startup.

## Disclaimer

TramitIA is an educational MVP that provides automated document checks for support workflows. It **does not** replace legal, administrative, or professional advice, and should not be used as the sole basis for legal or procedural decisions.

## Author

**Arnau Martí Ruiz**

Junior developer portfolio project built to explore PDF analysis workflows with FastAPI and PostgreSQL.
