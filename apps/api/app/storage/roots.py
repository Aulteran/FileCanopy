"""Startup reconciliation of root directories with their database rows (ADR-004 §1)."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import StorageRoot
from app.storage.resolver import StorageError, ensure_root_dir

log = logging.getLogger("filecanopy.storage")


def ensure_roots(sessions: "sessionmaker[Session]", settings: Settings) -> dict[str, int]:
    """Creates missing root directories (0o750) and marks unusable ones `missing` instead of
    crashing. A symlinked or non-directory root is refused, never followed. Admin-disabled roots
    are left alone. Returns counts by resulting status."""
    counts = {"active": 0, "missing": 0}
    with sessions() as db:
        for root in db.scalars(select(StorageRoot).where(StorageRoot.status != "disabled")):
            try:
                ensure_root_dir(settings.library_path, root.relative_dir)
                status = "active"
            except (StorageError, OSError) as exc:
                status = "missing"
                log.warning("storage root unusable (%s): %s", root.id, type(exc).__name__)
            if root.status != status:
                root.status = status
            counts[status] += 1
        db.commit()
    return counts
