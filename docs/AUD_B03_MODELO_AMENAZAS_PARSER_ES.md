# AUD-B03 — Procesador PDF: persistencia entre documentos y límite de confianza

**Estado: mitigación parcial experimental. NO cerrado.** Solo se han utilizado documentos ficticios y marcadores inventados. No se han ejecutado exploits ni un pentest de PDFs y **no se autoriza** el tratamiento de documentos reales de clientes.

## Modelo de amenazas

Un PDF es una entrada completamente no fiable. Incluso un parser escrito en Python puede tener fallos de lógica, agotamiento de recursos, dependencias vulnerables o problemas al interpretar estructuras malformadas. El objetivo de aislamiento es que, **si el parser resulta comprometido**, no pueda:

1. Ver credenciales de la API/base de datos, acceder a una red externa ni abrir el socket del broker web.
2. Alterar el resultado de documentos futuros, modificar su propio servicio o colocar archivos persistentes tras acabar su trabajo.
3. Agotar indefinidamente memoria, CPU, procesos o disco, ni obtener privilegios del host.
4. Recuperar bytes de documentos anteriores del almacenamiento temporal del servicio.

En el diseño actual `split-pdf-services`, broker y parser son contenedores y UIDs distintos, no tienen Docker socket ni red, y la imagen es de raíz de solo lectura. Es positivo. **Pero el subprocess que interpreta el PDF comparte el UID 65533, los montajes y el directorio del socket Unix con el servicio parser.** `TRAMITIA_PARSER_ONE_SHOT=1` termina el servicio después de un documento completo, pero Docker **reinicia el mismo contenedor**, no crea un entorno desechable por documento. Sin escape del contenedor, un proceso comprometido que actúe con el UID del parser puede modificar su directorio de socket y dejar estado observable tras el reinicio. Esta es una posibilidad derivada de la configuración de permisos; **no se ha demostrado la explotación de pypdf**.

## Mitigación parcial de esta PR

- Quitar el montaje `tmpfs: /tmp` del **contenedor parser** en `deploy/split-pdf-services.compose.yml`. Como la raíz ya es de solo lectura, elimina una ruta de escritura persistente entre reinicios. El broker de confianza conserva sus propios recursos; **no** se le aplica este cambio.
- `tests/smoke_split_pdf_services.sh` ahora ejecuta una comprobación real *dentro* del contenedor UID 65533: escribir un marcador de prueba en `/tmp` debe fallar por permisos o filesystem de solo lectura. Conserva verificaciones de ausencia de red/credenciales/Docker y parsing ficticio correcto.
- La misma prueba coloca **deliberadamente un marcador inocuo** en el directorio compartido `/run/tramitia-parser`, procesa un PDF ficticio, verifica que Docker reinicia el parser, y demuestra que el marcador **sigue presente** después. Después lo borra antes de la siguiente prueba. Esto es una evidencia **del riesgo todavía abierto**, no una prueba aprobatoria de aislamiento per-documento.
- `tests/test_parser_writable_surface.py` exige que el Compose parser mantenga raíz readonly, sin red/capacidades, y no vuelva a ofrecer un `tmpfs` /tmp escribible.

## Límites y criterios pendientes para cerrar AUD-B03

El directorio de socket **sigue siendo un bind mount escribible y persistente** para UID 65533; el subprocess no tiene una identidad de filesystem distinta. Un reinicio del servicio no borra automáticamente archivos ni hace inmutable el socket. Además, `--read-only` y Docker sin red **no sustituyen** la evaluación de vulnerabilidades de kernel/runtime, imágenes/digests ni mitigaciones frente a DoS.

**Antes de permitir clientes reales** se requiere un rediseño auditado donde *cada PDF* se interprete en un entorno desechable (contenedor nuevo con filesystem efímero, microVM o confinamiento equivalente) con **identidad/montajes de autoridad distintos del servicio de socket**. Debe demostrarse que ningún estado escribible del parser sobrevive de un documento al siguiente, que la identidad del broker no puede ser suplantada y que los recursos/timeout se cancelan tras fallos. Acompañar pruebas hostiles sintéticas de reintento, cuelgues, PDF corrupto, concurrencia, limpieza postcrash, cobertura del aislamiento de memoria, y una revisión de seguridad independiente.

Hasta entonces, esta PR es solo una reducción pequeña de superficie y evidencia de un bloqueo real que hay que resolver. **No fusionar nada a `main` por considerarlo producción-seguro.** El perfil de laboratorio puede usar PDFs ficticios para seguir midiendo controles.
