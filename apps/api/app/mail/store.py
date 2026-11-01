"""Recording what the mailbox delivered, and the quarantine files (M13, ADR-007).

One call, `ingest`, takes the raw bytes of one message and leaves exactly one `inbound_message`
row for its `(mailbox, UIDVALIDITY, UID)`: it is idempotent, so a retry or a restart cannot create
a second one. Quarantined attachments are written under app-data with server-chosen names; the
attacker's file name is only ever a display string. Verified mail keeps metadata only.
"""

import hashlib
import logging
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.auth import clock
from app.auth.service import audit
from app.config import Settings
from app.db.models import InboundAttachment, InboundMessage
from app.mail.decision import decide
from app.mail.mime import Limits, MimeRefused, ParsedMessage, parse_message

log = logging.getLogger("filecanopy.mail")


def mailbox_key(settings: Settings) -> str:
    raw = (
        f"{settings.imap_host}|{settings.imap_port}|{settings.imap_username}|{settings.imap_folder}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def quarantine_dir(settings: Settings) -> Path:
    return settings.app_data_path / "mail-quarantine"


def limits_of(settings: Settings) -> Limits:
    return Limits(
        settings.imap_max_message_bytes,
        settings.imap_max_parts,
        settings.imap_max_depth,
        settings.imap_max_attachments,
    )


def _stored_total(db: Session) -> int:
    total = db.scalar(
        select(func.coalesce(func.sum(InboundAttachment.size_bytes), 0))
        .join(InboundMessage, InboundMessage.id == InboundAttachment.message_id)
        .where(InboundMessage.content_state == "stored")
    )
    return int(total or 0)


def _existing(db: Session, key: str, uidvalidity: int, uid: int) -> InboundMessage | None:
    return db.scalar(
        select(InboundMessage).where(
            InboundMessage.mailbox_key == key,
            InboundMessage.uidvalidity == uidvalidity,
            InboundMessage.uid == uid,
        )
    )


def _base(
    key: str, uidvalidity: int, uid: int, received: datetime, size: int, status: str, reason: str
) -> InboundMessage:
    return InboundMessage(
        id=uuid.uuid4(),
        mailbox_key=key,
        uidvalidity=uidvalidity,
        uid=uid,
        received_at=received,
        size_bytes=size,
        status=status,
        reason=reason,
        destination="unknown",
        auth_summary={},
        attachment_count=0,
        content_state="none",
        created_at=clock.utcnow(),
    )


def record_unreadable(
    db: Session,
    settings: Settings,
    uidvalidity: int,
    uid: int,
    received: datetime,
    size: int,
    status: str,
    reason: str,
) -> InboundMessage:
    """A message that was not (or could not be) read: size and reason only, no content."""
    key = mailbox_key(settings)
    existing = _existing(db, key, uidvalidity, uid)
    if existing is not None:
        return existing
    row = _base(key, uidvalidity, uid, received, size, status, reason)
    db.add(row)
    _commit_unique(db)
    return _existing(db, key, uidvalidity, uid) or row


def _commit_unique(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:  # a concurrent writer recorded the same UID first
        db.rollback()


def ingest(
    db: Session,
    settings: Settings,
    uidvalidity: int,
    uid: int,
    received: datetime,
    raw: bytes,
) -> InboundMessage:
    key = mailbox_key(settings)
    existing = _existing(db, key, uidvalidity, uid)
    if existing is not None:
        return existing
    sha = hashlib.sha256(raw).hexdigest()
    first = db.scalar(
        select(InboundMessage)
        .where(InboundMessage.content_sha256 == sha, InboundMessage.status != "duplicate")
        .order_by(InboundMessage.created_at)
        .limit(1)
    )
    if first is not None:
        row = _base(key, uidvalidity, uid, received, len(raw), "duplicate", "DUPLICATE_CONTENT")
        row.content_sha256, row.duplicate_of = sha, first.id
        db.add(row)
        _commit_unique(db)
        return _existing(db, key, uidvalidity, uid) or row

    try:
        parsed: ParsedMessage | None = parse_message(raw, limits_of(settings))
        refused = None
    except MimeRefused as exc:
        parsed, refused = None, exc.reason

    row = _base(key, uidvalidity, uid, received, len(raw), "quarantined", "UNPARSEABLE")
    row.content_sha256 = sha
    files: Path | None = None
    if parsed is None:
        row.reason = refused or "UNPARSEABLE"
    else:
        decision = decide(db, parsed, settings)
        row.status, row.reason = decision.status, decision.reason
        row.destination = decision.destination
        row.resolved_user_id = decision.user_id
        row.auth_summary = decision.summary
        row.from_address = parsed.from_address
        row.subject = parsed.subject
        row.message_id_hash = parsed.message_id_hash
        row.attachment_count = len(parsed.attachments)
        keep = decision.status == "quarantined" and bool(parsed.attachments)
        total = sum(a.size for a in parsed.attachments)
        if keep and _stored_total(db) + total <= settings.mail_quarantine_quota_bytes:
            files = quarantine_dir(settings) / str(row.id)
            row.content_state = "stored"
        elif keep:
            row.content_state = "dropped"
        db.add(row)
        db.flush()
        for att in parsed.attachments:
            stored = f"{att.position}.bin" if row.content_state == "stored" else None
            db.add(
                InboundAttachment(
                    message_id=row.id,
                    position=att.position,
                    display_name=att.display_name,
                    content_type=att.content_type,
                    size_bytes=att.size,
                    sha256=att.sha256,
                    stored_name=stored,
                )
            )
    if parsed is None:
        db.add(row)
    try:
        if files is not None and parsed is not None:
            files.mkdir(parents=True, exist_ok=True, mode=0o700)
            for att in parsed.attachments:
                (files / f"{att.position}.bin").write_bytes(att.data)
        db.commit()
    except IntegrityError:
        db.rollback()
        _remove(files)
        return _existing(db, key, uidvalidity, uid) or row
    except Exception:
        db.rollback()
        _remove(files)
        raise
    return row


def _remove(path: Path | None) -> None:
    if path is not None:
        shutil.rmtree(path, ignore_errors=True)


def purge_content(db: Session, settings: Settings, message: InboundMessage) -> None:
    """Delete the stored bytes of one message; the metadata rows stay as history."""
    _remove(quarantine_dir(settings) / str(message.id))
    message.content_state = "purged"
    for att in db.scalars(
        select(InboundAttachment).where(InboundAttachment.message_id == message.id)
    ):
        att.stored_name = None


def sweep_retention(sessions: "sessionmaker[Session]", settings: Settings) -> int:
    days = settings.mail_quarantine_retention_days
    if days <= 0:
        return 0
    cutoff = clock.utcnow() - timedelta(days=days)
    with sessions() as db:
        old = list(
            db.scalars(
                select(InboundMessage).where(
                    InboundMessage.content_state == "stored",
                    InboundMessage.created_at < cutoff,
                )
            )
        )
        for msg in old:
            purge_content(db, settings, msg)
        if old:
            audit(db, "mail.retention_purge", count=len(old))
            db.commit()
        return len(old)


def internal_date_or_now(value: datetime | None) -> datetime:
    if value is None:
        return clock.utcnow()
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
