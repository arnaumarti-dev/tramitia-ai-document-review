"""Check container runtime restrictions without any customer files."""
import errno
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.pdf_processing import _worker_launch, parse_pdf_document
from scripts.generate_demo_pdf import make_demo_pdf


class ContainerWorkerOptionsTests(unittest.TestCase):
    def test_fail_closed_for_unknown_backend_or_invalid_image(self):
        with patch.dict(os.environ, {"TRAMITIA_PDF_WORKER_BACKEND": "invalid"}):
            with self.assertRaises(Exception) as ctx:
                _worker_launch(50, 200_000)
            self.assertIn("Configuración", str(ctx.exception))
        with patch.dict(os.environ, {
            "TRAMITIA_PDF_WORKER_BACKEND": "container",
            "TRAMITIA_PDF_WORKER_IMAGE": "--privileged",
        }):
            with self.assertRaises(Exception):
                _worker_launch(50, 200_000)

    def test_container_command_has_no_network_mounts_or_secrets(self):
        with patch.dict(os.environ, {
            "TRAMITIA_PDF_WORKER_BACKEND": "container",
            "TRAMITIA_PDF_WORKER_IMAGE": "tramitia-pdf-worker:ci",
            "TRAMITIA_JWT_SECRET": "SECRET-NEVER-EXPOSED",
            "POSTGRES_PASSWORD": "SECRET-NEVER-EXPOSED",
            "DOCKER_HOST": "tcp://remote.example.invalid:2375",
            "DOCKER_CONTEXT": "remote",
        }):
            command, env = _worker_launch(50, 200_000)
        self.assertEqual(command[:3], ["docker", "--host", "unix:///var/run/docker.sock"])
        for expected in (
            "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=32",
            "--memory=768m", "--memory-swap=768m", "--cpus=1",
            "--user=65534:65534", "--log-driver=none", "--pull=never",
        ):
            self.assertIn(expected, command)
        self.assertEqual(command[-3:], ["tramitia-pdf-worker:ci", "50", "200000"])
        self.assertNotIn("--volume", command)
        self.assertNotIn("--mount", command)
        self.assertNotIn("--privileged", command)
        self.assertNotIn("DOCKER_HOST", env)
        self.assertNotIn("DOCKER_CONTEXT", env)
        self.assertNotIn("TRAMITIA_JWT_SECRET", env)
        self.assertNotIn("POSTGRES_PASSWORD", env)


@unittest.skipUnless(
    os.environ.get("TRAMITIA_CONTAINER_PDF_TEST") == "1",
    "Needs a locally built disposable Docker worker image",
)
class RealContainerWorkerTests(unittest.TestCase):
    def setUp(self):
        self.original = os.environ.get("TRAMITIA_PDF_WORKER_BACKEND")
        os.environ["TRAMITIA_PDF_WORKER_BACKEND"] = "container"

    def tearDown(self):
        if self.original is None:
            os.environ.pop("TRAMITIA_PDF_WORKER_BACKEND", None)
        else:
            os.environ["TRAMITIA_PDF_WORKER_BACKEND"] = self.original

    def test_fictional_document_really_processed_in_container(self):
        text, count = parse_pdf_document(make_demo_pdf())
        self.assertEqual(count, 1)
        self.assertIn("Solicitud ficticia", text)

    def test_restricted_container_has_no_host_file_and_no_external_network(self):
        # The image contains no app secrets or host mounts. The probe uses
        # only the image's Python runtime, not untrusted PDF content.
        with tempfile.NamedTemporaryFile(prefix="tramitia-host-secret-", delete=True) as sentinel:
            host_file = sentinel.name
            command, environment = _worker_launch(50, 200_000)
            image = os.environ.get("TRAMITIA_PDF_WORKER_IMAGE", "tramitia-pdf-worker:local")
            idx = command.index(image)
            script = (
                "import errno,os,socket,sys;"
                "assert not os.path.exists(sys.argv[1]);"
                "assert 'POSTGRES_PASSWORD' not in os.environ;"
                "s=socket.socket();s.settimeout(1);"
                "code=s.connect_ex(('192.0.2.1',443));"
                "assert code in (errno.ENETUNREACH,errno.EHOSTUNREACH),code;"
                "print('sandbox-probes-pass')"
            )
            command[idx:]=["--entrypoint", "python", image, "-c", script, host_file]
            output = subprocess.run(
                command, input=b"", capture_output=True, check=False,
                timeout=20, env=environment,
            )
            self.assertEqual(output.returncode, 0, output.stderr.decode("utf-8", "replace"))
            self.assertIn(b"sandbox-probes-pass", output.stdout)


if __name__ == "__main__":
    unittest.main()
