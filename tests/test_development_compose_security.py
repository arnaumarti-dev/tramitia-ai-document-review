"""Keep the intentionally insecure example-password PostgreSQL DB on loopback.

The Compose file is only a local development convenience, not a server
deployment. Exposing its known test password to the LAN is unacceptable.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"


class DevelopmentComposeSecurityTests(unittest.TestCase):
    def test_example_password_postgresql_only_publishes_to_loopback(self):
        text = COMPOSE.read_text(encoding="utf-8-sig")
        self.assertIn("POSTGRES_PASSWORD: tramitia", text)
        published = re.findall(r'^\s*-\s*"([^"]+:5432)"\s*$', text, re.MULTILINE)
        self.assertEqual(
            published, ["127.0.0.1:5432:5432"],
            "Never expose example-password PostgreSQL on all/LAN interfaces",
        )
        self.assertNotIn('      - "5432:5432"', text)


if __name__ == "__main__":
    unittest.main()
