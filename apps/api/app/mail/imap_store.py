"""Incoming mailbox settings saved by an administrator (instead of environment variables).

One `app_setting` row, like the outgoing mail settings (`smtp_store`). The mailbox password is
sealed with the same key as AI provider keys (`FILECANOPY_SECRETS_KEY`) and is never returned by
any endpoint. Environment settings always win: when the mailbox is configured in the environment
nothing saved here is used and nothing can be saved.

The mailbox code reads its settings from the shared `Settings` object, so saved values are applied
to that object (`apply`); `clear` puts back what the environment gave.
"""

import base64
from dataclasses import dataclass
from typing import Any, Literal, get_args

from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.ai.secrets import SecretBox, SecretsError
from app.config import Settings
from app.db.models import AppSetting
from app.mail.address import normalize_address

KEY = "imap"
AAD = b"fc-imap-password"
LOOPBACK = ("localhost", "127.0.0.1", "::1")
Security = Literal["ssl", "starttls", "none"]
FIELDS = (
    "imap_host",
    "imap_port",
    "imap_security",
    "imap_username",
    "imap_password",
    "imap_folder",
    "imap_personal_address",
    "imap_ingest_address",
    "imap_authserv_id",
)


class ImapInvalid(ValueError):
    """The settings are not acceptable; the message is a short code, never user input."""


@dataclass(frozen=True)
class ImapConfig:
    host: str
    port: int
    security: str
    username: str
    password: str
    folder: str
    personal_address: str
    ingest_address: str
    authserv_id: str

    def __repr__(self) -> str:  # never print the password
        return f"ImapConfig(host={self.host!r}, port={self.port}, folder={self.folder!r})"


def validate(c: ImapConfig) -> ImapConfig:
    """Returns the config with the two intake addresses normalised."""
    if not c.host or any(ch in c.host for ch in "\r\n \t/"):
        raise ImapInvalid("IMAP_HOST_INVALID")
    if not 1 <= c.port <= 65535:
        raise ImapInvalid("IMAP_PORT_INVALID")
    if c.security not in get_args(Security):
        raise ImapInvalid("IMAP_SECURITY_INVALID")
    if c.security == "none" and c.host not in LOOPBACK:
        raise ImapInvalid("IMAP_PLAINTEXT_REFUSED")
    if not c.username or any(ch in c.username for ch in "\r\n") or not c.password:
        raise ImapInvalid("IMAP_LOGIN_INVALID")
    if not c.folder or len(c.folder) > 200 or any(ch in c.folder for ch in '"\\\r\n'):
        raise ImapInvalid("IMAP_FOLDER_INVALID")
    personal = normalize_address(c.personal_address)
    ingest = normalize_address(c.ingest_address)
    if not personal or not ingest or personal == ingest:
        raise ImapInvalid("IMAP_ADDRESSES_INVALID")
    if any(ch in c.authserv_id for ch in "\r\n \t"):
        raise ImapInvalid("IMAP_AUTHSERV_INVALID")
    return ImapConfig(
        c.host, c.port, c.security, c.username, c.password, c.folder,
        personal, ingest, c.authserv_id,
    )  # fmt: skip


def from_settings(s: Settings) -> ImapConfig:
    return ImapConfig(
        s.imap_host,
        s.imap_port,
        s.imap_security,
        s.imap_username,
        s.imap_password.get_secret_value(),
        s.imap_folder,
        s.imap_personal_address,
        s.imap_ingest_address,
        s.imap_authserv_id,
    )


def settings_with(s: Settings, c: ImapConfig) -> Settings:
    """A copy of the settings carrying `c`, for a connection test."""
    return s.model_copy(update=_updates(c))


def _updates(c: ImapConfig) -> dict[str, Any]:
    return {
        "imap_host": c.host,
        "imap_port": c.port,
        "imap_security": c.security,
        "imap_username": c.username,
        "imap_password": SecretStr(c.password),
        "imap_folder": c.folder,
        "imap_personal_address": c.personal_address,
        "imap_ingest_address": c.ingest_address,
        "imap_authserv_id": c.authserv_id,
    }


def apply(settings: Settings, c: ImapConfig) -> None:
    """Makes the shared settings carry `c` (the mailbox poller and the views read them)."""
    for name, value in _updates(c).items():
        setattr(settings, name, value)


def load(db: Session, box: SecretBox) -> ImapConfig | None:
    """The saved settings, or None when none are saved or the password cannot be opened."""
    row = db.get(AppSetting, KEY)
    if row is None or not isinstance(row.value, dict):
        return None
    v: dict[str, Any] = row.value
    sealed = v.get("password")
    if not isinstance(sealed, str) or not sealed:
        return None
    try:
        password = box.open(base64.b64decode(sealed), AAD)
    except (SecretsError, ValueError):
        return None  # key changed or value damaged: the mailbox stays off
    try:
        return ImapConfig(
            str(v["host"]),
            int(v["port"]),
            str(v["security"]),
            str(v["username"]),
            password,
            str(v["folder"]),
            str(v["personal_address"]),
            str(v["ingest_address"]),
            str(v.get("authserv_id", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def has_saved(db: Session) -> bool:
    row = db.get(AppSetting, KEY)
    return bool(row and isinstance(row.value, dict) and row.value.get("password"))


def save(db: Session, box: SecretBox, c: ImapConfig) -> None:
    value: dict[str, Any] = {
        "host": c.host,
        "port": c.port,
        "security": c.security,
        "username": c.username,
        "folder": c.folder,
        "personal_address": c.personal_address,
        "ingest_address": c.ingest_address,
        "authserv_id": c.authserv_id,
        "password": base64.b64encode(box.seal(c.password, AAD)).decode(),
    }
    row = db.get(AppSetting, KEY)
    if row is None:
        db.add(AppSetting(key=KEY, value=value))
    else:
        row.value = value


def clear(db: Session) -> bool:
    row = db.get(AppSetting, KEY)
    if row is None:
        return False
    db.delete(row)
    return True
