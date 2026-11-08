"""Notices an administrator when mail settings start (or stop) coming from the environment.

Environment variables always win over settings saved in the web UI. If an administrator later adds
mail variables that replace what they saved, the next start tells the active administrators once.
Only a source marker is remembered (`environment`, `saved` or `none`), never a value or a
password, and a notice is sent only when the source changes: not on every start.
"""

from sqlalchemy.orm import Session

from app.db.models import AppSetting
from app.domain.grants import notify
from app.mail import imap_store, smtp_store
from app.mail.notices import SOURCE_CHANGED, active_admins

KEY = "mail_sources"
SAVED_ROWS = {"smtp": smtp_store.KEY, "mailbox": imap_store.KEY}


def _record(db: Session) -> dict[str, str]:
    row = db.get(AppSetting, KEY)
    if row is None or not isinstance(row.value, dict):
        return {}
    return {str(k): str(v) for k, v in row.value.items()}


def remember(db: Session, part: str, source: str) -> None:
    """Records the source after the administrator saved or cleared settings in the UI."""
    value = _record(db)
    value[part] = source
    row = db.get(AppSetting, KEY)
    if row is None:
        db.add(AppSetting(key=KEY, value=value))
    else:
        row.value = value


def check(db: Session, *, env_smtp: bool, env_mailbox: bool) -> list[str]:
    """Compares each part's source with the one recorded at the last start, notifies the active
    administrators of a change that matters and records the new sources. Returns the details of
    the notices sent. The caller commits."""
    previous = _record(db)
    sent: list[str] = []
    for part, env in (("smtp", env_smtp), ("mailbox", env_mailbox)):
        saved = db.get(AppSetting, SAVED_ROWS[part]) is not None
        now = "environment" if env else ("saved" if saved else "none")
        before = previous.get(part) or ("saved" if saved else "none")
        # Moving onto the environment replaces what was saved; moving off it (the variables were
        # removed) falls back to the saved settings or to nothing.
        if (before == "saved" and now == "environment") or (
            before == "environment" and now != "environment"
        ):
            detail = f"{part}:{now}"
            for admin in active_admins(db):
                notify(db, admin, SOURCE_CHANGED, node_id=None, actor=None, detail=detail)
            sent.append(detail)
        remember(db, part, now)
    return sent
