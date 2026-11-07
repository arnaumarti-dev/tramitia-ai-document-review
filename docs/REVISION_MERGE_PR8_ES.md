# Revisión de integración de la PR #8 — borrador

**Estado:** no fusionada. Esta revisión no equivale a una aprobación para
producción ni sustituye una revisión independiente del código.

## Puertas técnicas verificables

- [x] Última CI de referencia antes de esta revisión: ejecución
  [#42324842488](https://github.com/arnaumarti-dev/tramitia-ai-document-review/actions/runs/42324842488)
  en verde sobre `9bcd2d4`.
- [x] La PR incorpora una batería de pruebas unitarias, de PostgreSQL,
  aislamiento de contenedores, Nginx y un recorrido completo con Chromium.
- [ ] **Comprobar CI en el HEAD definitivo** antes de retirar el borrador.
- [x] **Actualización sin pérdida de datos:** ejecución
  [#42325827001](https://github.com/arnaumarti-dev/tramitia-ai-document-review/actions/runs/42325827001)
  ha pasado la actualización de Alembic `0003_invites_password_revision`
  a `head` conservando organización, usuario y documento ficticios.
- [x] **Render, My Workspace:** los servicios existentes
  `tramitia-demo-ficticia` (rama PR #8, `autoDeploy=off`,
  `startCommand=python -m demo_private.server`) y `churn-mlops-demo`
  (otro repositorio, `autoDeploy=off`) no despliegan TramitIA desde
  `main`. Comprobado el 7 de noviembre de 2026. Esto no acredita
  configuraciones de otros hosts o proveedores.
- [x] Revisión focalizada del enlace API-broker: el perfil aislado ahora
  impide que la cuenta web sea propietaria o tenga escritura sobre el
  directorio privado, y comprueba la identidad del broker por SO_PEERCRED
  antes de enviar PDFs. Nuevas pruebas de configuración y peer no confiable.
  Esto **no** sustituye una auditoría independiente.
- [ ] Solicitar revisión independiente de código, sobre todo autenticación,
  aislamiento del analizador, sockets Unix, rutas de borrado y permisos.
- [ ] **Restauración de base de datos:** la CI incorpora una comprobación
  real de `pg_dump -Fc` y `pg_restore` en una base desechable separada,
  con organización, usuario, informe y eventos de auditoría ficticios.
  No se dará por superada hasta la CI del nuevo commit.
- [ ] Confirmar backup cifrado, frecuencia, custodia, restauración
  y ventana de mantenimiento antes de migrar un servidor real.

## Alcance del merge frente a uso comercial

Fusionar a `main` incorpora el código y las migraciones al repositorio.
No implica que se haya desplegado en producción. La rama de referencia
mantiene el perfil `TRAMITIA_DEPLOYMENT_PROFILE=development` como opción
local, y el perfil `isolated` exige un broker y un entorno Linux preparado.
El operador debe impedir cualquier exposición accidental del modo de
desarrollo a Internet.

La demo de Render utiliza el módulo aislado `demo_private` y no ejecuta
el backend de análisis. No utiliza documentos reales.

**Pendientes para clientes reales:** revisión de seguridad independiente;
configuración segura de TLS y operadores, control de backups y restauración,
política formal de retención/eliminación, RGPD y respuesta a incidentes.
Las pruebas automáticas exitosas no certifican seguridad ni cumplimiento.

## Procedimiento recomendado

1. Revisar el resultado completo de CI en el último commit y los comentarios.
2. Obtener una revisión humana del diff de seguridad y permisos.
3. Hacer una copia de seguridad de la base de datos existente, si la hay.
4. Comprobar la ruta de migración en staging con datos ficticios.
5. Confirmar que no haya despliegues automáticos conectados a `main`.
6. Solo entonces marcar la PR como lista para revisión; la fusión requiere
   autorización expresa y se debe verificar el estado del HEAD de nuevo.
