"""Instance-level storage administration. Reports root status and the shared-tree default only:
no names, counts or sizes of anyone's personal content, and no content access."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import Principal, client_ip, get_db, http_error, require_admin
from app.auth.service import audit
from app.db.models import AppSetting, StorageRoot, TrashItem
from app.domain import nodes, trash
from app.policy.deps import admin_access

router = APIRouter(prefix="/api/v1/admin/storage", tags=["admin"])


class PersonalRootStatus(BaseModel):
    user_id: uuid.UUID
    status: str  # a status code only; `scan_suspended` is reported here, never names or counts
    # Trash totals only (ADR-017 §3): never names, and no access to what is in it.
    trash_items: int = 0
    trash_bytes: int = 0


class StorageOverview(BaseModel):
    shared_root_node_id: uuid.UUID | None
    shared_root_status: str | None
    network_fs: bool  # any root on a network filesystem (shared tree decides the warning)
    fs_type: str | None
    network_acknowledged: bool
    shared_default_access: str
    shared_trash_items: int = 0
    shared_trash_bytes: int = 0
    trash_retention_days: int | None = None  # null: the stored setting is unreadable
    personal_roots: list[PersonalRootStatus]


class RetentionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int


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
    totals = {
        row.root_id: (int(row.n), int(row.size))
        for row in db.execute(
            select(
                TrashItem.root_id,
                func.count().label("n"),
                func.coalesce(func.sum(TrashItem.size_bytes), 0).label("size"),
            )
            .where(TrashItem.status.in_(trash.LIVE_ITEM_STATUSES))
            .group_by(TrashItem.root_id)
        )
    }
    shared_n, shared_size = totals.get(shared.id, (0, 0)) if shared else (0, 0)
    return StorageOverview(
        shared_root_node_id=nodes.root_node(db, shared.id).id if shared else None,
        shared_root_status=_status(shared) if shared else None,
        network_fs=any(r.network_fs for r in roots),
        fs_type=shared.fs_type if shared else None,
        network_acknowledged=db.get(AppSetting, NETWORK_ACK_KEY) is not None,
        shared_default_access=nodes.shared_default_access(db),
        shared_trash_items=shared_n,
        shared_trash_bytes=shared_size,
        trash_retention_days=trash.get_retention(db),
        personal_roots=[
            PersonalRootStatus(
                user_id=r.owner_user_id,
                status=_status(r),
                trash_items=totals.get(r.id, (0, 0))[0],
                trash_bytes=totals.get(r.id, (0, 0))[1],
            )
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


@router.put("/trash-retention", response_model=RetentionBody)
def set_trash_retention(
    body: RetentionBody,
    request: Request,
    admin: Principal = Depends(admin_access),
    db: Session = Depends(get_db),
) -> RetentionBody:
    """Days an item stays in the trash: 7 to 3650, or 0 to never delete automatically. Lowering it
    never shortens items already in the trash (ADR-017 §4)."""
    if not trash.valid_retention(body.days):
        raise http_error(422, "INVALID_RETENTION")
    trash.set_retention(db, body.days, by=admin.user.id, ip=client_ip(request))
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
