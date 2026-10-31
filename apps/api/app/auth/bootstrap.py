"""First-administrator bootstrap. There are no default credentials and no open registration.

While no administrator exists, a one-time setup token is required to create the first one. The
operator either supplies FILECANOPY_SETUP_TOKEN or reads the generated token from the API log
(host-level access, which is already inside the trust boundary). Only its SHA-256 is stored.
Creating the first admin writes the `setup_complete` marker; its primary key makes concurrent
setup attempts race to exactly one winner.
"""

import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth import clock
from app.auth.security import digest, digest_equal, new_token
from app.config import Settings
from app.db.models import AppSetting, User

log = logging.getLogger("filecanopy.auth")
COMPLETE_KEY = "setup_complete"
TOKEN_KEY = "bootstrap_token_hash"  # noqa: S105 (setting name, not a secret)


def setup_required(db: Session) -> bool:
    if db.get(AppSetting, COMPLETE_KEY) is not None:
        return False
    return db.scalar(select(User.id).limit(1)) is None


def mark_complete(db: Session) -> None:
    """Raises IntegrityError if another request already completed setup."""
    db.add(AppSetting(key=COMPLETE_KEY, value={"at": clock.utcnow().isoformat()}))
    db.flush()
    db.execute(delete(AppSetting).where(AppSetting.key == TOKEN_KEY))


def ensure_token(sessions: "sessionmaker[Session]", settings: Settings) -> str | None:
    """Called at startup (and lazily). Returns a freshly generated token, else None."""
    with sessions() as db:
        if not setup_required(db):
            db.execute(delete(AppSetting).where(AppSetting.key == TOKEN_KEY))
            db.commit()
            return None
        if settings.setup_token.get_secret_value():
            log.info("first-run setup required; using the configured FILECANOPY_SETUP_TOKEN")
            return None
        token = new_token()
        value: Any = {"hash": digest(token)}
        row = db.get(AppSetting, TOKEN_KEY)
        if row is None:
            db.add(AppSetting(key=TOKEN_KEY, value=value))
        else:
            row.value = value
        db.commit()
    log.warning("FIRST-RUN SETUP TOKEN (shown once; a restart issues a new one): %s", token)
    return token


def token_valid(db: Session, settings: Settings, presented: str) -> bool:
    candidate = digest(presented)
    configured = settings.setup_token.get_secret_value()
    if configured:
        return digest_equal(candidate, digest(configured))
    row = db.get(AppSetting, TOKEN_KEY)
    return row is not None and digest_equal(candidate, str(row.value.get("hash", "")))
