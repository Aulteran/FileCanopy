"""Which optional steps of the first-run "Prepare your server" screen the administrator skipped.

Stored as one instance-level setting so a skip survives a reload, like the check marks that come
from saved state. It only drives the screen: it never turns anything on or off.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.auth.deps import Principal, client_ip, get_db, require_admin
from app.auth.service import audit
from app.db.models import AppSetting

router = APIRouter(prefix="/api/v1/admin/setup-skips", tags=["admin"])

KEY = "setup.skipped_steps"
STEPS = ("ai", "semantic", "smtp", "users", "mail")


class SkipBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: Literal["ai", "semantic", "smtp", "users", "mail"]
    skipped: bool


def _load(db: Session) -> list[str]:
    row = db.get(AppSetting, KEY)
    raw = row.value if row and isinstance(row.value, list) else []
    return [s for s in STEPS if s in raw]


@router.get("")
def get_skips(
    _: Principal = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, list[str]]:
    return {"skipped": _load(db)}


@router.put("")
def set_skip(
    body: SkipBody,
    request: Request,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, list[str]]:
    now = set(_load(db))
    if body.skipped:
        now.add(body.step)
    else:
        now.discard(body.step)
    value = [s for s in STEPS if s in now]
    row = db.get(AppSetting, KEY)
    if row is None:
        db.add(AppSetting(key=KEY, value=value))
    else:
        row.value = value
    audit(
        db,
        "setup.step_skipped" if body.skipped else "setup.step_unskipped",
        actor=principal.user.id,
        ip=client_ip(request),
        step=body.step,
    )
    db.commit()
    return {"skipped": value}
