"""Read-only IMAP access (M13, ADR-007). The mailbox is opened with EXAMINE (read-only) and bodies
are fetched with BODY.PEEK, so nothing is flagged, moved or deleted. TLS certificates are
verified. The password is only handed to the library; no response text is ever logged."""

import calendar
import imaplib
import re
import ssl
from datetime import UTC, datetime
from typing import Protocol

from app.config import Settings

_SIZE = re.compile(rb"RFC822\.SIZE (\d+)")
_UIDVALIDITY = re.compile(rb"UIDVALIDITY (\d+)")


class MailboxError(Exception):
    """`code` is a stable short string safe to store and show: never server text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class MailboxClient(Protocol):
    def select(self, folder: str) -> int:
        """Opens read-only; returns UIDVALIDITY."""

    def uids_after(self, last_uid: int, limit: int) -> list[int]: ...

    def size_of(self, uid: int) -> int: ...

    def fetch(self, uid: int) -> tuple[bytes, datetime | None]: ...

    def close(self) -> None: ...


class ImapClient:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._conn: imaplib.IMAP4 | None = None

    def connect(self) -> "ImapClient":
        s = self._s
        try:
            ctx = ssl.create_default_context()
            if s.imap_security == "ssl":
                conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                    s.imap_host, s.imap_port, ssl_context=ctx, timeout=s.imap_timeout_seconds
                )
            else:
                conn = imaplib.IMAP4(s.imap_host, s.imap_port, timeout=s.imap_timeout_seconds)
                if s.imap_security == "starttls":
                    conn.starttls(ssl_context=ctx)
        except (OSError, ssl.SSLError, imaplib.IMAP4.error):
            raise MailboxError("CONNECT_FAILED") from None
        self._conn = conn
        try:
            conn.login(s.imap_username, s.imap_password.get_secret_value())
        except (imaplib.IMAP4.error, OSError):
            self.close()
            raise MailboxError("LOGIN_FAILED") from None
        return self

    @property
    def conn(self) -> imaplib.IMAP4:
        if self._conn is None:
            raise MailboxError("NOT_CONNECTED")
        return self._conn

    def select(self, folder: str) -> int:
        try:
            typ, _ = self.conn.select(f'"{folder}"', readonly=True)
            if typ != "OK":
                raise MailboxError("FOLDER_UNAVAILABLE")
            _, data = self.conn.response("UIDVALIDITY")
            m = _UIDVALIDITY.search(b" ".join(d for d in data if isinstance(d, bytes)))
            if m is None and data and data[0] is not None:
                m = re.search(rb"(\d+)", data[0] if isinstance(data[0], bytes) else b"")
            if m is None:
                raise MailboxError("NO_UIDVALIDITY")
            return int(m.group(1))
        except (imaplib.IMAP4.error, OSError):
            raise MailboxError("SELECT_FAILED") from None

    def uids_after(self, last_uid: int, limit: int) -> list[int]:
        try:
            typ, data = self.conn.uid("SEARCH", "UID", f"{last_uid + 1}:*")
        except (imaplib.IMAP4.error, OSError):
            raise MailboxError("SEARCH_FAILED") from None
        if typ != "OK" or not data or data[0] is None:
            return []
        raw = data[0] if isinstance(data[0], bytes) else b""
        uids = sorted(int(x) for x in raw.split() if x.isdigit() and int(x) > last_uid)
        return uids[:limit]

    def size_of(self, uid: int) -> int:
        try:
            typ, data = self.conn.uid("FETCH", str(uid), "(RFC822.SIZE)")
        except (imaplib.IMAP4.error, OSError):
            raise MailboxError("FETCH_FAILED") from None
        blob = b" ".join(d if isinstance(d, bytes) else b"" for d in (data or []))
        m = _SIZE.search(blob)
        if typ != "OK" or m is None:
            raise MailboxError("FETCH_FAILED")
        return int(m.group(1))

    def fetch(self, uid: int) -> tuple[bytes, datetime | None]:
        try:
            typ, data = self.conn.uid("FETCH", str(uid), "(INTERNALDATE BODY.PEEK[])")
        except (imaplib.IMAP4.error, OSError):
            raise MailboxError("FETCH_FAILED") from None
        if typ != "OK" or not data:
            raise MailboxError("FETCH_FAILED")
        for item in data:
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], bytes):
                stamp = imaplib.Internaldate2tuple(item[0])
                when = (
                    datetime.fromtimestamp(calendar.timegm(stamp), UTC)
                    if stamp is not None
                    else None
                )
                return item[1], when
        raise MailboxError("FETCH_FAILED")

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.logout()
            except Exception:  # noqa: S110 - the connection is being dropped anyway
                pass
