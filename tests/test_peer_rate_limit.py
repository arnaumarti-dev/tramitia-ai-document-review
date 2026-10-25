"""Peer-IP admission is shared, atomic, and never trusts HTTP IP headers."""
from concurrent.futures import ThreadPoolExecutor
import os
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ.setdefault(
    "TRAMITIA_JWT_SECRET", "only-for-tests-never-use-this-value-in-production-123",
)

from app.auth import Principal, issue_token
from app.db import get_conn
from app.main import app
from app.peer_rate_limit import (
    LIMITS, PeerRateLimited, PeerRateLimitUnavailable,
    _canonical_peer, _hashed_key, delete_expired_peer_counters,
    enforce_peer_rate_limit,
)
from scripts.generate_demo_pdf import make_demo_pdf


class PeerAdmissionAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.valid_headers = {
            "Authorization": "Bearer " + issue_token(Principal(11, 21, "owner"))
        }

    def test_forwarded_ip_header_is_ignored(self):
        with patch("app.main.enforce_peer_rate_limit",
                   side_effect=PeerRateLimited("login", 123)) as quota, patch(
            "app.main.authenticate"
        ) as auth:
            res = self.client.post(
                "/auth/login",
                json={"organization": "demo", "email": "demo@example.invalid", "password": "no"},
                headers={"X-Forwarded-For": "203.0.113.123"},
            )
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.headers.get("retry-after"), "123")
        quota.assert_called_once_with("testclient", "login")
        auth.assert_not_called()

    def test_auth_and_invite_limits_stop_expensive_work(self):
        with patch("app.main.enforce_peer_rate_limit",
                   side_effect=PeerRateLimited("invitation", 320)), patch(
            "app.main.accept_invitation"
        ) as invited:
            res = self.client.post("/auth/invitations/accept", json={
                "token": "a" * 30, "password": "secure-password-123",
            })
        self.assertEqual(res.status_code, 429, res.text)
        self.assertEqual(res.headers.get("retry-after"), "320")
        invited.assert_not_called()

    def test_rate_limit_db_down_fails_closed_on_login(self):
        with patch("app.main.enforce_peer_rate_limit",
                   side_effect=PeerRateLimitUnavailable()), patch(
            "app.main.authenticate"
        ) as auth:
            res = self.client.post("/auth/login", json={
                "organization": "demo", "email": "demo@example.invalid", "password": "no",
            })
        self.assertEqual(res.status_code, 503)
        auth.assert_not_called()

    def test_rate_limit_denies_pdf_before_user_quota_parser_or_persistence(self):
        member = patch("app.auth.get_membership_role", return_value="owner")
        member.start()
        self.addCleanup(member.stop)
        with patch("app.main.enforce_peer_rate_limit",
                   side_effect=PeerRateLimited("analyze", 10)), patch(
            "app.main.consume_analysis_quota"
        ) as account_quota, patch(
            "app.main.parse_pdf_document"
        ) as parser, patch(
            "app.main.save_analysis"
        ) as save:
            res = self.client.post(
                "/analyze",
                headers=self.valid_headers,
                files={"file": ("sample.pdf", make_demo_pdf(), "application/pdf")},
            )
        self.assertEqual(res.status_code, 429, res.text)
        self.assertEqual(res.headers.get("retry-after"), "10")
        account_quota.assert_not_called()
        parser.assert_not_called()
        save.assert_not_called()

    def test_invalid_peer_fails_closed_without_database_calls(self):
        with patch("app.peer_rate_limit.get_conn") as conn:
            with self.assertRaises(PeerRateLimitUnavailable):
                enforce_peer_rate_limit(None, "login")
            conn.assert_not_called()

    def test_peer_is_canonical_and_key_is_pseudonymous(self):
        ip_a = _canonical_peer("2001:0DB8:0000::0001")
        ip_b = _canonical_peer("2001:db8::1")
        self.assertEqual(ip_a, ip_b)
        k = _hashed_key(ip_a, "login")
        self.assertEqual(len(k), 64)
        self.assertNotIn(ip_a, k)
        self.assertNotEqual(k, _hashed_key(ip_a, "analyze"))


@unittest.skipUnless(
    os.environ.get("TRAMITIA_TEST_POSTGRES") == "1",
    "Requires disposable migrated PostgreSQL database",
)
class PostgreSQLPeerAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = get_conn()
        cls.peers = [
            "198.51.100." + str(20 + i) for i in range(3)
        ]
        cls.unique = uuid.uuid4().hex
        # IPv6 addresses are globally valid literals and are not a user
        # input header. Pseudonymous hashing makes cleanup safe and scoped.
        cls.peers = [f"2001:db8:{cls.unique[:4]}::{i+1}" for i in range(3)]
        cls.keys = [
            _hashed_key(peer, category)
            for peer in cls.peers for category in LIMITS
        ]

    @classmethod
    def tearDownClass(cls):
        try:
            with cls.conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM ip_rate_limits WHERE key_hash = ANY(%s)",
                    (cls.keys,),
                )
            cls.conn.commit()
        finally:
            cls.conn.close()

    def setUp(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "DELETE FROM ip_rate_limits WHERE key_hash = ANY(%s)",
                (self.keys,),
            )
        self.conn.commit()

    def _count(self, peer, category):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT request_count FROM ip_rate_limits WHERE key_hash=%s",
                (_hashed_key(peer, category),),
            )
            row = cur.fetchone()
        self.conn.rollback()
        return row[0] if row else None

    def test_isolated_per_peer_and_action_budget(self):
        p, q = self.peers[:2]
        with patch.dict(LIMITS, {"login": (2, 15)}):
            enforce_peer_rate_limit(p, "login")
            enforce_peer_rate_limit(p, "login")
            with self.assertRaises(PeerRateLimited) as err:
                enforce_peer_rate_limit(p, "login")
            self.assertGreater(err.exception.retry_after, 0)
            enforce_peer_rate_limit(q, "login")
            enforce_peer_rate_limit(p, "invitation")
        self.assertEqual(self._count(p, "login"), 2)
        self.assertEqual(self._count(q, "login"), 1)
        self.assertEqual(self._count(p, "invitation"), 1)

    def test_expired_window_allows_requests_again(self):
        p = self.peers[0]
        with patch.dict(LIMITS, {"login": (1, 15)}):
            enforce_peer_rate_limit(p, "login")
            with self.conn.cursor() as cur:
                cur.execute(
                    "UPDATE ip_rate_limits SET window_started_at = NOW() - INTERVAL '2 hours' WHERE key_hash = %s",
                    (_hashed_key(p, "login"),),
                )
            self.conn.commit()
            enforce_peer_rate_limit(p, "login")
            with self.assertRaises(PeerRateLimited):
                enforce_peer_rate_limit(p, "login")
        self.assertEqual(self._count(p, "login"), 1)

    def test_concurrent_requests_never_exceed_shared_ip_quota(self):
        p = self.peers[0]
        def one(_):
            try:
                enforce_peer_rate_limit(p, "analyze")
                return "ok"
            except PeerRateLimited:
                return "limit"
        with patch.dict(LIMITS, {"analyze": (4, 60)}):
            with ThreadPoolExecutor(max_workers=8) as pool:
                responses = list(pool.map(one, range(12)))
        self.assertEqual(responses.count("ok"), 4, responses)
        self.assertEqual(responses.count("limit"), 8, responses)
        self.assertEqual(self._count(p, "analyze"), 4)

    def test_cleanup_expired_counters_uses_hashed_identifiers(self):
        p = self.peers[0]
        enforce_peer_rate_limit(p, "login")
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE ip_rate_limits SET window_started_at = NOW() - INTERVAL '3 days' WHERE key_hash=%s",
                (_hashed_key(p, "login"),),
            )
        self.conn.commit()
        self.assertGreaterEqual(delete_expired_peer_counters(), 1)
        self.assertIsNone(self._count(p, "login"))


if __name__ == "__main__":
    unittest.main()
