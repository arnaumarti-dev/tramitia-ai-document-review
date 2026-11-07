#!/usr/bin/env bash
# Real two-container sandbox probe; uses an invented sample PDF only.
set -euo pipefail

group=tramitia-split-ci-web
account=tramitia-split-ci-web
suffix="${GITHUB_RUN_ID:-manual}-${GITHUB_RUN_ATTEMPT:-1}"
broker="tramitia-split-broker-$suffix"
parser="tramitia-split-parser-$suffix"
broker_dir=$(mktemp -d)
parser_dir=$(mktemp -d)
stage_dir=$(mktemp -d)

cleanup() {
  docker rm -f "$broker" "$parser" >/dev/null 2>&1 || true
  sudo rm -rf "$broker_dir" "$parser_dir" "$stage_dir"
}
trap cleanup EXIT

sudo groupadd --system "$group"
sudo useradd --system --no-create-home --gid "$group" --shell /usr/sbin/nologin "$account"
gid=$(getent group "$group" | cut -d: -f3)
uid=$(id -u "$account")
test -n "$gid"
test "$uid" -ne 65534
test "$uid" -ne 65533

sudo chown "65534:$gid" "$broker_dir"
sudo chmod 0750 "$broker_dir"
sudo chown 65533:65534 "$parser_dir"
sudo chmod 0750 "$parser_dir"

if sudo -u "$account" test -w "$broker_dir"; then
  echo "SECURITY FAILURE: web can replace broker socket" >&2
  exit 1
fi

# Untrusted parser service: different UID and *no broker socket mount*.
docker run --detach --restart unless-stopped --name "$parser" \
  --network=none --read-only --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --pids-limit=32 --memory=1g --memory-swap=1g --cpus=1 \
  --user=65533:65533 --group-add 65534 \
  --tmpfs=/tmp:rw,noexec,nosuid,nodev,size=8m \
  --mount "type=bind,src=$parser_dir,dst=/run/tramitia-parser" \
  --env TRAMITIA_PDF_BROKER_DAEMONLESS=1 \
  --env TRAMITIA_PDF_WORKER_BACKEND=subprocess \
  --env TRAMITIA_PARSER_ONE_SHOT=1 \
  --env TRAMITIA_BROKER_SOCKET=/run/tramitia-parser/parser.sock \
  --env TRAMITIA_BROKER_SOCKET_GID=65534 \
  --env TRAMITIA_BROKER_ALLOWED_PEER_UID=65534 \
  tramitia-pdf-broker:ci

wait_for_socket() {
  local socket="$1" container="$2"
  for attempt in $(seq 1 35); do
    if sudo test -S "$socket"; then return 0; fi
    if [ "$(docker inspect -f '{{.State.Running}}' "$container")" != "true" ]; then
      docker logs "$container" || true
      echo "Container died before publishing socket: $container" >&2
      return 1
    fi
    sleep 1
  done
  docker logs "$container" || true
  echo "Socket not created: $socket" >&2
  return 1
}
wait_for_socket "$parser_dir/parser.sock" "$parser"
test "$(sudo stat -c %u "$parser_dir/parser.sock")" = 65533
test "$(sudo stat -c %g "$parser_dir/parser.sock")" = 65534
test "$(sudo stat -c %a "$parser_dir/parser.sock")" = 660

# Trusted broker has an *independently controlled* socket directory,
# while parser's socket is mounted read-only into the broker container.
docker run --detach --rm --name "$broker" \
  --network=none --read-only --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --pids-limit=32 --memory=512m --memory-swap=512m --cpus=1 \
  --user=65534:65534 --group-add "$gid" \
  --tmpfs=/tmp:rw,noexec,nosuid,nodev,size=8m \
  --mount "type=bind,src=$broker_dir,dst=/run/tramitia" \
  --mount "type=bind,src=$parser_dir,dst=/run/tramitia-parser,readonly" \
  --env TRAMITIA_PDF_BROKER_DAEMONLESS=1 \
  --env TRAMITIA_PDF_WORKER_BACKEND=parser_socket \
  --env TRAMITIA_BROKER_SOCKET=/run/tramitia/pdf-broker.sock \
  --env "TRAMITIA_BROKER_SOCKET_GID=$gid" \
  --env "TRAMITIA_BROKER_ALLOWED_PEER_UID=$uid" \
  --env TRAMITIA_PARSER_SOCKET=/run/tramitia-parser/parser.sock \
  --env TRAMITIA_PARSER_ALLOWED_PEER_UID=65533 \
  tramitia-pdf-broker:ci

wait_for_socket "$broker_dir/pdf-broker.sock" "$broker"
test "$(sudo stat -c %u "$broker_dir/pdf-broker.sock")" = 65534
test "$(sudo stat -c %g "$broker_dir/pdf-broker.sock")" = "$gid"
test "$(sudo stat -c %a "$broker_dir/pdf-broker.sock")" = 660

# A compromised parser lacks any mount of the broker's authority/socket.
docker exec "$parser" python -c '
import os, socket, errno
assert os.geteuid() == 65533
assert not os.path.exists("/run/tramitia/pdf-broker.sock")
assert not os.path.exists("/var/run/docker.sock")
for name in ("TRAMITIA_JWT_SECRET", "POSTGRES_PASSWORD", "DATABASE_URL", "DOCKER_HOST"):
    assert name not in os.environ
s = socket.socket(); s.settimeout(1)
assert s.connect_ex(("192.0.2.1", 443)) in (errno.ENETUNREACH, errno.EHOSTUNREACH)
print("Untrusted PDF parser has no broker socket, Docker, network or secrets")
'

# Broker cannot modify the parser's socket directory or read parser's memory.
docker exec "$broker" python -c '
import os, socket, struct
assert os.geteuid() == 65534
assert not os.path.exists("/var/run/docker.sock")
assert os.path.exists("/run/tramitia-parser/parser.sock")
assert os.path.exists("/run/tramitia/pdf-broker.sock")
try:
    open("/run/tramitia-parser/must-not-exist", "wb")
except (PermissionError, OSError):
    pass
else:
    raise AssertionError("Broker can write parser socket mount")
probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
probe.settimeout(3)
probe.connect("/run/tramitia-parser/parser.sock")
pid, uid, gid = struct.unpack("3i", probe.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
assert uid == 65533, (pid, uid, gid)
probe.close()
print("Broker sees a separate parser UID and read-only socket mount")
'

# Real web-account integration with a disposable invented PDF, no JWT/DB.
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
    TRAMITIA_BROKER_SOCKET="$broker_dir/pdf-broker.sock" \
    "$(command -v python)" -c '
from app.pdf_processing import parse_pdf_document
from scripts.generate_demo_pdf import make_demo_pdf
content, pages = parse_pdf_document(make_demo_pdf())
assert pages == 1 and "Solicitud ficticia" in content
print("Fictional PDF passed web -> broker -> distinct parser container")
'
)

# A successful PDF request must restart the isolated parser container.
for attempt in $(seq 1 45); do
  restarts=$(docker inspect -f '{{.RestartCount}}' "$parser")
  if [ "$restarts" -ge 1 ] && sudo test -S "$parser_dir/parser.sock"; then
    break
  fi
  sleep 1
done
test "$(docker inspect -f '{{.RestartCount}}' "$parser")" -ge 1
wait_for_socket "$parser_dir/parser.sock" "$parser"

# Process a second invented PDF using the restarted parser service.
(
  cd "$stage_dir"
  sudo -n setpriv --reuid="$(id -u "$account")" \
    --regid="$(id -g "$account")" --init-groups \
    env -i PATH="$PATH" PYTHONDONTWRITEBYTECODE=1 \
    TRAMITIA_PDF_WORKER_BACKEND=broker \
    TRAMITIA_BROKER_SOCKET="$broker_dir/pdf-broker.sock" \
    "$(command -v python)" -c '
from app.pdf_processing import parse_pdf_document
from scripts.generate_demo_pdf import make_demo_pdf
content, pages = parse_pdf_document(make_demo_pdf())
assert pages == 1 and "Solicitud ficticia" in content
print("Second fictional PDF processed after parser restart")
'
)

# Fail closed if parser service is removed by operator.
docker rm -f "$parser" >/dev/null
(
  cd "$stage_dir"
  sudo -n setpriv --reuid="$(id -u "$account")" \
    --regid="$(id -g "$account")" --init-groups \
    env -i PATH="$PATH" PYTHONDONTWRITEBYTECODE=1 \
    TRAMITIA_PDF_WORKER_BACKEND=broker \
    TRAMITIA_BROKER_SOCKET="$broker_dir/pdf-broker.sock" \
    "$(command -v python)" -c '
from app.pdf_processing import parse_pdf_document, PdfProcessingError
from scripts.generate_demo_pdf import make_demo_pdf
try:
    parse_pdf_document(make_demo_pdf())
except PdfProcessingError as err:
    assert err.status_code == 503, (err.status_code, str(err))
else:
    raise AssertionError("The broker silently parsed without the parser service")
print("Absent parser fails closed with HTTP 503")
'
)
echo "Split broker/parser containers and distinct filesystem authority: PASS"
