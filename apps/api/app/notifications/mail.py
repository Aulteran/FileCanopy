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
from email.message import EmailMessage
from urllib.parse import quote

from app.config import Settings

log = logging.getLogger("filecanopy.mail")


def one_time_link(public_url: str, purpose: str, token: str) -> str:
    page = {"invitation": "invite", "pin_reset": "pin-reset"}.get(purpose, "reset")
    return f"{public_url.rstrip('/')}/{page}#token={quote(token, safe='')}"


def _build(settings: Settings, to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()  # rejects CR/LF in header values: no header injection
    msg["From"] = settings.smtp_from
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
        self._smtp_factory = smtp_factory
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mail")

    @property
    def configured(self) -> bool:
        return self._s.smtp_configured

    def _connect(self) -> smtplib.SMTP:
        s = self._s
        if self._smtp_factory is not None:
            return self._smtp_factory()
        if s.smtp_security == "ssl":
            return smtplib.SMTP_SSL(
                s.smtp_host,
                s.smtp_port,
                timeout=s.smtp_timeout_seconds,
                context=ssl.create_default_context(),
            )
        return smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds)

    def send(self, to: str, subject: str, body: str) -> bool:
        if not self.configured:
            return False
        s = self._s
        try:
            msg = _build(s, to, subject, body)
            with self._connect() as smtp:
                if s.smtp_security == "starttls" and self._smtp_factory is None:
                    smtp.starttls(context=ssl.create_default_context())
                if s.smtp_username:
                    smtp.login(s.smtp_username, s.smtp_password.get_secret_value())
                smtp.send_message(msg)
            return True
        except (OSError, smtplib.SMTPException, ValueError) as exc:
            # The class name only: messages from relays can echo addresses or credentials.
            log.warning("mail not sent: %s", type(exc).__name__)
            return False

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

    def test_message(self, to: str) -> tuple[str, str, str]:
        return (
            to,
            "FileCanopy test message",
            "This is a test message from FileCanopy. Outgoing mail works.\n",
        )
