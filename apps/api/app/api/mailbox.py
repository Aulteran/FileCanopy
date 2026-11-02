"""Inbound mail views (M13, ADR-007).

People see only their own `verified` messages. Administrators see the mailbox status and the
quarantine (`quarantined`, `error`, `rejected`) and nothing that was verified for a person: an
administrator has no way into someone's personal mail. Quarantined bytes are served only as an
inert download.
"""

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import (
    Principal,
    client_ip,
    ctx_of,
    current_principal,
    get_db,
    http_error,
    require_admin,
)
from app.auth.schemas import Ok, Strict
from app.auth.service import audit
from app.config import Settings
from app.db.models import (
    HIDDEN_NODE_STATUSES,
    AppSetting,
    InboundAttachment,
    InboundMessage,
    MailboxState,
    Node,
    StorageRoot,
)
from app.domain.fileops import OpCtx, OpError
from app.mail import ingest, store
from app.policy import engine
from app.policy.model import Actor, Privilege

router = APIRouter(prefix="/api/v1", tags=["mailbox"])

QUARANTINE_STATES = ("quarantined", "error", "rejected", "approved")
_STORED = re.compile(r"^\d{1,4}\.bin$")


class AttachmentOut(BaseModel):
    id: uuid.UUID
    display_name: str
    content_type: str
    size_bytes: int
    stored: bool
    outcome: str | None  # filed, or why it was not (ARCHIVE_*, TOO_LARGE, NOT_KEPT ...)


class MessageOut(BaseModel):
    id: uuid.UUID
    received_at: datetime
    from_address: str | None
    subject: str | None
    destination: str
    status: str
    reason: str | None
    size_bytes: int
    attachment_count: int
    content_state: str
    auth_summary: dict[str, Any]
    attachments: list[AttachmentOut]
    processing_state: str | None
    process_error: str | None
    filed_count: int
    # The folder the files went to, only while the reader can open it.
    filed_folder_id: uuid.UUID | None
    can_retry: bool


def _out(db: Session, m: InboundMessage, reader: Principal) -> MessageOut:
    atts = db.scalars(
        select(InboundAttachment)
        .where(InboundAttachment.message_id == m.id)
        .order_by(InboundAttachment.position)
    ).all()
    return MessageOut(
        id=m.id,
        received_at=m.received_at,
        from_address=m.from_address,
        subject=m.subject,
        destination=m.destination,
        status=m.status,
        reason=m.reason,
        size_bytes=m.size_bytes,
        attachment_count=m.attachment_count,
        content_state=m.content_state,
        auth_summary=dict(m.auth_summary or {}),
        processing_state=m.processing_state,
        process_error=m.process_error,
        filed_count=m.filed_count,
        filed_folder_id=_visible_folder(db, m, reader),
        can_retry=_can_retry(m),
        attachments=[
            AttachmentOut(
                id=a.id,
                display_name=a.display_name,
                content_type=a.content_type,
                size_bytes=a.size_bytes,
                stored=a.stored_name is not None,
                outcome=a.outcome,
            )
            for a in atts
        ],
    )


def _visible_folder(db: Session, m: InboundMessage, reader: Principal) -> uuid.UUID | None:
    if m.filed_node_id is None:
        return None
    node = db.get(Node, m.filed_node_id)
    if node is None or node.status in HIDDEN_NODE_STATUSES:
        return None
    actor = Actor(reader.user.id, session_id=reader.session.id)
    return node.id if Privilege.TRAVERSE in engine.effective(db, actor, node) else None


def _can_retry(m: InboundMessage) -> bool:
    return (
        m.processing_state == "failed"
        and m.manual_retries < 1
        and m.process_error not in ingest.FINAL_ERRORS
        and m.content_state == "stored"
    )


# ------------------------------------------------------------------ a person's own inbox


@router.get("/me/inbox", response_model=list[MessageOut])
def my_inbox(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100_000),
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> list[MessageOut]:
    rows = db.scalars(
        select(InboundMessage)
        .where(
            InboundMessage.resolved_user_id == principal.user.id,
            InboundMessage.status == "verified",
        )
        .order_by(InboundMessage.received_at.desc(), InboundMessage.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [_out(db, m, principal) for m in rows]


# ------------------------------------------------------------------ administrators


class MailboxStatus(BaseModel):
    configured: bool
    host: str | None
    folder: str | None
    personal_address: str | None
    ingest_address: str | None
    authentication_configured: bool
    # Sender checks are trusted but no message has passed yet: the operator has not shown that
    # the provider adds its header on every path (see the guide).
    authentication_unproven: bool
    auth_policy: str | None
    last_poll_at: datetime | None
    last_error: str | None
    failures: int
    quarantined: int
    errors: int
    verified: int
    shared_intake_set: bool
    waiting_for_intake: int
    failed_to_file: int


def _count_where(db: Session, *conditions: Any) -> int:
    return int(db.scalar(select(func.count()).select_from(InboundMessage).where(*conditions)) or 0)


def _waiting(db: Session) -> int:
    return _count_where(
        db,
        InboundMessage.processing_state == "failed",
        InboundMessage.process_error == "SHARED_INTAKE_UNSET",
    )


def _failed(db: Session) -> int:
    """A count only: whose mail it is and what it holds stay with the sender."""
    return _count_where(db, InboundMessage.processing_state == "failed")


@router.get("/admin/mailbox", response_model=MailboxStatus)
def mailbox_status(
    request: Request,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> MailboxStatus:
    s = ctx_of(request).settings
    state = db.get(MailboxState, store.mailbox_key(s)) if s.imap_configured else None

    def count(status: str) -> int:
        return int(
            db.scalar(
                select(func.count())
                .select_from(InboundMessage)
                .where(InboundMessage.status == status)
            )
            or 0
        )

    return MailboxStatus(
        configured=s.imap_configured,
        host=s.imap_host or None,
        folder=s.imap_folder if s.imap_configured else None,
        personal_address=s.imap_personal_address or None,
        ingest_address=s.imap_ingest_address or None,
        authentication_configured=bool(s.imap_authserv_id),
        authentication_unproven=bool(s.imap_authserv_id) and count("verified") == 0,
        auth_policy=s.imap_auth_policy if s.imap_configured else None,
        last_poll_at=state.last_poll_at if state else None,
        last_error=state.last_error if state else None,
        failures=state.failures if state else 0,
        quarantined=count("quarantined"),
        errors=count("error"),
        verified=count("verified"),
        shared_intake_set=ingest.shared_intake_id(db) is not None,
        waiting_for_intake=_waiting(db),
        failed_to_file=_failed(db),
    )


@router.post("/admin/mailbox/poll", response_model=Ok, status_code=202)
def poll_now(request: Request, _: Principal = Depends(require_admin)) -> Ok:
    if not ctx_of(request).settings.imap_configured:
        raise http_error(409, "MAILBOX_NOT_CONFIGURED")
    request.app.state.mailbox.poll_now()
    return Ok()


@router.get("/admin/quarantine", response_model=list[MessageOut])
def quarantine_list(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100_000),
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[MessageOut]:
    rows = db.scalars(
        select(InboundMessage)
        .where(InboundMessage.status.in_(QUARANTINE_STATES))
        .order_by(InboundMessage.received_at.desc(), InboundMessage.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [_out(db, m, admin) for m in rows]


def _quarantined(db: Session, message_id: uuid.UUID) -> InboundMessage:
    m = db.get(InboundMessage, message_id)
    if m is None or m.status not in QUARANTINE_STATES:
        raise http_error(404, "NOT_FOUND")  # a verified person's mail looks like nothing at all
    return m


@router.get("/admin/quarantine/{message_id}", response_model=MessageOut)
def quarantine_detail(
    message_id: uuid.UUID,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> MessageOut:
    return _out(db, _quarantined(db, message_id), admin)


@router.get("/admin/quarantine/{message_id}/attachments/{attachment_id}/content")
def quarantine_download(
    message_id: uuid.UUID,
    attachment_id: uuid.UUID,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> FileResponse:
    m = _quarantined(db, message_id)
    att = db.get(InboundAttachment, attachment_id)
    if (
        att is None
        or att.message_id != m.id
        or att.stored_name is None
        or m.status not in ("quarantined", "error")
    ):
        raise http_error(404, "NOT_FOUND")
    if not _STORED.match(att.stored_name):
        raise http_error(404, "NOT_FOUND")
    base = (store.quarantine_dir(ctx_of(request).settings) / str(m.id)).resolve()
    path = (base / att.stored_name).resolve()
    if path.parent != base or not path.is_file() or path.is_symlink():
        raise http_error(404, "NOT_FOUND")
    audit(
        db,
        "mail.attachment_downloaded",
        actor=admin.user.id,
        ip=client_ip(request),
        message=str(m.id),
        attachment=str(att.id),
    )
    db.commit()
    return FileResponse(
        Path(path),
        media_type="application/octet-stream",
        filename=f"quarantined-{att.stored_name}",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Cache-Control": "no-store",
        },
    )


class Reject(Strict):
    confirm: bool = False


@router.post("/admin/quarantine/{message_id}/reject", response_model=MessageOut)
def quarantine_reject(
    message_id: uuid.UUID,
    body: Reject,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> MessageOut:
    if not body.confirm:
        raise http_error(422, "CONFIRMATION_REQUIRED")
    m = _quarantined(db, message_id)
    if m.status in ("quarantined", "error"):
        store.purge_content(db, ctx_of(request).settings, m)
        m.status = "rejected"  # the original reason code stays
        audit(
            db,
            "mail.quarantine_rejected",
            actor=admin.user.id,
            ip=client_ip(request),
            message=str(m.id),
        )
        db.commit()
    return _out(db, m, admin)


# ------------------------------------------------------------------ retry (the sender)


@router.post("/me/inbox/{message_id}/retry", response_model=MessageOut, status_code=202)
def retry_filing(
    message_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> MessageOut:
    """One manual retry of a message that could not be filed; the sender's own mail only."""
    m = db.get(InboundMessage, message_id)
    if m is None or m.status != "verified" or m.resolved_user_id != principal.user.id:
        raise http_error(404, "NOT_FOUND")
    if not _can_retry(m):
        raise http_error(409, "NOT_RETRYABLE")
    if not ingest.requeue(db, m.id):
        raise http_error(409, "NOT_RETRYABLE")
    m.manual_retries += 1
    audit(db, "mail.retry", actor=principal.user.id, ip=client_ip(request), message=str(m.id))
    db.commit()
    db.refresh(m)
    request.app.state.mailbox.poll_now()
    return _out(db, m, principal)


# ------------------------------------------------------------------ shared intake (administrators)


class SharedIntakeOut(BaseModel):
    node_id: uuid.UUID | None
    name: str | None  # only while the administrator can open the folder
    usable: bool


class SharedIntakeIn(Strict):
    node_id: uuid.UUID | None


def _intake_out(db: Session, admin: Principal) -> SharedIntakeOut:
    wanted = ingest.shared_intake_id(db)
    if wanted is None:
        return SharedIntakeOut(node_id=None, name=None, usable=False)
    node = db.get(Node, wanted)
    actor = Actor(admin.user.id, session_id=admin.session.id)
    visible = node is not None and Privilege.TRAVERSE in engine.effective(db, actor, node)
    try:
        ingest.shared_intake(db)
        usable = True
    except ingest.Permanent:
        usable = False
    return SharedIntakeOut(
        node_id=wanted, name=node.name if visible and node else None, usable=usable
    )


@router.get("/admin/mailbox/shared-intake", response_model=SharedIntakeOut)
def get_shared_intake(
    admin: Principal = Depends(require_admin), db: Session = Depends(get_db)
) -> SharedIntakeOut:
    return _intake_out(db, admin)


@router.put("/admin/mailbox/shared-intake", response_model=SharedIntakeOut)
def set_shared_intake(
    body: SharedIntakeIn,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> SharedIntakeOut:
    """Designates the shared-tree folder that `ingest@` mail is filed into (null clears it). The
    administrator must be able to write there, and the folder must be in the shared tree."""
    row = db.get(AppSetting, ingest.SHARED_INTAKE_KEY)
    if body.node_id is None:
        if row is not None:
            db.delete(row)
    else:
        node = db.get(Node, body.node_id)
        root = db.get(StorageRoot, node.root_id) if node is not None else None
        actor = Actor(admin.user.id, session_id=admin.session.id)
        if (
            node is None
            or root is None
            or root.kind != "shared"
            or node.kind != "dir"
            or node.status != "active"
            or Privilege.WRITE not in engine.effective(db, actor, node)
        ):
            raise http_error(403, "FORBIDDEN")  # one answer for missing, personal and no access
        if row is None:
            db.add(AppSetting(key=ingest.SHARED_INTAKE_KEY, value=str(node.id)))
        else:
            row.value = str(node.id)
    audit(
        db,
        "mail.shared_intake_set",
        actor=admin.user.id,
        ip=client_ip(request),
        node_id=str(body.node_id) if body.node_id else None,
    )
    db.flush()
    requeued = ingest.requeue_intake_waiting(db) if body.node_id else 0
    db.commit()
    if requeued:
        request.app.state.mailbox.poll_now()
    return _intake_out(db, admin)


# ------------------------------------------------------------------ approval (administrators)


class Approve(Strict):
    destination_id: uuid.UUID
    attachment_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


def _forget_staged(settings: Settings, m: InboundMessage, att: InboundAttachment) -> None:
    """Deletes one attachment's staged bytes once it was filed or refused."""
    if att.stored_name is not None:
        (store.quarantine_dir(settings) / str(m.id) / att.stored_name).unlink(missing_ok=True)
        att.stored_name = None


@router.post("/admin/quarantine/{message_id}/approve", response_model=MessageOut)
def quarantine_approve(
    message_id: uuid.UUID,
    body: Approve,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> MessageOut:
    """Files chosen quarantined attachments into a *shared-tree* folder the administrator can
    write to, with the administrator's own rights, like an upload they made themselves. Approval
    never reaches a personal folder: the destination is refused unless it is in the shared tree,
    and it grants the unknown sender nothing."""
    settings = ctx_of(request).settings
    m = _quarantined(db, message_id)
    if m.status != "quarantined":
        raise http_error(409, "NOT_PENDING")
    dest = db.get(Node, body.destination_id)
    root = db.get(StorageRoot, dest.root_id) if dest is not None else None
    if dest is None or root is None or root.kind != "shared":
        raise http_error(403, "FORBIDDEN")  # missing, personal and unreadable look the same
    wanted = set(body.attachment_ids)
    every = list(db.scalars(select(InboundAttachment).where(InboundAttachment.message_id == m.id)))
    atts = [a for a in every if a.id in wanted]
    if len(atts) != len(wanted):
        raise http_error(404, "NOT_FOUND")
    if any(a.stored_name is None or a.outcome is not None for a in atts):
        raise http_error(409, "CONTENT_NOT_KEPT")
    ctx = OpCtx(
        db, settings, admin.user.id, client_ip(request), session_id=admin.session.id, source="email"
    )
    filed = 0
    for att in sorted(atts, key=lambda a: a.position):
        try:
            refusal = ingest.file_attachment(ctx, dest.id, m, att)
        except OpError as err:
            raise http_error(err.status, err.code) from err
        if refusal is None:
            filed += 1
        else:
            att.outcome = refusal
        _forget_staged(settings, m, att)
        db.commit()
    m.filed_count += filed
    m.filed_node_id = dest.id if filed else m.filed_node_id
    if all(a.stored_name is None for a in every):
        m.status = "approved"
        store.purge_content(db, settings, m)
    audit(
        db,
        "mail.quarantine_approved",
        actor=admin.user.id,
        ip=client_ip(request),
        message=str(m.id),
        filed=filed,
        destination=str(dest.id),
    )
    db.commit()
    return _out(db, m, admin)
