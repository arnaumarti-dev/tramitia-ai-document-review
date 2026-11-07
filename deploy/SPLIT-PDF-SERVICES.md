# Split daemonless PDF services — experimental security milestone

**Not production approved.** This design places the trusted broker and
untrusted parser in **different** prestarted Docker containers, non-root
identities and mount namespaces. They have no Docker socket, network, JWT,
database credentials or public ports. Use **only** fictional documents for
tests until the design has independent review and a production threat model.

```text
authenticated FastAPI (dedicated host web UID)
    |
    | /run/tramitia/pdf-broker.sock, host mode 0660
    v
broker container UID 65534 (NO worker subprocess)
    |   broker socket directory is writable only by UID 65534
    |
    | /run/tramitia-parser/parser.sock (read-only bind mount)
    | Linux SO_PEERCRED must identify peer UID 65533
    v
parser container UID 65533
    |  NO /run/tramitia broker socket mount
    |  NO web/API credentials, Docker socket or network
    +-- isolated Python subprocess pypdf worker (same parser-container UID)
```

The broker and parser each enforce Linux `PR_SET_DUMPABLE=0` on their
**service** process before binding their own socket. Every socket server
authenticates its caller's kernel-supplied UID. A web request has exactly
one broker hop; the broker sends it only to the separate parser socket.
The parser subprocess has no path to the privileged broker socket.

## Operator setup, Linux only

The operator, not either service, prepares these two distinct paths:

```bash
# Dedicated web user and group must be provisioned independently.
export TRAMITIA_WEB_UID="$(id -u tramitia-web)"
export TRAMITIA_WEB_GID="$(getent group tramitia-web | cut -d: -f3)"
test "$TRAMITIA_WEB_UID" -ne 65534
test "$TRAMITIA_WEB_UID" -ne 65533

sudo install -d -o 65534 -g "$TRAMITIA_WEB_GID" -m 0750 /run/tramitia
sudo install -d -o 65533 -g 65534 -m 0750 /run/tramitia-parser

docker compose -f deploy/split-pdf-services.compose.yml config --quiet
docker compose -f deploy/split-pdf-services.compose.yml build
docker compose -f deploy/split-pdf-services.compose.yml up -d
sudo stat -c '%a %u:%g %n' /run/tramitia/{pdf-broker.sock,}
sudo stat -c '%a %u:%g %n' /run/tramitia-parser/parser.sock
```

The commands are **development references**. In production, a robust
`systemd-tmpfiles` specification, socket cleanup, restart sequencing,
monitoring, kernel hardening, userns mapping and secure host placement need
explicit verification. Never mount either socket into unrelated containers.

The parser's socket directory is mounted **read-only** into the broker
container and not at all into the API. Only the web user's group can
connect to the broker socket; the web UID must differ from 65534. The
parser container has no bind mount of the broker socket or any other host
directories. Both use `network_mode: none` and drop all capabilities.
`TRAMITIA_PARSER_ALLOWED_PEER_UID=65533` rejects a socket responder
with the wrong UID; parser enforces caller UID 65534. If the parser is
unavailable, requests must fail closed with HTTP 503, never fall back
to local subprocess parsing in the broker.

## Tests and residual risks

The CI script `tests/smoke_split_pdf_services.sh` validates real Unix
permissions and peer UIDs, isolation of mounts, lack of Docker/network/
secrets, successful parsing of a fictional PDF, and fail-closed behavior
after removing the parser container.

**Important limitations before real client data:**

- The parser subprocess still shares its *parser service* UID and can
  potentially tamper with its parser socket. It cannot use the trusted
  broker UID or reach the broker socket mount, but a compromised parser
  might persist and manipulate subsequent document results.
- The experimental parser uses `TRAMITIA_PARSER_ONE_SHOT=1`, exiting
  after one authenticated request. Docker `restart: unless-stopped`
  starts a fresh parser service process for the next request. This is
  **a restart of the same container**, not a brand-new disposable container
  or VM per PDF. Socket availability is transient during restart (503 is
  possible), and restart latency and crash-loop behavior need monitoring.
  Resource ceilings, no network and minimal mounts mitigate but do not
  eliminate malicious PDF exploitation or kernel/container breakout risks.
  Per-PDF disposable sandboxes/VMs, image review/signing and independent
  security review remain necessary before production.
- Linux `SO_PEERCRED` UID values depend on user namespace mappings.
  Verify against the actual host runtime (no assumption that every
  rootless Docker/userns configuration has identical mapping).
- Any host root/Docker operator can still access the volumes.
- Operational controls for encrypted backup, HTTPS, log retention,
  access monitoring, customer consent, GDPR and incident response
  are outside these tests.

The **Render demo** is completely separate, uses fake data only and
must not be configured to use either of these PDF services.
