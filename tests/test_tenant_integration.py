"""Real PostgreSQL isolation tests. Enabled with TRAMITIA_TEST_POSTGRES=1."""
import io
import os
import unittest
import uuid

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.auth import Principal, hash_password, issue_token
from app.db import get_conn
from app.main import app
from scripts.manage_staff import add_staff, disable_staff, list_staff


@unittest.skipUnless(os.getenv("TRAMITIA_TEST_POSTGRES") == "1", "PostgreSQL integration tests disabled")
class TenantIsolationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.organizations = []
        cls.users = []
        cls.slugs = []
        cls.password = "only-ci-test-password-123"
        cls.conn = get_conn()
        try:
            with cls.conn.cursor() as cur:
                for index in (1, 2):
                    slug = f"test-tenant-{uuid.uuid4().hex[:12]}"
                    cur.execute(
                        "INSERT INTO organizations(slug, name) VALUES (%s, %s) RETURNING id",
                        (slug, f"Test Tenant {index}"),
                    )
                    org_id = cur.fetchone()[0]
                    email = f"tenant-{uuid.uuid4().hex[:12]}@example.invalid"
                    cur.execute(
                        "INSERT INTO users(email, password_hash) VALUES (%s, %s) RETURNING id",
                        (email, hash_password(cls.password)),
                    )
                    user_id = cur.fetchone()[0]
                    cur.execute(
                        "INSERT INTO memberships(user_id, organization_id, role) VALUES (%s,%s,'owner')",
                        (user_id, org_id),
                    )
                    cls.organizations.append(org_id)
                    cls.users.append((user_id, email))
                    cls.slugs.append(slug)
            cls.conn.commit()
        except Exception:
            cls.conn.rollback()
            cls.conn.close()
            raise
        cls.headers = [
            {"Authorization": "Bearer " + issue_token(Principal(user[0], org_id, "owner"))}
            for user, org_id in zip(cls.users, cls.organizations)
        ]

    @classmethod
    def tearDownClass(cls):
        try:
            with cls.conn.cursor() as cur:
                cur.execute("DELETE FROM analyses WHERE organization_id = ANY(%s)", (cls.organizations,))
                cur.execute("DELETE FROM memberships WHERE organization_id = ANY(%s)", (cls.organizations,))
                cur.execute("DELETE FROM users WHERE id = ANY(%s)", ([u[0] for u in cls.users],))
                cur.execute("DELETE FROM organizations WHERE id = ANY(%s)", (cls.organizations,))
            cls.conn.commit()
        except Exception:
            cls.conn.rollback()
            raise
        finally:
            cls.conn.close()

    @staticmethod
    def pdf():
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()

    def test_login_and_wrong_organization(self):
        user_id, email = self.users[0]
        res = self.client.post("/auth/login", json={
            "email": email, "password": self.password, "organization": self.slugs[0]
        })
        self.assertEqual(res.status_code, 200, res.text)
        me = self.client.get("/auth/me", headers={
            "Authorization": "Bearer " + res.json()["access_token"]
        })
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(me.json()["user_id"], user_id)
        self.assertEqual(me.json()["organization_id"], self.organizations[0])
        for payload in (
            {"email": email, "password": "wrong", "organization": self.slugs[0]},
            {"email": email, "password": self.password, "organization": self.slugs[1]},
        ):
            denied = self.client.post("/auth/login", json=payload)
            self.assertEqual(denied.status_code, 401, denied.text)

    def test_cross_tenant_upload_list_read_export_and_revocation(self):
        a, b = self.headers
        response = self.client.post(
            "/analyze", headers=a,
            files={"file": ("org_a.pdf", self.pdf(), "application/pdf")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        a_id = response.json()["id"]
        other = self.client.post(
            "/analyze", headers=b,
            files={"file": ("org_b.pdf", self.pdf(), "application/pdf")},
        )
        self.assertEqual(other.status_code, 200, other.text)
        b_id = other.json()["id"]

        list_a = self.client.get("/analyses", headers=a)
        list_b = self.client.get("/analyses", headers=b)
        self.assertEqual(list_a.status_code, 200, list_a.text)
        self.assertEqual(list_b.status_code, 200, list_b.text)
        self.assertIn(a_id, [item["id"] for item in list_a.json()["items"]])
        self.assertNotIn(b_id, [item["id"] for item in list_a.json()["items"]])
        self.assertIn(b_id, [item["id"] for item in list_b.json()["items"]])
        self.assertNotIn(a_id, [item["id"] for item in list_b.json()["items"]])

        for suffix in ("", "/export"):
            self.assertEqual(self.client.get(f"/analyses/{a_id}{suffix}", headers=b).status_code, 404)
            self.assertEqual(self.client.get(f"/analyses/{b_id}{suffix}", headers=a).status_code, 404)
            self.assertEqual(self.client.get(f"/analyses/{a_id}{suffix}", headers=a).status_code, 200)

        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE memberships SET is_active = false WHERE user_id = %s AND organization_id = %s",
                (self.users[0][0], self.organizations[0]),
            )
        self.conn.commit()
        try:
            self.assertEqual(self.client.get("/analyses", headers=a).status_code, 401)
        finally:
            with self.conn.cursor() as cur:
                cur.execute(
                    "UPDATE memberships SET is_active = true WHERE user_id = %s AND organization_id = %s",
                    (self.users[0][0], self.organizations[0]),
                )
            self.conn.commit()


    def test_login_rate_limit_blocks_after_five_failures(self):
        """Throttling is shared via PostgreSQL, not individual app workers."""
        email = f"unknown-{uuid.uuid4().hex[:10]}@example.invalid"
        for _ in range(5):
            response = self.client.post("/auth/login", json={
                "email": email, "password": "incorrect",
                "organization": self.slugs[0],
            })
            self.assertEqual(response.status_code, 401, response.text)
        blocked = self.client.post("/auth/login", json={
            "email": email, "password": "incorrect",
            "organization": self.slugs[0],
        })
        self.assertEqual(blocked.status_code, 429, blocked.text)
        self.assertEqual(blocked.headers.get("retry-after"), "900")

        # Same email under a different organization has a separate throttle key.
        other_org = self.client.post("/auth/login", json={
            "email": email, "password": "incorrect",
            "organization": self.slugs[1],
        })
        self.assertEqual(other_org.status_code, 401, other_org.text)

        # No raw email is stored in the rate-limit ledger.
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM login_attempts WHERE key_hash = %s", (email,))
            self.assertIsNone(cur.fetchone())

    def test_staff_provisioning_and_immediate_revocation(self):
        email = f"staff-{uuid.uuid4().hex[:12]}@example.invalid"
        staff_password = "random-demo-strong-123456"
        user_id = add_staff(self.slugs[0], email, "employee", staff_password)
        self.users.append((user_id, email))
        self.assertTrue(any(row[0] == email and row[1] == "employee" and row[2]
                            for row in list_staff(self.slugs[0])))
        with self.assertRaisesRegex(ValueError, "already registered"):
            add_staff(self.slugs[1], email, "employee", staff_password)

        login = self.client.post("/auth/login", json={
            "email": email, "password": staff_password, "organization": self.slugs[0]
        })
        self.assertEqual(login.status_code, 200, login.text)
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        self.assertEqual(self.client.get("/analyses", headers=headers).status_code, 200)
        different_org = self.client.post("/auth/login", json={
            "email": email, "password": staff_password, "organization": self.slugs[1]
        })
        self.assertEqual(different_org.status_code, 401, different_org.text)

        disable_staff(self.slugs[0], email)
        self.assertEqual(self.client.get("/analyses", headers=headers).status_code, 401)
        self.assertEqual(self.client.post("/auth/login", json={
            "email": email, "password": staff_password, "organization": self.slugs[0]
        }).status_code, 401)

    def test_last_owner_cannot_be_disabled(self):
        with self.assertRaisesRegex(ValueError, "last active owner"):
            disable_staff(self.slugs[0], self.users[0][1])

    def test_staff_management_roles_and_tenant_isolation(self):
        """Verify owner/admin/employee permissions on every management endpoint."""
        owner_a, owner_b = self.headers
        anonymous = self.client.get("/staff")
        self.assertEqual(anonymous.status_code, 401)
        response = self.client.get("/staff", headers=owner_a)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(self.users[0][1], [m["email"] for m in response.json()["items"]])
        self.assertNotIn(self.users[1][1], [m["email"] for m in response.json()["items"]])

        new_email = f"admin-{uuid.uuid4().hex[:12]}@example.invalid"
        result = self.client.post("/staff", headers=owner_a, json={
            "email": new_email, "role": "admin", "password": "admin-test-pass-123456",
        })
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(result.json()["role"], "admin")
        self.assertNotIn("password_hash", result.json())
        admin_id = result.json()["user_id"]
        self.users.append((admin_id, new_email))
        admin_token = {"Authorization": "Bearer " + issue_token(
            Principal(admin_id, self.organizations[0], "admin")
        )}

        # Admin may only create and disable employees, not other admins.
        forbidden_create = self.client.post("/staff", headers=admin_token, json={
            "email": f"another-{uuid.uuid4().hex[:10]}@example.invalid",
            "role": "admin", "password": "password-more-than-12",
        })
        self.assertEqual(forbidden_create.status_code, 403)

        worker_email = f"worker-{uuid.uuid4().hex[:12]}@example.invalid"
        worker = self.client.post("/staff", headers=admin_token, json={
            "email": worker_email.upper(), "role": "employee",
            "password": "employee-strong-123456",
        })
        self.assertEqual(worker.status_code, 201, worker.text)
        self.assertEqual(worker.json()["email"], worker_email)
        worker_id = worker.json()["user_id"]
        self.users.append((worker_id, worker_email))
        employee_token = {"Authorization": "Bearer " + issue_token(
            Principal(worker_id, self.organizations[0], "employee")
        )}

        for method, path, payload in (
            ("get", "/staff", None),
            ("post", "/staff", {"email": "new@example.invalid", "role": "employee",
                                "password": "strong-enough-pass"}),
            ("post", f"/staff/{admin_id}/disable", None),
        ):
            res = getattr(self.client, method)(
                path, headers=employee_token, **({"json": payload} if payload is not None else {})
            )
            self.assertEqual(res.status_code, 403, res.text)

        self.assertEqual(self.client.post(f"/staff/{admin_id}/disable",
                                          headers=admin_token).status_code, 403)
        self.assertEqual(self.client.post(f"/staff/{worker_id}/disable",
                                          headers=owner_b).status_code, 404)
        # Target remains active: cross-organization guesses cannot revoke.
        self.assertEqual(self.client.get("/analyses", headers=employee_token).status_code, 200)

        # Admin can disable employees and tokens are rejected immediately.
        self.assertEqual(self.client.post(f"/staff/{worker_id}/disable",
                                          headers=admin_token).status_code, 200)
        self.assertEqual(self.client.get("/analyses", headers=employee_token).status_code, 401)

        # No administrator can disable an owner or themselves.
        self.assertEqual(self.client.post(f"/staff/{self.users[0][0]}/disable",
                                          headers=admin_token).status_code, 403)
        self.assertEqual(self.client.post(f"/staff/{self.users[0][0]}/disable",
                                          headers=owner_a).status_code, 403)

        # Only the owner can revoke an administrator.
        self.assertEqual(self.client.post(f"/staff/{admin_id}/disable",
                                          headers=owner_a).status_code, 200)
        self.assertEqual(self.client.get("/staff", headers=admin_token).status_code, 401)

    def test_staff_creation_rejects_duplicates_and_invalid_input(self):
        owner_a, owner_b = self.headers
        email = f"unique-{uuid.uuid4().hex[:10]}@example.invalid"
        created = self.client.post("/staff", headers=owner_a, json={
            "email": email, "role": "employee", "password": "test-password-123456",
        })
        self.assertEqual(created.status_code, 201, created.text)
        self.users.append((created.json()["user_id"], email))
        duplicate = self.client.post("/staff", headers=owner_b, json={
            "email": email, "role": "employee", "password": "test-password-123456",
        })
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        for bad in (
            {"email": "invalid", "role": "employee", "password": "test-password-123456"},
            {"email": "valid@example.invalid", "role": "owner", "password": "test-password-123456"},
            {"email": "valid@example.invalid", "role": "employee", "password": "short"},
        ):
            denied = self.client.post("/staff", headers=owner_a, json=bad)
            self.assertEqual(denied.status_code, 422, denied.text)
