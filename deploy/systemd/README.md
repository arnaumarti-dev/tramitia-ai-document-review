# Linux service split: deployment reference, NOT production approval

This reference separates an internet-facing **FastAPI web account** from a
**PDF broker account** controlling Docker. The broker retains very powerful
privileges through the Docker socket. Even if the FastAPI account cannot
access it directly, a vulnerability in the broker could compromise the host.
For real customer documents, **prefer a separate broker VM/host and isolated
runtime** with an explicit reviewed IPC boundary instead of installing both
units on a shared public web host.

## Preconditions

- Linux server with systemd, a trusted local Docker daemon, NGINX, Python
  virtual environment at `/opt/tramitia/.venv` and root-owned application
  code at `/opt/tramitia`.
- Image built **ahead of time**:
  `docker build -f Dockerfile.pdf-worker -t tramitia-pdf-worker:local .`.
  Do not allow the service to build, pull or choose arbitrary images.
- Review and pin the base image and dependencies by digest, and review Docker
  daemon security, AppArmor/SELinux/seccomp, logs, retention and GDPR.
- An HTTPS proxy directly in front of FastAPI. Uvicorn listens **only on
  127.0.0.1:8000** and never trusts forwarded headers itself.
- PostgreSQL is separately secured and migrated (`alembic upgrade head`)
  with a backup/rollback plan; do not use the development database secrets.

## Identities

An administrator can create the required service accounts like this
(adapt to the distribution; do not run without reviewing existing identities):

```bash
sudo groupadd --system tramitia-web
sudo useradd --system --no-create-home --shell /usr/sbin/nologin \
    --gid tramitia-web tramitia-web
sudo useradd --system --no-create-home --shell /usr/sbin/nologin \
    --gid tramitia-web tramitia-broker

# IMPORTANT: never add the web user to docker. The broker unit adds
# SupplementaryGroups=docker only to its own service identity.
id -nG tramitia-web
getent group docker
```

The broker service owns `/run/tramitia`, mode `0750`, with group
`tramitia-web`; it creates a `0660` Unix socket with the same group.
Only the web account should have permission to reach this socket.
The broker must **never** be given JWT secrets or PostgreSQL credentials.

## Secrets and units

Create `/etc/tramitia/web.env` with administrator-owned permissions
`0600` and actual database/JWT credentials. Example *keys only*:

```ini
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=tramitia
POSTGRES_USER=tramitia
POSTGRES_PASSWORD=REPLACE_WITH_SECRET
TRAMITIA_JWT_SECRET=REPLACE_WITH_64_PLUS_RANDOM_HEX_CHARACTERS
TRAMITIA_TRUSTED_PROXY_IPS=127.0.0.1
```

Never commit secrets, put them into URLs, or pass them to the broker.
Install the two unit files under `/etc/systemd/system/` after validating
`WorkingDirectory`, paths, privileges and Linux hardening directives.
Review `systemd-analyze verify` before `systemctl enable`.
Start the broker first, then the web service.

```bash
sudo install -d -m 0700 /etc/tramitia
sudo chmod 0600 /etc/tramitia/web.env
sudo install -m 0644 deploy/systemd/tramitia-pdf-broker.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/tramitia-web.service /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/tramitia-pdf-broker.service /etc/systemd/system/tramitia-web.service
sudo systemctl daemon-reload
sudo systemctl start tramitia-pdf-broker.service
sudo systemctl start tramitia-web.service
```

The web unit explicitly enables `TRAMITIA_DEPLOYMENT_PROFILE=isolated`,
which refuses to start if running as root, using the subprocess/container
backend directly, accessing the local Docker socket or missing a valid
private broker socket. If the broker goes away later, uploads fail closed
with 503. **This startup check catches accidents; it is not a sandbox.**

Review permission checks under both service accounts and verify that
`tramitia-web` cannot read or write `/run/docker.sock` even if its
supplementary groups change. Restrict SSH/local users and other containers
from accessing the web app's loopback port (containers with host networking
and local processes can bypass intended proxy trust).

## Remaining production blockers

- Broker controls a rootful Docker daemon. Use a dedicated host/VM or a
  narrowly scoped rootless executor and remote broker transport designed
  for that topology; the included broker protocol is **Unix socket only**.
- Validate ingress real-IP chain end-to-end, connection quotas, WAF/DDoS
  protection, certificate rotation, and firewalling.
- Secure per-customer document retention/deletion, database backups,
  encryption, structured audit logs, operational metrics and GDPR review.
- Obtain a security review and perform deployment testing with fictional
  PDFs before accepting real records.

The CI workflow tests the Unix broker separation with two identities on
a disposable runner; it does **not** validate a real Linux installation or
prove suitability for production.
