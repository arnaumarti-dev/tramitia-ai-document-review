"""AUD-B03: guard the parser container's write surface in reference manifests.

This does not prove sandbox escape resistance or per-document isolation.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "split-pdf-services.compose.yml"
SMOKE = ROOT / "tests" / "smoke_split_pdf_services.sh"


class ParserWritableSurfaceTests(unittest.TestCase):
    def test_parser_has_no_shared_tmpfs_or_docker_socket(self):
        manifest = COMPOSE.read_text(encoding="utf-8")
        self.assertEqual(manifest.count("\n  pdf-parser:\n"), 1)
        self.assertEqual(manifest.count("\n  pdf-broker:\n"), 1)
        parser_section = manifest.split("\n  pdf-parser:\n", 1)[1].split(
            "\n  pdf-broker:\n", 1
        )[0]
        for setting in (
            "user: \"65533:65533\"",
            "read_only: true",
            "network_mode: none",
            "cap_drop: [ALL]",
            "no-new-privileges:true",
            "TRAMITIA_PARSER_ONE_SHOT: \"1\"",
            "source: /run/tramitia-parser",
        ):
            with self.subTest(setting=setting):
                self.assertIn(setting, parser_section)
        for forbidden in (
            "tmpfs:",
            "source: /run/tramitia\n",
            "/var/run/docker.sock",
            "privileged: true",
            "network_mode: host",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, parser_section)

    def test_smoke_checks_read_only_tmp_and_ongoing_socket_mount_risk(self):
        probe = SMOKE.read_text(encoding="utf-8")
        self.assertIn('"/tmp/tramitia-b03-probe-must-not-exist"', probe)
        self.assertIn("KNOWN B03 LIMITATION", probe)
        self.assertIn("b03-synthetic-marker", probe)
        self.assertIn('! sudo test -e "$parser_dir/b03-synthetic-marker"', probe)
        self.assertIn('docker exec "$parser" rm -f', probe)


if __name__ == "__main__":
    unittest.main()
