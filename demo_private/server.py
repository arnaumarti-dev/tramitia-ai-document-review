"""Password-protected read-only TramitIA preview; no PDF ingestion or database.

This is deliberately NOT app.main. Nothing here accepts document uploads.
Run only behind Render HTTPS with a strong unique environment password.
"""
import base64
import binascii
import hmac
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ASSET_DIR = Path(__file__).resolve().parent
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/demo.css": ("demo.css", "text/css; charset=utf-8"),
    "/demo.js": ("demo.js", "text/javascript; charset=utf-8"),
}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


class DemoHandler(BaseHTTPRequestHandler):
    # HTTP/1.0 closes after each response; avoids any persistent request-body
    # handling on this deliberately simple static service.
    server_version = "TramitIA-Preview"
    sys_version = ""

    def log_message(self, fmt, *args):
        # Do not log Authorization headers or arbitrary paths/query strings.
        return

    def _write(self, status, payload=b"", content_type="text/plain; charset=utf-8",
               head_only=False, challenge=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")
        self.send_header("Content-Security-Policy", CSP)
        if challenge:
            self.send_header("WWW-Authenticate", 'Basic realm="TramitIA Demo", charset="UTF-8"')
        self.end_headers()
        if not head_only and payload:
            self.wfile.write(payload)

    def _authorized(self):
        value = self.headers.get("Authorization", "")
        if not value.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(value[6:], validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return False
        username, sep, password = decoded.partition(":")
        if not sep:
            return False
        return (
            hmac.compare_digest(username, self.server.demo_username)
            and hmac.compare_digest(password, self.server.demo_password)
        )

    def _respond(self, head_only=False):
        route = urlsplit(self.path).path
        # Render can check health without a login, without exposing app data.
        if route == "/health":
            return self._write(200, b'{"status":"ok","mode":"fictional-demo"}',
                               "application/json; charset=utf-8", head_only)
        if not self._authorized():
            return self._write(401, b"Authentication required.",
                               head_only=head_only, challenge=True)
        asset = ASSETS.get(route)
        if asset is None:
            return self._write(404, b"Not found.", head_only=head_only)
        filename, content_type = asset
        return self._write(200, (ASSET_DIR / filename).read_bytes(),
                           content_type, head_only)

    def do_GET(self):
        self._respond()

    def do_HEAD(self):
        self._respond(head_only=True)

    def _deny_mutation(self):
        if not self._authorized():
            return self._write(401, b"Authentication required.", challenge=True)
        self._write(405, b"Read-only fictional demo: writes and uploads disabled.")

    def do_POST(self):
        self._deny_mutation()

    def do_PUT(self):
        self._deny_mutation()

    def do_PATCH(self):
        self._deny_mutation()

    def do_DELETE(self):
        self._deny_mutation()


def build_server(host="0.0.0.0", port=10000, *, password=None, username="demo"):
    secret = password if password is not None else os.environ.get("TRAMITIA_DEMO_PASSWORD", "")
    if not isinstance(secret, str) or len(secret) < 32:
        raise RuntimeError("TRAMITIA_DEMO_PASSWORD must be a unique 32+ character secret")
    if not isinstance(username, str) or not username or ":" in username:
        raise RuntimeError("Invalid demo username")
    server = ThreadingHTTPServer((host, int(port)), DemoHandler)
    server.demo_password = secret
    server.demo_username = username
    return server


def main():
    server = build_server(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "10000")),
        username=os.environ.get("TRAMITIA_DEMO_USER", "demo"),
    )
    print("TramitIA fictional preview listening (password required; uploads disabled)", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
