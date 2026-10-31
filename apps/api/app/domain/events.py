"""Append-only `file_event` writes (ADR-011 §9). Callers own the transaction.

Details carry node ids, safe names and an `uncertain` flag only: never host paths or content.
External changes never name a user."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.auth import clock
from app.db.models import FileEvent

EXTERNAL = "external_filesystem"


def record(
    db: Session,
    node_id: uuid.UUID,
    action: str,
    *,
    source: str,
    actor_user_id: uuid.UUID | None = None,
    actor_kind: str = "user",
    **details: Any,
) -> None:
    if actor_kind == "user" and actor_user_id is None:
        raise ValueError("a user event needs an actor")
    if actor_kind != "user" and actor_user_id is not None:
        raise ValueError("only user events name a user")
    db.add(
        FileEvent(
            node_id=node_id,
            at=clock.utcnow(),
            actor_kind=actor_kind,
            actor_user_id=actor_user_id,
            action=action,
            source=source,
            details=details,
        )
    )
