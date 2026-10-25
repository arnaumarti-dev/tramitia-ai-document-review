#!/usr/bin/env bash
# Disposable integration probe. The broker is an already-started Docker
# container with NO Docker socket, no network, no DB and no JWT secrets.
# A separate unprivileged host account sends an actual fictional PDF.
set -euo pipefail

group=tramitia-smoke-web
account=tramitia-smoke-web
container="tramitia-daemonless-ci-${GITHUB_RUN_ID:-manual}-${GITHUB_RUN_ATTEMPT:-1}"
socket_dir=$(mktemp -d)
stage_dir=$(mktemp -d)

cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  sudo rm -rf "$socket_dir" "$stage_dir"
}
trap cleanup EXIT

sudo groupadd --system "$group"
sudo useradd --system --no-create-home --gid "$group" \
  --shell /usr/sbin/nologin "$account"
gid=$(getent group "$group" | cut -d: -f3)
test -n "$gid"

# The dedicated broker can bind the socket, and only the web group can
# traverse the shared Unix-socket directory.
sudo chown "65534:$gid" "$socket_dir"
sudo chmod 0770 "$socket_dir"

docker run --detach --rm --name "$container" \
  --network=none --read-only --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --pids-limit=32 --memory=1g --memory-swap=1g --cpus=1 \
  --user=65534:65534 --group-add "$gid" \
  --tmpfs=/tmp:rw,noexec,nosuid,nodev,size=8m \
  --mount "type=bind,src=$socket_dir,dst=/run/tramitia" \
  --env TRAMITIA_PDF_BROKER_DAEMONLESS=1 \
  --env TRAMITIA_PDF_WORKER_BACKEND=subprocess \
  --env TRAMITIA_BROKER_SOCKET=/run/tramitia/pdf-broker.sock \
  --env "TRAMITIA_BROKER_SOCKET_GID=$gid" \
  tramitia-pdf-broker:ci

broker_socket="$socket_dir/pdf-broker.sock"
for attempt in $(seq 1 35); do
  if sudo test -S "$broker_socket"; then
    break
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$container")" != "true" ]; then
    docker logs "$container" || true
    echo "Daemonless broker exited unexpectedly" >&2
    exit 1
  fi
  sleep 1
done
if ! sudo test -S "$broker_socket"; then
  echo "Daemonless broker did not publish its Unix socket" >&2
  sudo stat -c '%A %u:%g %n' "$socket_dir" || true
  sudo ls -la "$socket_dir" || true
  docker inspect -f 'status={{.State.Status}} mounts={{json .Mounts}}' "$container" || true
  docker exec "$container" sh -c '
    id
    ls -lda /run/tramitia || true
    ls -la /run/tramitia || true
    ps aux || true
    test -S /run/tramitia/pdf-broker.sock && echo IN_CONTAINER_SOCKET_PRESENT
  ' || true
  docker logs "$container" || true
  exit 1
fi
test "$(sudo stat -c %a "$broker_socket")" = 660
test "$(sudo stat -c %u "$broker_socket")" = 65534
test "$(sudo stat -c %g "$broker_socket")" = "$gid"

# Validate its Linux container boundary *from inside the running broker*.
docker exec "$container" python -c '
import errno, os, socket
assert os.geteuid() == 65534
assert not os.path.exists("/var/run/docker.sock")
assert "TRAMITIA_JWT_SECRET" not in os.environ
assert "POSTGRES_PASSWORD" not in os.environ
probe = socket.socket()
probe.settimeout(1)
code = probe.connect_ex(("192.0.2.1", 443))
assert code in (errno.ENETUNREACH, errno.EHOSTUNREACH), code
print("Daemonless broker isolation verified")
'

if sudo -u "$account" test -r /var/run/docker.sock; then
  echo "SECURITY FAILURE: unprivileged API account can read Docker" >&2
  exit 1
fi

cp -a app scripts "$stage_dir/"
sudo chown -R root:root "$stage_dir"
sudo chmod -R u=rwX,go=rX "$stage_dir"
sudo chmod 0755 "$stage_dir"
(
  cd "$stage_dir"
  sudo -n setpriv --reuid="$(id -u "$account")" \
    --regid="$(id -g "$account")" --init-groups \
    env -i PATH="$PATH" PYTHONDONTWRITEBYTECODE=1 \
    TRAMITIA_PDF_WORKER_BACKEND=broker \
    TRAMITIA_BROKER_SOCKET="$broker_socket" \
    "$(command -v python)" -c '
from app.pdf_processing import parse_pdf_document
from scripts.generate_demo_pdf import make_demo_pdf
text, pages = parse_pdf_document(make_demo_pdf())
assert pages == 1 and "Solicitud ficticia" in text, (pages, text[:80])
print("PDF received by isolated daemonless broker and processed")
'
)

echo "Daemonless PDF broker and separate web OS identity: PASS"
