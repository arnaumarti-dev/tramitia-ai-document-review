"""AUD-B02: read-only host preflight for experimental Linux upload spooling.

Run on the intended LAB host after installing and restarting NGINX/systemd.
No writes, no service restarts, no network calls, and no customer documents.
Passing these checks is NOT an independent security or privacy audit.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
try:
    import pwd
except ImportError:  # Windows local development can still import tests.
    pwd = None
import re
import stat
import subprocess
import sys

from app.upload_temporary_storage import _filesystem_type


class HostCheckError(RuntimeError):
    pass


WEB_UPLOAD_DIR = Path("/run/tramitia-upload")
NGINX_UPLOAD_DIR = Path("/run/tramitia-nginx-body")


def verify_private_dir(path: Path, uid: int, mountinfo: str) -> None:
    """Enforce a real, owner-only directory on the effective tmpfs mount."""
    if not path.is_absolute():
        raise HostCheckError("Temporary directory must be absolute")
    try:
        # Resolve even symlinks in an ancestor, not merely at the leaf.
        resolved = path.resolve(strict=True)
        info = path.lstat()
    except (OSError, RuntimeError) as exc:
        raise HostCheckError(f"{path}: temporary directory missing or inaccessible") from exc
    if path != resolved:
        raise HostCheckError(f"{path}: symlinked temporary path")
    if not stat.S_ISDIR(info.st_mode):
        raise HostCheckError(f"{path}: expected a directory")
    if info.st_uid != uid:
        raise HostCheckError(f"{path}: wrong owner UID")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise HostCheckError(f"{path}: expected mode 0700")
    if _filesystem_type(path, mountinfo) != "tmpfs":
        raise HostCheckError(f"{path}: not backed by effective tmpfs mount")


def verify_no_swap(swaps: str) -> None:
    """Fail closed for active swap; encryption cannot be inferred here."""
    if len(swaps.splitlines()) > 1:
        raise HostCheckError(
            "Active swap detected. Verify an encrypted/ephemeral swap policy "
            "independently before allowing confidential material."
        )


def verify_systemd_service(properties: dict[str, str], unit: str) -> None:
    """Check the *loaded* service settings; do not print environment secrets."""
    expected = {
        "ActiveState": "active",
        "User": "tramitia-web",
        "Group": "tramitia-web",
        "RuntimeDirectoryMode": "0700",
        "UMask": "0077",
        "LimitCORE": "0",
    }
    for field, target in expected.items():
        value = properties.get(field, "").strip()
        if field in ("RuntimeDirectoryMode", "UMask"):
            try:
                # systemd emits octal mode strings, sometimes without leading 0.
                same = int(value, 8) == int(target, 8)
            except ValueError:
                same = False
        else:
            same = value == target
        if not same:
            raise HostCheckError(f"{unit}: unsafe or missing {field}")
    runtime_dirs = properties.get("RuntimeDirectory", "").split()
    if "tramitia-upload" not in runtime_dirs:
        raise HostCheckError(f"{unit}: missing private RuntimeDirectory")
    environment = properties.get("Environment", "")
    if not re.search(r"(?:^|\s)TMPDIR=/run/tramitia-upload(?:\s|$)", environment):
        raise HostCheckError(f"{unit}: TMPDIR not configured in the loaded unit")


def verify_nginx_config(config: str) -> None:
    # nginx -T includes comments and all included configuration files.
    directives = "\n".join(line.split("#", 1)[0] for line in config.splitlines())
    expected = (
        r"\bclient_body_temp_path\s+/run/tramitia-nginx-body\s*;",
        r"\bclient_max_body_size\s+21m\s*;",
        r"\bproxy_http_version\s+1\.1\s*;",
    )
    for pattern in expected:
        if not re.search(pattern, directives):
            raise HostCheckError("Loaded NGINX configuration lacks an upload control")
    blocks = re.findall(
        r"\blocation\s*=\s*/analyze\s*\{([^{}]*)\}", directives, flags=re.DOTALL,
    )
    if len(blocks) != 1 or not re.search(r"\bproxy_request_buffering\s+off\s*;", blocks[0]):
        raise HostCheckError("NGINX /analyze does not unambiguously disable request buffering")


def _run_read_only(*args: str) -> str:
    try:
        result = subprocess.run(
            args, check=True, text=True, capture_output=True, timeout=15,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        # Avoid leaking unit EnvironmentFile secrets or TLS keys via tool output.
        raise HostCheckError(f"Unable to inspect {args[0]} configuration") from exc
    return result.stdout + ("\n" + result.stderr if args[0] == "nginx" else "")


def verify_host(web_unit: str, nginx_user: str) -> None:
    if sys.platform != "linux" or pwd is None:
        raise HostCheckError("This preflight supports Linux only")
    if web_unit not in ("tramitia-web.service", "tramitia-web-daemonless.service"):
        raise HostCheckError("Unsupported experimental web unit")
    try:
        web_uid = pwd.getpwnam("tramitia-web").pw_uid
        nginx_uid = pwd.getpwnam(nginx_user).pw_uid
    except KeyError as exc:
        raise HostCheckError("Configured system user is missing") from exc
    try:
        mounts = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        swaps = Path("/proc/swaps").read_text(encoding="utf-8")
    except OSError as exc:
        raise HostCheckError("Cannot inspect Linux mounts or swap") from exc
    verify_private_dir(WEB_UPLOAD_DIR, web_uid, mounts)
    verify_private_dir(NGINX_UPLOAD_DIR, nginx_uid, mounts)
    verify_no_swap(swaps)

    fields = (
        "ActiveState,User,Group,RuntimeDirectory,RuntimeDirectoryMode,"
        "Environment,UMask,LimitCORE"
    )
    # Environment output may contain secrets. Parse it without echoing it.
    properties_output = _run_read_only("systemctl", "show", "--no-pager",
                                       "--property=" + fields, web_unit)
    props = dict(line.split("=", 1) for line in properties_output.splitlines() if "=" in line)
    verify_systemd_service(props, web_unit)
    verify_nginx_config(_run_read_only("nginx", "-T"))

    print("PASS: private web + NGINX tmpfs ownership, permissions and mounts")
    print("PASS: no active swap and loaded systemd/NGINX settings inspected")
    print("LIMITATION: not a reboot test, security audit, or proof of memory erasure.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--web-unit", default="tramitia-web-daemonless.service",
        choices=("tramitia-web.service", "tramitia-web-daemonless.service"),
    )
    parser.add_argument("--nginx-user", required=True, help="actual NGINX worker account")
    args = parser.parse_args()
    try:
        verify_host(args.web_unit, args.nginx_user)
    except HostCheckError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
