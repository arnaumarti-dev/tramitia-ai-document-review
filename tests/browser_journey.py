"""Real Chromium + FastAPI + PostgreSQL smoke journey with only fictional data.

Run against an *isolated test database*. This test will create and delete
organizations/users and is not safe to point at a real client database.
The server must already be running on TRAMITIA_BROWSER_BASE_URL (localhost).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from psycopg2 import sql
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.db import get_conn
from scripts import bootstrap_admin

BASE_URL = os.environ.get("TRAMITIA_BROWSER_BASE_URL", "http://127.0.0.1:8000")
OWNER_PASSWORD = "Demo-owner-password-123!"
EMPLOYEE_PASSWORD = "Demo-employee-password-456!"
CHANGED_PASSWORD = "Demo-employee-new-password-789!"


def fictional_pdf() -> bytes:
    """A searchable PDF with plainly fictional administrative details."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=460, height=600)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
    })
    stream = DecodedStreamObject()
    stream.set_data(
        b"BT /F1 12 Tf 20 540 Td "
        b"(Solicitud ficticia. Declaro presentar esta ayuda en fecha 24/10/2026. "
        b"Correo demo@example.invalid, telefono 612345678. "
        b"Calle Inventada 1. Firma de prueba. ) Tj ET"
    )
    page[NameObject("/Contents")] = stream
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def bootstrap_test_organization(slug: str, owner_email: str) -> None:
    """Run the actual interactive bootstrap entrypoint, with mocked test input."""
    argv = [
        "bootstrap_admin", "--slug", slug, "--name", "Gestoría Ficticia E2E",
        "--email", owner_email,
    ]
    with patch.object(sys, "argv", argv), patch(
        "getpass.getpass", side_effect=[OWNER_PASSWORD, OWNER_PASSWORD]
    ), contextlib.redirect_stdout(io.StringIO()):
        bootstrap_admin.main()


@unittest.skipUnless(
    os.environ.get("TRAMITIA_TEST_POSTGRES") == "1"
    and os.environ.get("TRAMITIA_BROWSER_E2E") == "1",
    "Requires explicitly enabled disposable PostgreSQL and Chromium",
)
class CompleteGestoriaBrowserJourney(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        nonce = uuid.uuid4().hex[:12]
        cls.slug_a = f"demo-gestoria-a-{nonce}"
        cls.slug_b = f"demo-gestoria-b-{nonce}"
        cls.owner_a = f"owner-a-{nonce}@example.invalid"
        cls.owner_b = f"owner-b-{nonce}@example.invalid"
        cls.employee = f"empleado-{nonce}@example.invalid"
        cls.filename = f"solicitud-ficticia-{nonce}.pdf"
        cls.slugs = [cls.slug_a, cls.slug_b]
        bootstrap_test_organization(cls.slug_a, cls.owner_a)
        bootstrap_test_organization(cls.slug_b, cls.owner_b)

    @classmethod
    def tearDownClass(cls):
        # The test fixture never deletes or modifies any data outside its own org IDs.
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM organizations WHERE slug = ANY(%s)", (cls.slugs,)
                )
                org_ids = [row[0] for row in cur.fetchall()]
                cur.execute(
                    "SELECT user_id FROM memberships WHERE organization_id = ANY(%s)",
                    (org_ids,),
                )
                user_ids = [row[0] for row in cur.fetchall()]
                cur.execute(
                    "DELETE FROM staff_invitations WHERE organization_id = ANY(%s)", (org_ids,)
                )
                cur.execute(
                    "DELETE FROM analyses WHERE organization_id = ANY(%s)", (org_ids,)
                )
                cur.execute(
                    "DELETE FROM memberships WHERE organization_id = ANY(%s)", (org_ids,)
                )
                cur.execute(
                    "DELETE FROM users WHERE id = ANY(%s)", (user_ids,)
                )
                cur.execute(
                    "DELETE FROM organizations WHERE id = ANY(%s)", (org_ids,)
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def login(page, slug: str, email: str, password: str):
        page.locator("#loginOrg").fill(slug)
        page.locator("#loginEmail").fill(email)
        page.locator("#loginPassword").fill(password)
        page.locator("#btnLogin").click()
        page.locator("#workspace").wait_for(state="visible")
        page.locator("#sessionInfo").wait_for(state="visible")

    def test_owner_invitation_employee_document_history_and_password(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                # An actual owner signs in, invites someone and copies a one-time URL.
                context_a = browser.new_context(accept_downloads=True)
                owner_page = context_a.new_page()
                owner_page.goto(BASE_URL + "/", wait_until="domcontentloaded")
                self.login(owner_page, self.slug_a, self.owner_a, OWNER_PASSWORD)
                owner_page.locator("#staffPanel").wait_for(state="visible")
                owner_page.locator("#staffEmail").fill(self.employee)
                owner_page.locator("#staffRole").select_option("employee")
                owner_page.locator("#btnAddStaff").click()
                owner_page.locator("#staffInviteLinkPanel").wait_for(state="visible")
                invite_url = owner_page.locator("#staffInviteLink").input_value()
                self.assertTrue(invite_url.startswith(BASE_URL + "/#invite="))
                self.assertIn("&org=" + self.slug_a, invite_url)
                self.assertNotIn(EMPLOYEE_PASSWORD, invite_url)

                # Separate browser profile simulates the employee opening that link.
                context_employee = browser.new_context(accept_downloads=True)
                employee_page = context_employee.new_page()
                employee_page.goto(invite_url, wait_until="domcontentloaded")
                employee_page.locator("#invitePanel").wait_for(state="visible")
                self.assertNotIn("invite=", employee_page.url)
                employee_page.locator("#inviteNewPassword").fill(EMPLOYEE_PASSWORD)
                employee_page.locator("#inviteConfirmPassword").fill(EMPLOYEE_PASSWORD)
                employee_page.locator("#btnAcceptInvite").click()
                employee_page.locator("#loginPanel").wait_for(state="visible")
                self.login(employee_page, self.slug_a, self.employee, EMPLOYEE_PASSWORD)
                self.assertFalse(employee_page.locator("#staffPanel").is_visible())

                # Upload a truly parseable fictional PDF through the browser UI.
                employee_page.locator("#file").set_input_files({
                    "name": self.filename,
                    "mimeType": "application/pdf",
                    "buffer": fictional_pdf(),
                })
                employee_page.locator("#btnAnalyze").click()
                employee_page.locator("#analysisCard .result-card").wait_for(state="visible")
                self.assertIn(
                    self.filename, employee_page.locator("#analysisCard").inner_text()
                )
                history_row = employee_page.locator(
                    "#historyBody tr", has_text=self.filename
                )
                history_row.wait_for(state="visible")
                analysis_id = int(history_row.get_attribute("data-id"))
                self.assertGreater(analysis_id, 0)

                # Both members of A may see the result. B must not see or export it.
                owner_page.locator("#btnRefresh").click()
                owner_page.locator(
                    "#historyBody tr", has_text=self.filename
                ).wait_for(state="visible")

                context_b = browser.new_context()
                other_page = context_b.new_page()
                other_page.goto(BASE_URL + "/", wait_until="domcontentloaded")
                self.login(other_page, self.slug_b, self.owner_b, OWNER_PASSWORD)
                other_page.locator("#historyBody").wait_for(state="visible")
                self.assertNotIn(self.filename, other_page.locator("#historyBody").inner_text())
                forbidden = other_page.evaluate(
                    """async id => {
                        const headers = {Authorization: 'Bearer ' + authToken};
                        const detail = await fetch('/analyses/' + id, {headers});
                        const exported = await fetch('/analyses/' + id + '/export', {headers});
                        return [detail.status, exported.status];
                    }""",
                    analysis_id,
                )
                self.assertEqual(forbidden, [404, 404])

                # Download a real export via authenticated fetch/blob in Chromium.
                with employee_page.expect_download() as download_info:
                    history_row.locator("button[data-export-id]").click()
                download = download_info.value
                self.assertEqual(download.suggested_filename, f"analysis_{analysis_id}.json")
                with open(download.path(), encoding="utf-8") as stream:
                    exported = json.load(stream)
                self.assertEqual(exported["id"], analysis_id)
                self.assertEqual(exported["filename"], self.filename)
                self.assertIsNone(exported["extracted_text"])
                self.assertEqual(len(exported["text_sha256"]), 64)

                # Employee changes password; previous session stops working.
                employee_page.locator("#currentPassword").fill(EMPLOYEE_PASSWORD)
                employee_page.locator("#newPassword").fill(CHANGED_PASSWORD)
                employee_page.locator("#newPasswordRepeat").fill(CHANGED_PASSWORD)
                employee_page.locator("#btnChangePassword").click()
                employee_page.locator("#workspace").wait_for(state="hidden")
                self.login(employee_page, self.slug_a, self.employee, CHANGED_PASSWORD)
                employee_page.locator(
                    "#historyBody tr", has_text=self.filename
                ).wait_for(state="visible")

                context_b.close()
                context_employee.close()
                context_a.close()
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
