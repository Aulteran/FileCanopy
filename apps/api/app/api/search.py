"""Search (M11): filenames and indexed text, for any signed-in person. Results are decided by the
policy engine per request (`app.search.query`); the route returns ids, names and snippets only."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth.deps import Principal, client_ip, current_principal, get_db, http_error
from app.auth.service import audit
from app.db.models import Node, SearchChunk, SearchDoc, SearchEmbed, SearchJob, StorageRoot, User
from app.policy.model import Actor
from app.preview import ocr
from app.search import query, semantic

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
    mode: str  # what actually ran: fulltext | semantic | hybrid


class StatusOut(BaseModel):
    enabled: bool
    ocr_available: bool
    indexed: int
    waiting: int
    semantic_active: bool


@router.get("/search", response_model=SearchOut)
def search(
    request: Request,
    q: str = Query(min_length=1, max_length=200),
    folder: uuid.UUID | None = None,
    limit: int = Query(default=25, ge=1, le=50),
    mode: Literal["auto", "fulltext", "semantic", "hybrid"] = "auto",
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> SearchOut:
    """`auto` is hybrid when semantic search is active and full-text otherwise. Asking for a
    semantic mode while it is off (or its endpoint is down) quietly gives full-text results; there
    is never a fallback to anything but local full-text search."""
    actor = Actor(principal.user.id, session_id=principal.session.id)
    sem: semantic.Semantic = request.app.state.semantic
    want = mode
    if want == "auto":
        want = "hybrid" if semantic.is_active(db, sem.settings) else "fulltext"
    qvec = None
    if want != "fulltext" and semantic.is_active(db, sem.settings):
        if not sem.allow_query(actor.user_id):
            raise http_error(429, "SEARCH_TOO_FAST")
        # The embedding call is a network round trip: end this request's transaction first so no
        # database connection is held while the model server thinks.
        db.commit()
        qvec = sem.query_vector(q)
    if want == "fulltext" or qvec is None:
        hits = query.search(db, actor, q, folder_id=folder, limit=limit)
        return SearchOut(hits=[HitOut(**vars(h)) for h in hits], mode="fulltext")
    near = semantic.semantic_hits(db, actor, sem, q, qvec=qvec, folder_id=folder, limit=limit * 2)
    if near is None:
        hits = query.search(db, actor, q, folder_id=folder, limit=limit)
        return SearchOut(hits=[HitOut(**vars(h)) for h in hits], mode="fulltext")
    if want == "semantic":
        return SearchOut(hits=[HitOut(**vars(h)) for h in near[:limit]], mode="semantic")
    text_hits = query.search(db, actor, q, folder_id=folder, limit=limit * 2)
    merged = semantic.fuse(text_hits, near, limit)
    return SearchOut(hits=[HitOut(**vars(h)) for h in merged], mode="hybrid")


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
        semantic_active=semantic.is_active(db, cfg),
    )


class SemanticMeOut(BaseModel):
    available: bool  # the administrator has a healthy local endpoint switched on
    opt_out: bool
    indexed_files: int  # the person's own files only
    waiting_files: int


class SemanticMeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    opt_out: bool


def _me_out(db: Session, request: Request, user: User) -> SemanticMeOut:
    root = db.scalar(select(StorageRoot.id).where(StorageRoot.owner_user_id == user.id))
    done = waiting = 0
    if root is not None:
        own = select(Node.id).where(Node.root_id == root)
        done = (
            db.scalar(
                select(func.count())
                .select_from(SearchEmbed)
                .where(SearchEmbed.node_id.in_(own), SearchEmbed.state == "done")
            )
            or 0
        )
        waiting = (
            db.scalar(
                select(func.count())
                .select_from(SearchEmbed)
                .where(SearchEmbed.node_id.in_(own), SearchEmbed.state.in_(("queued", "running")))
            )
            or 0
        )
    return SemanticMeOut(
        available=semantic.is_active(db, request.app.state.auth.settings),
        opt_out=user.semantic_opt_out,
        indexed_files=done,
        waiting_files=waiting,
    )


@router.get("/search/semantic/me", response_model=SemanticMeOut)
def semantic_me(
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> SemanticMeOut:
    return _me_out(db, request, principal.user)


@router.put("/search/semantic/me", response_model=SemanticMeOut)
def set_semantic_me(
    body: SemanticMeBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> SemanticMeOut:
    """Opting out removes this person's vectors at once; opting back in lets the indexer
    rebuild them."""
    user = db.get(User, principal.user.id)
    if user is None:
        raise http_error(401, "UNAUTHENTICATED")
    if user.semantic_opt_out != body.opt_out:
        user.semantic_opt_out = body.opt_out
        if body.opt_out:
            roots = select(StorageRoot.id).where(StorageRoot.owner_user_id == user.id)
            db.execute(delete(SearchChunk).where(SearchChunk.root_id.in_(roots)))
            db.execute(
                delete(SearchEmbed).where(
                    SearchEmbed.node_id.in_(select(Node.id).where(Node.root_id.in_(roots)))
                )
            )
        audit(
            db, "search.semantic_opt_out", actor=user.id, ip=client_ip(request), value=body.opt_out
        )
        db.commit()
    return _me_out(db, request, user)
