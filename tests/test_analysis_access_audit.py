"""Authorization, rollback and minimization for report access audit."""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ.setdefault(
    "TRAMITIA_JWT_SECRET",
    "only-for-tests-never-use-this-value-in-production-123",
)
from app.auth import Principal, issue_token
from app.main import app
from app.repository import get_analysis_by_id, get_analysis_export


class AccessAuditApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {
            "Authorization": "Bearer " + issue_token(Principal(11, 21, "owner"))
        }

    def test_only_current_managers_can_read_audit(self):
        with patch("app.main.list_access_events") as audit:
            self.assertEqual(self.client.get("/audit/accesses").status_code, 401)
            with patch("app.auth.get_membership_role", return_value="employee"):
                self.assertEqual(
                    self.client.get("/audit/accesses", headers=self.headers).status_code,
                    403,
                )
            audit.assert_not_called()

    def test_audit_scope_is_authenticated_organization_and_limit_is_bounded(self):
        with patch("app.auth.get_membership_role", return_value="admin"):
            with patch("app.main.list_access_events", return_value={
                "items": [], "limit": 12, "offset": 3
            }) as audit:
                response = self.client.get(
                    "/audit/accesses?limit=12&offset=3&organization_id=999",
                    headers=self.headers,
                )
                self.assertEqual(response.status_code, 200, response.text)
                audit.assert_called_once_with(21, limit=12, offset=3)
            with patch("app.main.list_access_events") as audit:
                self.assertEqual(
                    self.client.get("/audit/accesses?limit=10001", headers=self.headers).status_code,
                    422,
                )
                audit.assert_not_called()


class AccessAuditTransactionTests(unittest.TestCase):
    def test_no_audit_is_written_for_absent_or_cross_tenant_report(self):
        for getter in (get_analysis_by_id, get_analysis_export):
            with self.subTest(getter=getter.__name__):
                with patch("app.repository.get_conn") as connect:
                    connection = connect.return_value
                    cursor = connection.cursor.return_value.__enter__.return_value
                    cursor.fetchone.return_value = None
                    self.assertIsNone(getter(42, 21, 11))
                    cursor.execute.assert_called_once()
                    connection.commit.assert_not_called()

    def test_failed_audit_prevents_successful_report_read_or_export(self):
        for getter in (get_analysis_by_id, get_analysis_export):
            with self.subTest(getter=getter.__name__):
                with patch("app.repository.get_conn") as connect:
                    connection = connect.return_value
                    cursor = connection.cursor.return_value.__enter__.return_value
                    cursor.fetchone.return_value = {
                        "id": 42, "findings_json": [],
                    }
                    cursor.execute.side_effect = [None, RuntimeError("audit unavailable")]
                    with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                        getter(42, 21, 11)
                    self.assertEqual(cursor.execute.call_count, 2)
                    self.assertIn("FOR SHARE", cursor.execute.call_args_list[0].args[0])
                    self.assertNotIn("filename", cursor.execute.call_args_list[1].args[0])
                    connection.rollback.assert_called_once_with()
                    connection.commit.assert_not_called()
                    connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
