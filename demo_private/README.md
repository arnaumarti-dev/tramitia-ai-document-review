# TramitIA — vista previa online privada (datos ficticios)

Esta es una demo autónoma, sin acceso al backend real, PostgreSQL, broker
ni analizador PDF. No recibe archivos de usuarios, no crea cuentas, no guarda
datos, y las interacciones se simulan en el navegador. Muestra de forma
orientativa la experiencia de TramitIA; **NO valida documentos**.

## Acceso
El servidor solicita HTTP Basic Auth con usuario \`demo\` y una contraseña
aleatoria de 32+ caracteres. Solo usar HTTPS (Render), nunca exponer el puerto
local al exterior sin TLS. La contraseña va en la variable de entorno
\`TRAMITIA_DEMO_PASSWORD\` del servicio Render y **no** en GitHub.

## Configuración Render
- Crear un servicio web **nuevo**, runtime Python, plan gratuito,
  región Frankfurt y rama \`security/isolated-pdf-worker-20261025\`.
- Comando de build: \`python -m unittest discover -s tests -p test_demo_private.py -v\`.
- Comando de inicio: \`python -m demo_private.server\`.
- Variables: \`TRAMITIA_DEMO_PASSWORD\` = secreto aleatorio y
  \`TRAMITIA_DEMO_USER=demo\`.
- Auto-deploy desactivado. No configurar PostgreSQL, Docker o secretos de
  producción. No reutilizar el servicio de Churn.
- \`/health\` devuelve únicamente el estado de disponibilidad sin login;
  los demás recursos exigen contraseña y envían no-store/noindex/CSP.
- El servicio es públicamente enrutable en Render, **pero protegido por
  contraseña**. No equivale a una red privada o VPN. Usar ventana privada
  para terminar la sesión de HTTP Basic Auth.
- No compartir la contraseña públicamente ni introducir PDFs de clientes.

## Local

\`\`\`bash
export TRAMITIA_DEMO_PASSWORD='solo-pruebas-contraseña-aleatoria-32-o-mas'
python -m demo_private.server
\`\`\`

Para probar el servidor con tests de librería estándar:
\`python -m unittest discover -s tests -p test_demo_private.py -v\`.

Se sirve el HTML/CSS/JS locales con datos inventados. Cualquier petición POST
(incluida subida de PDFs) responde HTTP 405. El backend real no se importa ni
ejecuta. El estado de la PR #8 de seguridad se mantiene en borrador.
