"""Fail-closed verification of RAM-backed multipart upload spooling.

FastAPI/Starlette's UploadFile can spill a SpooledTemporaryFile to the
filesystem. PrivateTmp alone is not proof that its backing store is RAM.
Only invoked for the experimental isolated Linux profile.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import tempfile


def _unescape_mount_path(value: str) -> str:
    # /proc/self/mountinfo encodes spaces and other characters as octal.
    return re.sub(r"\\([0-7]{3})", lambda item: chr(int(item[1], 8)), value)


def _filesystem_type(directory: Path, mountinfo: str) -> str | None:
    """Choose the deepest mount containing directory (not just /run)."""
    best_depth = -1
    filesystem = None
    for line in mountinfo.splitlines():
        before, separator, after = line.partition(" - ")
        if not separator:
            continue
        fields = before.split()
        right = after.split()
        if len(fields) < 5 or not right:
            continue
        mountpoint = Path(_unescape_mount_path(fields[4]))
        if directory == mountpoint or mountpoint in directory.parents:
            depth = len(mountpoint.parts)
            if depth > best_depth:
                best_depth = depth
                filesystem = right[0]
    return filesystem


def verify_private_upload_tmpfs() -> None:
    """Reject isolated startup unless multipart temp files stay on private tmpfs."""
    configured = os.environ.get("TMPDIR", "")
    path = Path(configured)
    if not configured or not path.is_absolute() or path != path.resolve():
        raise RuntimeError("Isolated upload TMPDIR must be an absolute, non-symlinked directory")
    try:
        details = path.stat()
    except OSError as exc:
        raise RuntimeError("Isolated upload TMPDIR is unavailable") from exc
    if (not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.geteuid()
            or stat.S_IMODE(details.st_mode) & 0o077):
        raise RuntimeError("Isolated upload TMPDIR must be owned by the web UID and mode 0700")
    if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        raise RuntimeError("Isolated upload TMPDIR is not accessible to the web UID")
    # tempfile may have cached /tmp before the service was configured. Fail
    # instead of falsely claiming that setting TMPDIR redirects open files.
    if Path(tempfile.gettempdir()) != path:
        raise RuntimeError("Python tempfile is not using isolated upload TMPDIR")
    try:
        mounts = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("Cannot confirm isolated upload filesystem") from exc
    if _filesystem_type(path, mounts) != "tmpfs":
        raise RuntimeError("Isolated upload TMPDIR must reside on tmpfs")
