"""Search queries (M11). Candidates come from SQL narrowed to the person's entry points, but
nothing is returned until the policy engine has decided the node for this person *now*: names need
`traverse`, text needs `read` (so a locked Confidential vault matches nothing), and text of a
Private file also needs the local-processing consent. Snippets are cut after that decision."""

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, and_, bindparam, func, literal_column, or_, select, text
from sqlalchemy.orm import Session

from app.db.models import HIDDEN_NODE_STATUSES, Node, SearchDoc
from app.policy import engine, privacy
from app.policy.model import Actor, Privilege
from app.preview.sandbox import PreviewError
from app.preview.service import require_processing_allowed

MAX_TERMS = 8
MAX_TERM_CHARS = 64
SCAN_PAGE = 100
SCAN_CAP = 1000  # candidates examined per request, however many are hidden from the person
SNIPPET_RADIUS = 90


@dataclass
class Hit:
    node_id: uuid.UUID
    name: str
    kind: str
    parent_id: uuid.UUID | None
    match: str  # name | content
    snippet: str | None


def terms_of(q: str) -> list[str]:
    words = [w for w in re.split(r"\s+", q.strip().casefold()) if w]
    return [w[:MAX_TERM_CHARS] for w in words[:MAX_TERMS]]


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def snippet_of(body: str, terms: list[str]) -> str:
    low = body.casefold()
    pos = min((p for p in (low.find(t) for t in terms) if p >= 0), default=0)
    start = max(0, pos - SNIPPET_RADIUS)
    chunk = re.sub(r"\s+", " ", body[start : pos + SNIPPET_RADIUS * 2]).strip()
    return (
        ("… " if start > 0 else "") + chunk + (" …" if pos + SNIPPET_RADIUS * 2 < len(body) else "")
    )


def _scope(db: Session, actor: Actor, folder: Node | None) -> list[ColumnElement[bool]]:
    """SQL narrowing only (never the decision): the subtrees of the person's entry points."""
    points = engine.entry_points(db, actor)
    prefixes = [n.lineage for n in points]
    if folder is not None:
        prefixes = [p for p in [folder.lineage] if _inside(folder.lineage, prefixes)]
    if not prefixes:
        return [literal_column("1") == 0]
    # `lineage` is "/id/id/" (every ancestor and the node itself, each followed by a slash), so a
    # prefix match selects the entry point and everything below it. Ids contain no wildcards.
    return [or_(*[Node.lineage.like(p + "%") for p in prefixes])]


def _inside(lineage: str, prefixes: list[str]) -> bool:
    return any(lineage.startswith(p) for p in prefixes)


def search(
    db: Session,
    actor: Actor,
    q: str,
    *,
    folder_id: uuid.UUID | None = None,
    limit: int = 25,
) -> list[Hit]:
    terms = terms_of(q)
    if not terms:
        return []
    folder: Node | None = None
    if folder_id is not None:
        folder = db.get(Node, folder_id)
        if folder is None or Privilege.TRAVERSE not in engine.effective(db, actor, folder):
            return []  # same answer as an empty folder: nothing about the node is revealed
    scope = _scope(db, actor, folder)
    visible = Node.status.not_in(HIDDEN_NODE_STATUSES)
    hits: dict[uuid.UUID, Hit] = {}

    # ---- names
    name_filter = and_(*[Node.name_key.like(_like(t), escape="\\") for t in terms])
    for offset in range(0, SCAN_CAP, SCAN_PAGE):
        rows = list(
            db.scalars(
                select(Node)
                .where(visible, name_filter, Node.parent_id.is_not(None), *scope)
                .order_by(Node.kind, Node.name_key, Node.id)
                .offset(offset)
                .limit(SCAN_PAGE)
            )
        )
        if not rows:
            break
        for node in rows:
            if node.kind == "other":
                continue
            if Privilege.TRAVERSE in engine.effective(db, actor, node):
                hits[node.id] = Hit(node.id, node.name, node.kind, node.parent_id, "name", None)
                if len(hits) >= limit:
                    return _finish(db, actor, list(hits.values()))
    # ---- text
    match: Any
    rank: Any
    if db.get_bind().dialect.name == "postgresql":
        match = text("search_doc.tsv @@ websearch_to_tsquery('simple', :qm)").bindparams(
            bindparam("qm", " ".join(terms))
        )
        rank = text("ts_rank(search_doc.tsv, websearch_to_tsquery('simple', :qr)) DESC").bindparams(
            bindparam("qr", " ".join(terms))
        )
    else:
        match = and_(*[func.lower(SearchDoc.body).like(_like(t), escape="\\") for t in terms])
        rank = SearchDoc.indexed_at.desc()
    for offset in range(0, SCAN_CAP, SCAN_PAGE):
        rows2 = list(
            db.execute(
                select(Node, SearchDoc.body)
                .join(SearchDoc, SearchDoc.node_id == Node.id)
                .where(
                    visible,
                    match,
                    SearchDoc.revision == Node.revision,
                    SearchDoc.mtime_ns == func.coalesce(Node.mtime_ns, 0),
                    SearchDoc.size == func.coalesce(Node.size, 0),
                    *scope,
                )
                .order_by(rank, Node.id)
                .offset(offset)
                .limit(SCAN_PAGE)
            ).all()
        )
        if not rows2:
            break
        for node, body in rows2:
            if node.id in hits:
                hits[node.id].match = "name+content"
                hits[node.id].snippet = snippet_of(body, terms)
                continue
            if Privilege.READ not in engine.effective(db, actor, node):
                continue
            if privacy.effective_label(db, node) != "normal":
                try:
                    require_processing_allowed(db, node, actor.user_id)
                except PreviewError:
                    continue
            hits[node.id] = Hit(
                node.id, node.name, node.kind, node.parent_id, "content", snippet_of(body, terms)
            )
            if len(hits) >= limit:
                break
        if len(hits) >= limit:
            break
    return _finish(db, actor, list(hits.values())[:limit])


def _finish(db: Session, actor: Actor, hits: list[Hit]) -> list[Hit]:
    """A parent folder id is only given when the person may see that folder too."""
    for h in hits:
        if h.parent_id is not None:
            parent = db.get(Node, h.parent_id)
            if parent is None or Privilege.TRAVERSE not in engine.effective(db, actor, parent):
                h.parent_id = None
        if h.match == "name+content":
            h.match = "content"
    return hits
