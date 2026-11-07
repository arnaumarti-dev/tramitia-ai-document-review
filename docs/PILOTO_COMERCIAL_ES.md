# TramitIA — preparación del primer piloto comercial

**Estado: solo demostración con información ficticia.** No implica autorización
para procesar documentos de clientes reales ni que TramitIA compruebe la
validez legal de solicitudes.

## Problema a validar

Gestorías pequeñas que revisan solicitudes de subvenciones a mano pueden
necesitar una primera lectura que señale campos o señales textuales ausentes.
TramitIA utiliza extracción de texto PDF y **reglas heurísticas**; el resultado
es un apoyo a la revisión humana, no asesoramiento jurídico, evaluación de
elegibilidad ni una comprobación de firmas electrónicas.

## Demostración segura de 10 minutos

1. Preparar una base de datos de pruebas y dos organizaciones ficticias
   siguiendo [PRUEBA_LOCAL_ES.md](PRUEBA_LOCAL_ES.md).
2. Generar `solicitud-ficticia.pdf` con
   `python -m scripts.generate_demo_pdf`, sin nombres ni datos reales.
3. Como propietario, subir el PDF, mostrar puntuación, advertencias,
   historial y exportación JSON. Explicar que son indicios, no conclusiones.
4. Como empleado, demostrar acceso a su organización y acceso denegado a la
   otra (sin botón de borrado). Como propietario, mostrar el botón **Eliminar**
   con confirmación y comprobar que borra el informe de su propia gestoría
   mediante `DELETE /analyses/{id}`.
5. Solicitar *feedback* sin subir, enviar por correo ni conservar documentos
   de clientes. Anotar solo observaciones comerciales sin información personal.

## Preguntas clave a potenciales clientes

- ¿Cuántas solicitudes se revisan por semana y cuánto tarda la primera revisión?
- ¿Qué errores repetitivos provocan más retrabajo y costes?
- ¿Qué revisión *humana* no puede omitirse nunca?
- ¿Qué precisión mínima y trazabilidad exigen antes de confiar en alertas?
- ¿Aceptarían pagar por el ahorro una vez que el producto esté preparado?

## Criterios para abrir un piloto con documentos reales

No ofrecerlo hasta que se hayan completado, documentado y verificado:

- [ ] Separación efectiva y revisión independiente de seguridad del procesador PDF,
      incluido el riesgo de que parser y broker compartan UID/contenedor.
- [ ] Despliegue real revisado: HTTPS, proxy confiable, copias de seguridad,
      recuperación, monitorización, permisos y gestión de secretos.
- [ ] Política de conservación y eliminación (base de datos, copias,
      exportaciones); auditoría de accesos y borrados. Existe un registro
      inicial de borrados, no una auditoría completa de accesos. Consulta
      [DATOS_Y_RETENCION_ES.md](DATOS_Y_RETENCION_ES.md).
- [ ] Revisión RGPD, base jurídica, roles de responsable/encargado,
      contrato adecuado y evaluación de impacto si corresponde.
- [ ] Ensayos con casos representativos autorizados, tasa de falsos positivos,
      revisión humana y límites claros de responsabilidad.
- [ ] Soporte, condiciones comerciales, facturación y procedimiento de incidencias.

## Indicadores de validación comercial

Registrar únicamente datos agregados: número de gestorías entrevistadas,
interés en un piloto, tiempo de revisión manual frente al asistido,
errores detectados y precio que aceptarían. No almacenar nombres de clientes,
DNI, PDFs reales o extractos en hojas de ventas.

**Precios de trabajo** (hipótesis, no tarifas anunciadas): piloto 29 €/mes,
plan básico 49 €/mes, equipo 99 €/mes. Ajustarlos según costes de
infraestructura, límites de uso, confianza del cliente y resultados reales.

Hasta superar los criterios anteriores, únicamente mostrar una demostración
ficticia sin acceso público ni promesas de validación legal.
