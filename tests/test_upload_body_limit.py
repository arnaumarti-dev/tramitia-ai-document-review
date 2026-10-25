"""ASGI-level request ceiling tests; no real client documents or network."""
import asyncio
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.upload_body_limit import UploadBodyLimit


class UploadBodyLimitASGITests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, pieces, *, length=None, path="/analyze", cap=10, extra_headers=None):
        events = []
        called = []
        messages = iter([
            {
                "type": "http.request",
                "body": part,
                "more_body": i != len(pieces) - 1,
            } for i, part in enumerate(pieces)
        ])

        async def receive():
            return next(messages, {"type": "http.disconnect"})

        async def send(message):
            events.append(message)

        async def downstream(scope, receive, send):
            called.append(True)
            while True:
                packet = await receive()
                if packet["type"] != "http.request" or not packet["more_body"]:
                    break
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok", "more_body": False})

        headers = list(extra_headers or [])
        if length is not None:
            headers.append((b"content-length", str(length).encode()))
        scope = {
            "type": "http", "http_version": "1.1",
            "method": "POST", "path": path, "headers": headers,
        }
        guard = UploadBodyLimit(downstream, max_body_bytes=cap)
        await guard(scope, receive, send)
        response = next(e for e in events if e["type"] == "http.response.start")
        bodies = b"".join(e.get("body", b"") for e in events
                          if e["type"] == "http.response.body")
        return response["status"], bodies, called

    async def test_oversized_content_length_is_rejected_without_reading(self):
        status, body, called = await self._run([b"tiny"], length=11)
        self.assertEqual(status, 413)
        self.assertIn(b"supera", body)
        self.assertEqual(called, [])

    async def test_chunked_body_without_content_length_is_stopped_midstream(self):
        status, body, called = await self._run([b"123456", b"78901"])
        self.assertEqual(status, 413)
        self.assertIn(b"supera", body)
        self.assertEqual(called, [True])

    async def test_boundary_is_accepted_and_other_paths_not_restricted(self):
        status, body, called = await self._run([b"12345", b"67890"], length=10)
        self.assertEqual((status, body, called), (200, b"ok", [True]))
        status, body, called = await self._run([b"12345678901"], path="/auth/login")
        self.assertEqual((status, body, called), (200, b"ok", [True]))

    async def test_conflicting_or_malformed_content_lengths_are_rejected(self):
        for headers in (
            [(b"content-length", b"3"), (b"content-length", b"4")],
            [(b"content-length", b"bogus")],
            [(b"content-length", b"-1")],
            [(b"content-length", b"1" * 40)],
        ):
            with self.subTest(headers=headers):
                status, body, called = await self._run([b"x"], extra_headers=headers)
                self.assertEqual(status, 400)
                self.assertEqual(called, [])


class RequestLimitIntegrationTests(unittest.TestCase):
    def test_fastapi_rejects_oversized_multipart_before_pdf_parser(self):
        # Reduce only this test's ASGI ceiling so the check runs without
        # allocating a real 21 MiB file.
        with patch.object(app, "max_body_bytes", 128), patch(
            "app.main.parse_pdf_document"
        ) as parser, patch("app.main.save_analysis") as save:
            response = TestClient(app, raise_server_exceptions=False).post(
                "/analyze",
                files={"file": ("oversized.pdf", b"%PDF-1.7\\n" + b"a" * 200, "application/pdf")},
            )
        self.assertEqual(response.status_code, 413, response.text)
        parser.assert_not_called()
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
