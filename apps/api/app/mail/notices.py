"""In-app notices about inbound mail (M14, ADR-007).

Who hears what is the whole point of this module:

* mail filed into a **personal** folder is told to its owner and nobody else;
* mail filed into the **shared** intake is told to the sender and to the active administrators,
  who see a count and (only while they can open it) the folder, never a file name;
* a quarantined message, and a shared intake that is not set, reach administrators as a generic
  count that merges into one unread notice;
* a failed or partly refused message is told to the sender once.

A notice holds ids, a count and a static reason code. Subjects, addresses and file names never
enter one (they would outlive the access that let the reader see them).
"""

import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth import clock
from app.db.models import Notification, User
from app.domain.grants import notify

FILED = "mail_filed"  # to the sender: n files filed in a folder
SHARED = "mail_shared"  # to administrators: n files arrived in the shared intake
REFUSED = "mail_refused"  # to the sender: n attachments were not filed (detail = first reason)
FAILED = "mail_failed"  # to the sender: the message could not be filed (detail = reason)
QUARANTINE = "mail_quarantine"  # to administrators: n messages wait for review
INTAKE_UNSET = "mail_intake_unset"  # to administrators: shared mail waits for an intake folder

MAIL_KINDS = frozenset({FILED, SHARED, REFUSED, FAILED, QUARANTINE, INTAKE_UNSET})


def active_admins(db: Session) -> list[uuid.UUID]:
    return list(db.scalars(select(User.id).where(User.role == "admin", User.status == "active")))


def _merge_unread(db: Session, recipient: uuid.UUID, kind: str) -> bool:
    """Adds one to this person's unread notice of `kind`, if there is one."""
    done = db.execute(
        update(Notification)
        .where(
            Notification.recipient_user_id == recipient,
            Notification.kind == kind,
            Notification.read_at.is_(None),
        )
        .values(count=Notification.count + 1, created_at=clock.utcnow())
    )
    return bool(done.rowcount)  # type: ignore[attr-defined]


def tell_admins_merged(db: Session, kind: str) -> None:
    for admin in active_admins(db):
        if not _merge_unread(db, admin, kind):
            notify(db, admin, kind, node_id=None, actor=None, count=1)


def quarantine_arrived(db: Session) -> None:
    tell_admins_merged(db, QUARANTINE)


def intake_unset(db: Session) -> None:
    tell_admins_merged(db, INTAKE_UNSET)


def filed(
    db: Session,
    sender: uuid.UUID,
    folder: uuid.UUID,
    count: int,
    *,
    shared: bool,
    refused: int = 0,
    refusal: str | None = None,
) -> None:
    if count:
        notify(db, sender, FILED, node_id=folder, actor=None, count=count)
        if shared:
            for admin in active_admins(db):
                if admin != sender:
                    notify(db, admin, SHARED, node_id=folder, actor=None, count=count)
    if refused:
        notify(db, sender, REFUSED, node_id=folder, actor=None, count=refused, detail=refusal)


def failed(db: Session, sender: uuid.UUID, reason: str) -> None:
    notify(db, sender, FAILED, node_id=None, actor=None, count=1, detail=reason)
