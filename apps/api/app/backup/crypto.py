"""Archive encryption (ADR-015 section 6).

The instance holds only a *public* X25519 key. Each archive gets a fresh ephemeral key pair; the
shared secret, run through HKDF-SHA256, keys an AES-256-GCM stream. The recovery (private) key is
shown to the administrator once and is never stored by the app, so a copy of the database or of
the backup volume cannot decrypt anything. The archive writer keeps the file key in memory for the
duration of the run, which lets it verify what it just wrote without the recovery key.

File layout: ``FCB2`` | ephemeral public key (32) | nonce prefix (4) | context length (2) |
context | chunks. The context names the run the archive belongs to (root, chain, sequence). A chunk
is a 4-byte big-endian ciphertext length plus AES-GCM ciphertext of up to 64 KiB. The nonce is the
prefix plus a 64-bit chunk counter; the associated data is the whole header (context included)
plus a final-chunk flag, so truncation, reordering, a wrong key and an archive moved to another
run's name are all detected. (The public key is public: whoever can write the volume can still
forge a well-formed archive; the checksum the database holds is what catches that.)
"""

import base64
import hashlib
import io
import os
import struct
from typing import BinaryIO, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

MAGIC = b"FCB2"
FIXED_HEADER_LEN = 4 + 32 + 4 + 2
MAX_CONTEXT = 512
CHUNK = 64 * 1024
KEY_PREFIX = "fcbk1-"
INFO = b"filecanopy-backup-v2"


class Sink(Protocol):
    def write(self, data: bytes | bytearray | memoryview, /) -> int: ...


class CryptoError(Exception):
    """Base class; messages never contain key material or file content."""


class BadKey(CryptoError):
    """The recovery key is malformed."""


class DecryptionError(CryptoError):
    """Wrong key, damaged data or a truncated archive."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _public_raw(key: X25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def fingerprint(public_raw: bytes) -> str:
    return hashlib.sha256(public_raw).hexdigest()[:16]


def generate_keypair() -> tuple[str, str, str]:
    """Returns (recovery key text, public key text, fingerprint). Show the first one once."""
    private = X25519PrivateKey.generate()
    raw_public = _public_raw(private)
    return (
        KEY_PREFIX + _b64(private.private_bytes_raw()),
        _b64(raw_public),
        fingerprint(raw_public),
    )


def parse_recovery_key(text: str) -> X25519PrivateKey:
    text = text.strip()
    try:
        if not text.startswith(KEY_PREFIX):
            raise ValueError("prefix")
        raw = _unb64(text[len(KEY_PREFIX) :])
        if len(raw) != 32:
            raise ValueError("length")
        return X25519PrivateKey.from_private_bytes(raw)
    except ValueError as exc:
        raise BadKey("recovery key is malformed") from exc


def recovery_key_fingerprint(text: str) -> str:
    return fingerprint(_public_raw(parse_recovery_key(text)))


def parse_public_key(text: str) -> bytes:
    try:
        raw = _unb64(text)
    except ValueError as exc:
        raise BadKey("public key is malformed") from exc
    if len(raw) != 32:
        raise BadKey("public key is malformed")
    return raw


def _file_key(shared: bytes, ephemeral_public: bytes, recipient_public: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=ephemeral_public + recipient_public, info=INFO
    ).derive(shared)


def archive_context(root_id: object, chain_id: str, seq: int) -> bytes:
    """What an archive's header binds it to."""
    return f"fcb|{root_id}|{chain_id}|{seq}".encode()


def _nonce(prefix: bytes, counter: int) -> bytes:
    return prefix + struct.pack(">Q", counter)


class EncryptingWriter(io.RawIOBase):
    """A write-only file object that encrypts into `sink`. `close()` writes the final chunk but
    does not close the sink."""

    def __init__(self, sink: Sink, recipient_public: bytes, context: bytes = b"") -> None:
        super().__init__()
        if len(context) > MAX_CONTEXT:
            raise ValueError("context too long")
        self._sink = sink
        ephemeral = X25519PrivateKey.generate()
        eph_public = _public_raw(ephemeral)
        shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(recipient_public))
        self.file_key = _file_key(shared, eph_public, recipient_public)
        self._aes = AESGCM(self.file_key)
        self._prefix = os.urandom(4)
        self._header = MAGIC + eph_public + self._prefix + struct.pack(">H", len(context)) + context
        self._sink.write(self._header)
        self._buffer = bytearray()
        self._counter = 0
        self._finished = False

    def writable(self) -> bool:
        return True

    def _emit(self, data: bytes, final: bool) -> None:
        aad = self._header + (b"\x01" if final else b"\x00")
        sealed = self._aes.encrypt(_nonce(self._prefix, self._counter), data, aad)
        self._counter += 1
        self._sink.write(struct.pack(">I", len(sealed)))
        self._sink.write(sealed)

    def write(self, data: bytes | bytearray | memoryview) -> int:  # type: ignore[override]
        if self._finished:
            raise ValueError("write to a finished archive")
        self._buffer += data
        while len(self._buffer) > CHUNK:  # keep at least one byte for the final chunk
            self._emit(bytes(self._buffer[:CHUNK]), final=False)
            del self._buffer[:CHUNK]
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        if not self._finished:
            self._finished = True
            self._emit(bytes(self._buffer), final=True)
            self._buffer.clear()
        super().close()


class DecryptingReader(io.RawIOBase):
    """Reads plaintext from an archive written by `EncryptingWriter`. Give either the recovery
    key or the file key (the writer keeps it, for verification during a run). With `context`, an
    archive that was written for a different run is refused."""

    def __init__(
        self,
        source: BinaryIO,
        *,
        recovery_key: str | None = None,
        file_key: bytes | None = None,
        context: bytes | None = None,
    ) -> None:
        super().__init__()
        self._source = source
        fixed = source.read(FIXED_HEADER_LEN)
        if len(fixed) != FIXED_HEADER_LEN or fixed[:4] != MAGIC:
            raise DecryptionError("not an encrypted FileCanopy archive")
        (context_len,) = struct.unpack(">H", fixed[40:42])
        if context_len > MAX_CONTEXT:
            raise DecryptionError("archive is damaged")
        found = source.read(context_len)
        if len(found) != context_len:
            raise DecryptionError("archive is truncated")
        header = fixed + found
        self._header = header
        self.context = found  # authenticated by every chunk, so it is trustworthy once one opens
        self._prefix = header[36:40]
        if context is not None and found != context:
            raise DecryptionError("archive belongs to another run")
        if file_key is None:
            if recovery_key is None:
                raise BadKey("a key is required")
            private = parse_recovery_key(recovery_key)
            try:
                shared = private.exchange(X25519PublicKey.from_public_bytes(header[4:36]))
            except ValueError as exc:  # a low-order or malformed ephemeral key
                raise DecryptionError("damaged archive header") from exc
            file_key = _file_key(shared, header[4:36], _public_raw(private))
        self._aes = AESGCM(file_key)
        self._counter = 0
        self._pending = b""
        self._done = False

    def readable(self) -> bool:
        return True

    def _next_chunk(self) -> bool:
        if self._done:
            return False
        frame = self._source.read(4)
        if len(frame) != 4:
            raise DecryptionError("archive is truncated")
        (length,) = struct.unpack(">I", frame)
        if length < 16 or length > CHUNK + 16:
            raise DecryptionError("archive is damaged")
        sealed = self._source.read(length)
        if len(sealed) != length:
            raise DecryptionError("archive is truncated")
        nonce = _nonce(self._prefix, self._counter)
        try:
            plain = self._aes.decrypt(nonce, sealed, self._header + b"\x00")
        except InvalidTag:
            try:
                plain = self._aes.decrypt(nonce, sealed, self._header + b"\x01")
            except InvalidTag as exc:
                raise DecryptionError("wrong key or damaged archive") from exc
            self._done = True
            if self._source.read(1):
                raise DecryptionError("data after the final chunk") from None
        self._counter += 1
        self._pending += plain
        return True

    def readinto(self, buffer: bytearray | memoryview) -> int:  # type: ignore[override]
        while not self._pending and self._next_chunk():
            pass
        n = min(len(buffer), len(self._pending))
        buffer[:n] = self._pending[:n]
        self._pending = self._pending[n:]
        return n


def looks_encrypted(head: bytes) -> bool:
    return head[:4] == MAGIC
