"""Guard against mistakenly using the restore drill on non-CI databases."""
import os
import unittest
from unittest.mock import patch

from scripts.premerge_backup_restore_probe import _assert_disposable_ci


class BackupRestoreGuardTests(unittest.TestCase):
    def test_refuses_manual_and_remote_connections(self):
        good = {
            "CI": "true", "TRAMITIA_TEST_POSTGRES": "1",
            "POSTGRES_DB": "tramitia_test", "POSTGRES_HOST": "localhost",
            "POSTGRES_PORT": "5432", "POSTGRES_USER": "tramitia",
            "POSTGRES_PASSWORD": "synthetic-test-db-password",
        }
        with patch.dict(os.environ, good, clear=True):
            _assert_disposable_ci()
        for key, value in (
            ("CI", ""), ("TRAMITIA_TEST_POSTGRES", "0"),
            ("POSTGRES_DB", "tramitia"), ("POSTGRES_HOST", "db.example.com"),
            ("POSTGRES_PORT", "5433"), ("POSTGRES_USER", "prod_user"),
            ("POSTGRES_PASSWORD", ""),
        ):
            with self.subTest(key=key), patch.dict(
                os.environ, {**good, key: value}, clear=True
            ):
                with self.assertRaises(RuntimeError):
                    _assert_disposable_ci()


if __name__ == "__main__":
    unittest.main()
