"""AI organization proposals: settings, "suggest", the review queue (M16).

A person sees and decides only their own proposals. Nothing here lets a caller name a path, a
permission or a label: a proposal is created by the server from a validated model answer and
applied only through the file operations service (`app.ai.organize`).
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.ai import organize
from app.ai.gateway import AiGateway
from app.auth.deps import (
    Principal,
    client_ip,
    ctx_of,
    current_principal,
    get_db,
    http_error,
    require_admin,
)
from app.auth.schemas import Ok
from app.auth.service import audit
from app.db.models import AppSetting

router = APIRouter(prefix="/api/v1/ai/organize", tags=["ai"])

# An AI-layer refusal reaches the person as a plain, stable code without this internal prefix.
AI_PREFIX = "ai:"


class SuggestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_ids: list[uuid.UUID] = Field(min_length=1, max_length=organize.MAX_BATCH)


class ModeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(pattern="^(off|review|auto)$")


class InstanceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shared_mode: str | None = Field(default=None, pattern="^(off|review|auto)$")
    # Per mille (500 to 1000). 0 clears it, which disables auto everywhere.
    auto_min_per_mille: int | None = Field(default=None, ge=0, le=1000)


def _gateway(request: Request) -> AiGateway:
    gateway: AiGateway = request.app.state.ai
    return gateway


def _out(view: organize.ProposalView) -> dict[str, Any]:
    r = view.row
    return {
        "id": str(r.id),
        "node_id": str(r.node_id),
        "node_name": view.node_name,
        "parent_name": view.parent_name,
        "status": r.status,
        "kind": r.kind,
        "new_name": r.new_name,
        "target_parent_id": str(r.target_parent_id) if r.target_parent_id else None,
        "target_name": view.target_name,
        "new_folder_name": r.new_folder_name,
        "doc_type": r.doc_type,
        "reason": r.reason,
        "confidence": r.confidence,
        "sensitive": r.sensitive,
        "sensitive_reasons": r.sensitive_reasons,
        "needs_review": list(r.auto_blockers),
        "created_at": r.created_at,
        "outcome": r.outcome,
        "auto": r.auto,
        "operation_id": str(r.operation_id) if r.operation_id else None,
        "undoable": view.undoable,
    }


@router.get("/settings")
def get_settings(
    principal: Principal = Depends(current_principal), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return {
        "mode": principal.user.ai_organize,
        "shared_mode": organize.shared_mode(db),
        "auto_available": organize.threshold(db) is not None,
        "modes": list(organize.MODES),
    }


@router.put("/me", response_model=Ok)
def set_mode(
    body: ModeBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> Ok:
    principal.user.ai_organize = body.mode
    audit(db, "ai.organize_mode", actor=principal.user.id, ip=client_ip(request), value=body.mode)
    db.commit()
    return Ok()


@router.get("/instance")
def get_instance(
    _: Principal = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return {
        "shared_mode": organize.shared_mode(db),
        "auto_min_per_mille": organize.threshold(db),
        "min_allowed": organize.MIN_THRESHOLD,
        "stats": organize.stats(db, None),  # counts only, whole instance
    }


def _put_setting(db: Session, key: str, value: Any) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value


@router.put("/instance", response_model=Ok)
def set_instance(
    body: InstanceBody,
    request: Request,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Ok:
    value = body.auto_min_per_mille
    if value is not None and value != 0 and value < organize.MIN_THRESHOLD:
        raise http_error(422, "THRESHOLD_TOO_LOW")
    if body.shared_mode is not None:
        _put_setting(db, organize.SHARED_MODE_KEY, body.shared_mode)
    if value is not None:
        _put_setting(db, organize.THRESHOLD_KEY, value)
    audit(
        db,
        "ai.organize_instance",
        actor=principal.user.id,
        ip=client_ip(request),
        shared_mode=body.shared_mode,
        threshold=value,
    )
    db.commit()
    return Ok()


@router.post("/suggest")
def suggest(
    body: SuggestBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    results = organize.suggest(
        db,
        ctx_of(request).settings,
        _gateway(request),
        principal.user,
        principal.session.id,
        client_ip(request),
        body.node_ids,
    )
    items: list[dict[str, Any]] = []
    for res in results:
        item: dict[str, Any] = {"node_id": str(res.node_id)}
        if res.skipped is not None:
            item["skipped"] = res.skipped.removeprefix(AI_PREFIX)
        if res.proposal is not None:
            item["proposal_id"] = str(res.proposal.id)
            item["status"] = res.proposal.status
            item["applied"] = res.applied
        items.append(item)
    return {"items": items}


@router.get("/proposals")
def proposals(
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    status: str = Query(
        default="pending", pattern="^(pending|applied|dismissed|stale|failed|all)$"
    ),
) -> dict[str, Any]:
    views = organize.visible_proposals(db, principal.user.id, principal.session.id, status=status)
    return {"items": [_out(v) for v in views]}


def _decide(
    request: Request, db: Session, principal: Principal, pid: uuid.UUID, *, approve: bool
) -> dict[str, Any]:
    try:
        if approve:
            row = organize.apply(
                db,
                ctx_of(request).settings,
                pid,
                principal.user.id,
                session_id=principal.session.id,
                ip=client_ip(request),
            )
        else:
            row = organize.dismiss(db, pid, principal.user.id, client_ip(request))
    except organize.ApplyError as err:
        raise http_error(err.status, err.code) from err
    return {
        "id": str(row.id),
        "status": row.status,
        "operation_id": str(row.operation_id) if row.operation_id else None,
    }


@router.post("/proposals/{proposal_id}/approve")
def approve(
    proposal_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _decide(request, db, principal, proposal_id, approve=True)


@router.post("/proposals/{proposal_id}/dismiss")
def dismiss(
    proposal_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _decide(request, db, principal, proposal_id, approve=False)


@router.get("/stats")
def my_stats(
    principal: Principal = Depends(current_principal), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return {"buckets": organize.stats(db, principal.user.id)}
