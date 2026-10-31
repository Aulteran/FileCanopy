"""Routes for external-change reconciliation (ADR-011): rescan now, dismiss a missing node, and
decide a suspended root. Ids only; every route depends on a policy guard."""

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.auth import clock
from app.auth.deps import client_ip, get_db, http_error
from app.auth.schemas import Ok
from app.auth.service import audit
from app.db.models import Node, NodeGrant, ScanRun, StorageRoot
from app.domain import events
from app.domain.fileops import serialize_tree_changes
from app.policy import engine
from app.policy.deps import NodeContext, grant_admin_access, list_access, write_access
from app.policy.model import Actor
from app.storage.service import ReconcilerService

router = APIRouter(prefix="/api/v1", tags=["reconcile"])


class Queued(BaseModel):
    status: Literal["queued"] = "queued"


class ResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["apply", "keep"]


def _service(request: Request) -> ReconcilerService:
    service: ReconcilerService = request.app.state.reconciler
    return service


@router.post("/nodes/{node_id}/rescan", response_model=Queued, status_code=202)
def rescan_now(
    request: Request, ctx: NodeContext = Depends(list_access), db: Session = Depends(get_db)
) -> Queued:
    """Rescans this folder's subtree soon. At most once a minute per folder."""
    node = ctx.node
    if node.kind != "dir":
        raise http_error(422, "NOT_A_DIRECTORY")
    if not _service(request).request_rescan(node.root_id, node.id):
        raise http_error(429, "RESCAN_TOO_SOON")
    return Queued()


@router.post("/nodes/{node_id}/dismiss", response_model=Ok)
def dismiss_missing(
    request: Request, ctx: NodeContext = Depends(write_access), db: Session = Depends(get_db)
) -> Ok:
    """Stops showing a node that went missing outside the app. The row stays for audit; the
    grants on it and beneath it are removed. Nothing on disk is touched."""
    node = ctx.node
    serialize_tree_changes(db)  # the scanner may be reappearing this node right now
    db.refresh(node)
    if node.status != "missing":
        raise http_error(409, "NOT_MISSING")
    subtree = Node.lineage.startswith(node.lineage, autoescape=True)
    ids = list(db.scalars(select(Node.id).where(subtree, Node.status == "missing")))
    held = db.execute(
        select(NodeGrant.grantee_user_id, NodeGrant.level, NodeGrant.node_id).where(
            NodeGrant.node_id.in_(ids)
        )
    ).all()
    if held and not engine.can_administer_grants(db, Actor(ctx.principal.user.id), node):
        # Dismissing removes grants: only whoever may manage them (the owner, or an administrator
        # in the shared tree) can do that. Editors can dismiss nodes that carry no grants.
        raise http_error(403, "FORBIDDEN")
    db.execute(delete(NodeGrant).where(NodeGrant.node_id.in_(ids)))
    db.execute(
        update(Node)
        .where(Node.id.in_(ids), Node.status == "missing")
        .values(status="dismissed", updated_at=clock.utcnow())
        .execution_options(synchronize_session=False)
    )
    events.record(
        db, node.id, "dismissed", source="ui", actor_user_id=ctx.principal.user.id, name=node.name
    )
    audit(
        db,
        "file.dismissed",
        actor=ctx.principal.user.id,
        ip=client_ip(request),
        node_id=str(node.id),
        affected=len(ids),
        grants_removed=[{"user": str(u), "level": lvl, "node_id": str(n)} for u, lvl, n in held],
    )
    db.commit()
    return Ok()


class ScanStatus(BaseModel):
    scan_state: str | None
    # Tally of the plan that tripped the guard: counts only, never names or paths.
    counts: dict[str, int] | None = None
    reason: str | None = None


@router.get("/nodes/{node_id}/scan", response_model=ScanStatus)
def scan_status(
    ctx: NodeContext = Depends(grant_admin_access), db: Session = Depends(get_db)
) -> ScanStatus:
    """What a suspended root is waiting on, so apply is never a blind decision."""
    node = ctx.node
    root = db.get(StorageRoot, node.root_id)
    if node.parent_id is not None or root is None:
        raise http_error(422, "NOT_A_ROOT")
    if root.scan_state != "suspended":
        return ScanStatus(scan_state=root.scan_state)
    run = db.scalars(
        select(ScanRun)
        .where(ScanRun.root_id == root.id, ScanRun.outcome == "suspended")
        .order_by(ScanRun.started_at.desc())
    ).first()
    counts = {k: v for k, v in (run.counts or {}).items() if isinstance(v, int)} if run else None
    return ScanStatus(scan_state="suspended", counts=counts, reason=run.error_code if run else None)


@router.post("/nodes/{node_id}/scan/resolve", response_model=Ok)
def resolve_suspended_scan(
    body: ResolveBody,
    request: Request,
    ctx: NodeContext = Depends(grant_admin_access),
    db: Session = Depends(get_db),
) -> Ok:
    """A root whose scan was suspended by the mass-change guard waits for the administrator
    (shared tree) or the owner (personal folder): apply the changes, or keep it suspended."""
    node = ctx.node
    root = db.get(StorageRoot, node.root_id)
    if node.parent_id is not None or root is None:
        raise http_error(422, "NOT_A_ROOT")
    if root.scan_state != "suspended":
        raise http_error(409, "NOT_SUSPENDED")
    audit(
        db,
        "storage.scan_resolved",
        actor=ctx.principal.user.id,
        ip=client_ip(request),
        node_id=str(node.id),
        choice=body.action,
    )
    db.commit()
    if body.action == "apply":
        _service(request).request_apply(root.id)
    return Ok()
