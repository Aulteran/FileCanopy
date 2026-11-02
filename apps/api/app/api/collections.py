"""Virtual collections and AI grouping suggestions (M18).

Every response is built from what the calling person may read right now: members they cannot read
are dropped before anything is counted or named. A collection that is missing and one the person
may not open answer identically. Nothing here moves, copies or changes a file.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.ai import collect
from app.ai.gateway import AiGateway
from app.auth.deps import Principal, client_ip, ctx_of, current_principal, get_db, http_error
from app.db.models import Collection, Node, User
from app.domain import collections
from app.domain.collections import CollectionError
from app.policy.model import Actor

router = APIRouter(prefix="/api/v1", tags=["collections"])


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    node_ids: list[uuid.UUID] = Field(default_factory=list, max_length=collections.MAX_ADD)


class TitleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)


class MembersBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_ids: list[uuid.UUID] = Field(min_length=1, max_length=collections.MAX_ADD)


class ShareBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: str = Field(pattern="^(viewer|editor)$")


class SuggestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_ids: list[uuid.UUID] = Field(min_length=2, max_length=collect.MAX_FILES)


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=200)
    exclude: list[uuid.UUID] = Field(default_factory=list, max_length=collect.MAX_FILES)


def _actor(p: Principal) -> Actor:
    return Actor(p.user.id, "user", p.session.id)


def _err(err: CollectionError | collect.SuggestError) -> Exception:
    return http_error(err.status, err.code)


def _member_out(db: Session, actor: Actor, node: Node) -> dict[str, Any]:
    parent = collections.parent_of(db, actor, node)
    return {
        "node_id": str(node.id),
        "name": node.name,
        "size": node.size or 0,
        "revision": node.revision,
        "parent_id": str(parent.id) if parent is not None else None,
        "parent_name": parent.name if parent is not None else None,
    }


def _summary_out(s: collections.Summary) -> dict[str, Any]:
    c = s.collection
    return {
        "id": str(c.id),
        "title": c.title,
        "role": s.role,
        "owner_name": s.owner_name,
        "from_suggestion": c.suggestion_id is not None,
    }


def _detail(db: Session, actor: Actor, acc: collections.Access) -> dict[str, Any]:
    members = collections.readable_members(db, actor, acc.collection.id)
    owner = None
    if acc.role != "owner":
        row = db.get(User, acc.collection.owner_user_id)
        owner = row.name if row is not None else None
    return {
        "id": str(acc.collection.id),
        "title": acc.collection.title,
        "role": acc.role,
        "owner_name": owner,
        "count": len(members),
        # Only the owner sees it: for anyone else it would show activity on hidden members.
        "updated_at": acc.collection.updated_at if acc.role == "owner" else None,
        "members": [_member_out(db, actor, n) for n in members],
        "shares": [
            {"user_id": str(s.user_id), "name": s.name, "level": s.level}
            for s in collections.shares_of(db, acc)
        ],
    }


def _open(db: Session, principal: Principal, collection_id: uuid.UUID) -> collections.Access:
    try:
        return collections.access(db, principal.user.id, collection_id)
    except CollectionError as err:
        raise _err(err) from err


@router.get("/collections")
def list_collections(
    principal: Principal = Depends(current_principal), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return {"items": [_summary_out(s) for s in collections.list_for(db, _actor(principal))]}


@router.post("/collections", status_code=201)
def create_collection(
    body: CreateBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    actor = _actor(principal)
    try:
        coll = collections.create(
            db,
            principal.user.id,
            body.title,
            client_ip(request),
            node_ids=body.node_ids,
            actor=actor,
        )
        db.commit()
    except CollectionError as err:
        db.rollback()
        raise _err(err) from err
    return _detail(db, actor, collections.Access(coll, "owner"))


@router.get("/collections/{collection_id}")
def get_collection(
    collection_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _detail(db, _actor(principal), _open(db, principal, collection_id))


@router.put("/collections/{collection_id}")
def rename_collection(
    collection_id: uuid.UUID,
    body: TitleBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    acc = _open(db, principal, collection_id)
    try:
        collections.rename(db, acc, body.title, client_ip(request))
        db.commit()
    except CollectionError as err:
        db.rollback()
        raise _err(err) from err
    return _detail(db, _actor(principal), acc)


@router.delete("/collections/{collection_id}", status_code=204)
def delete_collection(
    collection_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> None:
    acc = _open(db, principal, collection_id)
    try:
        collections.delete_collection(db, acc, client_ip(request))
        db.commit()
    except CollectionError as err:
        db.rollback()
        raise _err(err) from err


@router.post("/collections/{collection_id}/members")
def add_members(
    collection_id: uuid.UUID,
    body: MembersBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    acc = _open(db, principal, collection_id)
    if acc.role == "viewer":
        raise http_error(403, "FORBIDDEN")
    actor = _actor(principal)
    try:
        collections.add_members(db, actor, acc.collection, body.node_ids, client_ip(request))
        db.commit()
    except CollectionError as err:
        db.rollback()
        raise _err(err) from err
    return _detail(db, actor, acc)


@router.post("/collections/{collection_id}/members/remove")
def remove_members(
    collection_id: uuid.UUID,
    body: MembersBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    acc = _open(db, principal, collection_id)
    if acc.role == "viewer":
        raise http_error(403, "FORBIDDEN")
    actor = _actor(principal)
    try:
        collections.remove_members(db, actor, acc.collection, body.node_ids, client_ip(request))
        db.commit()
    except CollectionError as err:
        db.rollback()
        raise _err(err) from err
    return _detail(db, actor, acc)


@router.put("/collections/{collection_id}/shares/{user_id}")
def share(
    collection_id: uuid.UUID,
    user_id: uuid.UUID,
    body: ShareBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    acc = _open(db, principal, collection_id)
    try:
        collections.set_share(db, acc, user_id, body.level, client_ip(request))
        db.commit()
    except CollectionError as err:
        db.rollback()
        raise _err(err) from err
    return _detail(db, _actor(principal), acc)


@router.delete("/collections/{collection_id}/shares/{user_id}")
def unshare(
    collection_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    acc = _open(db, principal, collection_id)
    try:
        collections.remove_share(db, acc, user_id, principal.user.id, client_ip(request))
        db.commit()
    except CollectionError as err:
        db.rollback()
        raise _err(err) from err
    if acc.role != "owner":
        return {"left": True}
    return _detail(db, _actor(principal), acc)


@router.get("/nodes/{node_id}/collections")
def collections_of_node(
    node_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Collections the person can open that reference this file. A file the person cannot read
    answers with an empty list, exactly like a file that is in none."""
    node = db.get(Node, node_id)
    items: list[Collection] = (
        collections.collections_of(db, _actor(principal), node) if node is not None else []
    )
    return {"items": [{"id": str(c.id), "title": c.title} for c in items]}


# ------------------------------------------------------------------ AI suggestions


def _suggestion_out(db: Session, actor: Actor, view: collect.View) -> dict[str, Any]:
    r = view.row
    return {
        "id": str(r.id),
        "status": r.status,
        "title": r.title,
        "reason": r.reason,
        "confidence": r.confidence,
        "created_at": r.created_at,
        "outcome": r.outcome,
        "collection_id": str(r.collection_id) if r.collection_id else None,
        "members": [_member_out(db, actor, n) for n in view.members],
    }


@router.post("/ai/collections/suggest")
def suggest(
    body: SuggestBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    gateway: AiGateway = request.app.state.ai
    try:
        res = collect.suggest(
            db,
            ctx_of(request).settings,
            gateway,
            principal.user,
            principal.session.id,
            client_ip(request),
            body.node_ids,
        )
    except collect.SuggestError as err:
        raise _err(err) from err
    out: dict[str, Any] = {
        "suggestion_ids": [str(r.id) for r in res.rows],
        "skipped": {str(k): v for k, v in res.skipped.items()},
    }
    if res.refused is not None:
        out["refused"] = res.refused
    if res.note is not None:
        out["note"] = res.note
    return out


@router.get("/ai/collections/suggestions")
def suggestions(
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    status: str = Query(default="pending", pattern="^(pending|approved|dismissed|stale|all)$"),
) -> dict[str, Any]:
    actor = _actor(principal)
    views = collect.visible(db, principal.user.id, principal.session.id, status=status)
    return {"items": [_suggestion_out(db, actor, v) for v in views]}


@router.post("/ai/collections/suggestions/{suggestion_id}/approve")
def approve(
    suggestion_id: uuid.UUID,
    body: ApproveBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        coll = collect.approve(
            db,
            suggestion_id,
            principal.user.id,
            principal.session.id,
            client_ip(request),
            title=body.title,
            exclude=body.exclude,
        )
    except collect.SuggestError as err:
        raise _err(err) from err
    return {"id": str(suggestion_id), "status": "approved", "collection_id": str(coll.id)}


@router.post("/ai/collections/suggestions/{suggestion_id}/dismiss")
def dismiss(
    suggestion_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        row = collect.dismiss(db, suggestion_id, principal.user.id, client_ip(request))
    except collect.SuggestError as err:
        raise _err(err) from err
    return {"id": str(row.id), "status": row.status}
