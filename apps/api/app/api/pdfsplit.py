"""PDF split proposals and duplicate suggestions (M17).

A person sees and decides only their own split proposals. Nothing here accepts a path, a
permission or a label: a proposal is built by the server from validated page descriptors, and a
split is written only through the file operations service (`app.ai.split`). Duplicate suggestions
are read-only.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.ai import split
from app.ai.gateway import AiGateway
from app.api.trash import _undo_conflict, undo_out
from app.auth.deps import Principal, client_ip, ctx_of, current_principal, get_db, http_error
from app.domain import undo as undo_domain
from app.domain.fileops import OpError
from app.policy.model import Actor
from app.preview.service import PreviewService
from app.search import duplicates

router = APIRouter(prefix="/api/v1", tags=["ai"])

AI_PREFIX = "ai:"


class SuggestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: uuid.UUID
    use_ai: bool = True


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drop_blank: bool = False
    # Optional: replace the proposed boundaries with these first pages (1-based) of each part.
    cuts: list[int] | None = Field(default=None, max_length=split.MAX_PARTS)


def _out(view: split.SplitView) -> dict[str, Any]:
    r = view.row
    return {
        "id": str(r.id),
        "node_id": str(r.node_id),
        "node_name": view.node_name,
        "parent_name": view.parent_name,
        "status": r.status,
        "source": r.source,
        "page_count": r.page_count,
        "parts": [
            {"start": p["start"], "end": p["end"], "name": name}
            for p, name in zip(r.parts, view.names, strict=True)
        ],
        "blank_pages": list(r.blank_pages),
        "left_out": list(r.left_out),
        "warnings": list(r.warnings),
        "reason": r.reason,
        "confidence": r.confidence,
        "created_at": r.created_at,
        "outcome": r.outcome,
        "created_nodes": list(r.outputs) if r.status == "applied" else [],
        "undoable": view.undoable,
    }


@router.post("/ai/split/suggest")
def suggest(
    body: SuggestBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    previews: PreviewService = request.app.state.previews
    gateway: AiGateway = request.app.state.ai
    res = split.propose(
        db,
        ctx_of(request).settings,
        gateway,
        previews,
        principal.user,
        principal.session.id,
        client_ip(request),
        body.node_id,
        use_ai=body.use_ai,
    )
    out: dict[str, Any] = {"node_id": str(res.node_id)}
    if res.skipped is not None:
        out["skipped"] = res.skipped.removeprefix(AI_PREFIX)
    if res.proposal is not None:
        out["proposal_id"] = str(res.proposal.id)
        out["status"] = res.proposal.status
    return out


@router.get("/ai/split/proposals")
def proposals(
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    status: str = Query(
        default="pending", pattern="^(pending|applied|dismissed|stale|failed|undone|all)$"
    ),
) -> dict[str, Any]:
    views = split.visible(db, principal.user.id, principal.session.id, status=status)
    return {"items": [_out(v) for v in views]}


@router.post("/ai/split/proposals/{proposal_id}/approve")
def approve(
    proposal_id: uuid.UUID,
    body: ApproveBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    previews: PreviewService = request.app.state.previews
    try:
        row = split.apply(
            db,
            ctx_of(request).settings,
            previews,
            proposal_id,
            principal.user.id,
            session_id=principal.session.id,
            ip=client_ip(request),
            drop_blank=body.drop_blank,
            cuts=body.cuts,
        )
    except split.SplitError as err:
        raise http_error(err.status, err.code) from err
    return {
        "id": str(row.id),
        "status": row.status,
        "created_nodes": list(row.outputs),
        "left_out": list(row.left_out),
    }


@router.post("/ai/split/proposals/{proposal_id}/dismiss")
def dismiss(
    proposal_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        row = split.dismiss(db, proposal_id, principal.user.id, client_ip(request))
    except split.SplitError as err:
        raise http_error(err.status, err.code) from err
    return {"id": str(row.id), "status": row.status}


@router.post("/ai/split/proposals/{proposal_id}/undo")
def undo(
    proposal_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    """Moves the parts the split created to the trash. Parts changed since stay and are listed."""
    try:
        result = split.undo(
            db,
            ctx_of(request).settings,
            proposal_id,
            principal.user.id,
            session_id=principal.session.id,
            ip=client_ip(request),
            key=idempotency_key,
        )
    except split.SplitError as err:
        raise http_error(err.status, err.code) from err
    except undo_domain.UndoConflict as conflict:
        raise _undo_conflict(conflict.reason) from conflict
    except OpError as err:
        raise http_error(err.status, err.code) from err
    return undo_out(result)


# ------------------------------------------------------------------ duplicates


@router.get("/duplicates")
def list_duplicates(
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    folder_id: uuid.UUID | None = None,
    near: bool = True,
) -> dict[str, Any]:
    """Exact (same SHA-256) and near-duplicate groups among files this person can read now.
    Report only: nothing is removed or changed by this call."""
    actor = Actor(principal.user.id, "user", principal.session.id)
    groups = duplicates.find_duplicates(db, actor, folder_id=folder_id, near=near)
    return {
        "groups": [
            {
                "kind": g.kind,
                "similarity": g.similarity,
                "members": [
                    {
                        "node_id": str(m.node_id),
                        "name": m.name,
                        "size": m.size,
                        "revision": m.revision,
                        "parent_id": str(m.parent_id) if m.parent_id else None,
                        "parent_name": m.parent_name,
                    }
                    for m in g.members
                ],
            }
            for g in groups
        ]
    }
