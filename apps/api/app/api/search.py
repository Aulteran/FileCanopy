"""Search (M11): filenames and indexed text, for any signed-in person. Results are decided by the
policy engine per request (`app.search.query`); the route returns ids, names and snippets only."""

import uuid

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import Principal, current_principal, get_db
from app.db.models import Node, SearchDoc, SearchJob, StorageRoot
from app.policy.model import Actor
from app.preview import ocr
from app.search import query

router = APIRouter(prefix="/api/v1", tags=["search"])


class HitOut(BaseModel):
    node_id: uuid.UUID
    name: str
    kind: str
    parent_id: uuid.UUID | None
    match: str
    snippet: str | None


class SearchOut(BaseModel):
    hits: list[HitOut]


class StatusOut(BaseModel):
    enabled: bool
    ocr_available: bool
    indexed: int
    waiting: int


@router.get("/search", response_model=SearchOut)
def search(
    q: str = Query(min_length=1, max_length=200),
    folder: uuid.UUID | None = None,
    limit: int = Query(default=25, ge=1, le=50),
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> SearchOut:
    actor = Actor(principal.user.id, session_id=principal.session.id)
    hits = query.search(db, actor, q, folder_id=folder, limit=limit)
    return SearchOut(hits=[HitOut(**vars(h)) for h in hits])


@router.get("/search/status", response_model=StatusOut)
def status(
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> StatusOut:
    """Counts for the person's own folder only, so the numbers reveal nothing about anyone else."""
    cfg = request.app.state.auth.settings
    root = db.scalar(select(StorageRoot.id).where(StorageRoot.owner_user_id == principal.user.id))
    indexed = waiting = 0
    if root is not None:
        indexed = db.scalar(select(func.count()).where(SearchDoc.root_id == root)) or 0
        waiting = (
            db.scalar(
                select(func.count())
                .select_from(SearchJob)
                .join(Node, Node.id == SearchJob.node_id)
                .where(Node.root_id == root, SearchJob.state.in_(("queued", "running")))
            )
            or 0
        )
    return StatusOut(
        enabled=cfg.search_enabled,
        ocr_available=ocr.configured(cfg.ocr_url),
        indexed=indexed,
        waiting=waiting,
    )
