"""AUD-B02 host verifier tests. No host changes or production documents."""
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

from scripts.verify_upload_host import (
    HostCheckError,
    verify_nginx_config,
    verify_no_swap,
    verify_private_dir,
    verify_systemd_service,
)

SAMPLE_NGINX = """
http {
  client_body_temp_path /run/tramitia-nginx-body;
  server { client_max_body_size 21m; proxy_http_version 1.1;
    location = /analyze {
      proxy_request_buffering off;
      proxy_pass http://127.0.0.1:8000;
    }
  }
}
"""

VALID_SERVICE = {
    "ActiveState": "active",
    "User": "tramitia-web",
    "Group": "tramitia-web",
    "RuntimeDirectory": "tramitia-upload",
    "RuntimeDirectoryMode": "0700",
    "UMask": "0077",
    "LimitCORE": "0",
    "Environment": "PYTHONDONTWRITEBYTECODE=1 TMPDIR=/run/tramitia-upload",
}


class HostPreflightTests(unittest.TestCase):
    def test_nginx_active_directives_required_not_commented_or_other_path(self):
        verify_nginx_config(SAMPLE_NGINX)
        for replacement in (
            "# proxy_request_buffering off;",
            "proxy_request_buffering on;",
        ):
            with self.subTest(replacement=replacement):
                with self.assertRaises(HostCheckError):
                    verify_nginx_config(
                        SAMPLE_NGINX.replace("proxy_request_buffering off;", replacement)
                    )
        with self.assertRaises(HostCheckError):
            verify_nginx_config(SAMPLE_NGINX.replace(
                "client_body_temp_path /run/tramitia-nginx-body;",
                "client_body_temp_path /var/lib/nginx;",
            ))
        with self.assertRaises(HostCheckError):
            verify_nginx_config(SAMPLE_NGINX.replace(
                "client_max_body_size 21m;", "client_max_body_size 100m;"
            ))

    def test_loaded_systemd_settings_cannot_silently_weaken(self):
        verify_systemd_service(VALID_SERVICE, "tramitia-web.service")
        for key, replacement in (
            ("ActiveState", "inactive"),
            ("User", "root"),
            ("RuntimeDirectoryMode", "0777"),
            ("RuntimeDirectory", ""),
            ("UMask", "0022"),
            ("LimitCORE", "infinity"),
            ("Environment", "TMPDIR=/tmp"),
        ):
            with self.subTest(setting=key):
                weakened = dict(VALID_SERVICE, **{key: replacement})
                with self.assertRaises(HostCheckError):
                    verify_systemd_service(weakened, "tramitia-web.service")

    def test_swap_requires_explicit_resolution_and_never_guesses_encryption(self):
        verify_no_swap("Filename\tType\tSize\tUsed\tPriority\n")
        with self.assertRaisesRegex(HostCheckError, "swap"):
            verify_no_swap(
                "Filename\tType\tSize\tUsed\tPriority\n/dev/sda2 partition 1048576 0 -2\n"
            )

    @unittest.skipUnless(sys.platform == "linux" and Path("/dev/shm").exists(),
                         "Linux /dev/shm is required")
    def test_real_tmpfs_directory_permissions_and_symlink_are_checked(self):
        mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="tramitia-host-preflight-",
                                         dir="/dev/shm") as parent:
            folder = Path(parent)
            folder.chmod(0o700)
            verify_private_dir(folder, os.getuid(), mountinfo)
            with self.assertRaisesRegex(HostCheckError, "wrong owner UID"):
                verify_private_dir(folder, os.getuid() + 9999, mountinfo)
            folder.chmod(0o755)
            with self.assertRaisesRegex(HostCheckError, "mode 0700"):
                verify_private_dir(folder, os.getuid(), mountinfo)
            folder.chmod(0o700)
            symlink = folder.parent / (folder.name + "-symlink")
            symlink.symlink_to(folder, target_is_directory=True)
            try:
                with self.assertRaisesRegex(HostCheckError, "symlink"):
                    verify_private_dir(symlink, os.getuid(), mountinfo)
            finally:
                symlink.unlink()
            with self.assertRaisesRegex(HostCheckError, "tmpfs"):
                verify_private_dir(folder, os.getuid(), (
                    "20 1 0:2 / / rw - ext4 /dev/root rw\n"
                ))


if __name__ == "__main__":
    unittest.main()
