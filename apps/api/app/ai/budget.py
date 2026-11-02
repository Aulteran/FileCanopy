"""Monthly AI spending caps (M15, design D5, D8).

Money is whole micro-dollars (1_000_000 = $1). A request first *reserves* its worst-case cost with
one conditional UPDATE, so parallel requests cannot together pass the cap; the reservation is
settled to the real cost afterwards or released on failure. Reservations a crashed process left
behind are released after `STALE` and the ledger row marked `expired`. Provider-side billing can
lag, so this is a guard rail and not a financial guarantee.
"""

import math
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import clock
from app.db.models import AiBudget, AiUsage

STALE = timedelta(minutes=15)
MAX_CAP_MICRO = 1_000_000 * 100_000  # $100,000: a typo guard, not a policy


def month_key(now: datetime | None = None) -> str:
    n = now or clock.utcnow()
    return f"{n.year:04d}-{n.month:02d}"


def cost_micro(tokens_in: int, tokens_out: int, price_in: int, price_out: int) -> int:
    """Prices are micro-dollars per million tokens; the result rounds up."""
    return math.ceil((tokens_in * price_in + tokens_out * price_out) / 1_000_000)


def estimate_tokens(utf8_bytes: int) -> int:
    """An upper bound for the reservation: a token is never shorter than one byte, so the UTF-8
    length of everything sent (CJK, emoji and code included) can only over-count (M15 review L2)."""
    return utf8_bytes + 32


def _row(db: Session, scope_key: str, month: str) -> AiBudget | None:
    return db.scalar(
        select(AiBudget).where(AiBudget.scope_key == scope_key, AiBudget.month == month)
    )


def ensure(db: Session, scope_key: str, default_cap: int) -> AiBudget:
    """This month's row, created from last month's cap (or the default) on first use."""
    month = month_key()
    row = _row(db, scope_key, month)
    if row is not None:
        return row
    previous = db.scalar(
        select(AiBudget).where(AiBudget.scope_key == scope_key).order_by(AiBudget.month.desc())
    )
    cap = previous.cap_micro if previous is not None else default_cap
    try:
        with db.begin_nested():
            db.add(AiBudget(scope_key=scope_key, month=month, cap_micro=cap))
    except IntegrityError:  # another request created it first
        pass
    db.commit()
    row = _row(db, scope_key, month)
    if row is None:
        raise RuntimeError("budget row missing")
    return row


def set_cap(db: Session, scope_key: str, default_cap: int, cap_micro: int) -> AiBudget:
    if not 0 <= cap_micro <= MAX_CAP_MICRO:
        raise ValueError("cap out of range")
    row = ensure(db, scope_key, default_cap)
    row.cap_micro = cap_micro
    db.commit()
    return row


def claim(db: Session, usage_id: object, to: str = "settling") -> bool:
    """Take ownership of a reservation before settling or releasing it. False when someone else
    already did (the stale sweep gave it back), so it is never returned twice (review L1)."""
    got: CursorResult[Any] = db.execute(  # type: ignore[assignment]
        update(AiUsage)
        .where(AiUsage.id == usage_id, AiUsage.status == "reserved")
        .values(status=to)
    )
    db.commit()
    return got.rowcount == 1


def sweep_stale(db: Session, scope_key: str) -> int:
    """Give back reservations older than `STALE`. Safe to run often."""
    cutoff = clock.utcnow() - STALE
    stale = db.scalars(
        select(AiUsage).where(
            AiUsage.scope_key == scope_key, AiUsage.status == "reserved", AiUsage.at < cutoff
        )
    ).all()
    for usage in stale:
        claimed: CursorResult[Any] = db.execute(  # type: ignore[assignment]
            update(AiUsage)
            .where(AiUsage.id == usage.id, AiUsage.status == "reserved")
            .values(status="expired", reason="reservation_expired")
        )
        if claimed.rowcount == 1:
            _give_back(db, usage.scope_key, usage.month, usage.reserved_micro)
    db.commit()
    return len(stale)


def _give_back(db: Session, scope_key: str, month: str, amount: int) -> None:
    db.execute(
        update(AiBudget)
        .where(AiBudget.scope_key == scope_key, AiBudget.month == month)
        .values(reserved_micro=AiBudget.reserved_micro - amount)
    )


def reserve(db: Session, scope_key: str, estimate: int, default_cap: int) -> str | None:
    """Atomically hold `estimate`. Returns the budget month, or None when it would pass the cap."""
    row = ensure(db, scope_key, default_cap)
    done: CursorResult[Any] = db.execute(  # type: ignore[assignment]
        update(AiBudget)
        .where(
            AiBudget.id == row.id,
            AiBudget.spent_micro + AiBudget.reserved_micro + estimate <= AiBudget.cap_micro,
        )
        .values(reserved_micro=AiBudget.reserved_micro + estimate)
    )
    db.commit()
    return row.month if done.rowcount == 1 else None


def settle(db: Session, scope_key: str, month: str, reserved: int, actual: int) -> None:
    db.execute(
        update(AiBudget)
        .where(AiBudget.scope_key == scope_key, AiBudget.month == month)
        .values(
            reserved_micro=AiBudget.reserved_micro - reserved,
            spent_micro=AiBudget.spent_micro + actual,
        )
    )
    db.commit()


def release(db: Session, scope_key: str, month: str, reserved: int) -> None:
    _give_back(db, scope_key, month, reserved)
    db.commit()


def usage_summary(db: Session, scope_key: str) -> dict[str, int | str]:
    month = month_key()
    row = _row(db, scope_key, month)
    return {
        "month": month,
        "cap_micro": row.cap_micro if row else -1,
        "spent_micro": row.spent_micro if row else 0,
        "reserved_micro": row.reserved_micro if row else 0,
    }
