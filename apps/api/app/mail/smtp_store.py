"""Outgoing mail settings saved by an administrator (instead of environment variables).

One `app_setting` row. The relay password is sealed with the same key as AI provider keys
(`FILECANOPY_SECRETS_KEY`), so a database copy alone reveals nothing; it is never returned by any
endpoint. Without that key a password cannot be stored (a relay without sign-in still can).
"""

import base64
from typing import Any

from sqlalchemy.orm import Session

from app.ai.secrets import SecretBox, SecretsError
from app.config import Settings
from app.db.models import AppSetting
from app.notifications.mail import SmtpConfig

KEY = "smtp"
AAD = b"fc-smtp-password"
LOOPBACK = ("localhost", "127.0.0.1", "::1")


class SmtpInvalid(ValueError):
    """The settings are not acceptable; the message is a short code, never user input."""


def validate(c: SmtpConfig, settings: Settings) -> None:
    if not c.host or any(ch in c.host for ch in "\r\n \t/"):
        raise SmtpInvalid("SMTP_HOST_INVALID")
    if not 1 <= c.port <= 65535:
        raise SmtpInvalid("SMTP_PORT_INVALID")
    if c.security not in ("starttls", "ssl", "none"):
        raise SmtpInvalid("SMTP_SECURITY_INVALID")
    if "@" not in c.sender or any(ch in c.sender for ch in "\r\n"):
        raise SmtpInvalid("SMTP_FROM_INVALID")
    if any(ch in c.username for ch in "\r\n"):
        raise SmtpInvalid("SMTP_USERNAME_INVALID")
    if c.security == "none" and not settings.smtp_allow_plaintext and c.host not in LOOPBACK:
        raise SmtpInvalid("SMTP_PLAINTEXT_REFUSED")


def load(db: Session, box: SecretBox, timeout: int) -> SmtpConfig | None:
    """The saved settings, or None when none are saved or the password cannot be opened."""
    row = db.get(AppSetting, KEY)
    if row is None or not isinstance(row.value, dict):
        return None
    v: dict[str, Any] = row.value
    password = ""
    sealed = v.get("password")
    if isinstance(sealed, str) and sealed:
        try:
            password = box.open(base64.b64decode(sealed), AAD)
        except (SecretsError, ValueError):
            return None  # key changed or value damaged: fall back rather than send wrongly
    try:
        return SmtpConfig(
            str(v["host"]),
            int(v["port"]),
            str(v["security"]),
            str(v.get("username", "")),
            password,
            str(v["sender"]),
            timeout,
        )
    except (KeyError, TypeError, ValueError):
        return None


def has_saved_password(db: Session) -> bool:
    row = db.get(AppSetting, KEY)
    return bool(row and isinstance(row.value, dict) and row.value.get("password"))


def save(db: Session, box: SecretBox, c: SmtpConfig) -> None:
    value: dict[str, Any] = {
        "host": c.host,
        "port": c.port,
        "security": c.security,
        "username": c.username,
        "sender": c.sender,
        "password": "",
    }
    if c.password:
        value["password"] = base64.b64encode(box.seal(c.password, AAD)).decode()
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
