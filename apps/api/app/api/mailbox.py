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
from pydantic import BaseModel
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
from app.db.models import InboundAttachment, InboundMessage, MailboxState
from app.mail import store

router = APIRouter(prefix="/api/v1", tags=["mailbox"])

QUARANTINE_STATES = ("quarantined", "error", "rejected")
_STORED = re.compile(r"^\d{1,4}\.bin$")


class AttachmentOut(BaseModel):
    id: uuid.UUID
    display_name: str
    content_type: str
    size_bytes: int
    stored: bool


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


def _out(db: Session, m: InboundMessage) -> MessageOut:
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
        attachments=[
            AttachmentOut(
                id=a.id,
                display_name=a.display_name,
                content_type=a.content_type,
                size_bytes=a.size_bytes,
                stored=a.stored_name is not None,
            )
            for a in atts
        ],
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
    return [_out(db, m) for m in rows]


# ------------------------------------------------------------------ administrators


class MailboxStatus(BaseModel):
    configured: bool
    host: str | None
    folder: str | None
    personal_address: str | None
    ingest_address: str | None
    authentication_configured: bool
    auth_policy: str | None
    last_poll_at: datetime | None
    last_error: str | None
    failures: int
    quarantined: int
    errors: int
    verified: int


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
        auth_policy=s.imap_auth_policy if s.imap_configured else None,
        last_poll_at=state.last_poll_at if state else None,
        last_error=state.last_error if state else None,
        failures=state.failures if state else 0,
        quarantined=count("quarantined"),
        errors=count("error"),
        verified=count("verified"),
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
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[MessageOut]:
    rows = db.scalars(
        select(InboundMessage)
        .where(InboundMessage.status.in_(QUARANTINE_STATES))
        .order_by(InboundMessage.received_at.desc(), InboundMessage.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [_out(db, m) for m in rows]


def _quarantined(db: Session, message_id: uuid.UUID) -> InboundMessage:
    m = db.get(InboundMessage, message_id)
    if m is None or m.status not in QUARANTINE_STATES:
        raise http_error(404, "NOT_FOUND")  # a verified person's mail looks like nothing at all
    return m


@router.get("/admin/quarantine/{message_id}", response_model=MessageOut)
def quarantine_detail(
    message_id: uuid.UUID,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> MessageOut:
    return _out(db, _quarantined(db, message_id))


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
    if att is None or att.message_id != m.id or att.stored_name is None or m.status == "rejected":
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
    if m.status != "rejected":
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
    return _out(db, m)
