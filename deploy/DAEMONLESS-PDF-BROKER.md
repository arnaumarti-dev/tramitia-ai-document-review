# Daemonless PDF broker: reference Linux architecture

This is a **development/reference deployment**, not an approval to process
real customer records. It removes the Docker control socket from **both**
FastAPI and the running PDF broker. Only a trusted host operator uses
Docker to *start* the broker container. No broker process invokes Docker
for each incoming PDF.

```text
TLS NGINX / trusted ingress
           |
   non-root FastAPI (NO Docker permissions)
           |
   /run/tramitia/pdf-broker.sock (private Unix socket, 0660)
           |
   prestarted, restricted PDF broker container (NO Docker socket/network)
           |
   resource-capped python -I child for each PDF (no JWT/DB secrets)
```

The broker container is restarted and constrained by the operator's
container runtime. Internally it uses the same worker subprocess limits
as the development host mode (CPU and virtual-memory limits, 15-second
wall timeout) with a **single active job**. The whole container also has a
1 GiB memory budget, one CPU, restricted PIDs, no network, a read-only
root filesystem, no capabilities, no-new-privileges and only the private
Unix socket directory as a writable host mount. No TCP port is exposed.

## Host prerequisites

- Linux with systemd, Docker/Compose available **only to trusted operators**,
  a separately secured PostgreSQL database, a TLS ingress and the FastAPI
  virtual environment at `/opt/tramitia/.venv`.
- A non-root account `tramitia-web`, group `tramitia-web`; **not a member**
  of the `docker` group.
- A host bind directory `/run/tramitia` owned by UID 65534 and group
  `tramitia-web`, mode `0770`. It must **not be world writable**. No other
  host directory is mounted into the broker.
- Bind FastAPI only to `127.0.0.1:8000`, disable Uvicorn proxy headers,
  and configure `TRAMITIA_DEPLOYMENT_PROFILE=isolated`.

An operator may configure the runtime directory using a vetted
`systemd-tmpfiles` entry:

```bash
getent group tramitia-web
# Inspect existing accounts and group membership before creating anything.
echo 'd /run/tramitia 0770 65534 tramitia-web -' | \
  sudo tee /etc/tmpfiles.d/tramitia-pdf-socket.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/tramitia-pdf-socket.conf
stat -c '%a %u %g' /run/tramitia
```

## Build/start the broker (operator only)

```bash
export TRAMITIA_WEB_GID="$(getent group tramitia-web | cut -d: -f3)"
# Review Dockerfile.pdf-broker and deploy/pdf-broker.compose.yml first.
docker compose -f deploy/pdf-broker.compose.yml config --quiet
docker compose -f deploy/pdf-broker.compose.yml build
docker compose -f deploy/pdf-broker.compose.yml up -d
test -S /run/tramitia/pdf-broker.sock
stat -c '%a %u %g' /run/tramitia/pdf-broker.sock
```

The deployment uses a numeric supplemental group, allowing the broker's
UID 65534 to create a socket readable/writable by the web group. The
broker itself does **not** receive the Docker socket, Docker CLI, secrets,
PostgreSQL credentials or application source files.

Start FastAPI as a separate OS account with
`deploy/systemd/tramitia-web-daemonless.service` (after reviewing/copying
the unit into the systemd path) and `/etc/tramitia/web.env` holding the
web-only database and JWT credentials. **Do not use**
`tramitia-pdf-broker.service` simultaneously with this mode: that older
unit launches an operator-privileged broker which accesses the Docker
daemon. Start the container **before** FastAPI. The isolated startup guard
will refuse to start FastAPI if its private broker socket is unavailable.
If the container stops later, uploads fail closed with HTTP 503.

## Verify and limitations

The CI workflow builds this container and checks, using a disposable
fictional PDF and a **different Docker-denied web account**, that the
broker can parse documents over the private socket. It also verifies
the container cannot access an external network or the Docker socket.

**Important security limitations:**

- The broker and its PDF-parsing subprocess have the same container UID
  and share the broker socket mount. A parser compromise could disrupt
  the broker and attempt socket misuse; this is **not equivalent to a
  separate microVM per uploaded PDF**. The container's limits and lack
  of secrets/network reduce impact, but do not eliminate host kernel
  or denial-of-service risks.
- A trusted operator/rootful Docker daemon still controls the host at
  deployment time. This implementation removes access from runtime
  application processes, not from the host administrator or Docker itself.
- The socket protocol is local-only and has no cross-host authentication;
  do not expose it via TCP or mount it to unrelated containers.
- The worker supervisor now reads **at most 3,000,001 stdout bytes**
  (limit + 1) while the process is executing. Output overflow or a 15-second
  end-to-end timeout kills and reaps the worker process; a separate feeder
  prevents pipe deadlocks. These checks reduce output-flood risks but are
  not a substitute for the container's 1 GiB limit, kernel protections,
  and per-document sandboxing.
- Review image signatures/digests, runtime security policies, host
  permissions, backups, ingress, customer consent, PDF retention and GDPR
  obligations before treating real client material.

Prefer a **separate VM/host and stronger per-document isolation** for a
production document-processing service. The included Compose and systemd
files are not production certification.
