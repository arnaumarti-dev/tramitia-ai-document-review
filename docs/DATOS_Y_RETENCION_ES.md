# TramitIA — datos y conservación (BORRADOR, solo pruebas)

**No usar documentos reales de clientes ni interpretar esto como certificación RGPD.**
Los plazos de conservación y la base jurídica han de aprobarse antes de un piloto.

## Inventario mínimo

- **PDF y texto:** el documento se analiza en memoria con un worker. No
  se guarda el PDF en PostgreSQL ni el texto extraído de los análisis
  nuevos (`analyses.extracted_text` es NULL). Pueden existir textos heredados.
- **Informes:** la tabla `analyses` conserva nombre de archivo, evaluación
  heurística, observaciones, hash de texto, fecha y organización.
  Estos metadatos pueden contener información personal.
- **Cuentas:** `users`, `memberships` y `staff_invitations` almacenan
  cuentas, roles y secretos derivados (hashes); revisar plazos y permisos.
- **Limitadores:** `login_attempts`, `analysis_quotas` e
  `ip_rate_limits` conservan contadores y claves.
- **Auditoría de borrado:** `analysis_deletion_audit` contiene solo ID
  de gestoría, usuario que borró, ID del informe, tipo de evento y fecha.
  No contiene nombres, contenidos, DNI, direcciones IP ni hashes del PDF.
  Los identificadores pueden seguir siendo datos personales indirectos.

## Funcionamiento verificado por código y tests

- `DELETE /analyses/{id}` exige propietario o administrador de la
  organización. Borrado y escritura del evento ocurren en la misma
  transacción: si falla registrar el evento, el borrado se revierte.
- `GET /audit/deletions` devuelve exclusivamente eventos de la propia
  gestoría, con paginación y acceso owner/admin. No registra lecturas,
  descargas, inicios de sesión ni intentos de borrado rechazados.
- Borrar informes no elimina JSON exportados, ficheros de terceros,
  backups ni registros externos.
- Los eventos se eliminan al borrar una organización (FK CASCADE). No
  hay una política automática de eliminación de eventos individuales.
- Retroceder la migración `0006` destruye eventos: debe revisarse antes.

## Decisiones pendientes

| Categoría | Conservación | Acción necesaria |
| --- | --- | --- |
| Informes y hallazgos | Por definir | Plazo justificado por finalidad y contrato |
| Eventos de auditoría | Por definir | Plazo proporcional, responsables y acceso |
| Backups | Por definir | Cifrado, caducidad, recuperación y supresión |
| Copias JSON en dispositivos | Fuera de la API | Normas de exportación y gestión de copias |
| Cuentas y contadores | Por definir | Cierre de cuentas y eliminación de temporales |

**No se ha programado ningún borrado automático.** Antes de ello se debe
aprobar una política y probar una purga simulada con alcance por organización,
respaldo controlado, trazabilidad y restauración verificada.

## Riesgos y obligaciones

Completar una revisión independiente de seguridad del parser, permisos
y despliegue TLS; auditoría de accesos y exportaciones; respuesta a
incidentes, contrato con gestorías, análisis de riesgos, derechos de
las personas y obligaciones RGPD aplicables. El registro de borrado
es un avance técnico limitado, no una auditoría integral.
