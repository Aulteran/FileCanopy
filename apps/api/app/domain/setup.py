"""The setup window of a storage root (ADR-011 §1).

A root starts `open`: its first content may arrive through a setup import. It becomes `closed`, for
good, when its eligible actor finishes or skips setup, or when any node is created by another path
(an upload, a new folder, a copy, or a reconciler pass that finds content). Content created by the
setup import itself never closes it. Callers own the transaction.
"""

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth import clock
from app.auth.service import audit
from app.db.models import ImportBatch, ImportEntry, StorageRoot


def close_window(
    db: Session,
    root_id: uuid.UUID,
    reason: str,
    *,
    actor: uuid.UUID | None = None,
    ip: str | None = None,
) -> bool:
    """Closes an open window and audits it. Returns True only for the call that closed it."""
    done = db.execute(
        update(StorageRoot)
        .where(StorageRoot.id == root_id, StorageRoot.setup_state == "open")
        .values(setup_state="closed")
        .execution_options(synchronize_session=False)
    )
    if not done.rowcount:  # type: ignore[attr-defined]
        return False
    audit(db, "storage.setup_closed", actor=actor, ip=ip, root_id=str(root_id), reason=reason)
    return True


def root_import_active(db: Session, root_id: uuid.UUID) -> bool:
    """True while a file of a running import is being written into the root, that is, an entry is
    in the `writing` state. Between a file appearing on disk and its node being committed it would
    look like an outside change to the reconciler. Sending a manifest, resuming or merely having a
    batch open does not count: only an entry actually in flight."""
    return (
        db.scalar(
            select(ImportEntry.id)
            .join(ImportBatch, ImportBatch.id == ImportEntry.batch_id)
            .where(
                ImportBatch.root_id == root_id,
                ImportBatch.status == "running",
                ImportEntry.status == "writing",
            )
            .limit(1)
        )
        is not None
    )


def should_pause_scan(db: Session, root: StorageRoot, ceiling_seconds: int) -> bool:
    """Whether the reconciler must skip this root now because an import is writing into it. The
    pause is bounded: after `ceiling_seconds` of continuous pause the scan proceeds (and the root
    reports "scan delayed" until a pass finds no import writing). Commits when it changes state."""
    if not root_import_active(db, root.id):
        if root.scan_paused_since is not None:
            root.scan_paused_since = None
            db.commit()
        return False
    now = clock.utcnow()
    if root.scan_paused_since is None:
        root.scan_paused_since = now
        db.commit()
        return True
    return (now - root.scan_paused_since).total_seconds() < ceiling_seconds


def active_batch_count(db: Session, root_id: uuid.UUID, kinds: tuple[str, ...]) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(ImportBatch)
            .where(
                ImportBatch.root_id == root_id,
                ImportBatch.kind.in_(kinds),
                ImportBatch.status.in_(("planned", "running", "stopped")),
            )
        )
        or 0
    )
