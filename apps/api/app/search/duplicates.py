"""Duplicate and near-duplicate suggestions (M17). Local and deterministic: no AI, no network.

This module only reports. It has no way to delete, move or change anything (the tests check the
imports), so a duplicate is never removed unless the person acts on it themselves, through the
ordinary trash, which can be undone.

* Exact: the same SHA-256 and size, from the stored `content_hash`. A file appears only once the
  background hashing has reached it.
* Near: Normal files whose extracted text is almost the same (bottom-k sketches over five-word
  shingles, candidate pairs from shared minimum hashes, similarity estimated from the sketches).
  Private and Confidential files are never compared by text.

Every member is decided per person at request time: a file the person cannot read now is left out,
and a group with fewer than two readable members is not reported at all, so neither a name nor a
count of other people's files can leak.
"""

import hashlib
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import ColumnElement, and_, func, literal_column, or_, select
from sqlalchemy.orm import Session

from app.db.models import HIDDEN_NODE_STATUSES, Node, SearchDoc
from app.policy import engine, privacy
from app.policy.model import Actor, Privilege

EXACT_SCAN_CAP = 500  # hash groups examined per request
NEAR_SCAN_CAP = 1000  # documents compared per request
MAX_GROUPS = 50
MAX_MEMBERS = 20
MIN_TEXT_CHARS = 200
SHINGLE_WORDS = 5
SKETCH = 64
BUCKET_HASHES = 4  # candidate pairs must share one of a document's smallest hashes
MIN_SIMILARITY = 850  # per mille
MAX_BUCKET = 40  # a bucket larger than this is common boilerplate, not evidence

_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass
class Member:
    node_id: uuid.UUID
    name: str
    size: int
    revision: int
    parent_id: uuid.UUID | None
    parent_name: str | None


@dataclass
class Group:
    kind: str  # exact | near
    similarity: int  # per mille; 1000 for exact
    members: list[Member] = field(default_factory=list)


def _scope(db: Session, actor: Actor, folder: Node | None) -> list[ColumnElement[bool]]:
    """SQL narrowing only (never the decision): the subtrees of the person's entry points."""
    prefixes = [n.lineage for n in engine.entry_points(db, actor)]
    if folder is not None:
        prefixes = [folder.lineage] if any(folder.lineage.startswith(p) for p in prefixes) else []
    if not prefixes:
        return [literal_column("1") == 0]
    return [or_(*[Node.lineage.like(p + "%") for p in prefixes])]


def _can_read(db: Session, actor: Actor, node: Node) -> bool:
    return Privilege.READ in engine.effective(db, actor, node)


def _member(db: Session, actor: Actor, node: Node) -> Member:
    parent = db.get(Node, node.parent_id) if node.parent_id else None
    shown = parent is not None and Privilege.TRAVERSE in engine.effective(db, actor, parent)
    return Member(
        node.id,
        node.name,
        node.size or 0,
        node.revision,
        parent.id if shown and parent is not None else None,
        parent.name if shown and parent is not None else None,
    )


def exact_groups(db: Session, actor: Actor, folder: Node | None) -> list[Group]:
    scope = _scope(db, actor, folder)
    visible = Node.status.not_in(HIDDEN_NODE_STATUSES)
    rows = db.execute(
        select(Node.content_hash, Node.size)
        .where(
            visible,
            Node.kind == "file",
            Node.content_hash.is_not(None),
            Node.size > 0,
            *scope,
        )
        .group_by(Node.content_hash, Node.size)
        .having(func.count() > 1)
        .order_by(Node.size.desc(), Node.content_hash)
        .limit(EXACT_SCAN_CAP)
    ).all()
    out: list[Group] = []
    for digest, size in rows:
        nodes = db.scalars(
            select(Node)
            .where(
                visible,
                Node.kind == "file",
                Node.content_hash == digest,
                Node.size == size,
                *scope,
            )
            .order_by(Node.name_key, Node.id)
            .limit(MAX_MEMBERS * 5)
        )
        members = [_member(db, actor, n) for n in nodes if _can_read(db, actor, n)]
        if len(members) >= 2:
            out.append(Group("exact", 1000, members[:MAX_MEMBERS]))
            if len(out) >= MAX_GROUPS:
                break
    return out


# ------------------------------------------------------------------ near duplicates


def sketch_of(text: str) -> list[int]:
    """The SKETCH smallest 64-bit hashes of the text's five-word shingles."""
    words = [w.casefold() for w in _WORD.findall(text)]
    if len(words) < SHINGLE_WORDS:
        return []
    hashes: set[int] = set()
    for i in range(len(words) - SHINGLE_WORDS + 1):
        shingle = " ".join(words[i : i + SHINGLE_WORDS]).encode()
        hashes.add(int.from_bytes(hashlib.blake2b(shingle, digest_size=8).digest(), "big"))
    return sorted(hashes)[:SKETCH]


def similarity(a: list[int], b: list[int]) -> int:
    """Estimated Jaccard similarity, per mille, from two bottom-k sketches."""
    if not a or not b:
        return 0
    k = min(SKETCH, len(a) + len(b))
    merged = sorted(set(a) | set(b))[:k]
    both = set(a) & set(b)
    shared = sum(1 for h in merged if h in both)
    return round(1000 * shared / len(merged)) if merged else 0


def near_groups(db: Session, actor: Actor, folder: Node | None) -> list[Group]:
    scope = _scope(db, actor, folder)
    rows = db.execute(
        select(Node, SearchDoc.body)
        .join(SearchDoc, SearchDoc.node_id == Node.id)
        .where(
            Node.status.not_in(HIDDEN_NODE_STATUSES),
            Node.kind == "file",
            SearchDoc.revision == Node.revision,
            SearchDoc.mtime_ns == func.coalesce(Node.mtime_ns, 0),
            SearchDoc.size == func.coalesce(Node.size, 0),
            func.length(SearchDoc.body) >= MIN_TEXT_CHARS,
            and_(*scope),
        )
        .order_by(Node.id)
        .limit(NEAR_SCAN_CAP * 2)
    ).all()
    docs: list[tuple[Node, list[int]]] = []
    for node, body in rows:
        if len(docs) >= NEAR_SCAN_CAP:
            break
        # Text of a protected file is never compared; reading it is the person's decision only.
        if not _can_read(db, actor, node) or privacy.effective_label(db, node) != "normal":
            continue
        sk = sketch_of(body)
        if len(sk) >= 8:
            docs.append((node, sk))
    buckets: dict[int, list[int]] = {}
    for idx, (_, sk) in enumerate(docs):
        for h in sk[:BUCKET_HASHES]:
            buckets.setdefault(h, []).append(idx)
    parent = list(range(len(docs)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    best: dict[tuple[int, int], int] = {}
    for members in buckets.values():
        if len(members) < 2 or len(members) > MAX_BUCKET:
            continue
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                if (a, b) in best:
                    continue
                na, nb = docs[a][0], docs[b][0]
                if na.content_hash and na.content_hash == nb.content_hash and na.size == nb.size:
                    continue  # already reported as exact
                s = similarity(docs[a][1], docs[b][1])
                best[(a, b)] = s
                if s >= MIN_SIMILARITY:
                    parent[find(a)] = find(b)
    groups: dict[int, list[int]] = {}
    for idx in range(len(docs)):
        groups.setdefault(find(idx), []).append(idx)
    out: list[Group] = []
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        pair_scores = [
            s for (a, b), s in best.items() if a in idxs and b in idxs and s >= MIN_SIMILARITY
        ]
        if not pair_scores:
            continue
        nodes = sorted((docs[i][0] for i in idxs), key=lambda n: (n.name_key, str(n.id)))
        out.append(
            Group("near", min(pair_scores), [_member(db, actor, n) for n in nodes[:MAX_MEMBERS]])
        )
    out.sort(key=lambda g: (-g.similarity, g.members[0].name))
    return out[:MAX_GROUPS]


def find_duplicates(
    db: Session, actor: Actor, *, folder_id: uuid.UUID | None = None, near: bool = True
) -> list[Group]:
    folder: Node | None = None
    if folder_id is not None:
        folder = db.get(Node, folder_id)
        if folder is None or Privilege.TRAVERSE not in engine.effective(db, actor, folder):
            return []  # same answer as an empty folder
    exact = exact_groups(db, actor, folder)
    return exact + (near_groups(db, actor, folder) if near else [])
