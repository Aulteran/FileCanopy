"""Sealing provider API keys before they reach the database (M15, design D1).

AES-256-GCM with a random nonce per value. The key lives outside the database
(`FILECANOPY_SECRETS_KEY`), so a database copy or backup alone reveals no API key. The associated
data names the provider row, so a ciphertext copied onto another row (another person's, or the
instance's) does not open. Nothing here logs or returns plaintext; errors carry no key material.
"""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"FS1"
NONCE_LEN = 12
KEY_LEN = 32


class SecretsError(Exception):
    """A key could not be sealed or opened. The message never holds secret material."""


class SecretsNotConfigured(SecretsError):
    """`FILECANOPY_SECRETS_KEY` is empty: keys cannot be stored."""


def parse_key(value: str) -> bytes:
    """32 random bytes as base64 (standard or URL-safe, padding optional)."""
    text = value.strip()
    padded = text + "=" * (-len(text) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.replace("+", "-").replace("/", "_"))
    except (binascii.Error, ValueError) as exc:
        raise SecretsError("secrets key is not valid base64") from exc
    if len(raw) != KEY_LEN:
        raise SecretsError("secrets key must be 32 bytes")
    return raw


def aad_for(provider_id: object, owner_user_id: object | None) -> bytes:
    return f"fc-ai-provider:{provider_id}:{owner_user_id or 'instance'}".encode()


class SecretBox:
    def __init__(self, key: bytes | None) -> None:
        self._aead = AESGCM(key) if key is not None else None

    @classmethod
    def from_setting(cls, value: str) -> "SecretBox":
        return cls(parse_key(value) if value.strip() else None)

    @property
    def configured(self) -> bool:
        return self._aead is not None

    def seal(self, plaintext: str, aad: bytes) -> bytes:
        if self._aead is None:
            raise SecretsNotConfigured("secrets key is not configured")
        nonce = os.urandom(NONCE_LEN)
        return MAGIC + nonce + self._aead.encrypt(nonce, plaintext.encode(), aad)

    def open(self, blob: bytes, aad: bytes) -> str:
        if self._aead is None:
            raise SecretsNotConfigured("secrets key is not configured")
        if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + NONCE_LEN + 16:
            raise SecretsError("sealed value is malformed")
        nonce = blob[len(MAGIC) : len(MAGIC) + NONCE_LEN]
        try:
            return self._aead.decrypt(nonce, blob[len(MAGIC) + NONCE_LEN :], aad).decode()
        except (InvalidTag, UnicodeDecodeError) as exc:
            raise SecretsError("sealed value cannot be opened") from exc
