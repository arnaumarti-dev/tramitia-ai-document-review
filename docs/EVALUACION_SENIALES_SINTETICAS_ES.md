# Evaluación reproducible de señales textuales — solo sintéticos

Este conjunto **NO** mide la precisión de TramitIA en documentos de gestorías.
Mide el acuerdo de seis alertas con etiquetas redactadas manualmente para
textos artificiales inventados. No contiene PDFs ni documentos de clientes.

La rama depende de la PR #11, que introduce las reglas auditadas; no modifica
`main` ni la PR #9. Todos los ejemplos son ficticios.

## Corpus y métricas

`tests/fixtures/validation_signals_v1.json` contiene 30 textos con
etiquetas explícitas para `INVALID_ID_CONTROL`, `MISSING_ID`,
`INVALID_DATE`, `MISSING_DATE`, `SIGNATURE_REQUIRED` y
`SIGNATURE_UNVERIFIED`. Hay al menos dos ejemplos positivos y negativos
por etiqueta. El resultado no representa una distribución real de solicitudes.

El evaluador calcula por código TP, FP, FN y TN, precisión
(TP/(TP+FP)), sensibilidad (TP/(TP+FN)), F1 y tasas de error. Un denominador
cero se muestra como N/D, **no como un 100 % inventado**. Los casos que dan
errores se identifican para que puedan corregirse reglas específicas.

- `SIGNATURE_REQUIRED`: indicios textuales de falta de firma; **no**
  demuestra nada sobre una firma electrónica que pudiera estar en el PDF.
- `SIGNATURE_UNVERIFIED`: mención a firma sin evidencia comprobada;
  **no** verifica una firma ni su validez.
- DNI/NIE: las etiquetas representan el dígito/letra de control de una
  cadena ficticia, nunca identidad ni titularidad.

## Ejecución

```bash
python -m scripts.evaluate_validation_corpus --format markdown
python -m scripts.evaluate_validation_corpus --format json
python -m unittest discover -s tests -p test_validation_benchmark.py -v
```

GitHub Actions muestra la tabla en Job Summary. Las tasas de FP/FN
**no hacen fallar CI automáticamente**: es un conjunto deliberadamente
adversario y esos errores se muestran para orientar mejoras. Sí es un
error bloqueante que una regla textual vuelva a producir estado `OK`
como si acreditara la validez de un documento.

El corpus tiene versión `schema_version: 1`. La regla para extenderlo
es **nunca borrar casos que fallen solo para mejorar los porcentajes**.
Registrar de forma revisable la razón de una nueva etiqueta, ejecutarlo
antes y después del cambio de código y pedir revisión humana.

## Qué falta para un benchmark de precisión del producto

- Muestra externa representativa de tipos de documentos y convocatorias,
  obtenida con autorización y conforme a la política de privacidad.
- Guía de anotación y doble etiquetado ciego por personas especializadas,
  con resolución de desacuerdos.
- Conjuntos separados de desarrollo y evaluación, intervalos de confianza,
  tasa de rechazo `no evaluable` y métricas por tipo de documento.
- Evaluar **PDF completo**, OCR, calidad de extracción, firmas PAdES,
  contenido escaneado y documentos en otros idiomas.
- Validación de seguridad, RGPD y controles operativos de la
  [auditoría #10](https://github.com/arnaumarti-dev/tramitia-ai-document-review/issues/10).

**Prohibido** anunciar las métricas de estos 30 textos como precisión
comercial, probabilidad de concesión, autenticidad o validez legal.
