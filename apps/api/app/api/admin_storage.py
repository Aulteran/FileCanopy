"""Instance-level storage administration. Reports root status and the shared-tree default only:
no names, counts or sizes of anyone's personal content, and no content access."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import Principal, client_ip, get_db, require_admin
from app.auth.service import audit
from app.db.models import AppSetting, StorageRoot
from app.domain import nodes

router = APIRouter(prefix="/api/v1/admin/storage", tags=["admin"])


class PersonalRootStatus(BaseModel):
    user_id: uuid.UUID
    status: str  # a status code only; `scan_suspended` is reported here, never names or counts


class StorageOverview(BaseModel):
    shared_root_node_id: uuid.UUID | None
    shared_root_status: str | None
    network_fs: bool  # any root on a network filesystem (shared tree decides the warning)
    fs_type: str | None
    network_acknowledged: bool
    shared_default_access: str
    personal_roots: list[PersonalRootStatus]


class DefaultAccessBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Literal["none", "viewer", "editor"]


NETWORK_ACK_KEY = "network_storage_acknowledged"


def _status(root: StorageRoot) -> str:
    return "scan_suspended" if root.status == "active" and root.scan_state else root.status


@router.get("", response_model=StorageOverview)
def storage_overview(
    _: Principal = Depends(require_admin), db: Session = Depends(get_db)
) -> StorageOverview:
    roots = list(db.scalars(select(StorageRoot).order_by(StorageRoot.created_at)))
    shared = next((r for r in roots if r.kind == "shared"), None)
    return StorageOverview(
        shared_root_node_id=nodes.root_node(db, shared.id).id if shared else None,
        shared_root_status=_status(shared) if shared else None,
        network_fs=any(r.network_fs for r in roots),
        fs_type=shared.fs_type if shared else None,
        network_acknowledged=db.get(AppSetting, NETWORK_ACK_KEY) is not None,
        shared_default_access=nodes.shared_default_access(db),
        personal_roots=[
            PersonalRootStatus(user_id=r.owner_user_id, status=_status(r))
            for r in roots
            if r.kind == "personal" and r.owner_user_id is not None
        ],
    )


@router.put("/shared-default-access", response_model=DefaultAccessBody)
def set_default_access(
    body: DefaultAccessBody,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> DefaultAccessBody:
    """Applies to accounts created afterwards; existing grants are edited individually."""
    nodes.set_shared_default_access(db, body.value)
    audit(
        db,
        "storage.default_access_set",
        actor=admin.user.id,
        ip=client_ip(request),
        value=body.value,
    )
    db.commit()
    return body


@router.post("/network-ack", status_code=204)
def acknowledge_network_storage(
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    """One-time, audited acknowledgment that the library is on network storage: outside changes
    can take up to the rescan interval to appear (ADR-011 §8)."""
    if db.get(AppSetting, NETWORK_ACK_KEY) is None:
        db.add(AppSetting(key=NETWORK_ACK_KEY, value=True))
        audit(db, "storage.network_acknowledged", actor=admin.user.id, ip=client_ip(request))
        db.commit()
