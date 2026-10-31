"""Single time source so expiry behaviour is testable without sleeping."""

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)
