"""Startup configuration guard for isolated Linux deployments.

The guard is enabled only with TRAMITIA_DEPLOYMENT_PROFILE=isolated.
It detects dangerous deployment mistakes; it is NOT a kernel sandbox and
cannot protect against a compromised process that can change its privileges.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import stat
import sys


def _require_explicit_isolated_secrets() -> None:
    """Refuse accidental development DB credentials in isolated deployments.

    Development defaults are intentionally retained for the local example,
    but they must never silently become production credentials. The secret
    values are neither printed nor included in error messages.
    """
    required = (
        "POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER",
        "POSTGRES_PASSWORD", "TRAMITIA_JWT_SECRET",
    )
    for name in required:
        if not os.environ.get(name, "").strip():
            raise RuntimeError(f"Isolated deployment requires explicit {name}")
    password = os.environ["POSTGRES_PASSWORD"].strip()
    jwt_secret = os.environ["TRAMITIA_JWT_SECRET"].strip()
    prohibited = {"tramitia", "password", "changeme", "replace_with_secret"}
    if password.lower() in prohibited:
        raise RuntimeError("Isolated deployment refuses a default database password")
    if (len(jwt_secret) < 32
            or jwt_secret.lower() in prohibited
            or jwt_secret.startswith("REPLACE_WITH_")):
        raise RuntimeError("Isolated deployment requires a non-placeholder JWT secret")


def validate_web_deployment() -> None:
    profile = os.getenv("TRAMITIA_DEPLOYMENT_PROFILE", "development")
    if profile == "development":
        return
    if profile != "isolated":
        raise RuntimeError("Unknown TramitIA deployment profile")
    _require_explicit_isolated_secrets()

    if sys.platform != "linux" or not hasattr(os, "geteuid"):
        raise RuntimeError("Isolated deployments require Linux/POSIX")
    if os.geteuid() == 0:
        raise RuntimeError("FastAPI must never start as root")
    if os.getenv("TRAMITIA_PDF_WORKER_BACKEND") != "broker":
        raise RuntimeError("Isolated FastAPI must use the PDF broker")

    # Docker group permissions or root-equivalent access in the web account
    # would nullify the separation from the privileged broker.
    docker_sock = Path("/var/run/docker.sock")
    if docker_sock.exists() and os.access(docker_sock, os.W_OK):
        raise RuntimeError("FastAPI account has access to Docker control socket")

    socket_path = Path(os.getenv(
        "TRAMITIA_BROKER_SOCKET", "/run/tramitia/pdf-broker.sock"
    ))
    if not socket_path.is_absolute() or socket_path.is_symlink():
        raise RuntimeError("Invalid or symlinked PDF broker socket path")
    directory = socket_path.parent
    if directory.is_symlink():
        raise RuntimeError("PDF broker socket directory must not be a symlink")
    try:
        directory_info = directory.stat()
    except OSError as exc:
        raise RuntimeError("PDF broker socket directory is unavailable") from exc
    if (not stat.S_ISDIR(directory_info.st_mode)
            or directory_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
        raise RuntimeError("PDF broker directory must not be group/world writable")
    # Owner can chmod a directory even if currently mode 0500. A web-owned
    # directory would permit replacing the socket after this startup guard.
    if directory_info.st_uid == os.geteuid():
        raise RuntimeError("FastAPI must not own the broker socket directory")
    if os.access(directory, os.W_OK):
        raise RuntimeError("FastAPI must not be able to replace the broker socket")
    try:
        socket_info = socket_path.stat()
    except OSError as exc:
        raise RuntimeError("PDF broker socket is unavailable") from exc
    if not stat.S_ISSOCK(socket_info.st_mode):
        raise RuntimeError("PDF broker socket is not a Unix socket")
    if socket_info.st_mode & stat.S_IWOTH:
        raise RuntimeError("PDF broker socket must not be world writable")
    if socket_info.st_uid in (0, os.geteuid()):
        raise RuntimeError("The broker socket must belong to a distinct non-root account")
    if not os.access(socket_path, os.W_OK):
        raise RuntimeError("FastAPI cannot access its private PDF broker socket")

    # No unsafe fallback is used when the broker dies after startup; the
    # request path already fails closed with HTTP 503.
