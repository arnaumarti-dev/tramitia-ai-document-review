# TramitIA

**Revisión preliminar de documentos administrativos para gestorías y equipos que preparan solicitudes de subvenciones.**

[![Regression and isolation tests](https://github.com/arnaumarti-dev/tramitia-ai-document-review/actions/workflows/tests.yml/badge.svg)](https://github.com/arnaumarti-dev/tramitia-ai-document-review/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> [!IMPORTANT]
> **Estado: MVP local de desarrollo, no apto para documentos reales de clientes ni para producción.**
> TramitIA realiza **comprobaciones heurísticas**, no una validación jurídica, una verificación de firmas electrónicas ni una evaluación de elegibilidad para subvenciones. Los resultados requieren revisión humana. Una CI correcta **no equivale** a una auditoría independiente de seguridad.

## ¿Qué problema aborda?

Preparar expedientes administrativos puede requerir revisar repetidamente documentos en busca de campos y señales textuales ausentes. TramitIA permite probar una **primera revisión asistida**: extrae texto seleccionable de un PDF, ejecuta reglas predefinidas, presenta advertencias explicadas y conserva un historial de informes por organización.

**Público objetivo en validación:** gestorías y equipos que gestionan solicitudes de subvenciones. No se han acreditado todavía resultados comerciales, ahorro de tiempo ni precisión con expedientes reales.

## Funcionalidades disponibles en `main`

| Área | Implementación actual |
| --- | --- |
| Documentos | Subida de PDF con texto seleccionable, extracción con `pypdf` y límites de tamaño/recursos |
| Revisión | Reglas para DNI/NIE, correo, teléfono, fecha, dirección y expresiones relacionadas con solicitud, declaración y firma |
| Resultado | Hallazgos, severidad, recomendaciones orientativas, puntuación heurística de 0 a 100 y estado `OK` / `REVISAR` / `RIESGO` |
| Historial | Listado paginado, filtros, detalle y exportación JSON de informes |
| Organizaciones | Cuentas y análisis separados por gestoría; comprobaciones de pertenencia y permisos en el servidor |
| Equipo | Roles `owner`, `admin` y `employee`; invitaciones de un solo uso, desactivación de miembros y cambio de contraseña |
| Auditoría parcial | Registro por organización de consultas de detalle, solicitudes de exportación y borrados de informes |
| Controles de acceso | JWT de corta duración, contraseñas con Argon2id, revocación de sesión, cuotas de análisis y controles de frecuencia por conexión |
| Calidad | Pruebas unitarias, integración PostgreSQL, frontend y recorrido E2E con Chromium en GitHub Actions |

**Límites de entrada vigentes:** máximo 20 MiB por PDF, 50 páginas, 200.000 caracteres extraídos y 21 MiB para la solicitud multipart completa. Se aplican cuotas provisionales de 20 intentos por usuario/hora y 100 por organización/hora.

**Importante:** la versión actual de `main` **no realiza OCR**. Los PDFs escaneados, sin texto seleccionable o vacíos no se analizan de forma fiable. La [PR #9](https://github.com/arnaumarti-dev/tramitia-ai-document-review/pull/9) propone rechazar los documentos sin texto extraíble y dar un aviso explícito; **no está fusionada en esta versión ni añade OCR**.

## Inicio rápido (solo pruebas locales con datos ficticios)

### Requisitos

- Python **3.11** (versión usada en CI).
- Docker con Docker Compose para PostgreSQL 16.
- Git y un puerto local `5432` libre.

> El `docker-compose.yml` de la raíz levanta **únicamente PostgreSQL**, con credenciales de demostración y puerto limitado a `127.0.0.1`. **No lo expongas a una red ni lo uses como configuración productiva.**

### 1. Clonar e instalar

**PowerShell (Windows):**

~~~powershell
git clone https://github.com/arnaumarti-dev/tramitia-ai-document-review.git
cd tramitia-ai-document-review
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
~~~

**Linux/macOS:** usa `source .venv/bin/activate` para activar el entorno virtual después de crearlo.

### 2. Preparar la base de datos y la autenticación

~~~powershell
docker compose up -d
$env:TRAMITIA_JWT_SECRET = python -c "import secrets; print(secrets.token_hex(32))"
python -m alembic upgrade head
python -m scripts.bootstrap_admin --slug demo-gestoria --name "Gestoría de pruebas" --email propietario@example.invalid
~~~

El comando de inicialización pide una contraseña de prueba. Utiliza un correo inventado y **no reutilices contraseñas personales**. La variable `TRAMITIA_JWT_SECRET` debe configurarse en cada sesión de terminal desde la que inicies la API.

### 3. Generar un PDF ficticio y ejecutar la web

~~~powershell
python -m scripts.generate_demo_pdf --output solicitud-ficticia.pdf
python -m uvicorn app.main:app --reload
~~~

Abre **http://127.0.0.1:8000/** e inicia sesión con:

- **Organización:** `demo-gestoria`
- **Correo:** `propietario@example.invalid`
- **Contraseña:** la que hayas elegido al crear la cuenta

Carga `solicitud-ficticia.pdf` para probar el flujo de análisis, el historial, el detalle y la exportación.

**Guía paso a paso:** [Prueba local completa, invitaciones y separación entre gestorías](docs/PRUEBA_LOCAL_ES.md).

**Al terminar:** detén Uvicorn con `Ctrl+C` y ejecuta `docker compose down`. **No uses `down -v` si necesitas conservar los datos de prueba**: elimina el volumen de PostgreSQL.

## Cómo funciona

~~~text
Navegador (HTML / CSS / JavaScript)
             |
             v
FastAPI: autenticación + organización + cuotas + límites de subida
             |
             v
Extractor PDF (pypdf, fuera del proceso web)
             |
             v
Texto extraído -> reglas heurísticas -> puntuación + observaciones
                                              |
                                              v
                                    PostgreSQL (por organización)
                                              |
                                              v
                               Dashboard / historial / JSON
~~~

El backend se implementa con **Python, FastAPI, Pydantic, PostgreSQL 16, psycopg2, Alembic y PyJWT**. Las contraseñas se almacenan mediante hashes Argon2id. Las comprobaciones actuales son reglas sobre texto, **no un modelo de IA entrenado ni un sistema de comprensión jurídica avanzada**.

Los análisis nuevos guardan informe, hallazgos, hash y metadatos, pero **no conservan el PDF original ni el texto íntegro extraído en PostgreSQL**. Los metadatos y hallazgos aún pueden contener datos personales; además, pueden existir registros históricos de versiones anteriores.

### Procesamiento PDF y aislamiento

Hay dos contextos distintos:

- **Desarrollo local (predeterminado):** FastAPI delega el análisis a un subproceso con tiempo máximo de ejecución y, en Linux, límites de CPU y memoria. **No constituye una sandbox completa**.
- **Perfil aislado experimental (Linux):** FastAPI se comunica por un socket Unix protegido con un broker; la arquitectura de referencia separa broker y parser en contenedores e identidades no root distintos, sin acceso a Docker desde la API. El parser se reinicia después de cada petición autenticada, pero **no se crea una VM o contenedor desechable por documento**.

El aislamiento experimental **no está aprobado para producción** ni sustituye una revisión independiente. En caso de fallo del broker/parser, el modo aislado debe rechazar el análisis en lugar de recurrir a un parser no aislado.

Consulta [arquitectura de servicios separados](deploy/SPLIT-PDF-SERVICES.md) y [broker sin Docker en el proceso web](deploy/DAEMONLESS-PDF-BROKER.md). Esas guías son **referencias de laboratorio**, no instrucciones de despliegue productivo listas para copiar.

## API principal

Documentación interactiva en desarrollo local: **http://127.0.0.1:8000/docs**.

| Método | Ruta | Propósito |
| --- | --- | --- |
| `GET` | `/health` | Estado básico del servicio |
| `POST` | `/auth/login` | Inicio de sesión por organización |
| `GET` | `/auth/me` | Identidad y rol de la sesión |
| `POST` | `/auth/invitations/accept` | Aceptación de invitaciones |
| `POST` | `/auth/password` | Cambio de contraseña y revocación de sesiones |
| `GET` | `/staff` | Equipo de la organización (owner/admin) |
| `POST` | `/staff/invitations` | Invitación de miembros autorizados |
| `POST` | `/staff/{user_id}/disable` | Desactivación de un miembro autorizado |
| `POST` | `/analyze` | Análisis heurístico de un PDF |
| `GET` | `/analyses` | Historial filtrable y paginado |
| `GET` | `/analyses/{analysis_id}` | Detalle de un informe |
| `GET` | `/analyses/{analysis_id}/export` | Solicitud de exportación JSON |
| `DELETE` | `/analyses/{analysis_id}` | Eliminación por owner/admin |
| `GET` | `/audit/accesses` | Registro parcial de accesos y exportaciones |
| `GET` | `/audit/deletions` | Registro de borrados |

Las rutas de análisis e informes requieren una cuenta autenticada. No se acepta un identificador de organización arbitrario proporcionado por el navegador como autorización.

## Pruebas y CI

**Pruebas Python:**

~~~powershell
python -m pip install httpx
python -m unittest discover -s tests -v
~~~

**Frontend (Node.js 22):**

~~~powershell
node --check frontend/app.js
node --test tests/test_frontend.cjs
~~~

Para las pruebas PostgreSQL integradas se necesita una base **efímera** migrada y `TRAMITIA_TEST_POSTGRES=1`; la CI prepara ese entorno automáticamente. El workflow [`Regression and isolation tests`](.github/workflows/tests.yml) también comprueba migraciones, restauración de una base de prueba, configuraciones de aislamiento y un recorrido real con Chromium.

**CI verde = regresiones conocidas superadas en el entorno de pruebas.** No demuestra que el software sea seguro frente a todos los ataques ni que esté listo para uso legal o empresarial.

## Seguridad, privacidad y uso responsable

**No subas documentos reales de clientes, datos médicos, DNI reales ni información confidencial a este MVP.**

Controles incorporados: autenticación y roles, separación lógica de organizaciones, límites de peticiones y PDFs, auditoría parcial, procesado de documentos fuera de FastAPI y configuración experimental de aislamiento Linux.

**Pendiente antes de cualquier piloto real:**

1. **Revisión de seguridad independiente**, especialmente del parser PDF, broker, permisos, sesiones y aislamiento entre organizaciones.
2. **Despliegue operativo revisado:** TLS/HTTPS, proxy confiable, secretos, copias cifradas, recuperación, supervisión y respuesta a incidentes.
3. **Política aprobada de conservación y borrado:** datos, copias, registros de auditoría y exportaciones. El borrado de un informe **no elimina** copias JSON previamente descargadas ni respaldos.
4. **Cumplimiento de protección de datos aplicable**, contratos y responsabilidades entre proveedor y clientes.
5. **Validación funcional** sobre casos representativos autorizados, medición de errores y revisión humana obligatoria.

El registro actual cubre consultas de detalle, **solicitudes** de exportación y borrados; no acredita que la descarga terminara ni representa una auditoría de seguridad integral. Consulta el [borrador de datos y retención](docs/DATOS_Y_RETENCION_ES.md).

## Qué no hace todavía

- **OCR:** no extrae el texto de PDFs que solo contienen imágenes.
- **Comprobación normativa:** no consulta convocatorias oficiales ni verifica requisitos legales particulares.
- **Firma electrónica:** no comprueba criptográficamente certificados, firmas o validez administrativa.
- **Análisis semántico avanzado:** no usa un motor de IA para interpretar obligaciones jurídicas.
- **SaaS público listo para cobrar:** no hay alta autónoma de clientes, recuperación segura de cuentas, facturación ni infraestructura productiva validada.
- **Procesamiento masivo:** no dispone de una cola robusta de trabajos con escalado entre instancias.

### Próximos hitos propuestos

1. Resolver el caso de PDFs sin texto extraíble ([PR #9](https://github.com/arnaumarti-dev/tramitia-ai-document-review/pull/9)) y diseñar OCR aislado.
2. Añadir reglas configurables por tipo de documento/convocatoria y explicar cada resultado con evidencia verificable.
3. Completar revisión de seguridad externa y preparación operativa antes de usar datos reales.
4. Validar el problema y los resultados con gestorías mediante **demostraciones con datos ficticios**, sin prometer validez jurídica.
5. Estudiar extracción de requisitos de fuentes oficiales y comparación asistida de expedientes, **una vez validada su viabilidad**.

## Documentación y recursos

| Recurso | Contenido |
| --- | --- |
| [Prueba local](docs/PRUEBA_LOCAL_ES.md) | Recorrido manual de gestoría, empleados y documentos ficticios |
| [Retención de datos (borrador)](docs/DATOS_Y_RETENCION_ES.md) | Inventario, trazabilidad parcial y decisiones pendientes |
| [Piloto comercial (borrador)](docs/PILOTO_COMERCIAL_ES.md) | Hipótesis de mercado y condiciones antes de tratar documentos reales; revisar su estado histórico |
| [Aislamiento PDF experimental](deploy/SPLIT-PDF-SERVICES.md) | Arquitectura Linux de broker/parser separados y riesgos |
| [Broker de referencia](deploy/DAEMONLESS-PDF-BROKER.md) | Alternativa experimental sin Docker en el proceso web |
| [Demo independiente](demo_private/README.md) | Vista previa con **datos inventados**; no analiza documentos ni ejecuta el backend real |
| [GitHub Actions](https://github.com/arnaumarti-dev/tramitia-ai-document-review/actions) | Pruebas automáticas y resultados de CI |

## Licencia

Distribuido bajo la [licencia MIT](LICENSE).

**Autor:** Arnau Martí Ruiz · Proyecto en desarrollo, orientado a aprendizaje técnico y validación de una posible solución B2B.
