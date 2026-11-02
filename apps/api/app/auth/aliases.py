"""Sender aliases: extra addresses a person proves they control (M13, ADR-007).

Adding or removing an address needs a proof (the account password, or a fresh provider
re-authentication of this session): a cookie alone never changes who may send as someone. The
mailed link works only for the signed-in person who asked, once, and a verified address belongs to
one account. The reply to "add" is the same whether or not the address is taken.
"""

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import clock
from app.auth.deps import (
    Principal,
    client_ip,
    ctx_of,
    current_principal,
    get_db,
    http_error,
)
from app.auth.pin_router import check_password, consume_reauth
from app.auth.schemas import Ok, Strict
from app.auth.security import digest, new_token
from app.auth.service import audit, valid_email
from app.db.models import User, UserEmailAlias
from app.mail.address import address_forms, normalize_address, reserved_addresses

router = APIRouter(prefix="/api/v1/me/emails", tags=["aliases"])


class AliasProof(Strict):
    """Exactly one of these."""

    password: str | None = Field(default=None, max_length=1024)
    reauth: bool = False


class AddAlias(AliasProof):
    email: str = Field(max_length=320)


class VerifyAlias(Strict):
    token: str = Field(max_length=256)


class AliasOut(BaseModel):
    id: uuid.UUID
    email: str
    verified: bool
    created_at: datetime


class EmailsOut(BaseModel):
    primary: str
    aliases: list[AliasOut]
    can_add: bool  # outbound mail is needed to prove control of an address
    limit: int


def _require_proof(request: Request, db: Session, principal: Principal, proof: AliasProof) -> None:
    if (proof.password is None) == (not proof.reauth):
        raise http_error(422, "PROOF_REQUIRED")
    if proof.password is not None:
        check_password(request, db, principal, proof.password)
    elif not consume_reauth(db, principal, need_strong=True):
        db.commit()
        raise http_error(403, "REAUTH_REQUIRED")


def _owned_elsewhere(db: Session, user_id: uuid.UUID, address: str) -> bool:
    forms = address_forms(address)
    other_primary = db.scalar(select(User.id).where(User.email.in_(forms), User.id != user_id))
    other_alias = db.scalar(
        select(UserEmailAlias.id).where(
            UserEmailAlias.email.in_(forms),
            UserEmailAlias.verified_at.is_not(None),
            UserEmailAlias.user_id != user_id,
        )
    )
    return other_primary is not None or other_alias is not None


@router.get("", response_model=EmailsOut)
def list_emails(
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> EmailsOut:
    ctx = ctx_of(request)
    rows = db.scalars(
        select(UserEmailAlias)
        .where(UserEmailAlias.user_id == principal.user.id)
        .order_by(UserEmailAlias.created_at)
    ).all()
    return EmailsOut(
        primary=principal.user.email,
        aliases=[
            AliasOut(
                id=r.id, email=r.email, verified=r.verified_at is not None, created_at=r.created_at
            )
            for r in rows
        ],
        can_add=ctx.mailer.configured and ctx.settings.max_aliases_per_user > 0,
        limit=ctx.settings.max_aliases_per_user,
    )


@router.post("", status_code=202, response_model=Ok)
def add_email(
    body: AddAlias,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> Ok:
    ctx, user, ip = ctx_of(request), principal.user, client_ip(request)
    if not ctx.mailer.configured:
        raise http_error(503, "MAIL_NOT_CONFIGURED")
    address = normalize_address(body.email)
    if address is None or not valid_email(address):
        raise http_error(422, "INVALID_EMAIL")
    keys = ctx.throttle.alias_keys(str(user.id), address, ip)
    if (retry := ctx.throttle.retry_after(db, keys)) is not None:
        db.commit()
        raise http_error(429, "TOO_MANY_ATTEMPTS", {"Retry-After": str(retry)})
    _require_proof(request, db, principal, body)
    ctx.throttle.record_failure(db, keys)  # counts every request, successful or not
    if address in reserved_addresses(ctx.settings):
        db.commit()
        raise http_error(409, "ADDRESS_RESERVED")
    if address in address_forms(user.email) or user.email in address_forms(address):
        db.commit()
        raise http_error(409, "ALREADY_YOUR_ADDRESS")
    mine = db.scalar(
        select(UserEmailAlias).where(
            UserEmailAlias.user_id == user.id, UserEmailAlias.email == address
        )
    )
    if mine is not None and mine.verified_at is not None:
        db.commit()
        raise http_error(409, "ALREADY_VERIFIED")
    total = len(
        db.scalars(select(UserEmailAlias.id).where(UserEmailAlias.user_id == user.id)).all()
    )
    if mine is None and total >= ctx.settings.max_aliases_per_user:
        db.commit()
        raise http_error(409, "ALIAS_LIMIT")
    if _owned_elsewhere(db, user.id, address):
        # Same answer as success; nothing is sent and nothing is stored.
        audit(db, "mail.alias_refused", actor=user.id, target=user.id, ip=ip)
        db.commit()
        return Ok()
    token = new_token()
    now = clock.utcnow()
    expires = now + timedelta(seconds=ctx.settings.alias_ttl_seconds)
    if mine is None:
        mine = UserEmailAlias(user_id=user.id, email=address, created_at=now)
        db.add(mine)
    mine.token_hash, mine.token_expires_at = digest(token), expires
    audit(db, "mail.alias_requested", actor=user.id, target=user.id, ip=ip, alias=str(mine.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return Ok()
    ctx.mailer.send_in_background(*ctx.mailer.alias_verification(address, token))
    return Ok()


@router.post("/verify", response_model=AliasOut)
def verify_email(
    body: VerifyAlias,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> AliasOut:
    ctx, user, ip = ctx_of(request), principal.user, client_ip(request)
    keys = ctx.throttle.ip_keys("alias-token", ip)
    if (retry := ctx.throttle.retry_after(db, keys)) is not None:
        db.commit()
        raise http_error(429, "TOO_MANY_ATTEMPTS", {"Retry-After": str(retry)})
    now = clock.utcnow()
    row = db.scalar(select(UserEmailAlias).where(UserEmailAlias.token_hash == digest(body.token)))
    if (
        row is None
        or row.user_id != user.id
        or row.verified_at is not None
        or row.token_expires_at is None
        or row.token_expires_at <= now
    ):
        ctx.throttle.record_failure(db, keys)
        db.commit()
        raise http_error(400, "INVALID_TOKEN")
    if _owned_elsewhere(db, user.id, row.email):
        db.delete(row)
        audit(db, "mail.alias_refused", actor=user.id, target=user.id, ip=ip)
        db.commit()
        raise http_error(409, "EMAIL_IN_USE")
    row.verified_at, row.token_hash, row.token_expires_at = now, None, None
    audit(db, "mail.alias_verified", actor=user.id, target=user.id, ip=ip, alias=str(row.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise http_error(409, "EMAIL_IN_USE") from None
    return AliasOut(id=row.id, email=row.email, verified=True, created_at=row.created_at)


@router.post("/{alias_id}/remove", response_model=Ok)
def remove_email(
    alias_id: uuid.UUID,
    body: AliasProof,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> Ok:
    _require_proof(request, db, principal, body)
    row = db.get(UserEmailAlias, alias_id)
    if row is None or row.user_id != principal.user.id:
        db.commit()
        raise http_error(404, "NOT_FOUND")
    db.delete(row)
    audit(
        db,
        "mail.alias_removed",
        actor=principal.user.id,
        target=principal.user.id,
        ip=client_ip(request),
        alias=str(alias_id),
    )
    db.commit()
    return Ok()
