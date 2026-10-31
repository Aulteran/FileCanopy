"""Entry points, node metadata and folder grants. Ids only: no endpoint accepts a path.

Every `{node_id}` route depends on a policy guard; unauthorized and nonexistent ids both yield
403 FORBIDDEN (ADR-004 §5)."""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import Principal, client_ip, current_principal, get_db, http_error
from app.auth.schemas import Ok
from app.db.models import Node, NodeGrant, StorageRoot, User
from app.domain import grants
from app.policy import engine
from app.policy.deps import NodeContext, grant_admin_access, traverse_access
from app.policy.model import Actor, Privilege

router = APIRouter(prefix="/api/v1", tags=["storage"])


class EntryPoint(BaseModel):
    node_id: uuid.UUID
    name: str
    kind: Literal["personal", "shared", "shared_with_me"]
    owner_name: str | None = None


class NodeOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    root_kind: str
    my_privileges: list[str]
    parent_id: uuid.UUID | None


class GrantOut(BaseModel):
    node_id: uuid.UUID
    grantee_user_id: uuid.UUID
    grantee_name: str
    level: str
    created_at: datetime


class GrantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: Literal["viewer", "editor"]


@router.get("/roots", response_model=list[EntryPoint])
def list_roots(
    principal: Principal = Depends(current_principal), db: Session = Depends(get_db)
) -> list[EntryPoint]:
    out: list[EntryPoint] = []
    for node in engine.entry_points(db, Actor(principal.user.id)):
        root = db.get(StorageRoot, node.root_id)
        if root is None:
            continue
        if root.kind == "personal" and root.owner_user_id == principal.user.id:
            out.append(EntryPoint(node_id=node.id, name="My files", kind="personal"))
        elif root.kind == "shared":
            out.append(EntryPoint(node_id=node.id, name=node.name, kind="shared"))
        else:
            owner = db.get(User, root.owner_user_id) if root.owner_user_id else None
            out.append(
                EntryPoint(
                    node_id=node.id,
                    name=node.name,
                    kind="shared_with_me",
                    owner_name=owner.name if owner else None,
                )
            )
    return out


@router.get("/nodes/{node_id}", response_model=NodeOut)
def get_node(ctx: NodeContext = Depends(traverse_access), db: Session = Depends(get_db)) -> NodeOut:
    node = ctx.node
    root = db.get(StorageRoot, node.root_id)
    parent_id: uuid.UUID | None = None
    if node.parent_id is not None:  # a parent the actor cannot see is never revealed
        parent = db.get(Node, node.parent_id)
        if Privilege.TRAVERSE in engine.effective(db, Actor(ctx.principal.user.id), parent):
            parent_id = node.parent_id
    return NodeOut(
        id=node.id,
        name=node.name,
        kind=node.kind,
        root_kind=root.kind if root else "unknown",
        my_privileges=sorted(p.value for p in ctx.privileges),
        parent_id=parent_id,
    )


@router.get("/nodes/{node_id}/grants", response_model=list[GrantOut])
def list_grants(
    ctx: NodeContext = Depends(grant_admin_access), db: Session = Depends(get_db)
) -> list[GrantOut]:
    rows = db.execute(
        select(NodeGrant, User.name)
        .join(User, User.id == NodeGrant.grantee_user_id)
        .where(NodeGrant.node_id == ctx.node.id)
        .order_by(NodeGrant.created_at, User.name)
    ).all()
    return [
        GrantOut(
            node_id=g.node_id,
            grantee_user_id=g.grantee_user_id,
            grantee_name=name,
            level=g.level,
            created_at=g.created_at,
        )
        for g, name in rows
    ]


@router.put("/nodes/{node_id}/grants/{user_id}", response_model=GrantOut)
def put_grant(
    user_id: uuid.UUID,
    body: GrantBody,
    request: Request,
    ctx: NodeContext = Depends(grant_admin_access),
    db: Session = Depends(get_db),
) -> GrantOut:
    node, me = ctx.node, ctx.principal.user
    root = db.get(StorageRoot, node.root_id)
    grantee = db.get(User, user_id)
    if grantee is None or grantee.status == "disabled":
        raise http_error(422, "UNKNOWN_GRANTEE")
    if node.kind != "dir":
        raise http_error(422, "NOT_A_DIRECTORY")
    if root is not None and root.kind == "personal" and grantee.id == me.id:
        raise http_error(422, "CANNOT_GRANT_SELF")
    grant = grants.set_grant(db, node, grantee.id, body.level, me.id, client_ip(request))
    db.commit()
    return GrantOut(
        node_id=node.id,
        grantee_user_id=grantee.id,
        grantee_name=grantee.name,
        level=grant.level,
        created_at=grant.created_at,
    )


@router.delete("/nodes/{node_id}/grants/{user_id}", response_model=Ok)
def delete_grant(
    user_id: uuid.UUID,
    request: Request,
    ctx: NodeContext = Depends(grant_admin_access),
    db: Session = Depends(get_db),
) -> Ok:
    if not grants.revoke_grant(db, ctx.node, user_id, ctx.principal.user.id, client_ip(request)):
        db.rollback()
        raise http_error(404, "GRANT_NOT_FOUND")
    db.commit()
    return Ok()
