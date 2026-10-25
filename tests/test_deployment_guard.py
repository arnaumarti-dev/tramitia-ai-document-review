"""Assert the web/broker privilege boundary and deployment reference defaults."""
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.deployment_guard import validate_web_deployment
from app.main import app
from app.pdf_broker import create_server
from scripts.generate_demo_pdf import make_demo_pdf


class IsolatedWebStartupTests(unittest.TestCase):
    def setUp(self):
        if not hasattr(socket, "AF_UNIX") or os.name != "posix":
            self.skipTest("POSIX Unix socket needed")
        self.folder = tempfile.TemporaryDirectory(prefix="tramitia-isolated-")
        self.path = str(Path(self.folder.name) / "broker.sock")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(1)
        os.chmod(self.path, 0o660)
        self.env = patch.dict(os.environ, {
            "TRAMITIA_DEPLOYMENT_PROFILE": "isolated",
            "TRAMITIA_PDF_WORKER_BACKEND": "broker",
            "TRAMITIA_BROKER_SOCKET": self.path,
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def tearDown(self):
        if hasattr(self, "sock"):
            self.sock.close()
            self.folder.cleanup()

    @staticmethod
    def fake_access(path, mode):
        if str(path) == "/var/run/docker.sock":
            return False
        return os.path.exists(path)

    def test_isolated_mode_startup_succeeds_only_with_private_socket(self):
        with patch("app.deployment_guard.os.geteuid", return_value=1000), patch(
            "app.deployment_guard.os.access", side_effect=self.fake_access
        ):
            validate_web_deployment()
            # Validate the real FastAPI startup hook, not only helper logic.
            with TestClient(app) as client:
                self.assertEqual(client.get("/health").status_code, 200)

    def test_rejects_root_and_unsafe_subprocess_fallback(self):
        with patch("app.deployment_guard.os.geteuid", return_value=0):
            with self.assertRaisesRegex(RuntimeError, "root"):
                validate_web_deployment()
        with patch("app.deployment_guard.os.geteuid", return_value=1000), patch.dict(
            os.environ, {"TRAMITIA_PDF_WORKER_BACKEND": "subprocess"}
        ):
            with self.assertRaisesRegex(RuntimeError, "broker"):
                validate_web_deployment()

    def test_web_account_must_not_access_docker_socket(self):
        with patch("app.deployment_guard.os.geteuid", return_value=1000), patch(
            "app.deployment_guard.Path.exists", return_value=True
        ), patch("app.deployment_guard.os.access", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "Docker"):
                validate_web_deployment()

    def test_fail_closed_when_broker_absent_or_insecure(self):
        with patch("app.deployment_guard.os.geteuid", return_value=1000), patch(
            "app.deployment_guard.os.access", side_effect=self.fake_access
        ):
            with patch.dict(os.environ, {
                "TRAMITIA_BROKER_SOCKET": self.path + ".missing"
            }):
                with self.assertRaisesRegex(RuntimeError, "unavailable"):
                    validate_web_deployment()
            os.chmod(self.path, 0o666)
            with self.assertRaisesRegex(RuntimeError, "world writable"):
                validate_web_deployment()
            os.chmod(self.path, 0o660)
            fake_link = str(Path(self.folder.name) / "symlink.sock")
            Path(fake_link).symlink_to(self.path)
            with patch.dict(os.environ, {"TRAMITIA_BROKER_SOCKET": fake_link}):
                with self.assertRaisesRegex(RuntimeError, "symlinked"):
                    validate_web_deployment()

    def test_unknown_profile_is_rejected(self):
        with patch.dict(os.environ, {
            "TRAMITIA_DEPLOYMENT_PROFILE": "production-please",
        }):
            with self.assertRaisesRegex(RuntimeError, "Unknown"):
                validate_web_deployment()


class ReferenceUnitTests(unittest.TestCase):
    def test_web_unit_cannot_access_docker_and_requires_broker(self):
        content = Path("deploy/systemd/tramitia-web.service").read_text()
        for required in (
            "User=tramitia-web", "Group=tramitia-web",
            "TRAMITIA_DEPLOYMENT_PROFILE=isolated",
            "TRAMITIA_PDF_WORKER_BACKEND=broker",
            "InaccessiblePaths=-/run/docker.sock",
            "--host 127.0.0.1", "--no-proxy-headers",
            "NoNewPrivileges=true", "ProtectSystem=strict",
        ):
            self.assertIn(required, content)
        self.assertNotIn("SupplementaryGroups=docker", content)

    def test_broker_unit_has_separate_identity_no_application_secrets(self):
        content = Path("deploy/systemd/tramitia-pdf-broker.service").read_text()
        for required in (
            "User=tramitia-broker", "Group=tramitia-web",
            "SupplementaryGroups=docker",
            "RuntimeDirectoryMode=0750",
            "TRAMITIA_BROKER_SOCKET_GROUP=tramitia-web",
            "TRAMITIA_PDF_WORKER_BACKEND=container",
            "NoNewPrivileges=true", "IPAddressDeny=any",
        ):
            self.assertIn(required, content)
        self.assertNotIn("EnvironmentFile=/etc/tramitia/web.env", content)
        self.assertNotIn("TRAMITIA_JWT_SECRET=", content)
        self.assertNotIn("POSTGRES_PASSWORD=", content)

    def test_socket_group_config_rejects_ambiguous_settings(self):
        with tempfile.TemporaryDirectory(prefix="tramitia-socket-options-") as folder:
            path = str(Path(folder) / "broker.sock")
            with patch.dict(os.environ, {
                "TRAMITIA_BROKER_SOCKET_GID": "123",
                "TRAMITIA_BROKER_SOCKET_GROUP": "tramitia-web",
            }):
                with self.assertRaisesRegex(RuntimeError, "either"):
                    create_server(path)
            self.assertFalse(Path(path).exists())


if __name__ == "__main__":
    unittest.main()
