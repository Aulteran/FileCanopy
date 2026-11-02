"""Filing verified mail, and approving quarantined attachments (M14, ADR-007).

A verified message waits in a queue (`processing_state = pending`) with its attachment bytes
staged under app-data. A worker claims one message, re-checks that the sender still owns the
address (`revalidate`), picks the destination folder, and files each attachment through the same
`fileops.Upload` the web UI uses, acting as the sender. Nothing here chooses a path from message
content: the personal destination is looked up from the *sender's own* personal root, the shared one
is the folder an administrator designated, and every write is checked by the normal policy engine.

Failures are sorted into permanent ones (no permission, a locked folder, an unset intake: the sender
is told once) and transient ones (retried with a growing delay up to five attempts, then a dead
letter with one notice). Every claim counts as an attempt, so a message that kills the worker is
dead-lettered too.
"""

import hashlib
import logging
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.auth import clock
from app.auth.service import audit
from app.config import Settings
from app.db.models import (
    HIDDEN_NODE_STATUSES,
    AppSetting,
    InboundAttachment,
    InboundMessage,
    Node,
    StorageRoot,
    User,
)
from app.domain import fileops, nodes
from app.domain.fileops import OpCtx, OpError
from app.mail import archives, notices, store
from app.mail.decision import revalidate
from app.storage.names import name_key

log = logging.getLogger("filecanopy.mail")

INTAKE_NAME = "Email inbox"
SHARED_INTAKE_KEY = "mail.shared_intake_node_id"
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = (60, 300, 900, 3600)
CLAIM_LEASE = timedelta(minutes=10)
BATCH = 20
READ_CHUNK = 1024 * 1024
# Failures an explicit retry cannot fix; the rest may be retried once by the sender.
FINAL_ERRORS = frozenset({"ATTACHMENTS_REFUSED", "SENDER_CHANGED"})


class Permanent(Exception):
    """Do not retry: `code` is the reason shown to the sender."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ------------------------------------------------------------------ destinations


def shared_intake_id(db: Session) -> uuid.UUID | None:
    row = db.get(AppSetting, SHARED_INTAKE_KEY)
    if row is None or not isinstance(row.value, str):
        return None
    try:
        return uuid.UUID(row.value)
    except ValueError:
        return None


def shared_intake(db: Session) -> Node:
    wanted = shared_intake_id(db)
    if wanted is None:
        raise Permanent("SHARED_INTAKE_UNSET")
    node = db.get(Node, wanted)
    root = db.get(StorageRoot, node.root_id) if node is not None else None
    if (
        node is None
        or root is None
        or root.kind != "shared"
        or node.kind != "dir"
        or node.status != "active"
    ):
        raise Permanent("INTAKE_UNAVAILABLE")
    return node


def personal_intake(ctx: OpCtx, user: User) -> Node:
    """`Email inbox` at the top of the sender's own personal root, created on first use with the
    sender's own rights. The folder is found by its normalised name, so a renamed-by-case copy is
    reused; anything else under that name (a file, a missing or busy node) is unavailable."""
    db = ctx.db
    root = db.scalar(
        select(StorageRoot).where(
            StorageRoot.kind == "personal", StorageRoot.owner_user_id == user.id
        )
    )
    if root is None or root.status != "active":
        raise Permanent("NO_PERSONAL_ROOT")
    top = nodes.root_node(db, root.id)

    def existing() -> Node | None:
        return db.scalar(
            select(Node).where(
                Node.parent_id == top.id,
                Node.name_key == name_key(INTAKE_NAME),
                Node.status.not_in(HIDDEN_NODE_STATUSES),
            )
        )

    found = existing()
    if found is None:
        try:
            return fileops.create_folder(ctx, top.id, INTAKE_NAME, None).node
        except OpError as err:
            if err.code != "NAME_CONFLICT":
                raise
            found = existing()
    if found is None or found.kind != "dir" or found.status != "active":
        raise Permanent("INTAKE_UNAVAILABLE")
    return found


# ------------------------------------------------------------------ filing one attachment


def staged_path(settings: Settings, msg: InboundMessage, att: InboundAttachment) -> Path | None:
    if msg.content_state != "stored" or not att.stored_name:
        return None
    base = store.quarantine_dir(settings) / str(msg.id)
    path = base / att.stored_name
    return path if path.is_file() else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(READ_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _upload(ctx: OpCtx, folder: uuid.UUID, name: str, path: Path, key: str) -> Node:
    upload = fileops.Upload(ctx, folder, name, "keep_both", key)
    try:
        upload.begin()
        if upload.replayed is not None:  # filed before a crash; reuse that file
            return upload.replayed.node
        with path.open("rb") as fh:
            while chunk := fh.read(READ_CHUNK):
                upload.write(chunk)
        return upload.finish().node
    except OpError as err:
        upload.abort(err.code)
        raise
    except BaseException:
        upload.abort()
        raise


def file_attachment(
    ctx: OpCtx, folder: uuid.UUID, msg: InboundMessage, att: InboundAttachment
) -> str | None:
    """Files one staged attachment into `folder` as `ctx.actor`. Returns None when it was filed
    (the attachment row then names the file) or the refusal code. Raises OpError for a problem
    with the destination and OSError for the staged bytes."""
    settings = ctx.settings
    path = staged_path(settings, msg, att)
    if path is None:
        return "NOT_KEPT"
    if path.stat().st_size > settings.max_upload_bytes:
        return "TOO_LARGE"
    if _sha256(path) != att.sha256:
        return "CORRUPT"
    try:
        archives.check(path, att.display_name, store.archive_limits(settings))
    except archives.ArchiveRefused as exc:
        return exc.code
    key = f"mail:{att.id}"
    try:
        node = _upload(ctx, folder, att.display_name, path, key)
    except OpError as err:
        if err.code != "INVALID_NAME":
            raise
        node = _upload(ctx, folder, f"attachment-{att.position}", path, f"{key}:n")
    att.node_id, att.outcome = node.id, "filed"
    return None


# ------------------------------------------------------------------ the queue


def _claim(db: Session, message_id: uuid.UUID) -> bool:
    now = clock.utcnow()
    won = db.execute(
        update(InboundMessage)
        .where(
            InboundMessage.id == message_id,
            InboundMessage.processing_state == "pending",
            (InboundMessage.process_after.is_(None)) | (InboundMessage.process_after <= now),
        )
        .values(
            process_after=now + CLAIM_LEASE, process_attempts=InboundMessage.process_attempts + 1
        )
    )
    db.commit()
    return bool(won.rowcount)  # type: ignore[attr-defined]


def _fail(
    db: Session, settings: Settings, msg: InboundMessage, code: str, *, tell: bool = True
) -> None:
    msg.processing_state, msg.process_error = "failed", code
    msg.processed_at, msg.process_after = clock.utcnow(), None
    if code in FINAL_ERRORS or msg.manual_retries >= 1:  # no retry is possible: free the bytes
        store.purge_content(db, settings, msg)
    audit(db, "mail.failed", target=msg.resolved_user_id, message=str(msg.id), reason=code)
    if tell and msg.resolved_user_id is not None:
        notices.failed(db, msg.resolved_user_id, code)
    if code == "SHARED_INTAKE_UNSET":
        notices.intake_unset(db)
    db.commit()


def _unrecognised(db: Session, settings: Settings, msg: InboundMessage) -> None:
    """The mapping seen on arrival no longer holds (alias removed or moved, account disabled):
    nothing is filed. The message becomes ordinary quarantine; bytes stay only for shared mail."""
    msg.status, msg.reason = "quarantined", "SENDER_CHANGED"
    msg.resolved_user_id, msg.processing_state, msg.process_after = None, None, None
    if msg.destination != "shared":
        # A person's personal mail must not become readable to administrators through the
        # quarantine list: keep no bytes, subject, sender or file names.
        store.purge_content(db, settings, msg)
        msg.subject, msg.from_address = None, None
        for att in db.scalars(
            select(InboundAttachment).where(InboundAttachment.message_id == msg.id)
        ):
            att.display_name = f"attachment-{att.position}"

    audit(db, "mail.sender_changed", message=str(msg.id))
    notices.quarantine_arrived(db)
    db.commit()


def _over_cap(db: Session, settings: Settings, user_id: uuid.UUID) -> bool:
    since = clock.utcnow() - timedelta(days=1)
    done = db.scalar(
        select(func.count())
        .select_from(InboundMessage)
        .where(
            InboundMessage.resolved_user_id == user_id,
            InboundMessage.processing_state.in_(("filed", "partial")),
            InboundMessage.processed_at >= since,
        )
    )
    return (done or 0) >= settings.mail_daily_messages_per_sender


def _http_to_code(err: OpError) -> str:
    if err.code == "LOCKED":
        return "LOCKED"
    return "INTAKE_FORBIDDEN" if err.status == 403 else err.code[:40]


def _file_all(db: Session, settings: Settings, msg: InboundMessage, user: User) -> None:
    if msg.content_state == "dropped":  # the staging quota was full when it arrived
        raise Permanent("STORAGE_FULL")
    ctx = OpCtx(db, settings, user.id, None, source="email")  # no session: never unlocked
    folder = personal_intake(ctx, user) if msg.destination == "personal" else shared_intake(db)
    atts = list(
        db.scalars(
            select(InboundAttachment)
            .where(InboundAttachment.message_id == msg.id)
            .order_by(InboundAttachment.position)
        )
    )
    for att in atts:
        if att.outcome is not None:
            continue
        msg.process_after = clock.utcnow() + CLAIM_LEASE  # keep the claim while work goes on
        refusal = file_attachment(ctx, folder.id, msg, att)
        if refusal is not None:
            att.outcome = refusal
        db.commit()
    filed = [a for a in atts if a.outcome == "filed"]
    refused = [a for a in atts if a.outcome != "filed"]
    msg.filed_count, msg.filed_node_id = len(filed), folder.id
    msg.processed_at, msg.process_after, msg.process_error = clock.utcnow(), None, None
    if filed and refused:
        msg.processing_state = "partial"
    elif refused:
        msg.processing_state = "failed"
        msg.process_error = "ATTACHMENTS_REFUSED"
    else:
        msg.processing_state = "filed"
    store.purge_content(db, settings, msg)
    first = refused[0].outcome if refused else None
    if msg.processing_state == "failed":
        notices.failed(db, user.id, "ATTACHMENTS_REFUSED")
    else:
        notices.filed(
            db,
            user.id,
            folder.id,
            len(filed),
            shared=msg.destination == "shared",
            refused=len(refused),
            refusal=first,
        )
    audit(
        db,
        "mail.filed",
        target=user.id,
        message=str(msg.id),
        filed=len(filed),
        refused=len(refused),
        destination=msg.destination,
    )
    db.commit()


def process_message(
    sessions: "sessionmaker[Session]", settings: Settings, message_id: uuid.UUID
) -> str:
    """One queue step for one message. Returns the resulting state (or 'skipped')."""
    with sessions() as db:
        if not _claim(db, message_id):
            return "skipped"
        msg = db.get(InboundMessage, message_id)
        if msg is None:
            return "skipped"
        try:
            user = revalidate(db, msg)
            if user is None:
                _unrecognised(db, settings, msg)
                return "quarantined"
            if msg.process_attempts > MAX_ATTEMPTS:
                raise Permanent("PROCESSING_FAILED")
            if _over_cap(db, settings, user.id):
                raise Permanent("RATE_LIMITED")
            _file_all(db, settings, msg, user)
            return msg.processing_state or "filed"
        except Permanent as exc:
            db.rollback()
            msg = db.get(InboundMessage, message_id) or msg
            _fail(db, settings, msg, exc.code)
            return "failed"
        except OpError as err:
            db.rollback()
            msg = db.get(InboundMessage, message_id) or msg
            if err.status in (403, 404, 422):
                _fail(db, settings, msg, _http_to_code(err))
                return "failed"
            return _retry(db, settings, msg, err.code)
        except Exception as exc:
            db.rollback()
            log.warning("mail filing failed: %s", type(exc).__name__)
            msg = db.get(InboundMessage, message_id) or msg
            return _retry(db, settings, msg, type(exc).__name__)


def _retry(db: Session, settings: Settings, msg: InboundMessage, why: str) -> str:
    if msg.process_attempts >= MAX_ATTEMPTS:
        audit(db, "mail.dead_letter", message=str(msg.id), reason=why[:40])
        _fail(db, settings, msg, "PROCESSING_FAILED")
        return "failed"
    delay = BACKOFF_SECONDS[min(msg.process_attempts - 1, len(BACKOFF_SECONDS) - 1)]
    msg.process_after = clock.utcnow() + timedelta(seconds=max(delay, 0))
    msg.process_error = None
    db.commit()
    return "pending"


def process_pending(sessions: "sessionmaker[Session]", settings: Settings) -> int:
    """Works through the messages that are due. Returns how many were handled."""
    handled = 0
    with sessions() as db:
        now = clock.utcnow()
        ids = list(
            db.scalars(
                select(InboundMessage.id)
                .where(
                    InboundMessage.processing_state == "pending",
                    (InboundMessage.process_after.is_(None))
                    | (InboundMessage.process_after <= now),
                )
                .order_by(InboundMessage.received_at, InboundMessage.id)
                .limit(BATCH)
            )
        )
    for message_id in ids:
        if process_message(sessions, settings, message_id) != "skipped":
            handled += 1
    return handled


def requeue(db: Session, message_id: uuid.UUID) -> bool:
    """Back to the queue with a fresh attempt count. The caller checks who may ask."""
    done = db.execute(
        update(InboundMessage)
        .where(InboundMessage.id == message_id, InboundMessage.processing_state == "failed")
        .values(
            processing_state="pending",
            process_attempts=0,
            process_after=clock.utcnow(),
            process_error=None,
        )
    )
    return bool(done.rowcount)  # type: ignore[attr-defined]


def requeue_intake_waiting(db: Session) -> int:
    """After an administrator sets the shared intake: shared mail that waited for it goes again."""
    ids = list(
        db.scalars(
            select(InboundMessage.id).where(
                InboundMessage.processing_state == "failed",
                InboundMessage.process_error == "SHARED_INTAKE_UNSET",
                InboundMessage.content_state == "stored",
            )
        )
    )
    return sum(1 for i in ids if requeue(db, i))
