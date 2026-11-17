# AUD-H06 — autorización antes de interpretar multipart

**Alcance:** hardening experimental de `POST /analyze`; solo datos ficticios en pruebas. **No** supone que los PDFs reales estén autorizados ni que la aplicación esté certificada para producción.

## Riesgo confirmado en revisión de ejecución

En FastAPI, `UploadFile = File(...)` obliga a parsear el cuerpo multipart antes de ejecutar las dependencias del endpoint, incluyendo `Depends(current_principal)`. Un usuario sin credenciales podía enviar hasta 21 MiB de multipart y provocar que Starlette creara un `SpooledTemporaryFile` antes de recibir HTTP 401. Los límites NGINX/ASGI permanecían activos, pero no garantizaban un coste mínimo para peticiones no autenticadas.

## Control propuesto

`app/upload_auth_preflight.py` incorpora un **control ASGI previo** estrictamente para `POST /analyze`, antes de Starlette y del parser de multipart. Examina solo cabeceras de autorización sin llamar a `receive()`. Falta de Bearer, cabeceras duplicadas, credenciales ilegibles o malformadas producen 401 inmediato; se valida la firma/caducidad del JWT y la pertenencia activa del usuario **usando la misma función `current_principal`** que los endpoints, en un hilo para evitar bloquear el event loop. Si la verificación no puede completarse, se devuelve 503 sin leer la subida. No se registra ni revela el token ni el texto del error de base de datos.

Tras terminar el cuerpo, FastAPI vuelve a comprobar `current_principal`, impidiendo que una revocación durante una subida larga quede aceptada por una validación antigua. La capa externa `UploadBodyLimit` sigue rechazando Content-Length declarado por encima del límite con 413, incluso si el cliente está anónimo: no se consume ningún byte para ello. Por tanto, **un 413 externo puede preceder al 401**.

## Autenticación previa en NGINX al integrar ambas medidas

Con `proxy_request_buffering off`, el cierre anticipado de FastAPI durante una subida anónima grande puede convertirse en HTTP 502. La integración introduce `auth_request /_tramitia_upload_auth`: NGINX hace una **petición interna GET /auth/me**, sin cuerpo, antes de transmitir el PDF al backend. El URI interno es inaccesible directamente y propaga exclusivamente la cabecera Authorization esperada. La prueba NGINX→FastAPI verifica 401 anónimo, 200 para PDF ficticio autenticado de 20 MiB, 413 por exceso de tamaño, desconexión y recuperación tras SIGKILL.

Una petición autenticada válida provoca **hasta tres comprobaciones de membresía** (subrequest NGINX, middleware ASGI y dependencia del endpoint). Falta medir latencia, consumo de conexiones a PostgreSQL y carga con usuarios concurrentes. Las caídas del verificador fallan cerradas, pero la respuesta del proxy puede ser HTTP 500 en lugar de 503; se requiere tratamiento operacional y métricas antes del despliegue.

## Pruebas

`tests/test_upload_auth_preflight.py` incluye comprobaciones ASGI sin lectura del cuerpo para solicitudes anónimas, con token vencido/revocado, credenciales duplicadas y errores de conexión; confirma que el resto de rutas no cambia. Una prueba de integración `TestClient` inspecciona que Starlette **no crea** `SpooledTemporaryFile` para peticiones rechazadas y que la petición autenticada continúa pasando por la extracción ficticia tras la doble comprobación de pertenencia.

## Riesgos residuales

- Una credencial válida todavía puede forzar spooling multipart antes de los contadores de cuota que se aplican en el endpoint (prioridad de siguiente fase). La comprobación temprana **duplica la consulta de membresía**, que hay que perfilar y monitorizar.
- Una solicitud sin credenciales aún gasta recursos de TLS/NGINX/cabeceras; este cambio **no es protección DDoS**, límite de concurrencia ni sustituto de `client_body_timeout`, límites de body y rate-limit del proxy.
- El middleware y los endpoints con dependencias no comparten una identidad cacheada porque la revalidación reduce la ventana de revocación. Las credenciales aceptadas pueden quedar revocadas antes de procesar el documento y no se reserva cuota antes del parseo multipart.
- **Compatibilidad con AUD-B02 (PR #15):** la rama conjunta de la PR #17 adapta el fixture de NGINX y las pruebas multipart de 20 MiB para usar un JWT ficticio y comprobar membresía, tanto antes como después de la subida. La CI conjunta ha terminado en verde, pero ninguna prueba sustituye la revisión de un host Linux de laboratorio.
- Un sistema listo para documentos de clientes requerirá completar AUD-B01–B04, seguridad de dependencias y revisión independiente.

No cambia la demo Render, ni las políticas de retención, ni los despliegues de producción.
