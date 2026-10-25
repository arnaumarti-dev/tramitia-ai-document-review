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


def validate_web_deployment() -> None:
    profile = os.getenv("TRAMITIA_DEPLOYMENT_PROFILE", "development")
    if profile == "development":
        return
    if profile != "isolated":
        raise RuntimeError("Unknown TramitIA deployment profile")

    if os.name != "posix" or not hasattr(os, "geteuid"):
        raise RuntimeError("Isolated deployments require Linux/POSIX")
    if os.geteuid() == 0:
        raise RuntimeError("FastAPI must never start as root")
    if os.getenv("TRAMITIA_PDF_WORKER_BACKEND") != "broker":
        raise RuntimeError("Isolated FastAPI must use the PDF broker")

    # Docker group permissions or root-equivalent access in the web account
    # would nullify the separation from the privileged broker.
    docker_sock = Path("/var/run/docker.sock")
    if docker_sock.exists() and os.access(docker_sock, os.R_OK | os.W_OK):
        raise RuntimeError("FastAPI account has access to Docker control socket")

    socket_path = Path(os.getenv(
        "TRAMITIA_BROKER_SOCKET", "/run/tramitia/pdf-broker.sock"
    ))
    if not socket_path.is_absolute() or socket_path.is_symlink():
        raise RuntimeError("Invalid or symlinked PDF broker socket path")
    try:
        mode = socket_path.stat().st_mode
    except OSError as exc:
        raise RuntimeError("PDF broker socket is unavailable") from exc
    if not stat.S_ISSOCK(mode):
        raise RuntimeError("PDF broker socket is not a Unix socket")
    if mode & stat.S_IWOTH:
        raise RuntimeError("PDF broker socket must not be world writable")
    if not os.access(socket_path, os.W_OK):
        raise RuntimeError("FastAPI cannot access its private PDF broker socket")

    # No unsafe fallback is used when the broker dies after startup; the
    # request path already fails closed with HTTP 503.
