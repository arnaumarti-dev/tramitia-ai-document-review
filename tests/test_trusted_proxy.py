"""Proxied client identity is trusted only with exact peer-IP allowlisting."""
import os
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app.main import _consume_peer_budget
from app.peer_rate_limit import PeerRateLimitUnavailable, resolve_request_peer


def request(peer: str, headers=()):
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/auth/login",
        "headers": list(headers),
        "client": (peer, 65000),
        "scheme": "http",
        "server": ("127.0.0.1", 8000),
    })


class TrustedProxyTests(unittest.TestCase):
    def test_disabled_trust_ignores_all_untrusted_forwarding_headers(self):
        with patch.dict(os.environ, {"TRAMITIA_TRUSTED_PROXY_IPS": ""}):
            peer = resolve_request_peer(request("127.0.0.1", [
                (b"x-real-ip", b"203.0.113.7"),
                (b"x-forwarded-for", b"198.51.100.15"),
            ]))
        self.assertEqual(peer, "127.0.0.1")

    def test_trusted_peer_uses_exact_overwritten_ip(self):
        with patch.dict(os.environ, {
            "TRAMITIA_TRUSTED_PROXY_IPS": "127.0.0.1, ::1",
        }):
            peer = resolve_request_peer(request("127.0.0.1", [
                (b"x-real-ip", b"2001:0DB8::0001"),
                (b"x-forwarded-for", b"192.0.2.99"),
            ]))
        self.assertEqual(peer, "2001:db8::1")

    def test_untrusted_direct_client_cannot_spoof_proxy_header(self):
        with patch.dict(os.environ, {"TRAMITIA_TRUSTED_PROXY_IPS": "127.0.0.1"}):
            peer = resolve_request_peer(request("192.0.2.20", [
                (b"x-real-ip", b"203.0.113.70"),
            ]))
        self.assertEqual(peer, "192.0.2.20")

    def test_proxy_missing_duplicate_or_invalid_header_fails_closed(self):
        bad = [
            [],
            [(b"x-real-ip", b"203.0.113.1"), (b"X-REAL-IP", b"203.0.113.2")],
            [(b"x-real-ip", b"203.0.113.1,198.51.100.2")],
            [(b"x-real-ip", b" 203.0.113.1")],
            [(b"x-real-ip", b"not_an_ip")],
            [(b"x-real-ip", b"\xff")],
        ]
        with patch.dict(os.environ, {"TRAMITIA_TRUSTED_PROXY_IPS": "127.0.0.1"}):
            for headers in bad:
                with self.subTest(headers=headers), self.assertRaises(
                    PeerRateLimitUnavailable
                ):
                    resolve_request_peer(request("127.0.0.1", headers))

    def test_invalid_or_too_broad_trust_config_fails_closed(self):
        configs = [
            "0.0.0.0/0", "*", "127.0.0.1,,::1", "unknownhost",
            ",".join("127.0.0." + str(i) for i in range(1, 11)),
        ]
        for config in configs:
            with self.subTest(config=config), patch.dict(os.environ, {
                "TRAMITIA_TRUSTED_PROXY_IPS": config,
            }), self.assertRaises(PeerRateLimitUnavailable):
                resolve_request_peer(request("127.0.0.1"))

    def test_actual_fastapi_admission_receives_overwritten_client_ip(self):
        with patch.dict(os.environ, {
            "TRAMITIA_TRUSTED_PROXY_IPS": "127.0.0.1",
        }), patch("app.main.enforce_peer_rate_limit") as reserve:
            _consume_peer_budget(request("127.0.0.1", [
                (b"X-Real-IP", b"198.51.100.77"),
                (b"X-Forwarded-For", b"203.0.113.88"),
            ]), "login")
        reserve.assert_called_once_with("198.51.100.77", "login")


if __name__ == "__main__":
    unittest.main()
