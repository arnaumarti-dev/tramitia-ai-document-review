"""Minimal standalone PDF parsing subprocess.

Started via: python -I /absolute/path/app/pdf_worker.py <max_pages> <max_chars>
Only the PDF is provided over stdin. No application credentials or database
connections are needed. This is a resource cap, NOT a filesystem/network sandbox.
"""
from __future__ import annotations

import io
import json
import sys

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_VIRTUAL_MEMORY_BYTES = 768 * 1024 * 1024
CPU_SOFT_SECONDS = 8
CPU_HARD_SECONDS = 9


def _set_resource_limits() -> None:
    if sys.platform == "win32":
        # Windows needs Job Objects/containerization for a reliable memory cap.
        # The parent still enforces the 15-second wall-clock timeout.
        return
    import resource

    # Fail closed on POSIX if mandatory limits cannot be applied.
    resource.setrlimit(
        resource.RLIMIT_AS,
        (MAX_VIRTUAL_MEMORY_BYTES, MAX_VIRTUAL_MEMORY_BYTES),
    )
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SOFT_SECONDS, CPU_HARD_SECONDS))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _write(payload: dict) -> None:
    # Never echo raw PDF bytes or exception tracebacks to the caller.
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
    sys.stdout.flush()


def main() -> None:
    _set_resource_limits()

    if len(sys.argv) != 3:
        sys.exit(2)
    max_pages = int(sys.argv[1])
    max_chars = int(sys.argv[2])
    if not (1 <= max_pages <= 50 and 1 <= max_chars <= 200_000):
        sys.exit(2)

    data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        _write({"error": "invalid"})
        return

    # Import PDF parsing only after applying resource limits.
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            _write({"error": "encrypted"})
            return
        count = len(reader.pages)
        if count == 0:
            _write({"error": "empty"})
            return
        if count > max_pages:
            _write({"error": "page_limit"})
            return

        chunks = []
        total_chars = 0
        for page in reader.pages:
            segment = (page.extract_text() or "") + "\n"
            total_chars += len(segment)
            if total_chars > max_chars:
                _write({"error": "text_limit"})
                return
            chunks.append(segment)
        _write({"pages": count, "text": "".join(chunks)})
    except (PdfReadError, ValueError, KeyError, TypeError, RecursionError,
            OverflowError, MemoryError, OSError):
        _write({"error": "invalid"})


if __name__ == "__main__":
    main()
