"""Append-only `file_event` writes (ADR-011 §9, ADR-017). Callers own the transaction.

Details carry node ids, safe names, sizes and an `uncertain` flag only: never host paths, content
or email addresses. External changes never name a user. An `ai_agent` event names the user it acted
for (`on_behalf_of`) and the proposal that was approved, never an actor user of its own.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.auth import clock
from app.db.models import FileEvent

EXTERNAL = "external_filesystem"
SYSTEM = "system"
AI_AGENT = "ai_agent"


def record(
    db: Session,
    node_id: uuid.UUID,
    action: str,
    *,
    source: str,
    actor_user_id: uuid.UUID | None = None,
    actor_kind: str = "user",
    on_behalf_of: uuid.UUID | None = None,
    correlation_id: uuid.UUID | None = None,
    **details: Any,
) -> None:
    if actor_kind == "user" and actor_user_id is None:
        raise ValueError("a user event needs an actor")
    if actor_kind != "user" and actor_user_id is not None:
        raise ValueError("only user events name a user")
    if actor_kind == AI_AGENT:
        if on_behalf_of is None or not details.get("proposal_id"):
            raise ValueError("an AI event names the user it acted for and the proposal")
    elif on_behalf_of is not None:
        raise ValueError("only AI events act on behalf of a user")
    db.add(
        FileEvent(
            node_id=node_id,
            at=clock.utcnow(),
            actor_kind=actor_kind,
            actor_user_id=actor_user_id,
            action=action,
            source=source,
            details=details,
            correlation_id=correlation_id,
            on_behalf_of_user_id=on_behalf_of,
        )
    )
