# TramitIA — prueba completa local (sin documentos reales)

La prueba automática de GitHub Actions ya recorre la aplicación en un navegador Chromium real, con PostgreSQL y documentos completamente ficticios. Esta guía permite repetir el recorrido **manualmente en Windows** para revisar también la experiencia visual.

**No utilices archivos de clientes ni credenciales reales.** Este proyecto sigue siendo un MVP de desarrollo, sin despliegue endurecido ni medidas completas de privacidad. Usa una base de datos independiente.

## 1. Preparar el entorno

En PowerShell, desde la carpeta del repositorio (una vez descargada la rama con los cambios):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
docker compose -p tramitia-demo up -d
$env:TRAMITIA_JWT_SECRET = python -c "import secrets; print(secrets.token_hex(32))"
alembic upgrade head
```

Se requiere Python, Docker Desktop y un puerto **5432** libre. La contraseña del PostgreSQL incluido en Docker es **solo para desarrollo** y no debe usarse en un servidor accesible por Internet. El archivo `docker-compose.yml` puede usar el puerto 5432 existente; si ya tienes otro PostgreSQL, ajusta el puerto y `POSTGRES_PORT` antes de continuar.

## 2. Crear una gestoría ficticia

```powershell
python -m scripts.bootstrap_admin --slug demo-gestoria --name "Gestoría de Pruebas" --email propietario@example.invalid
```

El programa pide una contraseña ficticia de 12 o más caracteres. No uses una contraseña personal habitual.

## 3. Crear un PDF de prueba

```powershell
python -m scripts.generate_demo_pdf --output solicitud-ficticia.pdf
```

Genera un PDF **inventado** que contiene texto reconocible por el analizador. El comando no sobrescribe ningún archivo existente.

## 4. Iniciar TramitIA

```powershell
uvicorn app.main:app --reload
```

Abre [http://127.0.0.1:8000/](http://127.0.0.1:8000/) y entra con el identificador `demo-gestoria`, correo `propietario@example.invalid` y la contraseña creada.

## 5. Probar invitaciones y documentos

1. En **Equipo de la gestoría**, añade `empleado@example.invalid` con rol **Empleado**.
2. Copia el enlace de invitación que aparece. Ábrelo en una ventana de incógnito y crea una contraseña ficticia diferente.
3. Inicia sesión como empleado, usando `demo-gestoria` y `empleado@example.invalid`.
4. Sube `solicitud-ficticia.pdf` y comprueba que aparece un resultado, con puntuación y observaciones.
5. Abre el historial, pulsa sobre el informe y descarga su JSON.
6. Regresa a la sesión de propietario y actualiza el historial: ambos usuarios deberían ver el análisis de su gestoría.
7. Cambia la contraseña del empleado. La aplicación debe cerrar la sesión y permitir iniciar otra con la nueva contraseña. La antigua debe dejar de funcionar.

Para comprobar aislamiento entre gestorías, crea una **segunda gestoría ficticia** con un correo distinto y comprueba que no aparece el documento de la primera.

## Limpieza

Detén Uvicorn con **Ctrl+C**. Para detener la base de datos de pruebas:

```powershell
docker compose -p tramitia-demo down
```

**No utilices `down -v` si deseas conservar esta base de datos de pruebas**: elimina sus volúmenes y todos los datos almacenados.

## Comprobaciones automatizadas

La PR de prueba añade `tests/browser_journey.py` a GitHub Actions. El workflow arranca PostgreSQL efímero, aplica migraciones, levanta FastAPI, abre Chromium, crea dos organizaciones, prueba invitaciones, PDFs, historial, exportaciones y separación por gestoría, y limpia los registros. Para pruebas locales con un navegador real, consulta los pasos del workflow `.github/workflows/tests.yml`.

**Superar estas pruebas no implica que el producto esté listo para producción**. Sigue pendiente una revisión de seguridad, protección ante PDFs maliciosos, gestión de recuperación de cuentas, TLS/HTTPS, copias de seguridad y medidas RGPD.
