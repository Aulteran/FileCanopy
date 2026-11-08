"""Optional outbound mail (M12, ADR-020). Invitations, password resets and a test message.

Best effort and bounded: a send returns True/False and never raises, so an unreachable relay can
not break the action that wanted a mail. Messages hold no file names or content, only a one-time
link whose secret sits in the URL fragment. Recipient addresses, links and credentials are never
logged.
"""

import logging
import smtplib
import ssl
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.parse import quote

from app.config import Settings

log = logging.getLogger("filecanopy.mail")


def one_time_link(public_url: str, purpose: str, token: str) -> str:
    page = {"invitation": "invite", "pin_reset": "pin-reset", "alias": "verify-email"}.get(
        purpose, "reset"
    )
    return f"{public_url.rstrip('/')}/{page}#token={quote(token, safe='')}"


@dataclass(frozen=True)
class SmtpConfig:
    """The outgoing mail settings in force: saved by an administrator, else the environment."""

    host: str
    port: int
    security: str  # "starttls", "ssl" or "none"
    username: str
    password: str
    sender: str
    timeout: int = 10

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender)

    @classmethod
    def from_settings(cls, s: Settings) -> "SmtpConfig":
        return cls(
            s.smtp_host,
            s.smtp_port,
            s.smtp_security,
            s.smtp_username,
            s.smtp_password.get_secret_value(),
            s.smtp_from,
            s.smtp_timeout_seconds,
        )

    def __repr__(self) -> str:  # never print the password
        return f"SmtpConfig(host={self.host!r}, port={self.port}, sender={self.sender!r})"


def _build(sender: str, to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()  # rejects CR/LF in header values: no header injection
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


class Mailer:
    def __init__(
        self,
        settings: Settings,
        smtp_factory: Callable[[], smtplib.SMTP] | None = None,
    ) -> None:
        self._s = settings
        self._env = SmtpConfig.from_settings(settings)
        self._saved: SmtpConfig | None = None
        self._smtp_factory = smtp_factory
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mail")

    @property
    def config(self) -> SmtpConfig:
        """What the server uses now: settings from the environment win over saved ones."""
        if self._env.configured:
            return self._env
        return self._saved or self._env

    @property
    def from_environment(self) -> SmtpConfig:
        return self._env

    def use_saved(self, config: SmtpConfig | None) -> None:
        self._saved = config

    @property
    def is_saved(self) -> bool:
        return self._saved is not None and not self._env.configured

    @property
    def configured(self) -> bool:
        return self.config.configured

    def _connect(self, c: SmtpConfig) -> smtplib.SMTP:
        if self._smtp_factory is not None:
            return self._smtp_factory()
        if c.security == "ssl":
            return smtplib.SMTP_SSL(
                c.host, c.port, timeout=c.timeout, context=ssl.create_default_context()
            )
        return smtplib.SMTP(c.host, c.port, timeout=c.timeout)

    def send(self, to: str, subject: str, body: str) -> bool:
        return self.try_send(self.config, to, subject, body) is None

    def try_send(self, c: SmtpConfig, to: str, subject: str, body: str) -> str | None:
        """Sends with the given settings. None on success, else a short reason code. Never raises
        and never returns text from the relay (it can echo addresses or credentials)."""
        if not c.configured:
            return "SMTP_NOT_CONFIGURED"
        stage = "SMTP_CONNECT_FAILED"
        try:
            msg = _build(c.sender, to, subject, body)
            with self._connect(c) as smtp:
                if c.security == "starttls" and self._smtp_factory is None:
                    stage = "SMTP_TLS_FAILED"
                    smtp.starttls(context=ssl.create_default_context())
                if c.username:
                    stage = "SMTP_AUTH_FAILED"
                    smtp.login(c.username, c.password)
                stage = "SMTP_REJECTED"
                smtp.send_message(msg)
            return None
        except ssl.SSLError as exc:
            log.warning("mail not sent: %s", type(exc).__name__)
            return "SMTP_TLS_FAILED"
        except (OSError, smtplib.SMTPException, ValueError) as exc:
            # The class name only: messages from relays can echo addresses or credentials.
            log.warning("mail not sent: %s", type(exc).__name__)
            return stage

    def send_in_background(self, to: str, subject: str, body: str) -> None:
        """Fire and forget, so a reply never reveals (by timing) whether a mail was needed."""
        if self.configured:
            self._pool.submit(self.send, to, subject, body)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------ messages

    def invitation(self, to: str, name: str, token: str) -> tuple[str, str, str]:
        link = one_time_link(self._s.public_url, "invitation", token)
        hours = self._s.invitation_ttl_seconds // 3600
        return (
            to,
            "Your FileCanopy invitation",
            f"Hello {name},\n\nYou have been invited to FileCanopy. Open this link to "
            f"get started:\n\n{link}\n\nThe link works once and expires in {hours} hours. "
            "If you did not expect this message, ignore it.\n",
        )

    def reset(self, to: str, token: str) -> tuple[str, str, str]:
        link = one_time_link(self._s.public_url, "reset", token)
        minutes = self._s.reset_ttl_seconds // 60
        return (
            to,
            "Reset your FileCanopy password",
            f"Open this link to choose a new password:\n\n{link}\n\nThe link works once and "
            f"expires in {minutes} minutes. If you did not ask for this, ignore this message; "
            "your password has not changed.\n",
        )

    def pin_reset(self, to: str, token: str) -> tuple[str, str, str]:
        link = one_time_link(self._s.public_url, "pin_reset", token)
        minutes = self._s.reset_ttl_seconds // 60
        return (
            to,
            "Choose a new FileCanopy PIN",
            f"Open this link while you are signed in to choose a new PIN:\n\n{link}\n\nThe "
            f"link works once and expires in {minutes} minutes. If you did not ask for this, "
            "ignore this message; your PIN has not changed.\n",
        )

    def alias_verification(self, to: str, token: str) -> tuple[str, str, str]:
        link = one_time_link(self._s.public_url, "alias", token)
        hours = self._s.alias_ttl_seconds // 3600
        return (
            to,
            "Confirm this address for FileCanopy",
            "Someone asked to use this address as a sender address in FileCanopy. If that was "
            f"you, open this link while you are signed in:\n\n{link}\n\nThe link works once "
            f"and expires in {hours} hours. If you did not ask for this, ignore this message.\n",
        )

    def test_message(self, to: str) -> tuple[str, str, str]:
        return (
            to,
            "FileCanopy test message",
            "This is a test message from FileCanopy. Outgoing mail works.\n",
        )
