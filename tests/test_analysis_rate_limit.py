"""Request quotas: controlled API errors + real PostgreSQL atomic counters."""
from concurrent.futures import ThreadPoolExecutor
import os
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ.setdefault(
    "TRAMITIA_JWT_SECRET", "only-for-tests-never-use-this-value-in-production-123",
)

from app.analysis_rate_limit import (
    AnalysisQuotaExceeded, AnalysisQuotaUnavailable,
    consume_analysis_quota,
)
from app.auth import Principal, issue_token
from app.db import get_conn
from app.main import app
from scripts.generate_demo_pdf import make_demo_pdf


class AnalysisQuotaApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {
            "Authorization": "Bearer " + issue_token(Principal(11, 21, "owner"))
        }
        membership = patch("app.auth.get_membership_role", return_value="owner")
        membership.start()
        self.addCleanup(membership.stop)

    def test_user_and_organization_quota_return_429_without_parsing_or_saving(self):
        for scope in ("usuario", "gestoría"):
            with self.subTest(scope=scope), patch(
                "app.main.consume_analysis_quota",
                side_effect=AnalysisQuotaExceeded(scope, 300),
            ) as quota, patch("app.main.parse_pdf_document") as parse, patch(
                "app.main.save_analysis"
            ) as save:
                res = self.client.post("/analyze", headers=self.headers, files={
                    "file": ("ficticio.pdf", make_demo_pdf(), "application/pdf"),
                })
            self.assertEqual(res.status_code, 429, res.text)
            self.assertEqual(res.headers.get("retry-after"), "300")
            self.assertIn(scope, res.json()["detail"])
            quota.assert_called_once_with(11, 21)
            parse.assert_not_called()
            save.assert_not_called()

    def test_db_unavailable_fails_closed_without_pdf_worker_or_persistence(self):
        with patch("app.main.consume_analysis_quota",
                   side_effect=AnalysisQuotaUnavailable()), patch(
            "app.main.parse_pdf_document"
        ) as parse, patch("app.main.save_analysis") as save:
            res = self.client.post("/analyze", headers=self.headers, files={
                "file": ("ficticio.pdf", make_demo_pdf(), "application/pdf"),
            })
        self.assertEqual(res.status_code, 503)
        parse.assert_not_called()
        save.assert_not_called()

    def test_malformed_pdf_rejected_before_charging_quota(self):
        with patch("app.main.consume_analysis_quota") as quota:
            res = self.client.post("/analyze", headers=self.headers, files={
                "file": ("falso.pdf", b"not a pdf", "application/pdf"),
            })
        self.assertEqual(res.status_code, 400)
        quota.assert_not_called()


@unittest.skipUnless(
    os.getenv("TRAMITIA_TEST_POSTGRES") == "1",
    "Requires explicitly enabled disposable migrated PostgreSQL database",
)
class PersistentQuotaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = get_conn()
        cls.org_ids = []
        cls.user_ids = []
        try:
            with cls.conn.cursor() as cursor:
                for _ in range(2):
                    slug = f"quota-test-{uuid.uuid4().hex[:14]}"
                    cursor.execute(
                        "INSERT INTO organizations(slug,name) VALUES (%s, %s) RETURNING id",
                        (slug, "Fictitious quota tenant"),
                    )
                    cls.org_ids.append(int(cursor.fetchone()[0]))
                for _ in range(2):
                    email = f"quota-{uuid.uuid4().hex[:14]}@example.invalid"
                    cursor.execute(
                        "INSERT INTO users(email, password_hash) VALUES (%s,%s) RETURNING id",
                        (email, "test-hash-not-for-login"),
                    )
                    cls.user_ids.append(int(cursor.fetchone()[0]))
                for user_id in cls.user_ids:
                    cursor.execute(
                        "INSERT INTO memberships(user_id, organization_id, role) VALUES (%s,%s,'employee')",
                        (user_id, cls.org_ids[0]),
                    )
            cls.conn.commit()
        except Exception:
            cls.conn.rollback()
            cls.conn.close()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            with cls.conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM memberships WHERE user_id = ANY(%s)",
                    (cls.user_ids,),
                )
                cursor.execute("DELETE FROM users WHERE id = ANY(%s)", (cls.user_ids,))
                cursor.execute("DELETE FROM organizations WHERE id = ANY(%s)",
                               (cls.org_ids,))
            cls.conn.commit()
        finally:
            cls.conn.close()

    def setUp(self):
        with self.conn.cursor() as cursor:
            cursor.execute("DELETE FROM analysis_user_quotas WHERE user_id = ANY(%s)",
                           (self.user_ids,))
            cursor.execute(
                "DELETE FROM analysis_org_quotas WHERE organization_id = ANY(%s)",
                (self.org_ids,),
            )
        self.conn.commit()

    def counters(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, used FROM analysis_user_quotas WHERE user_id = ANY(%s) ORDER BY user_id",
                (self.user_ids,),
            )
            users = dict(cur.fetchall())
            cur.execute(
                "SELECT organization_id, used FROM analysis_org_quotas WHERE organization_id = ANY(%s)",
                (self.org_ids,),
            )
            orgs = dict(cur.fetchall())
        self.conn.rollback()
        return users, orgs

    def test_per_user_limit_rolls_back_both_counters_and_other_org_is_untouched(self):
        user, other_user = self.user_ids
        org_a, org_b = self.org_ids
        with patch("app.analysis_rate_limit.MAX_ANALYSES_PER_USER_HOUR", 2), patch(
            "app.analysis_rate_limit.MAX_ANALYSES_PER_ORG_HOUR", 10
        ):
            consume_analysis_quota(user, org_a)
            consume_analysis_quota(user, org_a)
            with self.assertRaises(AnalysisQuotaExceeded) as ctx:
                consume_analysis_quota(user, org_a)
            self.assertEqual(ctx.exception.scope, "usuario")
            self.assertGreater(ctx.exception.retry_after, 0)
            consume_analysis_quota(other_user, org_b)
        users, orgs = self.counters()
        self.assertEqual(users[user], 2)
        self.assertEqual(users[other_user], 1)
        self.assertEqual(orgs[org_a], 2)
        self.assertEqual(orgs[org_b], 1)

    def test_org_limit_does_not_consume_failed_users_quota(self):
        one, two = self.user_ids
        org = self.org_ids[0]
        with patch("app.analysis_rate_limit.MAX_ANALYSES_PER_USER_HOUR", 10), patch(
            "app.analysis_rate_limit.MAX_ANALYSES_PER_ORG_HOUR", 2
        ):
            consume_analysis_quota(one, org)
            consume_analysis_quota(two, org)
            with self.assertRaises(AnalysisQuotaExceeded) as ctx:
                consume_analysis_quota(one, org)
            self.assertEqual(ctx.exception.scope, "gestoría")
        users, orgs = self.counters()
        self.assertEqual(users[one], 1)
        self.assertEqual(users[two], 1)
        self.assertEqual(orgs[org], 2)

    def test_new_hour_resets_counters(self):
        user, org = self.user_ids[0], self.org_ids[0]
        with patch("app.analysis_rate_limit.MAX_ANALYSES_PER_USER_HOUR", 1), patch(
            "app.analysis_rate_limit.MAX_ANALYSES_PER_ORG_HOUR", 1
        ):
            consume_analysis_quota(user, org)
            with self.conn.cursor() as cur:
                cur.execute(
                    "UPDATE analysis_user_quotas SET window_start = NOW() - INTERVAL '2 hours' WHERE user_id = %s",
                    (user,),
                )
                cur.execute(
                    "UPDATE analysis_org_quotas SET window_start = NOW() - INTERVAL '2 hours' WHERE organization_id = %s",
                    (org,),
                )
            self.conn.commit()
            consume_analysis_quota(user, org)
            with self.assertRaises(AnalysisQuotaExceeded):
                consume_analysis_quota(user, org)
        users, orgs = self.counters()
        self.assertEqual(users[user], 1)
        self.assertEqual(orgs[org], 1)

    def test_concurrent_calls_across_connections_never_exceed_org_limit(self):
        user, org = self.user_ids[0], self.org_ids[0]
        def request(_):
            try:
                consume_analysis_quota(user, org)
                return "accepted"
            except AnalysisQuotaExceeded:
                return "limited"
        with patch("app.analysis_rate_limit.MAX_ANALYSES_PER_USER_HOUR", 20), patch(
            "app.analysis_rate_limit.MAX_ANALYSES_PER_ORG_HOUR", 3
        ):
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(request, range(12)))
        self.assertEqual(results.count("accepted"), 3, results)
        self.assertEqual(results.count("limited"), 9, results)
        users, orgs = self.counters()
        self.assertEqual(users[user], 3)
        self.assertEqual(orgs[org], 3)


if __name__ == "__main__":
    unittest.main()
