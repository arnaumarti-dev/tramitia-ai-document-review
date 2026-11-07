"""Remove expired pseudonymous IP rate counters (run once daily in a trusted job).

Uses the configured PostgreSQL credentials. This is NOT a request handler.
The only output is a row count; raw IP addresses are never stored or logged.
"""
from app.peer_rate_limit import delete_expired_peer_counters


def main() -> None:
    removed = delete_expired_peer_counters()
    print(f"Expired peer quota counters removed: {removed}")


if __name__ == "__main__":
    main()
