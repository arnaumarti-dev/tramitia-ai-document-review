"""AUD-B02: synthetic RAM-backed multipart spooling and crash-recovery tests."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
os.environ.setdefault("TRAMITIA_JWT_SECRET", "synthetic-combined-b02-h06-test-secret-32-characters")
from app.auth import Principal, issue_token
from app.main import app
from app.upload_temporary_storage import _filesystem_type, verify_private_upload_tmpfs


def require_shm(test):
    if sys.platform != "linux" or not Path("/dev/shm").is_dir():
        test.skipTest("Linux /dev/shm tmpfs required")


class PrivateUploadStorageTests(unittest.TestCase):
    def test_mount_selection_uses_most_specific_mount(self):
        mounts = (
            "10 1 0:2 / / rw - ext4 /dev/root rw\n"
            "11 10 0:3 / /run rw - tmpfs tmpfs rw\n"
            "12 11 0:4 / /run/not-private rw - ext4 /dev/other rw\n"
        )
        self.assertEqual(_filesystem_type(Path("/run/tramitia-upload"), mounts), "tmpfs")
        self.assertEqual(_filesystem_type(Path("/run/not-private/upload"), mounts), "ext4")

    def test_isolated_guard_accepts_private_tmpfs_but_rejects_unsafe_modes(self):
        require_shm(self)
        with tempfile.TemporaryDirectory(prefix="tramitia-b02-", dir="/dev/shm") as folder:
            path = Path(folder)
            path.chmod(0o700)
            with patch.dict(os.environ, {"TMPDIR": folder}), patch("tempfile.tempdir", None):
                verify_private_upload_tmpfs()
                path.chmod(0o755)
                try:
                    with self.assertRaisesRegex(RuntimeError, "mode 0700"):
                        verify_private_upload_tmpfs()
                finally:
                    path.chmod(0o700)
                with patch("app.upload_temporary_storage._filesystem_type", return_value="ext4"):
                    with self.assertRaisesRegex(RuntimeError, "tmpfs"):
                        verify_private_upload_tmpfs()
                with patch("tempfile.tempdir", "/tmp"):
                    with self.assertRaisesRegex(RuntimeError, "not using"):
                        verify_private_upload_tmpfs()

    def test_isolated_guard_rejects_missing_and_symlinked_dir(self):
        require_shm(self)
        with tempfile.TemporaryDirectory(prefix="tramitia-b02-", dir="/dev/shm") as folder:
            link = Path(folder) / "link"
            target = Path(folder) / "target"
            target.mkdir(mode=0o700)
            link.symlink_to(target, target_is_directory=True)
            with patch.dict(os.environ, {"TMPDIR": ""}):
                with self.assertRaisesRegex(RuntimeError, "absolute"):
                    verify_private_upload_tmpfs()
            with patch.dict(os.environ, {"TMPDIR": str(link)}):
                with self.assertRaisesRegex(RuntimeError, "non-symlinked"):
                    verify_private_upload_tmpfs()

    def test_full_20_mib_multipart_spools_in_private_tmpfs_and_is_closed(self):
        require_shm(self)
        import starlette.formparsers as formparsers
        with tempfile.TemporaryDirectory(prefix="tramitia-b02-", dir="/dev/shm") as folder:
            Path(folder).chmod(0o700)
            observed = []
            original = formparsers.SpooledTemporaryFile

            def recording_spool(*args, **kwargs):
                item = original(*args, **kwargs)
                observed.append(item)
                return item

            data = b"%PDF-1.7\n" + b"A" * (20 * 1024 * 1024 - 9)
            # Exercise BOTH H06's pre-multipart membership gate and the
            # endpoint re-check, with a synthetic signed token and no DB.
            token = issue_token(Principal(1, 1, "owner"))
            with patch.dict(os.environ, {"TMPDIR": folder}), patch("tempfile.tempdir", None), patch.object(
                formparsers, "SpooledTemporaryFile", side_effect=recording_spool
            ), patch("app.auth.get_membership_role", return_value="owner") as membership, patch(
                "app.main._consume_peer_budget"
            ), patch("app.main.consume_analysis_quota"), patch(
                "app.main.save_analysis", return_value=99
            ), patch(
                "app.main.parse_pdf_document",
                return_value=("Solicitud ficticia para pruebas de seguridad.", 1),
            ) as parse:
                response = TestClient(app, raise_server_exceptions=False).post(
                    "/analyze",
                    headers={"Authorization": "Bearer " + token},
                    files={"file": ("synthetic.pdf", data, "application/pdf")},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["id"], 99)
                self.assertEqual(len(parse.call_args.args[0]), 20 * 1024 * 1024)
                self.assertGreaterEqual(membership.call_count, 2)
            self.assertTrue(observed, "Starlette did not create an upload spool")
            self.assertTrue(any(getattr(item, "_rolled", False) for item in observed))
            self.assertTrue(all(item.closed for item in observed))
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_crashed_worker_releases_unlinked_spool(self):
        require_shm(self)
        with tempfile.TemporaryDirectory(prefix="tramitia-b02-", dir="/dev/shm") as folder:
            code = (
                "import tempfile,time; "
                "f=tempfile.SpooledTemporaryFile(max_size=1024*1024); "
                "f.write(b'Z'*(3*1024*1024)); f.flush(); "
                "print('READY',flush=True); time.sleep(120)"
            )
            child = subprocess.Popen(
                [sys.executable, "-c", code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "TMPDIR": folder, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            try:
                self.assertEqual(child.stdout.readline().strip(), "READY")
                child.kill()  # Simulate SIGKILL, not a graceful close().
                child.wait(timeout=10)
                self.assertEqual(child.returncode, -signal.SIGKILL)
                self.assertEqual(list(Path(folder).iterdir()), [])
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=10)
                child.stdout.close()
                child.stderr.close()

    def test_reference_nginx_uses_nonbuffered_uploads_and_private_temp_path(self):
        config = Path("deploy/nginx/nginx.conf").read_text()
        self.assertIn("client_body_temp_path /run/tramitia-nginx-body;", config)
        upload_block = config.split("location = /analyze {", 1)[1].split("}", 1)[0]
        self.assertIn("proxy_request_buffering off;", upload_block)
        self.assertIn("proxy_http_version 1.1;", config)
        for unit in ("tramitia-web.service", "tramitia-web-daemonless.service"):
            systemd = Path("deploy/systemd", unit).read_text()
            for item in ("Environment=TMPDIR=/run/tramitia-upload",
                         "RuntimeDirectory=tramitia-upload",
                         "RuntimeDirectoryMode=0700",
                         "UMask=0077", "LimitCORE=0"):
                self.assertIn(item, systemd)


if __name__ == "__main__":
    unittest.main()
