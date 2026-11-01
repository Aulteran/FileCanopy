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
from app.policy import engine, privacy
from app.policy.deps import NodeContext, grant_admin_access, traverse_access
from app.policy.model import Actor, Privilege

router = APIRouter(prefix="/api/v1", tags=["storage"])


class EntryPoint(BaseModel):
    node_id: uuid.UUID
    name: str
    kind: Literal["personal", "shared", "shared_with_me"]
    owner_name: str | None = None
    # The setup window is open and this person may use it (ADR-011 §1).
    setup_open: bool = False


class NodeOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    root_kind: str
    my_privileges: list[str]
    parent_id: uuid.UUID | None
    # Root nodes only, and only for whoever may manage the root (administrator for the shared tree,
    # owner for a personal folder); otherwise null. Never names or counts of anything inside.
    can_manage: bool = False
    setup_state: str | None = None
    scan_state: str | None = None
    # An import has held the reconciler off this root for a long time (bounded by a ceiling), so
    # changes made outside the app may show up late. Managers only.
    scan_delayed: bool = False
    # Folders the caller may share: how many people they are shared with. Null for everyone else.
    shared_with: int | None = None
    # Label of this folder and of its place, and whether the Confidential lock is shut (M09).
    privacy_own: str = "normal"
    privacy_effective: str = "normal"
    locked: bool = False


class GrantOut(BaseModel):
    # For an inherited grant: the node it is set on (above this folder) and that folder's name.
    inherited_from_id: uuid.UUID | None = None
    inherited_from_name: str | None = None
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
    for node in engine.entry_points(db, Actor(principal.user.id, session_id=principal.session.id)):
        root = db.get(StorageRoot, node.root_id)
        if root is None:
            continue
        setup_open = (
            node.parent_id is None
            and root.setup_state == "open"
            and engine.can_administer_grants(
                db, Actor(principal.user.id, session_id=principal.session.id), node
            )
        )
        if root.kind == "personal" and root.owner_user_id == principal.user.id:
            out.append(
                EntryPoint(node_id=node.id, name="My files", kind="personal", setup_open=setup_open)
            )
        elif root.kind == "shared":
            out.append(
                EntryPoint(node_id=node.id, name=node.name, kind="shared", setup_open=setup_open)
            )
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
        if Privilege.TRAVERSE in engine.effective(
            db, Actor(ctx.principal.user.id, session_id=ctx.principal.session.id), parent
        ):
            parent_id = node.parent_id
    manage = (
        node.parent_id is None
        and root is not None
        and engine.can_administer_grants(
            db, Actor(ctx.principal.user.id, session_id=ctx.principal.session.id), node
        )
    )
    shared_with: int | None = None
    if node.kind == "dir" and engine.can_administer_grants(
        db, Actor(ctx.principal.user.id, session_id=ctx.principal.session.id), node
    ):
        shared_with = len(grants.users_with_access(db, grants.chain_ids(node)))
    scope = privacy.confidential_scope(db, node)
    locked = scope is not None and (
        privacy.live_unlock(db, ctx.principal.user.id, ctx.principal.session.id, scope) is None
    )
    return NodeOut(
        id=node.id,
        name=node.name,
        kind=node.kind,
        root_kind=root.kind if root else "unknown",
        my_privileges=sorted(p.value for p in ctx.privileges),
        parent_id=parent_id,
        can_manage=manage,
        setup_state=root.setup_state if manage and root else None,
        scan_state=root.scan_state if manage and root else None,
        scan_delayed=bool(manage and root and root.scan_paused_since is not None),
        shared_with=shared_with,
        privacy_own=node.privacy_label,
        privacy_effective=privacy.effective_label(db, node),
        locked=locked,
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
    out = [
        GrantOut(
            node_id=g.node_id,
            grantee_user_id=g.grantee_user_id,
            grantee_name=name,
            level=g.level,
            created_at=g.created_at,
        )
        for g, name in rows
    ]
    # Access that comes from a bigger share above this folder, read-only here.
    inherited = grants.inherited_grants(db, ctx.node)
    names = {
        r.id: r.name
        for r in db.execute(
            select(User.id, User.name).where(
                User.status == "active", User.id.in_({g.grantee_user_id for g, _ in inherited})
            )
        )
    }
    for g, above in inherited:
        if g.grantee_user_id not in names:
            continue
        out.append(
            GrantOut(
                inherited_from_id=above.id,
                inherited_from_name=above.name,
                node_id=g.node_id,
                grantee_user_id=g.grantee_user_id,
                grantee_name=names.get(g.grantee_user_id, "Someone"),
                level=g.level,
                created_at=g.created_at,
            )
        )
    return out


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
    if privacy.locked_scopes_in(db, me.id, ctx.principal.session.id, node):
        raise http_error(403, "LOCKED")  # sharing would let another account unlock it (M09)
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


@router.delete("/nodes/{node_id}/grants-mine", response_model=Ok)
def leave_share(
    request: Request,
    ctx: NodeContext = Depends(traverse_access),
    db: Session = Depends(get_db),
) -> Ok:
    """A person gives up a folder someone else shared with them. Only a direct grant on a folder in
    another person's personal area can be left; the shared tree is managed by the administrator."""
    me = ctx.principal.user
    root = db.get(StorageRoot, ctx.node.root_id)
    if root is None or root.kind != "personal" or root.owner_user_id in (None, me.id):
        raise http_error(422, "NOT_A_SHARE")
    if not grants.revoke_grant(db, ctx.node, me.id, me.id, client_ip(request)):
        db.rollback()
        raise http_error(404, "GRANT_NOT_FOUND")
    grants.notify(db, root.owner_user_id, "share_left", node_id=ctx.node.id, actor=me.id)
    db.commit()
    return Ok()
