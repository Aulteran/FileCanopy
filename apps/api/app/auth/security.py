"""Password hashing, opaque tokens and request-origin helpers. Wraps maintained libraries only."""

import hashlib
import hmac
import secrets
from urllib.parse import urlparse

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import Settings

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256  # bounds Argon2 work an unauthenticated caller can request
_COMMON = {
    "password1234",
    "123456789012",
    "qwertyuiop12",
    "letmein12345",
    "changeme1234",
    "administrator",
    "passwordpassword",
}


class PasswordPolicyError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def check_password_policy(password: str, email: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError("PASSWORD_TOO_SHORT")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError("PASSWORD_TOO_LONG")
    lowered = password.lower()
    local = email.split("@", 1)[0]
    if (
        lowered in _COMMON
        or lowered == email
        or (len(local) >= 4 and local in lowered)
        or len(set(password)) < 4
    ):
        raise PasswordPolicyError("PASSWORD_TOO_WEAK")


class Passwords:
    """Argon2id. Hashes embed their parameters, so cost can be raised later via rehash."""

    def __init__(self, settings: Settings) -> None:
        self._hasher = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_kib,
            parallelism=settings.argon2_parallelism,
        )
        # Verified against when the account does not exist, so timing does not reveal it.
        self._dummy = self._hasher.hash(secrets.token_urlsafe(16))

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, stored: str | None, password: str) -> bool:
        try:
            self._hasher.verify(stored or self._dummy, password)
        except (VerificationError, InvalidHashError):
            return False
        return stored is not None

    def needs_rehash(self, stored: str) -> bool:
        return self._hasher.check_needs_rehash(stored)


def new_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits


def digest(token: str) -> str:
    """High-entropy random tokens need a fast hash, not a password hash."""
    return hashlib.sha256(token.encode()).hexdigest()


def digest_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def csrf_token_for(session_token_hash: str) -> str:
    """Stable per-session CSRF token, derived so nothing extra is stored."""
    return hmac.new(session_token_hash.encode(), b"filecanopy-csrf-v1", hashlib.sha256).hexdigest()


def origin_allowed(origin: str, host: str | None, extra: str) -> bool:
    parsed = urlparse(origin)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    allowed = {o.strip().rstrip("/") for o in extra.split(",") if o.strip()}
    return (host is not None and parsed.netloc == host) or origin.rstrip("/") in allowed
