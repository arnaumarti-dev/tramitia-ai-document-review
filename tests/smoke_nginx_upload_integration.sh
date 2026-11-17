#!/usr/bin/env bash
# AUD-B02: real NGINX TLS -> TramitIA/Starlette multipart via localhost.
# Synthetic only; requires a disposable Linux Docker runner.
set -euo pipefail
if [[ "\${CI:-}" != "true" && "\${TRAMITIA_ALLOW_NGINX_SMOKE:-}" != "1" ]]; then
  echo "Explicit disposable test environment required" >&2
  exit 2
fi
test "$(findmnt -no FSTYPE -T /dev/shm)" = "tmpfs"
workdir=$(mktemp -d)
upload_dir=$(mktemp -d /dev/shm/tramitia-b02-proxy.XXXXXXXX)
chmod 0700 "$upload_dir"
container="tramitia-b02-nginx-\${GITHUB_RUN_ID:-local}-\${GITHUB_RUN_ATTEMPT:-1}"
server_pid=""
hold_pid=""
cleanup() {
  if [[ -n "$hold_pid" ]]; then kill "$hold_pid" 2>/dev/null || true; wait "$hold_pid" 2>/dev/null || true; fi
  if [[ -n "$server_pid" ]]; then kill "$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true; fi
  docker rm -f "$container" >/dev/null 2>&1 || true
  rm -rf -- "$workdir" "$upload_dir"
}
trap cleanup EXIT

mkdir -m 0700 "$workdir/tls"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -keyout "$workdir/tls/privkey.pem" -out "$workdir/tls/fullchain.pem" \
  -subj '/CN=tramitia.example.invalid' >/dev/null 2>&1
# Change only local test ports in a temporary copy, not tracked ingress.
sed -e 's/listen 80 default_server;/listen 18080 default_server;/' \
    -e 's/listen 80;/listen 18080;/' \
    -e 's/listen 443 ssl default_server;/listen 18443 ssl default_server;/' \
    -e 's/listen 443 ssl;/listen 18443 ssl;/' \
    -e 's@proxy_pass http://127.0.0.1:8000;@proxy_pass http://127.0.0.1:18081;@g' \
    deploy/nginx/nginx.conf > "$workdir/nginx.conf"
nginx_uid=$(docker run --rm --entrypoint sh nginx:1.27-alpine -c 'id -u nginx')
nginx_gid=$(docker run --rm --entrypoint sh nginx:1.27-alpine -c 'id -g nginx')
[[ "$nginx_uid" =~ ^[0-9]+$ && "$nginx_gid" =~ ^[0-9]+$ ]]
docker run --detach --rm --name "$container" --network host \
  --tmpfs "/run/tramitia-nginx-body:rw,noexec,nosuid,nodev,size=32m,mode=0700,uid=$nginx_uid,gid=$nginx_gid" \
  --mount "type=bind,src=$workdir/nginx.conf,dst=/etc/nginx/nginx.conf,readonly" \
  --mount "type=bind,src=$workdir/tls,dst=/etc/nginx/tls,readonly" \
  nginx:1.27-alpine >/dev/null
docker exec "$container" nginx -t
docker exec "$container" sh -c '
  test "$(stat -c %a /run/tramitia-nginx-body)" = 700
  test "$(stat -c %u /run/tramitia-nginx-body)" = "$(id -u nginx)"
  test "$(stat -c %g /run/tramitia-nginx-body)" = "$(id -g nginx)"
  awk '"'"'$2=="/run/tramitia-nginx-body" && $3=="tmpfs" { found=1 } END { exit !found }'"'"' /proc/mounts
'

start_backend() {
  TMPDIR="$upload_dir" PYTHONPATH="$PWD" TRAMITIA_DEPLOYMENT_PROFILE=development \
    TRAMITIA_NGINX_UPLOAD_SMOKE=1 PYTHONDONTWRITEBYTECODE=1 \
    python -u tests/b02_nginx_test_server.py >"$workdir/backend.log" 2>&1 &
  server_pid=$!
  for attempt in $(seq 1 80); do
    if curl --silent --fail --max-time 1 http://127.0.0.1:18081/health >/dev/null; then return 0; fi
    if ! kill -0 "$server_pid" 2>/dev/null; then cat "$workdir/backend.log" >&2; return 1; fi
    sleep 0.25
  done
  echo "Synthetic FastAPI fixture failed to become healthy" >&2
  cat "$workdir/backend.log" >&2
  return 1
}
check_clean() {
  test -z "$(find "$upload_dir" -mindepth 1 -print -quit)"
  docker exec "$container" sh -c 'test -z "$(ls -A /run/tramitia-nginx-body)"'
}
curl_proxy() {
  curl --silent --show-error --insecure --noproxy '*' \
    --resolve 'tramitia.example.invalid:18443:127.0.0.1' --max-time 50 "$@"
}
python - "$workdir" <<'PY'
from pathlib import Path
import sys
folder = Path(sys.argv[1])
for name, size in [("twenty.pdf", 20 * 1024 * 1024),
                   ("oversized.pdf", 21 * 1024 * 1024 + 4096)]:
    with (folder / name).open("wb") as file:
        file.write(b"%PDF-1.7\n")
        file.truncate(size)
PY
start_backend
test "$(curl_proxy -o "$workdir/health.json" -w '%{http_code}' https://tramitia.example.invalid:18443/health)" = 200
code=$(curl_proxy -o "$workdir/accepted.json" -w '%{http_code}' \
  -F "file=@$workdir/twenty.pdf;filename=synthetic.pdf;type=application/pdf" \
  https://tramitia.example.invalid:18443/analyze)
if [[ "$code" != 200 ]]; then echo "20 MiB upload got $code" >&2; cat "$workdir/accepted.json" >&2; exit 1; fi
python - "$workdir/accepted.json" <<'PY'
import json, sys
with open(sys.argv[1]) as f: body=json.load(f)
assert body["id"] == 9001 and body["filename"] == "synthetic.pdf", body
PY
check_clean
echo "NGINX -> FastAPI multipart 20 MiB: PASS"
code=$(curl_proxy -o "$workdir/rejected.json" -w '%{http_code}' \
  -F "file=@$workdir/oversized.pdf;filename=oversized.pdf;type=application/pdf" \
  https://tramitia.example.invalid:18443/analyze)
test "$code" = 413 || { echo "Expected 413; got $code" >&2; exit 1; }
check_clean
echo "NGINX >21 MiB rejection: PASS"

# Send 5 MiB of a deliberately incomplete multipart body over real TLS.
# In hold mode the client stays alive to let the test inspect an active
# anonymous tempfile descriptor before killing FastAPI with SIGKILL.
partial_upload() {
  python - "$1" <<'PY'
import socket, ssl, sys, time
mode=sys.argv[1]
boundary=b"synthetic-test-boundary"
opening=(b"--"+boundary+b"\r\n"
         b'Content-Disposition: form-data; name="file"; filename="partial.pdf"\r\n'
         b"Content-Type: application/pdf\r\n\r\n%PDF-1.7\n")
header=(b"POST /analyze HTTP/1.1\r\nHost: tramitia.example.invalid\r\n"
        b"Content-Type: multipart/form-data; boundary="+boundary+b"\r\n"
        b"Content-Length: 20971520\r\nConnection: close\r\n\r\n")
ctx=ssl._create_unverified_context()
with socket.create_connection(("127.0.0.1",18443),timeout=10) as sock:
    with ctx.wrap_socket(sock,server_hostname="tramitia.example.invalid") as tls:
        tls.settimeout(20)
        tls.sendall(header+opening)
        for _ in range(20): tls.sendall(b"X"*(256*1024))
        print("PARTIAL_SENT",flush=True)
        if mode=="hold": time.sleep(35)
PY
}
partial_upload abort >"$workdir/aborted.log"
sleep 1
check_clean
test "$(curl_proxy -o /dev/null -w '%{http_code}' https://tramitia.example.invalid:18443/health)" = 200
echo "Disconnected multipart: no named residual files: PASS"

partial_upload hold >"$workdir/held.log" 2>&1 &
hold_pid=$!
for attempt in $(seq 1 100); do
  if grep -q PARTIAL_SENT "$workdir/held.log"; then break; fi
  if ! kill -0 "$hold_pid" 2>/dev/null; then cat "$workdir/held.log" >&2; exit 1; fi
  sleep 0.1
done
grep -q PARTIAL_SENT "$workdir/held.log"
seen=0
for attempt in $(seq 1 60); do
  for fd in "/proc/$server_pid/fd/"*; do
    target=$(readlink "$fd" 2>/dev/null || true)
    if [[ "$target" == "$upload_dir/"* ]]; then seen=1; break; fi
  done
  if [[ "$seen" = 1 ]]; then break; fi
  sleep 0.2
done
test "$seen" = 1 || { echo "Active FastAPI spool was not observed" >&2; cat "$workdir/backend.log" >&2; exit 1; }
kill -KILL "$server_pid"
wait "$server_pid" 2>/dev/null || true
server_pid=""
kill "$hold_pid" 2>/dev/null || true
wait "$hold_pid" 2>/dev/null || true
hold_pid=""
sleep 1
check_clean
start_backend
code=$(curl_proxy -o "$workdir/recovered.json" -w '%{http_code}' \
  -F "file=@$workdir/twenty.pdf;filename=recovered.pdf;type=application/pdf" \
  https://tramitia.example.invalid:18443/analyze)
test "$code" = 200 || { echo "Post-SIGKILL 20 MiB upload got $code" >&2; exit 1; }
check_clean
echo "Active spool, SIGKILL, restart and successful 20 MiB retry: PASS"
