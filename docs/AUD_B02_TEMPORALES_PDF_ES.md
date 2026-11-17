# AUD-B02 — Ficheros temporales de las subidas PDF (laboratorio Linux)

**Estado:** endurecimiento experimental, pendiente de revisión independiente. **No usar documentos de clientes reales.** Esta guía explica el recorrido de bytes, los controles implementados y las pruebas; **no certifica** borrado irrecuperable, cifrado ni cumplimiento RGPD.

## Recorrido de los bytes

1. **Cliente → NGINX TLS:** `/analyze` permite como máximo 21 MiB de cuerpo multipart. Antes NGINX mantenía `proxy_request_buffering` predeterminado (activado) y un `client_body_buffer_size` de 128 KiB: cuerpos mayores podían caer en su directorio `client_body_temp_path`, sin garantía de tmpfs. Ahora `proxy_request_buffering off` envía los bytes al upstream con HTTP/1.1 sin esperar a leer el cuerpo entero. Como defensa adicional, se fija `client_body_temp_path /run/tramitia-nginx-body`; el operador debe asegurar que **ese directorio** existe, tiene propietario NGINX y modo 0700 y está sobre tmpfs. El cambio no demuestra que toda versión/configuración de NGINX jamás escriba un archivo.
2. **NGINX → FastAPI/Starlette:** `UploadFile` se parsea **antes** de que la función `analyze()` ejecute las comprobaciones de autenticación/cuotas; el guard ASGI de 21 MiB limita tamaño antes/durante ese parsing, pero **no evita** el spool de solicitudes no autenticadas (AUD-H06 pendiente). Starlette usa un `SpooledTemporaryFile` que puede pasar de RAM a un fichero temporal al superar su umbral. `PrivateTmp=true` aísla el espacio de nombres, **no** lo convierte en memoria.
3. **Subida temporal en FastAPI:** las dos unidades systemd de referencia establecen `TMPDIR=/run/tramitia-upload` y `RuntimeDirectory=tramitia-upload` con modo 0700, `UMask=0077` y usuario `tramitia-web`. En Linux, `/run` suele ser tmpfs, pero **no se presupone**: el arranque aislado rechaza el servicio salvo que el directorio exista, sea propiedad del UID web, no sea un symlink, no permita acceso a grupo/otros, que Python `tempfile` use exactamente esa ruta y que el mount efectivo sea `tmpfs`, comprobado con `/proc/self/mountinfo`. La ruta temporal puede contener el PDF completo mientras dure la solicitud.
4. **PDF en memoria → broker/parser:** `analyze()` lee hasta 20 MiB + 1 en una variable `bytes` y pasa los datos por stdin/socket al proceso de extracción. Existen copias temporales en RAM de los procesos; no se escribe el PDF original ni el texto íntegro nuevo en PostgreSQL. Los metadatos/hallazgos sí se conservan. El aislamiento del parser continúa siendo experimental (AUD-B03).

## Operación del proxy en un host Linux de laboratorio

El usuario NGINX varía con la distribución (ejemplo: `www-data`). Antes de iniciar o recargar NGINX, el administrador debe establecer su ruta de temporales, sin dejar que caiga en almacenamiento persistente:

```bash
# Ajusta nginx_user al trabajador real (podría llamarse nginx).
nginx_user=www-data
findmnt -T /run -no FSTYPE           # exigir: tmpfs
sudo install -d -m 0700 -o "$nginx_user" -g "$nginx_user" /run/tramitia-nginx-body
findmnt -T /run/tramitia-nginx-body -no FSTYPE  # exigir: tmpfs
stat -c '%a %U:%G %n' /run/tramitia-nginx-body  # exigir: 700 y NGINX
sudo nginx -t
```

El directorio de NGINX en `/run` desaparece después de un reinicio: crear una unidad/tmpfiles.d verificada que lo prepare **antes** de NGINX. No asumir que `nginx -t` demuestra la seguridad de sus montajes. Evitar logs de cuerpos multipart, paths de documentos sensibles y volcados de memoria.

## Operación de FastAPI aislado

La unidad systemd crea el directorio `/run/tramitia-upload` propiedad de `tramitia-web` con modo 0700 en cada arranque. Verificar:

```bash
findmnt -T /run/tramitia-upload -no FSTYPE  # tmpfs
stat -c '%a %U:%G %n' /run/tramitia-upload # 700, tramitia-web
systemctl show tramitia-web.service -p Environment -p RuntimeDirectory
```

No introducir `TMPDIR` tarde, después de que Python `tempfile` haya cacheado su ruta. El guard rechaza esa divergencia. Si `/run` reside en un filesystem persistente, si el mount se cambia o el directorio es inseguro, **FastAPI aislado no arranca**. El perfil local `development` sigue admitiendo temporales normales, solo para datos ficticios.

**RAM no equivale a cifrado ni a eliminación segura.** El kernel puede escribir páginas de tmpfs a swap. Desactivar swap o protegerlo con cifrado temporal apropiado, controlar core dumps/hibernación, memoria del host, logs y acceso root, y auditar el comportamiento del sistema operativo. El proceso root y el administrador del host conservan acceso privilegiado. La semántica habitual de temporales anónimos permite que el kernel libere descriptores al cerrar o matar el proceso; no acredita borrado forense de RAM/swap.

## Pruebas y límites

- `tests/test_upload_temporary_storage.py`: verificación del mount efectivo, permisos/symlinks, multipart **sintético de 20 MiB** por FastAPI/Starlette que fuerza rollover, cierre de descriptor y ausencia de nombres residuales, además de un subproceso de prueba terminado con SIGKILL.
- El E2E de CI ejecuta Uvicorn aislado con `TMPDIR` privado en `/dev/shm`; todos los componentes de prueba usan **solo datos inventados**.
- El chequeo de configuración NGINX verifica sintaxis, no recorre su ruta real con 20 MiB ni simula cortes/arranques en un host productivo. Falta una prueba de proxy real extremo a extremo con truncados, 413, desconexiones y limpieza bajo presión; revisar permisos tras reinicio.
- No se prueba un despliegue de producción, swap cifrado ni recuperación después de reinicio del host. Faltan test hostiles de parser (AUD-B03), autenticación antes de parsing multipart (AUD-H06), control operacional de retención (AUD-B04) y revisión independiente.

**Criterio para considerar B02 cerrable en el futuro:** pruebas integradas de NGINX y ASGI de 20 MiB, fallo/abort de proceso y reinicio real en Linux, revisión de montajes/propietarios de ambos servicios y política de swap/volcados, y evidencias revisadas por otra persona. Hasta entonces, B02 sigue abierto.
