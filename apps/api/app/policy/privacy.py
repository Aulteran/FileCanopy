"""Privacy labels, Confidential unlocks and the AI routing rule (M09, 03_SECURITY_PRIVACY.md).

Labels sit on folders. The effective label of a node is the most restrictive label on the node or
any ancestor. Confidential content needs an unlock: a row bound to one login session that the
server created after verifying the account password and that expires after five minutes without
interactive use. No unlock row exists for jobs (they carry no session), so they are always locked.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import clock
from app.db.models import ConfidentialUnlock, Node

LABELS = ("normal", "private", "confidential")
RANK = {label: i for i, label in enumerate(LABELS)}
IDLE_LOCK = timedelta(minutes=5)
RENEW_GRANULARITY = timedelta(seconds=5)
# Sent by the web app on requests the person made; never by timers or pollers.
INTERACTIVE_HEADER = "x-fc-interactive"


def stricter(a: str, b: str) -> str:
    return a if RANK.get(a, 0) >= RANK.get(b, 0) else b


def lineage_ids(node: Node) -> list[uuid.UUID]:
    return [uuid.UUID(part) for part in node.lineage.strip("/").split("/")]


def label_chain(db: Session, node: Node) -> list[tuple[uuid.UUID, str]]:
    """(id, own label) from the root down to the node."""
    ids = lineage_ids(node)
    rows = dict(db.execute(select(Node.id, Node.privacy_label).where(Node.id.in_(ids))).all())
    return [(i, rows.get(i, "normal")) for i in ids]


def effective_label(db: Session, node: Node) -> str:
    label = "normal"
    for _, own in label_chain(db, node):
        label = stricter(label, own)
    return label


def confidential_scope(db: Session, node: Node) -> uuid.UUID | None:
    """The outermost Confidential folder at or above the node: the unit that is unlocked."""
    for node_id, own in label_chain(db, node):
        if own == "confidential":
            return node_id
    return None


def live_unlock(
    db: Session, user_id: uuid.UUID, session_id: uuid.UUID | None, scope: uuid.UUID
) -> ConfidentialUnlock | None:
    if session_id is None:  # jobs and anything not tied to a login session are never unlocked
        return None
    row = db.scalar(
        select(ConfidentialUnlock).where(
            ConfidentialUnlock.session_id == session_id, ConfidentialUnlock.scope_node_id == scope
        )
    )
    if row is None or row.user_id != user_id:
        return None
    if row.last_activity_at + IDLE_LOCK <= clock.utcnow():
        return None
    return row


def renew(db: Session, row: ConfidentialUnlock) -> None:
    """Only interactive requests inside the unlocked scope call this."""
    now = clock.utcnow()
    if now - row.last_activity_at >= RENEW_GRANULARITY:
        row.last_activity_at = now
        db.commit()


@dataclass(frozen=True)
class AiRoute:
    allowed: bool
    reason: str


def ai_route_allowed(
    label: str, provider: str, *, local_opt_in: bool = False, unlocked_by_person: bool = False
) -> AiRoute:
    """The one decision every AI path (classification, OCR text, embeddings, summaries, chat, MCP)
    must ask before it sends or processes content. `provider` is "cloud" or "local". There is no
    fallback from local to cloud for Private or Confidential content."""
    if provider not in ("cloud", "local"):
        return AiRoute(False, "unknown_provider")
    if label not in RANK:
        return AiRoute(False, "unknown_label")  # fail closed
    if label == "normal":
        return AiRoute(True, "normal")
    if provider == "cloud":
        return AiRoute(False, "protected_never_cloud")
    if not local_opt_in:
        return AiRoute(False, "local_opt_in_required")
    if label == "confidential" and not unlocked_by_person:
        return AiRoute(False, "unlock_required")
    return AiRoute(True, "local_opt_in")
