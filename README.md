# TramitIA

TramitIA is a **local MVP** for reviewing administrative PDF documents, focused on subsidy application use cases. It allows users to upload a PDF, extracts text, applies predefined validation rules, generates a risk score, stores results in PostgreSQL, and lets users review historical analyses and export reports as JSON.

> This project is intended as a learning/portfolio MVP and is **not** a production-ready legal or administrative system.

## Features

- Sign in to a specific organization and upload PDFs (up to 20 MiB, 50 pages, and 200,000 extracted text characters) from a web dashboard.
- Extract text from PDFs with `pypdf`.
- Run rule-based validations for subsidy application documents.
- Generate a risk score (`0-100`) and status (`OK`, `REVISAR`, `RIESGO`).
- Store organization-scoped metadata and findings in PostgreSQL (raw extracted text is not stored for new analyses).
- List and filter past analyses (filename, minimum score, status, document type).
- View and download only the analyses belonging to the authenticated organization.

## Tech Stack

- **Backend:** FastAPI, Uvicorn
- **Database:** PostgreSQL 16
- **PDF processing:** pypdf
- **Data validation/models:** Pydantic
- **Frontend:** HTML, CSS, Vanilla JavaScript
- **Containerization:** Docker Compose (PostgreSQL service)
- **Authentication:** short-lived signed JWT bearer token, Argon2id password hashes, current database membership checks
- **Migrations:** Alembic

## Architecture

High-level flow:

1. User uploads a PDF from the frontend dashboard.
2. FastAPI backend reads the file and extracts text with `pypdf`.
3. Validation rules are applied to the extracted text (IDs, email, phone, signature/date/address/request/declaration signals, etc.).
4. The app calculates a score and risk status.
5. Analysis is persisted in PostgreSQL with a verified organization ID (`analyses` table).
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

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

Linux/macOS:

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
- `POSTGRES_PASSWORD` (default: `tramitia`, **development only**)
- `TRAMITIA_JWT_SECRET` (**required**, at least 32 cryptographically random characters; no default)

Example:

```bash
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
export POSTGRES_DB=tramitia
export POSTGRES_USER=tramitia
export POSTGRES_PASSWORD=tramitia
export TRAMITIA_JWT_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
```

### 4) Migrate database and create the first owner

Start PostgreSQL with `docker compose up -d`, then run:

```bash
alembic upgrade head
python -m scripts.bootstrap_admin --slug mi-gestoria --name "Mi Gestoría" --email owner@example.com
```

The bootstrap command prompts for a password; never store it in the repository
or supply it in command arguments. Login via the dashboard with organization
`mi-gestoria`, the owner email, and your password. Each request verifies that
the user's membership in the organization is still active.

**Legacy rows:** the migration preserves analyses created before tenant support
with `organization_id = NULL`. They are inaccessible to every organization
until an administrator designs and audits a manual ownership reassignment.
Never bulk-assign historical rows to an arbitrary organization.

### 5) Run the API

```bash
uvicorn app.main:app --reload
```

Backend default URL:
- `http://127.0.0.1:8000`

### 6) Open the dashboard

The frontend dashboard is served directly by FastAPI:

```text
http://127.0.0.1:8000/
```

Swagger API docs:

```text
http://127.0.0.1:8000/docs
```

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

- `POST /auth/login`
  - JSON body: `{"organization":"mi-gestoria","email":"owner@example.com","password":"..."}`
  - Returns a short-lived bearer token (30 minutes). No public registration route.
- `GET /auth/me`
  - Requires `Authorization: Bearer <token>`. Returns user, organization and role.
- `POST /analyze`
  - Multipart upload (`file`) of a PDF.
  - Returns summarized analysis result.
  - HTTP 400: empty, unreadable or encrypted PDF; HTTP 413: file exceeds 20 MiB.
  - Parsing and database work run in a worker thread. The size cap bounds the parser input, not PDF decompression or the multipart upload itself.

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
  - Full JSON export for download (hash included; new analyses have no retained extracted text).

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

## Prueba completa de uso

Se ha añadido una [guía de prueba local en español](docs/PRUEBA_LOCAL_ES.md)
para recorrer el flujo propietario → invitación → empleado → análisis de
PDF ficticio → historial y exportación. Puedes crear el PDF de prueba sin
datos reales mediante:

```bash
python -m scripts.generate_demo_pdf --output solicitud-ficticia.pdf
```

GitHub Actions ejecuta también una prueba de extremo a extremo en **Chromium
real + FastAPI + PostgreSQL 16** (`tests/browser_journey.py`). El test
utiliza organizaciones inventadas y una base efímera, no cuentas reales.

## Tests

Run `python -m pip install -r requirements.txt httpx` and
`python -m unittest discover -s tests -v` for isolated unit tests.
For real PostgreSQL isolation coverage, set `TRAMITIA_TEST_POSTGRES=1`
with an **ephemeral, migrated test database**. With Node.js 22, run
`node --test tests/test_frontend.cjs`. GitHub Actions runs both test
suites, applies migrations, and verifies cross-organization denial against PostgreSQL.
Frontend tests use a simulated DOM rather than a browser.

## Current Limitations

- Uses **heuristic, rule-based** checks (regex/text signals), not advanced legal NLP.
- No OCR pipeline for scanned/image-only PDFs.
- Initial authentication and organization isolation exist; no self-service onboarding, password reset, IP-aware rate limiting, or hardened session management.
- No background queue/job system for large batch workloads.
- Alembic handles schema creation, but deployment migration/backup procedures still require operational review.
- CORS and setup are tuned for local development.
- Validation logic is currently tailored to subsidy application patterns and Spanish-language wording.

## Roadmap

- Add OCR support for scanned PDFs.
- Add configurable rule sets by procedure/document type.
- Extend bootstrap into secure organization/member administration and audited onboarding.
- Extend automated tests with live PostgreSQL integration coverage.
- Add granular role authorization, invitation flow, account recovery and session revocation.
- Improve explainability of scoring and findings.
- Add containerized backend service for one-command local startup.
- Add AI-based requirement extraction from official calls or instructions.
- Compare user documents against automatically extracted requirements.

## Invitaciones y cambio de contraseña (MVP)

El propietario puede gestionar el equipo desde la interfaz web: ver usuarios,
invitar empleados o administradores y desactivar accesos. Los administradores
pueden invitar y desactivar **solo empleados**. Los empleados normales no
pueden entrar al panel ni invitar a nadie.

1. El responsable rellena correo y rol. TramitIA genera un enlace de invitación,
   válido durante **24 horas** y de **un solo uso**, que se muestra una vez.
2. El responsable comunica el enlace por un canal privado. **No hay envío
   automático de correo ni verificación del control del buzón**: pendiente antes
   de producción. El destinatario abre el enlace y elige su propia contraseña
   de al menos 12 caracteres.
3. El servidor crea el usuario con contraseña Argon2 y lo asocia a **la misma
   gestoría del responsable**, nunca a una organización escogida por el cliente.
   El token solo se almacena como SHA-256; los enlaces caducados, revocados o
   ya usados se rechazan. Las invitaciones anteriores al mismo correo se
   revocan cuando se emite otra.
4. Cualquier usuario autenticado puede cambiar su propia contraseña
   desde **Seguridad de mi cuenta**. La API incrementa la versión de su
   sesión y **todos sus tokens anteriores dejan de funcionar**, incluso si
   su caducidad de 30 minutos no se ha cumplido. Debe iniciar sesión de nuevo.

Rutas:

- `GET /staff` – hasta 200 miembros de la gestoría autenticada.
- `POST /staff/invitations` – correo y rol; devuelve un token secreto solo
  una vez. Requiere propietario o administrador; solo propietario invita admins.
- `POST /auth/invitations/accept` – token y contraseña personal para activar
  la cuenta. Sin login previo.
- `POST /auth/password` – contraseña actual y nueva; requiere sesión vigente
  y revoca inmediatamente todas las sesiones del usuario tras el cambio.
- `POST /staff/{user_id}/disable` – desactiva empleados y administradores según
  rol; no se puede desactivar al propietario en este flujo.

**Importante:** los enlaces se envían manualmente. No los publiques en
GitHub, chats públicos, logs, screenshots o tickets. La CLI de gestión sigue
existiendo para soporte local, pero el panel web ya no pide a un gestor que
conozca o establezca las contraseñas de los empleados. Se necesitan
verificación de correo, invitaciones por proveedor seguro, recuperación de
cuenta, auditoría y revisión de seguridad antes del despliegue.

## Gestión de empleados (solo operador del servidor)

La CLI está reservada para tareas excepcionales del operador. Una
persona autorizada con acceso al servidor puede crear empleados desde la
terminal, después de ejecutar `alembic upgrade head`.

```bash
python -m scripts.manage_staff --slug mi-gestoria add --email empleado@example.com --role employee
python -m scripts.manage_staff --slug mi-gestoria add --email responsable@example.com --role admin
python -m scripts.manage_staff --slug mi-gestoria list
python -m scripts.manage_staff --slug mi-gestoria disable --email empleado@example.com
```

Las contraseñas se solicitan interactivamente y se guardan mediante un hash
Argon2. No introduzcas contraseñas en argumentos ni en commits. Solo se admiten
correos nuevos; añadir usuarios ya existentes a otras organizaciones necesitará
un futuro flujo explícito de invitación/consentimiento. Un empleado desactivado
pierde acceso incluso con un token JWT no caducado, porque su membresía se
comprueba en cada petición. No se permite desactivar al último propietario.

**Límite de intentos:** cinco contraseñas incorrectas en 15 minutos para la
misma combinación gestoría/correo bloquean el acceso durante 15 minutos. El
contador compartido se guarda en PostgreSQL mediante un HMAC del identificador,
no con los correos en texto plano. Sigue haciendo falta protección perimetral
por IP, supervisión, recuperación de cuentas y mitigación del bloqueo
malicioso de cuentas para un despliegue real.

## PDF resource guardrails (development)

The `/analyze` route rejects files exceeding **20 MiB**, with no `%PDF-`
header, without pages, with more than **50 pages**, or with more than
**200,000 extracted characters**. Requests exceeding the size, page, or
text limits receive HTTP 413; malformed documents receive HTTP 400.
These rejected documents are **not stored** in the analysis history.
Client-supplied filenames are reduced to a safe basename of at most
200 characters; MIME headers alone are not accepted as proof of a PDF.
Normal short PDFs still receive the same heuristic analysis.

**These checks do not bound the CPU or memory used by `pypdf` while it
opens a hostile PDF or extracts an individual page.** They are preliminary
input restrictions, not a security sandbox. Before any production rollout,
perform PDF analysis in an isolated, resource-capped worker with CPU,
memory, process time and decompression limits, plus malware policies.
The multipart request may also be buffered before the endpoint's 20 MiB
file check; enforce a **whole-request size limit** at the reverse proxy.
No real customer documents should be used until these protections and
privacy controls have been assessed.

## PDF analysis in an isolated subprocess (development)

`POST /analyze` does **not** import or run the third-party PDF parser in
the web-server process. It passes the verified PDF bytes over stdin to a
short-lived `python -I app/pdf_worker.py` subprocess. The child parses and
extracts text, enforces the existing maximum of **50 pages / 200,000 text
characters**, and returns just page count and extracted text as JSON over
stdout. The main server computes the SHA-256, applies validation rules,
and saves the tenant-scoped report as before.

Resource restrictions:

- **15-second elapsed-time limit**: FastAPI terminates the child when
  `subprocess.run(..., timeout=15)` expires, returning HTTP 422 and
  never recording a failed analysis. A crashed or memory-exhausted worker
  also fails without persisting a report.
- On **Linux/POSIX**, the child applies `RLIMIT_AS` of **768 MiB** and
  `RLIMIT_CPU` of **8 seconds soft / 9 seconds hard**, before importing
  `pypdf`; memory dumps are disabled.
- **Windows** does not provide `resource.setrlimit`. Development on Windows
  retains the wall-clock timeout, but **does not guarantee a memory or CPU
  limit**. Production requires Linux with container/cgroup isolation or
  Windows Job Objects with explicit memory and CPU ceilings.
- The subprocess receives a minimal allowlisted environment; the
  JWT secret, database password and any application credentials are
  deliberately **not forwarded**. The PDF is never written to a temporary
  file by this worker.
- A non-blocking semaphore permits at most **one active PDF subprocess per
  FastAPI process**. Further concurrent uploads get HTTP 503 rather than
  launching multiple high-memory parsers. Multiple web-server processes or
  replicas require their own global concurrency controls and queue.

**This is not a complete sandbox.** A process still runs under the same
OS user as the web server, and no filesystem, syscall or network isolation
is enforced. A compromised PDF parser could therefore read files accessible
to that account. Before production with real client data, run the
processor under a distinct unprivileged identity in a container with
network disabled, read-only filesystem, syscall restrictions and cgroup
limits; cap concurrent workers, enforce an HTTP upload-body ceiling
*before* multipart buffering, and review operational logs, data retention,
security and GDPR requirements.

### PDF broker: separate the web API from the Docker control plane

**Recommended architecture for the next development stage (Linux only):**

1. Start the `app.pdf_broker` service under a **separate OS account**
   that is permitted to access the local Docker daemon. Pre-build the
   fixed image using `docker build -f Dockerfile.pdf-worker -t tramitia-pdf-worker:local .`.
2. Configure the broker with `TRAMITIA_PDF_WORKER_BACKEND=container`,
   `TRAMITIA_PDF_WORKER_IMAGE=tramitia-pdf-worker:local`, and
   `TRAMITIA_BROKER_SOCKET=/run/tramitia/pdf-broker.sock`.
   Start `python -m app.pdf_broker` under that account. The socket
   directory must already exist and **must not be world-writable**.
3. Give only the web-server user access to that socket via a dedicated
   Unix group. Set `TRAMITIA_BROKER_SOCKET_GID` to that group's **numeric
   GID** to create a mode `0660` socket; default is owner-only `0600`.
   Keep the containing directory traversable only by this group.
4. Run FastAPI with `TRAMITIA_PDF_WORKER_BACKEND=broker` and the same
   `TRAMITIA_BROKER_SOCKET` path. **Do not** grant the FastAPI account
   Docker socket/group permissions. The broker is not an HTTP endpoint;
   its socket is local and accepts only PDF bytes, a page ceiling and
   a character ceiling, never arbitrary Docker commands or image names.

The fixed-size protocol checks message sizes (20 MiB input), pages
(1–50), characters (1–200,000), and returns bounded JSON (3 MB).
The privileged broker supervises the same ephemeral Docker worker with
no network, no mounts, restricted Linux capabilities and memory/CPU caps.
Invalid requests are discarded, failures return limited error categories,
and a missing broker fails with HTTP 503, **without falling back to
unrestricted PDF parsing**.

The CI workflow verifies the broker protocol and exercises the end-to-end
Chromium journey with broker → Docker container. **CI creates two dedicated
OS identities**: `tramitia-ci-web` (no Docker group/daemon access) and
`tramitia-ci-broker` (Docker group membership, but no application credentials).
A dedicated Unix group owns the broker socket (mode `0660`), allowing
the API to send PDF jobs without acquiring Docker privileges. CI verifies
the Unix socket owner, group, and permissions, tests a real PDF extraction
under the web identity, and runs the complete Chromium browser journey
using these separate accounts. The broker starts with a clean allowlisted
environment: it does not receive JWT or PostgreSQL credentials.
Production must reproduce this account separation, socket ownership and
isolation policies with a reviewed deployment configuration.

**Residual risks:** The broker itself holds Docker host privileges, and
a compromised broker may still control the host. Production requires
a dedicated host/VM or restricted container-runtime broker, independently
managed identity and least-privilege socket policies, denial-of-service
protection, tenant-aware job quotas, audit, and security review. Never
mount the host Docker socket into the public-facing API process.

### Container-restricted PDF parsing (optional development mode)

On Linux with a **local trusted Docker daemon**, the PDF parser can run in
a disposable container with no network, no host mounts, a read-only root
filesystem, disabled privilege escalation, all Linux capabilities dropped,
a non-root user, 32-process limit, 768 MiB RAM cap, and one CPU quota.
The image has only Python, pypdf and the standalone parser script.

```bash
docker build -f Dockerfile.pdf-worker -t tramitia-pdf-worker:local .
export TRAMITIA_PDF_WORKER_BACKEND=container
export TRAMITIA_PDF_WORKER_IMAGE=tramitia-pdf-worker:local
uvicorn app.main:app
```

Use `TRAMITIA_PDF_WORKER_BACKEND=subprocess` (the default) for lightweight
local development; **that mode has no filesystem or network isolation**.
Unknown backends fail closed. The image must be built in advance; the API
refuses to pull arbitrary images from an external registry. Containers are
launched via the **local Docker socket** (remote Docker contexts ignored),
and PDFs and extraction results travel only over stdin/stdout. Each worker
uses an ephemeral container with no persistent volume or attached network.
The CI suite runs a real container probe for network, host filesystem and
secrets, then runs the complete Chromium scenario with this mode enabled.

**Important security tradeoff:** granting the web-server process access
to a rootful Docker socket effectively gives it high host privileges.
The container isolates the PDF parser but **does not** isolate the web
server from the daemon. This configuration is for development/CI only;
production must move the Docker/container launcher into a separate
least-privileged worker service, broker or VM and remove Docker API
access from the internet-facing application. Enforce a separate service
identity, seccomp/AppArmor policies, image digest pinning, audit logs,
whole-request upload and memory caps, queue quotas and secure retention.
Do not expose the current application to client documents.

## Security and production status

**Development-only; do not upload real client documents.** The repository
is public, but credentials, PDFs and extracted data must remain private.
The dashboard stores JWTs in memory only, requiring login again after refresh.
Production still needs HTTPS, strict secret rotation, per-user rate limiting,
account recovery, backup/restore, security monitoring, audit logs, content
retention/deletion policy, malicious PDF sandboxing, and GDPR review.
There is no claim of regulatory or legal correctness. All risk scores are
heuristic and require qualified human review.

## Disclaimer

TramitIA is an educational MVP that provides automated document checks for support workflows. It **does not** replace legal, administrative, or professional advice, and should not be used as the sole basis for legal or procedural decisions.

## License

This project is licensed under the MIT License.

## Author

**Arnau Martí Ruiz**

Junior developer portfolio project built to explore PDF analysis workflows with FastAPI and PostgreSQL.
