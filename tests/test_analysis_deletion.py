"""Authorization and tenant-scope tests for destructive analysis deletion."""
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


class AnalysisDeletionTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {
            "Authorization": "Bearer " + issue_token(Principal(11, 21, "owner"))
        }

    def test_anonymous_and_employee_cannot_delete(self):
        with patch("app.main.delete_analysis") as deletion:
            self.assertEqual(self.client.delete("/analyses/42").status_code, 401)
            with patch("app.auth.get_membership_role", return_value="employee"):
                response = self.client.delete(
                    "/analyses/42", headers=self.headers
                )
                self.assertEqual(response.status_code, 403, response.text)
            deletion.assert_not_called()

    def test_only_authenticated_organization_is_used_for_deletion(self):
        with patch("app.auth.get_membership_role", return_value="owner"):
            with patch("app.main.delete_analysis", return_value=False) as deletion:
                response = self.client.delete(
                    "/analyses/42", headers=self.headers
                )
                self.assertEqual(response.status_code, 404, response.text)
                deletion.assert_called_once_with(42, 21)

            with patch("app.main.delete_analysis", return_value=True) as deletion:
                response = self.client.delete(
                    "/analyses/42", headers=self.headers
                )
                self.assertEqual(response.status_code, 204, response.text)
                self.assertFalse(response.content)
                deletion.assert_called_once_with(42, 21)

    def test_admin_can_delete_but_revoked_session_cannot(self):
        with patch("app.auth.get_membership_role", return_value="admin"):
            with patch("app.main.delete_analysis", return_value=True) as deletion:
                self.assertEqual(self.client.delete(
                    "/analyses/42", headers=self.headers
                ).status_code, 204)
                deletion.assert_called_once_with(42, 21)

        with patch("app.auth.get_membership_role", return_value=None):
            with patch("app.main.delete_analysis") as deletion:
                self.assertEqual(self.client.delete(
                    "/analyses/42", headers=self.headers
                ).status_code, 401)
                deletion.assert_not_called()


if __name__ == "__main__":
    unittest.main()
