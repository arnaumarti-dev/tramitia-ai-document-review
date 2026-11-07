"""Read-only private preview security checks (no app or DB dependency)."""
import base64
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from demo_private.server import build_server

SECRET = "unit-test-fake-private-demo-password-12345"


class PrivateDemoSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = build_server("127.0.0.1", 0, password=SECRET)
        cls.base = "http://127.0.0.1:" + str(cls.server.server_port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self, path, user=None, password=None, method="GET", data=None):
        headers = {}
        if user is not None:
            credentials = base64.b64encode(
                (user + ":" + (password or "")).encode("utf-8")
            ).decode("ascii")
            headers["Authorization"] = "Basic " + credentials
        req = Request(self.base + path, headers=headers, method=method, data=data)
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            return error.code, error.headers, error.read()

    def test_health_discloses_no_data(self):
        code, headers, body = self.request("/health")
        self.assertEqual(code, 200)
        self.assertEqual(body, b'{"status":"ok","mode":"fictional-demo"}')
        self.assertEqual(headers["Cache-Control"], "no-store, max-age=0")

    def test_assets_require_a_password_and_dont_cache(self):
        for path in ("/", "/demo.css", "/demo.js"):
            with self.subTest(path=path):
                status, headers, _ = self.request(path)
                self.assertEqual(status, 401)
                self.assertIn("Basic ", headers["WWW-Authenticate"])
                self.assertEqual(headers["X-Robots-Tag"], "noindex, nofollow, noarchive")
                self.assertEqual(headers["Cache-Control"], "no-store, max-age=0")
                for user, password in (
                    ("demo", "incorrect"),
                    ("wrong", SECRET),
                    ("démo", SECRET),
                ):
                    status, _, _ = self.request(path, user, password)
                    self.assertEqual(status, 401)
                status, headers, body = self.request(path, "demo", SECRET)
                self.assertEqual(status, 200)
                self.assertGreater(len(body), 50)
                self.assertIn("default-src 'none'", headers["Content-Security-Policy"])

    def test_no_real_backend_and_no_uploads_or_writes(self):
        for path in ("/docs", "/analyze", "/auth/login", "/analyses", "/../README.md"):
            self.assertEqual(self.request(path, "demo", SECRET)[0], 404)
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                status, _, body = self.request(
                    "/analyze", "demo", SECRET, method, b"%PDF-1.7 arbitrary payload"
                )
                self.assertEqual(status, 405)
                self.assertIn(b"uploads disabled", body)

    def test_password_must_not_be_missing_or_short(self):
        for secret in ("", "too-short"):
            with self.subTest(secret=secret):
                with self.assertRaises(RuntimeError):
                    build_server("127.0.0.1", 0, password=secret)


if __name__ == "__main__":
    unittest.main()
