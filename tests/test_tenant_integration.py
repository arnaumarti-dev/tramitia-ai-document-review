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

    def invite_and_accept(self, headers, email, role, password="test-user-secure-password"):
        response = self.client.post("/staff/invitations", headers=headers, json={
            "email": email, "role": role,
        })
        self.assertEqual(response.status_code, 201, response.text)
        invite = response.json()
        self.assertEqual(invite["expires_in"], 86400)
        self.assertNotIn("password", invite)
        accepted = self.client.post("/auth/invitations/accept", json={
            "token": invite["invite_token"], "password": password,
        })
        self.assertEqual(accepted.status_code, 200, accepted.text)
        reused = self.client.post("/auth/invitations/accept", json={
            "token": invite["invite_token"], "password": password,
        })
        self.assertEqual(reused.status_code, 400, reused.text)
        login = self.client.post("/auth/login", json={
            "email": email, "organization": self.slugs[0], "password": password,
        })
        self.assertEqual(login.status_code, 200, login.text)
        user_id = login.json()["user_id"]
        self.users.append((user_id, email))
        return user_id, {"Authorization": "Bearer " + login.json()["access_token"]}

    def test_invitation_is_one_use_hashed_and_expires(self):
        email = f"expiring-{uuid.uuid4().hex[:12]}@example.invalid"
        invite = self.client.post("/staff/invitations", headers=self.headers[0], json={
            "email": email, "role": "employee"
        })
        self.assertEqual(invite.status_code, 201, invite.text)
        token = invite.json()["invite_token"]
        with self.conn.cursor() as cursor:
            cursor.execute(
                "SELECT token_hash, expires_at FROM staff_invitations WHERE email = %s",
                (email,),
            )
            row = cursor.fetchone()
            self.assertNotEqual(token, row[0])
            self.assertEqual(len(row[0]), 64)
            cursor.execute(
                "UPDATE staff_invitations SET expires_at = NOW() - INTERVAL '1 minute' WHERE email = %s",
                (email,),
            )
        self.conn.commit()
        expired = self.client.post("/auth/invitations/accept", json={
            "token": token, "password": "new-secure-password-123"
        })
        self.assertEqual(expired.status_code, 400, expired.text)
        self.assertEqual(self.client.post("/auth/invitations/accept", json={
            "token": "invalid-token-aaaaaaaaaaaaaaaaaaaaaaaa",
            "password": "new-secure-password-123"
        }).status_code, 400)

    def test_staff_invitation_management_roles_and_tenant_isolation(self):
        owner_a, owner_b = self.headers
        self.assertEqual(self.client.get("/staff").status_code, 401)
        roster = self.client.get("/staff", headers=owner_a)
        self.assertEqual(roster.status_code, 200, roster.text)
        self.assertIn(self.users[0][1], [member["email"] for member in roster.json()["items"]])
        self.assertNotIn(self.users[1][1], [member["email"] for member in roster.json()["items"]])

        admin_email = f"admin-{uuid.uuid4().hex[:12]}@example.invalid"
        admin_id, admin_token = self.invite_and_accept(owner_a, admin_email, "admin")
        worker_email = f"worker-{uuid.uuid4().hex[:12]}@example.invalid"

        self.assertEqual(self.client.post("/staff/invitations", headers=admin_token, json={
            "email": f"admin2-{uuid.uuid4().hex[:12]}@example.invalid", "role": "admin",
        }).status_code, 403)

        invitation = self.client.post("/staff/invitations", headers=admin_token, json={
            "email": worker_email, "role": "employee", "organization_id": self.organizations[1],
        })
        self.assertEqual(invitation.status_code, 201, invitation.text)
        accepted = self.client.post("/auth/invitations/accept", json={
            "token": invitation.json()["invite_token"],
            "password": "worker-personal-password",
        })
        self.assertEqual(accepted.status_code, 200, accepted.text)
        login = self.client.post("/auth/login", json={
            "email": worker_email, "password": "worker-personal-password",
            "organization": self.slugs[0],
        })
        self.assertEqual(login.status_code, 200, login.text)
        worker_id = login.json()["user_id"]
        employee_token = {"Authorization": "Bearer " + login.json()["access_token"]}
        self.users.append((worker_id, worker_email))
        wrong_org = self.client.post("/auth/login", json={
            "email": worker_email, "password": "worker-personal-password",
            "organization": self.slugs[1],
        })
        self.assertEqual(wrong_org.status_code, 401)

        self.assertEqual(self.client.get("/staff", headers=employee_token).status_code, 403)
        self.assertEqual(self.client.post("/staff/invitations", headers=employee_token, json={
            "email": "another@example.invalid", "role": "employee",
        }).status_code, 403)
        self.assertEqual(self.client.post(f"/staff/{admin_id}/disable",
                                          headers=employee_token).status_code, 403)
        self.assertEqual(self.client.post(f"/staff/{admin_id}/disable",
                                          headers=admin_token).status_code, 403)
        self.assertEqual(self.client.post(f"/staff/{worker_id}/disable",
                                          headers=owner_b).status_code, 404)
        self.assertEqual(self.client.get("/analyses", headers=employee_token).status_code, 200)
        self.assertEqual(self.client.post(f"/staff/{worker_id}/disable",
                                          headers=admin_token).status_code, 200)
        self.assertEqual(self.client.get("/analyses", headers=employee_token).status_code, 401)
        self.assertEqual(self.client.post(f"/staff/{self.users[0][0]}/disable",
                                          headers=admin_token).status_code, 403)
        self.assertEqual(self.client.post(f"/staff/{self.users[0][0]}/disable",
                                          headers=owner_a).status_code, 403)
        self.assertEqual(self.client.post(f"/staff/{admin_id}/disable",
                                          headers=owner_a).status_code, 200)
        self.assertEqual(self.client.get("/staff", headers=admin_token).status_code, 401)

    def test_invitation_duplicate_existing_user_and_invalid_input(self):
        owner_a, owner_b = self.headers
        existing_email = self.users[0][1]
        for owner in (owner_a, owner_b):
            existing = self.client.post("/staff/invitations", headers=owner, json={
                "email": existing_email, "role": "employee"
            })
            self.assertEqual(existing.status_code, 409, existing.text)
        for bad in (
            {"email": "invalid", "role": "employee"},
            {"email": "valid@example.invalid", "role": "owner"},
        ):
            response = self.client.post("/staff/invitations", headers=owner_a, json=bad)
            self.assertEqual(response.status_code, 422, response.text)

    def test_password_rotation_rejects_all_previous_tokens(self):
        email = f"password-{uuid.uuid4().hex[:12]}@example.invalid"
        user_id, headers = self.invite_and_accept(
            self.headers[0], email, "employee", password="previous-personal-password"
        )
        self.assertEqual(self.client.post("/auth/password", headers=headers, json={
            "current_password": "incorrect-password",
            "new_password": "new-strong-personal-password",
        }).status_code, 400)
        self.assertEqual(self.client.get("/analyses", headers=headers).status_code, 200)
        changed = self.client.post("/auth/password", headers=headers, json={
            "current_password": "previous-personal-password",
            "new_password": "new-strong-personal-password",
        })
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertTrue(changed.json()["reauthentication_required"])
        self.assertEqual(self.client.get("/analyses", headers=headers).status_code, 401)
        self.assertEqual(self.client.post("/auth/password", headers=headers, json={
            "current_password": "new-strong-personal-password",
            "new_password": "another-password-attempt",
        }).status_code, 401)
        self.assertEqual(self.client.post("/auth/login", json={
            "email": email, "organization": self.slugs[0],
            "password": "previous-personal-password",
        }).status_code, 401)
        newer = self.client.post("/auth/login", json={
            "email": email, "organization": self.slugs[0],
            "password": "new-strong-personal-password",
        })
        self.assertEqual(newer.status_code, 200, newer.text)
        self.assertEqual(self.client.get("/analyses", headers={
            "Authorization": "Bearer " + newer.json()["access_token"]
        }).status_code, 200)
