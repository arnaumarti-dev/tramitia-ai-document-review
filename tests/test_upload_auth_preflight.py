"""AUD-H06: deny unauthenticated uploads without reading or spooling a byte."""
from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ.setdefault(
    "TRAMITIA_JWT_SECRET", "synthetic-ci-only-session-secret-with-32-plus-characters"
)

from app.auth import Principal, issue_token
from app.main import app
from app.upload_auth_preflight import UploadAuthenticationPreflight

VALID_TOKEN = issue_token(Principal(31, 41, "owner"))
FAKE_PDF = b"%PDF-1.7\n" + b"X" * (2 * 1024 * 1024)


class AuthPreflightASGITests(unittest.IsolatedAsyncioTestCase):
    async def _send_request(self, headers=None, path="/analyze", method="POST"):
        events = []
        downstream_called = []
        read_calls = []

        async def receive():
            read_calls.append(True)
            raise AssertionError("Unauthenticated request body was consumed")

        async def send(message):
            events.append(message)

        async def downstream(scope, receive, send):
            downstream_called.append(True)
            await send({"type": "http.response.start", "status": 202, "headers": []})
            await send({"type": "http.response.body", "body": b"accepted"})

        scope = {
            "type": "http", "path": path, "method": method,
            "headers": headers or [], "client": ("127.0.0.1", 12345),
        }
        gate = UploadAuthenticationPreflight(downstream)
        await gate(scope, receive, send)
        status = next(msg["status"] for msg in events if msg["type"] == "http.response.start")
        return status, downstream_called, read_calls, events

    async def test_no_token_duplicate_malformed_and_oversized_headers_do_not_parse(self):
        candidates = [
            [],
            [(b"Authorization", b"Basic abc")],
            [(b"Authorization", b"Bearer ")],
            [(b"authorization", b"Bearer abc"), (b"AUTHORIZATION", b"Bearer abc")],
            [(b"authorization", b"Bearer " + b"a" * 5000)],
            [(b"authorization", b"Bearer abc\xff")],
            [(b"authorization", b"Bearer abc other")],
        ]
        for headers in candidates:
            with self.subTest(headers=headers), patch(
                "app.upload_auth_preflight.current_principal"
            ) as auth:
                status, downstream, reads, events = await self._send_request(headers)
                self.assertEqual(status, 401)
                self.assertEqual(downstream, [])
                self.assertEqual(reads, [])
                self.assertTrue(any(
                    (b"www-authenticate", b"Bearer") in message.get("headers", [])
                    for message in events
                    if message["type"] == "http.response.start"
                ))
                auth.assert_not_called()

    async def test_expired_or_revoked_token_fails_before_receiving(self):
        for status_code in (401, 403):
            with self.subTest(status_code=status_code), patch(
                "app.upload_auth_preflight.current_principal",
                side_effect=__import__("fastapi").HTTPException(status_code=status_code),
            ):
                status, downstream, reads, _ = await self._send_request([
                    (b"authorization", b"Bearer " + VALID_TOKEN.encode()),
                ])
                self.assertEqual(status, 401 if status_code == 401 else 503)
                self.assertEqual((downstream, reads), ([], []))

    async def test_auth_database_error_fails_closed_without_body_reads(self):
        with patch("app.upload_auth_preflight.current_principal",
                   side_effect=OSError("synthetic DB unavailable")):
            status, downstream, reads, events = await self._send_request([
                (b"authorization", b"Bearer " + VALID_TOKEN.encode()),
            ])
        self.assertEqual((status, downstream, reads), (503, [], []))
        self.assertNotIn(b"synthetic DB unavailable", b"".join(
            msg.get("body", b"") for msg in events
        ))

    async def test_authorized_token_passes_and_non_upload_routes_are_unchanged(self):
        with patch("app.upload_auth_preflight.current_principal",
                   return_value=Principal(31, 41, "owner")) as auth:
            status, downstream, reads, _ = await self._send_request([
                (b"authorization", b"Bearer " + VALID_TOKEN.encode()),
            ])
            self.assertEqual((status, downstream, reads), (202, [True], []))
            auth.assert_called_once()
            status, downstream, reads, _ = await self._send_request(
                path="/auth/login", method="POST",
            )
            self.assertEqual((status, downstream, reads), (202, [True], []))
            auth.assert_called_once()


class AuthPreflightIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.spool = patch("starlette.formparsers.SpooledTemporaryFile")
        self.created = self.spool.start()
        self.addCleanup(self.spool.stop)

    def test_anonymous_multipart_never_constructs_starlette_spooled_file(self):
        response = self.client.post(
            "/analyze", files={"file": ("ficticio.pdf", FAKE_PDF, "application/pdf")}
        )
        self.assertEqual(response.status_code, 401, response.text)
        self.created.assert_not_called()

    def test_invalid_and_revoked_session_never_construct_starlette_spooled_file(self):
        with patch("app.auth.get_membership_role", return_value=None):
            response = self.client.post(
                "/analyze",
                headers={"Authorization": "Bearer " + VALID_TOKEN},
                files={"file": ("ficticio.pdf", FAKE_PDF, "application/pdf")},
            )
        self.assertEqual(response.status_code, 401, response.text)
        self.created.assert_not_called()
        self.created.reset_mock()

        response = self.client.post(
            "/analyze",
            headers={"Authorization": "Bearer signed-but-not-valid"},
            files={"file": ("ficticio.pdf", FAKE_PDF, "application/pdf")},
        )
        self.assertEqual(response.status_code, 401, response.text)
        self.created.assert_not_called()

    def test_authorized_request_rechecks_membership_after_upload(self):
        # Test with actual multipart parsing and no customer data, using a
        # real temporary file constructor (spool is not mocked in this case).
        self.spool.stop()
        with patch("app.auth.get_membership_role", return_value="owner") as membership, patch(
            "app.main._consume_peer_budget"
        ), patch("app.main.consume_analysis_quota"), patch(
            "app.main.parse_pdf_document",
            return_value=("Solicitud ficticia. Telefono 612345678. Firma de prueba.", 1),
        ), patch("app.main.save_analysis", return_value=17):
            response = self.client.post(
                "/analyze",
                headers={"Authorization": "Bearer " + VALID_TOKEN},
                files={"file": ("demo.pdf", FAKE_PDF, "application/pdf")},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], 17)
        self.assertGreaterEqual(membership.call_count, 2)


if __name__ == "__main__":
    unittest.main()
