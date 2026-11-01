"""External-change reconciliation (ADR-011 §6, §7, §9).

A pass over one root has three stages:

1. **Plan** (read only): walk the tree with the no-symlink resolver, compare each directory with
   its database children and collect changes. Appeared and vanished entries are then matched by
   inode and, for files, by content hash, so a move on the host keeps one identity.
2. **Guard**: a pass that would mark most of a root missing or modified (an unmounted or replaced
   bind mount) applies nothing and suspends the root until a person decides.
3. **Apply**: changes are committed in small batches under the tree-change lock. Every change is
   re-validated against the database first, so an operation the app is running (a `planned`
   journal row, a `busy` claim, a revision that moved on) is never overwritten by stale scan data.

The scanner acts as the `external_filesystem` actor: it writes metadata and events only, never
file content, and never names a user. Files that disappear become `missing`, never "deleted".
"""

import hashlib
import logging
import os
import stat
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, literal, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.auth import clock
from app.config import Settings
from app.db.models import HIDDEN_NODE_STATUSES, FileOperation, Node, ScanRun, StorageRoot
from app.domain import events, setup
from app.domain.fileops import serialize_tree_changes
from app.domain.nodes import path_components
from app.storage import files, roots
from app.storage.identity import fingerprint, s64
from app.storage.names import InvalidName, name_key, validate_name
from app.storage.resolver import StorageError, StorageMissing, open_dir

log = logging.getLogger("filecanopy.reconcile")

BATCH = 500
MASS_FRACTION = 0.5
MASS_MINIMUM = 100
PENDING_STATUSES = ("active", "reconcile_needed")
# Content hashes of tiny files (empty, a few bytes) are common to unrelated files, so they never
# link a vanished node to a new file.
MIN_HASH_MATCH = 1024
# "Apply" on a suspended root only proceeds if the fresh plan is not much larger than what the
# person reviewed; otherwise the root is suspended again with the new numbers.
APPLY_DRIFT = 0.10
APPLY_DRIFT_SLACK = 10


# ------------------------------------------------------------------ model of a plan


@dataclass(frozen=True)
class Snap:
    """A node as the planner saw it. Applying re-reads the node and compares `revision`."""

    id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    kind: str
    status: str
    revision: int
    lineage: str
    size: int | None
    fs_dev: int | None
    fs_ino: int | None
    mtime_ns: int | None
    content_hash: str | None
    raw_name_digest: str | None
    unsupported_reason: str | None

    @property
    def key(self) -> tuple[str, str]:
        return ("d", self.raw_name_digest) if self.raw_name_digest else ("n", self.name)


def _snap(n: Node) -> Snap:
    return Snap(
        n.id,
        n.parent_id,
        n.name,
        n.kind,
        n.status,
        n.revision,
        n.lineage,
        n.size,
        n.fs_dev,
        n.fs_ino,
        n.mtime_ns,
        n.content_hash,
        n.raw_name_digest,
        n.unsupported_reason,
    )


@dataclass
class DiskEntry:
    raw: str  # name as the kernel reports it (used for syscalls)
    name: str  # displayed/stored name (undecodable bytes replaced)
    digest: str | None  # SHA-256 of the raw bytes, only for undecodable names
    kind: str  # dir | file | other
    reason: str | None
    st: os.stat_result

    @property
    def key(self) -> tuple[str, str]:
        return ("d", self.digest) if self.digest else ("n", self.name)

    @property
    def identity(self) -> tuple[int, int]:
        return s64(self.st.st_dev), s64(self.st.st_ino)


@dataclass
class Appeared:
    idx: int
    parent_id: uuid.UUID | None  # an existing node ...
    parent_idx: int | None  # ... or an appeared directory created earlier in this plan
    entry: DiskEntry
    comps: tuple[str, ...]  # raw path components from the root directory


@dataclass
class Move:
    node: Snap
    target: Appeared
    uncertain: bool
    modified: bool = False


@dataclass
class WorkItem:
    node: Snap | None  # None: a newly appeared directory (all of its entries are new)
    comps: tuple[str, ...]
    pending_idx: int | None = None


@dataclass
class Plan:
    fingerprints: list[tuple[Snap, DiskEntry]] = field(default_factory=list)
    modified: list[tuple[Snap, DiskEntry]] = field(default_factory=list)
    reappeared: list[tuple[Snap, DiskEntry]] = field(default_factory=list)
    moves: list[Move] = field(default_factory=list)
    dir_creates: list[Appeared] = field(default_factory=list)
    creates: list[Appeared] = field(default_factory=list)
    missing: list[Snap] = field(default_factory=list)
    unreadable: int = 0

    def counts(self) -> dict[str, int]:
        return {
            "modified": len(self.modified),
            "moved": len(self.moves),
            "created": len(self.dir_creates) + len(self.creates),
            "missing": len(self.missing),
            "reappeared": len(self.reappeared),
            "unreadable": self.unreadable,
        }


# ------------------------------------------------------------------ disk side


def is_mount_point(st: os.stat_result, root_dev: int) -> bool:
    """A directory on another device than its root is a mounted drive (ADR-011 §3)."""
    return s64(st.st_dev) != s64(root_dev)


def classify(
    raw: str, st: os.stat_result, root_dev: int
) -> tuple[str, str | None, str, str | None]:
    """Returns (kind, unsupported_reason, display_name, raw_digest) for one directory entry."""
    encoded = os.fsencode(raw)
    digest: str | None = None
    name = raw
    try:
        encoded.decode("utf-8")
    except UnicodeDecodeError:
        digest = hashlib.sha256(encoded).hexdigest()
        name = encoded.decode("utf-8", errors="replace")
        return "other", "undecodable_name", name, digest
    try:
        validate_name(raw)
    except InvalidName:
        return "other", "invalid_name", name, None
    mode = st.st_mode
    if stat.S_ISLNK(mode):
        return "other", "symlink", name, None
    if stat.S_ISDIR(mode):
        if is_mount_point(st, root_dev):
            return "other", "mounted_drive", name, None
        return "dir", None, name, None
    if stat.S_ISREG(mode):
        return "file", None, name, None
    return "other", "special", name, None


def list_directory(dir_fd: int, root_dev: int) -> list[DiskEntry]:
    out: list[DiskEntry] = []
    with os.scandir(dir_fd) as it:
        for entry in it:
            if files.reserved_name(entry.name):
                continue  # another operation's staging file
            try:
                st = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            kind, reason, name, digest = classify(entry.name, st, root_dev)
            out.append(DiskEntry(entry.name, name, digest, kind, reason, st))
    return out


# ------------------------------------------------------------------ planning


class Planner:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        root: StorageRoot,
        stop: threading.Event | None = None,
    ) -> None:
        self.db, self.settings, self.root = db, settings, root
        self.stop = stop or threading.Event()
        self.plan = Plan()
        self._next_idx = 0
        self._vanished: dict[uuid.UUID, Snap] = {}
        self._appeared: list[Appeared] = []  # append-only: `run` slices it by position
        self._file_apps: dict[int, Appeared] = {}  # appeared non-directories not yet linked
        self._dev: int | None = None

    # -- helpers
    def _children(self, node_id: uuid.UUID) -> list[Snap]:
        rows = self.db.scalars(
            select(Node).where(Node.parent_id == node_id, Node.status.not_in(HIDDEN_NODE_STATUSES))
        )
        return [_snap(n) for n in rows]

    def _new(self, parent: WorkItem, entry: DiskEntry) -> Appeared:
        idx, self._next_idx = self._next_idx, self._next_idx + 1
        return Appeared(
            idx,
            parent.node.id if parent.node is not None else None,
            parent.pending_idx,
            entry,
            (*parent.comps, entry.raw),
        )

    def _missing_in_root(self, kind: str) -> list[Snap]:
        rows = self.db.scalars(
            select(Node).where(
                Node.root_id == self.root.id, Node.status == "missing", Node.kind == kind
            )
        )
        return [_snap(n) for n in rows]

    # -- stage 1: directory walk
    def run(self, start: Snap, comps: tuple[str, ...]) -> Plan:
        queue: deque[WorkItem] = deque([WorkItem(start, comps)])
        # Directories that vanished, including already-missing ones, can be found again by inode.
        dir_pool: dict[tuple[int, int], list[Snap]] = defaultdict(list)
        pool_loaded = False
        resolved = 0
        while True:
            while queue:
                if self.stop.is_set():
                    raise InterruptedError
                self._scan_directory(queue.popleft(), queue)
            fresh = [a for a in self._appeared[resolved:] if a.entry.kind == "dir"]
            resolved = len(self._appeared)
            if not fresh:
                break
            if not pool_loaded:
                for snap in [*self._missing_in_root("dir"), *self._vanished.values()]:
                    if snap.kind == "dir" and snap.fs_ino is not None and snap.fs_dev is not None:
                        dir_pool[(snap.fs_dev, snap.fs_ino)].append(snap)
                pool_loaded = True
            else:  # directories that vanished since the pool was built
                for snap in self._vanished.values():
                    if snap.kind == "dir" and snap.fs_ino is not None and snap.fs_dev is not None:
                        if snap not in dir_pool[(snap.fs_dev, snap.fs_ino)]:
                            dir_pool[(snap.fs_dev, snap.fs_ino)].append(snap)
            for app in fresh:
                candidates = dir_pool.get(app.entry.identity, [])
                if (
                    len(candidates) == 1
                    and candidates[0].id != start.id
                    and self._dir_plausible(candidates[0], app)
                ):
                    snap = candidates[0]
                    dir_pool.pop(app.entry.identity)
                    self._vanished.pop(snap.id, None)
                    self.plan.moves.append(Move(snap, app, uncertain=False))
                    queue.append(WorkItem(snap, app.comps))
                else:
                    self.plan.dir_creates.append(app)
                    queue.append(WorkItem(None, app.comps, pending_idx=app.idx))
        self._match_files()
        return self.plan

    def _dir_plausible(self, snap: Snap, app: Appeared) -> bool:
        """Inode numbers are reused and a folder carries grants. An inode match links a missing
        or vanished folder to a new one only if it also has the same name or shares a child name,
        so a freed inode number can never hand an old folder's identity (and its grants) to an
        unrelated new folder."""
        if snap.name == app.entry.name:
            return True
        kids = {c.name for c in self._children(snap.id)}
        if not kids:
            return False
        try:
            with open_dir(self.settings.library_path, self.root.relative_dir, app.comps) as fd:
                return bool(kids & set(os.listdir(fd)))
        except (StorageError, OSError):
            return False

    def _scan_directory(self, item: WorkItem, queue: "deque[WorkItem]") -> None:
        # Database first, then disk: an app operation that commits in between shows up as a
        # name the database does not know (deferred at apply), never as a reversed rename.
        children: dict[tuple[str, str], list[Snap]] = defaultdict(list)
        if item.node is not None:
            for child in self._children(item.node.id):
                children[child.key].append(child)
        try:
            with open_dir(self.settings.library_path, self.root.relative_dir, item.comps) as fd:
                root_dev = self._root_dev()
                entries = list_directory(fd, root_dev)
        except (StorageError, OSError):
            self.plan.unreadable += 1  # vanished mid-pass or unreadable: next pass looks again
            return
        matched: set[uuid.UUID] = set()
        for entry in entries:
            options = children.get(entry.key, [])
            # An active node wins over a missing one of the same name unless only the missing one
            # has the right kind.
            options = sorted(options, key=lambda s: (s.kind != entry.kind, s.status == "missing"))
            snap = options[0] if options else None
            if snap is None:
                self._appear(item, entry)
                continue
            matched.add(snap.id)
            if snap.status == "busy":
                continue  # the app owns it right now
            if snap.kind != entry.kind:
                if snap.status != "missing":
                    self._vanished[snap.id] = snap
                self._appear(item, entry)
                continue
            self._compare(snap, entry, item, queue)
        for group in children.values():
            for snap in group:
                if snap.id not in matched and snap.status in PENDING_STATUSES:
                    self._vanished[snap.id] = snap

    def _root_dev(self) -> int:
        if self._dev is None:
            with open_dir(self.settings.library_path, self.root.relative_dir) as fd:
                self._dev = os.fstat(fd).st_dev
        return self._dev

    def _appear(self, parent: WorkItem, entry: DiskEntry) -> None:
        app = self._new(parent, entry)
        self._appeared.append(app)
        if entry.kind != "dir":
            self._file_apps[app.idx] = app

    def _compare(
        self, snap: Snap, entry: DiskEntry, item: WorkItem, queue: "deque[WorkItem]"
    ) -> None:
        dev, ino = entry.identity
        same_identity = snap.fs_dev == dev and snap.fs_ino == ino
        if snap.status == "missing":
            self.plan.reappeared.append((snap, entry))
        elif snap.status == "reconcile_needed":
            if snap.fs_ino is None or same_identity:  # unambiguous: disk matches the database
                self.plan.fingerprints.append((snap, entry))
            # otherwise it stays flagged for an administrator
        elif entry.kind == "file":
            if snap.mtime_ns is None or snap.fs_ino is None:
                self.plan.fingerprints.append((snap, entry))  # first sight: fill silently
            elif snap.size != entry.st.st_size or snap.mtime_ns != entry.st.st_mtime_ns:
                self.plan.modified.append((snap, entry))
            elif not same_identity:
                self.plan.fingerprints.append((snap, entry))
        elif (
            not same_identity
            or snap.fs_ino is None
            or (entry.kind == "other" and snap.unsupported_reason != entry.reason)
        ):
            self.plan.fingerprints.append((snap, entry))
        if entry.kind == "dir" and snap.status != "reconcile_needed":
            queue.append(WorkItem(snap, (*item.comps, entry.raw)))

    # -- stage 1b: files that moved
    def _match_files(self) -> None:
        appeared = [a for a in self._file_apps.values() if a.entry.kind == "file"]
        if not appeared:
            self._finish_missing()
            return
        pool: dict[uuid.UUID, Snap] = {s.id: s for s in self._missing_in_root("file")}
        for snap in self._vanished.values():
            if snap.kind == "file":
                pool[snap.id] = snap
        for snap in self._vanished.values():  # files below directories that are gone
            if snap.kind == "dir":
                for n in self.db.scalars(
                    select(Node).where(
                        Node.lineage.startswith(snap.lineage, autoescape=True),
                        Node.id != snap.id,
                        Node.kind == "file",
                        Node.status.in_(PENDING_STATUSES),
                    )
                ):
                    pool[n.id] = _snap(n)
        by_inode: dict[tuple[int, int], list[Snap]] = defaultdict(list)
        for snap in pool.values():
            if snap.fs_dev is not None and snap.fs_ino is not None:
                by_inode[(snap.fs_dev, snap.fs_ino)].append(snap)
        unmatched: list[Appeared] = []
        for app in appeared:
            hits = by_inode.get(app.entry.identity, [])
            if len(hits) == 1:
                snap = hits[0]
                same = (
                    snap.size == app.entry.st.st_size and snap.mtime_ns == app.entry.st.st_mtime_ns
                )
                if snap.status == "missing" and not same:
                    unmatched.append(app)  # long gone and different: an inode number reused
                    continue
                self._link(snap, app, uncertain=not same, modified=not same, pool=pool)
                by_inode.pop(app.entry.identity)
            else:
                unmatched.append(app)
        self._match_by_hash(unmatched, pool)
        self._finish_missing()

    def _link(
        self, snap: Snap, app: Appeared, *, uncertain: bool, modified: bool, pool: dict[Any, Snap]
    ) -> None:
        self._file_apps.pop(app.idx, None)
        self.plan.moves.append(Move(snap, app, uncertain, modified))
        pool.pop(snap.id, None)
        self._vanished.pop(snap.id, None)

    def _match_by_hash(self, appeared: list[Appeared], pool: dict[uuid.UUID, Snap]) -> None:
        by_size: dict[int, list[Snap]] = defaultdict(list)
        for snap in pool.values():
            if snap.content_hash and snap.size is not None and snap.size >= MIN_HASH_MATCH:
                by_size[snap.size].append(snap)
        hashed: dict[tuple[int, str], list[Appeared]] = defaultdict(list)
        for app in appeared:
            if app.entry.st.st_size not in by_size:
                continue  # hashed only when some vanished node has the same size
            digest = self._hash(app)
            if digest is not None:
                hashed[(app.entry.st.st_size, digest)].append(app)
        for (size, digest), apps in hashed.items():
            nodes = [s for s in by_size[size] if s.content_hash == digest]
            if len(apps) == 1 and len(nodes) == 1:  # ambiguity on either side links nothing
                self._link(nodes[0], apps[0], uncertain=True, modified=False, pool=pool)

    def _hash(self, app: Appeared) -> str | None:
        try:
            with open_dir(self.settings.library_path, self.root.relative_dir, app.comps[:-1]) as fd:
                digest, _ = files.hash_file(fd, app.entry.raw)
                return digest
        except (StorageError, OSError, InvalidName):
            return None

    def _finish_missing(self) -> None:
        self.plan.missing = list(self._vanished.values())
        self.plan.creates = list(self._file_apps.values())


# ------------------------------------------------------------------ apply


def _lock_key(root_id: uuid.UUID) -> int:
    return (root_id.int ^ 0x46435F5343414E) & 0x7FFFFFFFFFFFFFFF


class Applier:
    def __init__(self, sessions: "sessionmaker[Session]", settings: Settings, root_id: uuid.UUID):
        self.sessions, self.settings, self.root_id = sessions, settings, root_id
        self.run_id = uuid.uuid4()
        self.done: defaultdict[str, int] = defaultdict(int)
        self.deferred = 0
        self.created_ids: dict[int, uuid.UUID] = {}

    # -- plumbing
    def apply(self, plan: Plan) -> None:
        self._created_dirs(plan.dir_creates)
        self._batches(plan.moves, self._move)
        self._batches(plan.fingerprints, self._fingerprint)
        self._batches(plan.modified, self._modified)
        self._batches(plan.missing, self._missing)  # frees names before reappear/create
        self._batches(plan.reappeared, self._reappeared)
        self._batches(plan.creates, self._create)

    def _batches(self, items: list[Any], fn: Any) -> None:
        for start in range(0, len(items), BATCH):
            chunk = items[start : start + BATCH]
            try:
                self._apply_chunk(chunk, fn)
            except Exception as exc:  # one bad change must not stall the whole root
                log.warning("scan batch failed, retrying one by one: %s", type(exc).__name__)
                for item in chunk:
                    try:
                        self._apply_chunk([item], fn)
                    except Exception:
                        log.warning("scan change skipped after an error")
                        self.deferred += 1

    def _apply_chunk(self, chunk: list[Any], fn: Any) -> None:
        with self.sessions() as db:
            serialize_tree_changes(db)
            blocked = _blocked(db)
            deferred = 0
            for item in chunk:
                if not fn(db, item, blocked):
                    deferred += 1
            db.commit()
            self.deferred += deferred

    def _fresh(self, db: Session, snap: Snap, blocked: "Blocked") -> Node | None:
        node = db.get(Node, snap.id)
        if (
            node is None
            or node.revision != snap.revision
            or node.status != snap.status
            or node.parent_id != snap.parent_id
            or node.name != snap.name
            or blocked.hits(node)
        ):
            return None
        return node

    @staticmethod
    def _stamp(node: Node, entry: DiskEntry, run: uuid.UUID) -> None:
        node.fs_dev, node.fs_ino, node.mtime_ns = fingerprint(entry.st)
        node.last_seen_run = run
        if entry.kind == "file":
            node.size = entry.st.st_size
        node.unsupported_reason = entry.reason
        node.updated_at = clock.utcnow()

    def _event(self, db: Session, node_id: uuid.UUID, action: str, **details: Any) -> None:
        events.record(
            db,
            node_id,
            action,
            source="scan",
            actor_kind=events.EXTERNAL,
            **details,
        )

    # -- change kinds
    def _fingerprint(self, db: Session, item: tuple[Snap, DiskEntry], blocked: "Blocked") -> bool:
        snap, entry = item
        node = self._fresh(db, snap, blocked)
        if node is None:
            return False
        self._stamp(node, entry, self.run_id)
        if node.status == "reconcile_needed":
            node.status = "active"
            self.done["resolved"] += 1
        return True

    def _modified(self, db: Session, item: tuple[Snap, DiskEntry], blocked: "Blocked") -> bool:
        snap, entry = item
        node = self._fresh(db, snap, blocked)
        if node is None:
            return False
        node.revision += 1
        node.content_hash = None
        self._stamp(node, entry, self.run_id)
        self._event(db, node.id, "external_modified", name=node.name, size=node.size)
        self.done["modified"] += 1
        return True

    def _reappeared(self, db: Session, item: tuple[Snap, DiskEntry], blocked: "Blocked") -> bool:
        snap, entry = item
        node = self._fresh(db, snap, blocked)
        parent = db.get(Node, node.parent_id) if node and node.parent_id else None
        if node is None or parent is None or parent.status not in PENDING_STATUSES:
            return False
        if node.parent_id is not None and self._name_taken(db, node.parent_id, node.name):
            return False  # an active node holds the name now: try again next pass
        if entry.kind == "file" and (
            node.size != entry.st.st_size or node.mtime_ns != entry.st.st_mtime_ns
        ):
            node.content_hash = None
        node.status = "active"
        node.revision += 1
        self._stamp(node, entry, self.run_id)
        self._event(db, node.id, "reappeared", name=node.name)
        self.done["reappeared"] += 1
        return True

    def _parent_for(self, db: Session, app: Appeared, blocked: "Blocked") -> Node | None:
        parent_id = (
            app.parent_id
            if app.parent_id is not None
            else self.created_ids.get(app.parent_idx if app.parent_idx is not None else -1)
        )
        parent = db.get(Node, parent_id) if parent_id is not None else None
        if (
            parent is None
            or parent.kind != "dir"
            or parent.status not in PENDING_STATUSES
            or blocked.hits(parent)
        ):
            return None
        return parent

    @staticmethod
    def _name_taken(db: Session, parent_id: uuid.UUID, name: str) -> bool:
        return (
            db.scalar(
                select(Node.id)
                .where(
                    Node.parent_id == parent_id,
                    Node.name == name,
                    Node.status.not_in((*HIDDEN_NODE_STATUSES, "missing")),
                )
                .limit(1)
            )
            is not None
        )

    def _unique_display(self, db: Session, parent_id: uuid.UUID, entry: DiskEntry) -> str | None:
        name = entry.name
        if entry.digest is None:
            return None if self._name_taken(db, parent_id, name) else name
        stem, ext = os.path.splitext(name)
        for i in range(1, 100):
            candidate = name if i == 1 else f"{stem} ({i}){ext}"
            if not self._name_taken(db, parent_id, candidate):
                return candidate
        return None

    def _make(self, db: Session, app: Appeared, parent: Node, name: str) -> Node:
        entry = app.entry
        now = clock.utcnow()
        node_id = uuid.uuid4()
        node = Node(
            id=node_id,
            root_id=parent.root_id,
            parent_id=parent.id,
            name=name,
            name_key=name_key(name),
            kind=entry.kind,
            lineage=f"{parent.lineage}{node_id}/",
            status="active",
            revision=1,
            size=entry.st.st_size if entry.kind == "file" else None,
            raw_name_digest=entry.digest,
            unsupported_reason=entry.reason,
            created_at=now,
            updated_at=now,
        )
        node.fs_dev, node.fs_ino, node.mtime_ns = fingerprint(entry.st)
        node.last_seen_run = self.run_id
        db.add(node)
        db.flush()
        setup.close_window(db, parent.root_id, "content")  # content found by a scan (ADR-011 §1)
        self._event(db, node.id, "external_created", name=name, kind=entry.kind)
        self.done["created"] += 1
        return node

    def _created_dirs(self, dirs: list[Appeared]) -> None:
        ordered = sorted(dirs, key=lambda a: len(a.comps))  # parents before children
        for start in range(0, len(ordered), BATCH):
            with self.sessions() as db:
                serialize_tree_changes(db)
                blocked = _blocked(db)
                for app in ordered[start : start + BATCH]:
                    parent = self._parent_for(db, app, blocked)
                    name = self._unique_display(db, parent.id, app.entry) if parent else None
                    if parent is None or name is None:
                        self.deferred += 1
                        continue
                    self.created_ids[app.idx] = self._make(db, app, parent, name).id
                db.commit()

    def _create(self, db: Session, app: Appeared, blocked: "Blocked") -> bool:
        parent = self._parent_for(db, app, blocked)
        name = self._unique_display(db, parent.id, app.entry) if parent else None
        if parent is None or name is None:
            return False
        self._make(db, app, parent, name)
        return True

    def _move(self, db: Session, move: Move, blocked: "Blocked") -> bool:
        node = self._fresh(db, move.node, blocked)
        parent = self._parent_for(db, move.target, blocked)
        if node is None or parent is None or parent.lineage.startswith(node.lineage):
            return False
        entry = move.target.entry
        name = self._unique_display(db, parent.id, entry)
        if name is None:
            return False
        old_name, old_lineage, was_missing = node.name, node.lineage, node.status == "missing"
        new_lineage = f"{parent.lineage}{node.id}/"
        if move.node.kind == "dir":
            db.execute(
                update(Node)
                .where(Node.lineage.startswith(old_lineage, autoescape=True), Node.id != node.id)
                .values(
                    lineage=literal(new_lineage) + func.substr(Node.lineage, len(old_lineage) + 1)
                )
                .execution_options(synchronize_session=False)
            )
        node.parent_id, node.name, node.name_key = parent.id, name, name_key(name)
        node.raw_name_digest = entry.digest
        node.lineage = new_lineage
        node.status = "active"
        node.revision += 1
        if move.modified or (entry.kind == "file" and node.size != entry.st.st_size):
            node.content_hash = None
        self._stamp(node, entry, self.run_id)
        db.flush()
        db.expire_all()
        self._event(
            db,
            node.id,
            "external_moved",
            old_name=old_name,
            name=name,
            uncertain=move.uncertain,
        )
        if move.modified:
            self._event(db, node.id, "external_modified", name=name, size=node.size)
        if was_missing:
            self._event(db, node.id, "reappeared", name=name)
        self.done["moved"] += 1
        return True

    def _missing(self, db: Session, snap: Snap, blocked: "Blocked") -> bool:
        node = self._fresh(db, snap, blocked)
        if node is None or self._still_present(db, node):
            return False
        result = db.execute(
            update(Node)
            .where(
                Node.lineage.startswith(node.lineage, autoescape=True),
                Node.status.in_(PENDING_STATUSES),
            )
            .values(status="missing", updated_at=clock.utcnow())
            .execution_options(synchronize_session=False)
        )
        self._event(
            db,
            node.id,
            "missing",
            name=node.name,
            affected=int(result.rowcount or 0),  # type: ignore[attr-defined]
        )
        self.done["missing"] += 1
        return True

    def _still_present(self, db: Session, node: Node) -> bool:
        """Last look before declaring something missing: editors that save through a temporary
        file make the original vanish for a moment."""
        parent = db.get(Node, node.parent_id) if node.parent_id else None
        root = db.get(StorageRoot, node.root_id)
        if parent is None or root is None:
            return False
        try:
            with open_dir(
                self.settings.library_path, root.relative_dir, path_components(db, parent)
            ) as fd:
                info = files.lstat_entry(fd, node.name)
            # Only an entry of the same kind keeps the node alive: a folder that took over a
            # file's name (or the reverse) must not block the old node from going missing.
            if info is None or node.kind == "other":
                return info is not None
            wanted = stat.S_ISDIR if node.kind == "dir" else stat.S_ISREG
            return wanted(info.st_mode)
        except (StorageError, OSError):
            return True  # cannot tell: do not mark anything missing on a failed look


@dataclass
class Blocked:
    """What the app is working on right now: `busy` claims and nodes named by `planned` journal
    rows. A scan change touching any of these (or anything beneath a busy node) is deferred."""

    busy: set[uuid.UUID]
    planned: set[uuid.UUID]

    def hits(self, node: Node) -> bool:
        if node.id in self.planned or (node.parent_id in self.planned):
            return True
        return any(uuid.UUID(p) in self.busy for p in node.lineage.strip("/").split("/"))


def _blocked(db: Session) -> Blocked:
    busy = set(db.scalars(select(Node.id).where(Node.status == "busy")))
    planned: set[uuid.UUID] = set()
    for node_id, target in db.execute(
        select(FileOperation.node_id, FileOperation.target_parent_id).where(
            FileOperation.status == "planned"
        )
    ):
        planned.update(i for i in (node_id, target) if i is not None)
    return Blocked(busy, planned)


# ------------------------------------------------------------------ guard


def affected_count(db: Session, plan: Plan) -> int:
    """Nodes the plan would mark missing (with their subtrees) or modified."""
    affected = len(plan.modified)
    for snap in plan.missing:
        if snap.kind == "dir":
            affected += (
                db.scalar(
                    select(func.count())
                    .select_from(Node)
                    .where(
                        Node.lineage.startswith(snap.lineage, autoescape=True),
                        Node.status.in_(PENDING_STATUSES),
                    )
                )
                or 0
            )
        else:
            affected += 1
    return affected


def would_exceed_guard(db: Session, root_id: uuid.UUID, affected: int) -> bool:
    """True if applying a plan would mark more than half of a root (and more than 100 nodes)
    missing or modified: an unmounted or replaced bind mount looks exactly like that."""
    total = (
        db.scalar(
            select(func.count())
            .select_from(Node)
            .where(Node.root_id == root_id, Node.status.in_(PENDING_STATUSES))
        )
        or 0
    )
    return affected > MASS_MINIMUM and affected > MASS_FRACTION * total


def reviewed_affected(db: Session, root_id: uuid.UUID) -> int | None:
    """What the person saw when the root was suspended: the latest suspended run's tally."""
    run = db.scalars(
        select(ScanRun)
        .where(ScanRun.root_id == root_id, ScanRun.outcome == "suspended")
        .order_by(ScanRun.started_at.desc())
    ).first()
    if run is None or not isinstance(run.counts, dict):
        return None
    value = run.counts.get("affected")
    return int(value) if isinstance(value, int) else None


# ------------------------------------------------------------------ one pass


@dataclass
class PassResult:
    outcome: str  # ok | partial | suspended | failed | skipped
    counts: dict[str, int]
    error: str | None = None


class Reconciler:
    """Runs scan passes. Thread-safe: one pass per root at a time (in-process lock plus a
    PostgreSQL advisory lock, so a second API process cannot overlap)."""

    def __init__(self, sessions: "sessionmaker[Session]", settings: Settings) -> None:
        self.sessions, self.settings = sessions, settings
        self.stop = threading.Event()
        self._locks: dict[uuid.UUID, threading.Lock] = defaultdict(threading.Lock)

    def run_root(
        self,
        root_id: uuid.UUID,
        trigger: str,
        *,
        subtree: uuid.UUID | None = None,
        force: bool = False,
    ) -> PassResult:
        lock = self._locks[root_id]
        if not lock.acquire(blocking=False):
            return PassResult("skipped", {})
        try:
            with self.sessions() as guard_db:
                if not self._advisory(guard_db, root_id):
                    return PassResult("skipped", {})
                try:
                    return self._run(root_id, trigger, subtree, force)
                except Exception as exc:  # never leave a run "running"
                    log.exception("scan pass crashed")
                    self._fail_open_runs(root_id)
                    return PassResult("failed", {}, type(exc).__name__[:48])
                finally:
                    self._advisory_release(guard_db, root_id)
        finally:
            lock.release()

    def _fail_open_runs(self, root_id: uuid.UUID) -> None:
        with self.sessions() as db:
            db.execute(
                update(ScanRun)
                .where(ScanRun.root_id == root_id, ScanRun.outcome == "running")
                .values(outcome="failed", error_code="SCAN_ERROR", finished_at=clock.utcnow())
            )
            db.commit()

    @staticmethod
    def _advisory(db: Session, root_id: uuid.UUID) -> bool:
        if db.get_bind().dialect.name != "postgresql":
            return True
        return bool(db.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": _lock_key(root_id)}))

    @staticmethod
    def _advisory_release(db: Session, root_id: uuid.UUID) -> None:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _lock_key(root_id)})

    def _run(
        self, root_id: uuid.UUID, trigger: str, subtree: uuid.UUID | None, force: bool
    ) -> PassResult:
        with self.sessions() as db:
            root = db.get(StorageRoot, root_id)
            if root is None or root.status != "active":
                return PassResult("skipped", {})
            if root.scan_state == "suspended" and not force:
                return PassResult("skipped", {})
            if self._left_library_device(root):
                root.status = "unsupported_mount"  # a drive was mounted over the root
                db.commit()
                return PassResult("skipped", {})
            if setup.should_pause_scan(db, root, self.settings.scan_pause_ceiling_seconds):
                return PassResult("skipped", {})  # a file is being written; the next pass looks
            run = ScanRun(
                root_id=root_id,
                trigger=trigger,
                started_at=clock.utcnow(),
                outcome="running",
            )
            db.add(run)
            db.commit()
            run_id = run.id
            start_node = db.get(Node, subtree) if subtree else None
            if start_node is None:
                start_node = db.scalars(
                    select(Node).where(Node.root_id == root_id, Node.parent_id.is_(None))
                ).one()
            if start_node.root_id != root_id or start_node.kind != "dir":
                return self._finish(run_id, PassResult("skipped", {}))
            if start_node.status not in PENDING_STATUSES:
                return self._finish(run_id, PassResult("skipped", {}))
            comps = tuple(path_components(db, start_node))
            start = _snap(start_node)
            try:
                plan = Planner(db, self.settings, root, self.stop).run(start, comps)
            except InterruptedError:
                return self._finish(run_id, PassResult("partial", {}, "STOPPED"))
            except (StorageMissing, StorageError, OSError) as exc:
                log.warning("scan of a root failed: %s", type(exc).__name__)
                return self._finish(run_id, PassResult("failed", {}, "ROOT_UNREADABLE"))
            counts = plan.counts()
            counts["affected"] = affected_count(db, plan)
            reviewed = reviewed_affected(db, root_id) if force else None
            drifted = (
                reviewed is not None
                and counts["affected"] > reviewed * (1 + APPLY_DRIFT) + APPLY_DRIFT_SLACK
            )
            if drifted or (not force and would_exceed_guard(db, root_id, counts["affected"])):
                root = db.get(StorageRoot, root_id)
                if root is not None:
                    root.scan_state = "suspended"
                    db.commit()
                log.warning("scan suspended: too many changes at once (%s)", root_id)
                code = "CHANGED_SINCE_REVIEW" if drifted else "MASS_CHANGE"
                return self._finish(run_id, PassResult("suspended", counts, code))
            if force and root.scan_state == "suspended":
                root.scan_state = None
                db.commit()
        applier = Applier(self.sessions, self.settings, root_id)
        applier.apply(plan)
        counts = {**dict(applier.done), "deferred": applier.deferred, "unreadable": plan.unreadable}
        outcome = "partial" if plan.unreadable else "ok"
        return self._finish(run_id, PassResult(outcome, counts))

    def _left_library_device(self, root: StorageRoot) -> bool:
        library = self.settings.library_path
        try:
            return roots.root_device(library, root.relative_dir) != os.stat(library).st_dev
        except (StorageError, OSError):
            return False  # unreadable is reported by the pass itself

    def _finish(self, run_id: uuid.UUID, result: PassResult) -> PassResult:
        with self.sessions() as db:
            run = db.get(ScanRun, run_id)
            if run is not None:
                run.finished_at = clock.utcnow()
                run.outcome = result.outcome
                run.error_code = result.error
                run.counts = result.counts
                db.commit()
        return result

    def run_all(self, trigger: str) -> dict[uuid.UUID, PassResult]:
        with self.sessions() as db:
            ids = list(db.scalars(select(StorageRoot.id).where(StorageRoot.status == "active")))
        out: dict[uuid.UUID, PassResult] = {}
        for root_id in ids:
            if self.stop.is_set():
                break
            out[root_id] = self.run_root(root_id, trigger)
        return out

    # -- background hashing (ADR-011 §6)
    def hash_pending(self, root_id: uuid.UUID, *, limit: int | None = None) -> int:
        """Fills `content_hash` for files that have none, throttled to `hash_rate_bytes`. Only
        hashes a file whose size and mtime still match what the database recorded."""
        rate = self.settings.hash_rate_bytes
        if rate <= 0:
            return 0
        hashed, cursor = 0, None
        while not self.stop.is_set() and (limit is None or hashed < limit):
            with self.sessions() as db:
                root = db.get(StorageRoot, root_id)
                if root is None or root.status != "active" or root.scan_state == "suspended":
                    return hashed  # a suspended root may sit on a replaced mount: read nothing
                query = select(Node).where(
                    Node.root_id == root_id,
                    Node.kind == "file",
                    Node.status == "active",
                    Node.content_hash.is_(None),
                    Node.mtime_ns.is_not(None),
                )
                if cursor is not None:
                    query = query.where(Node.id > cursor)
                node = db.scalars(query.order_by(Node.id).limit(1)).first()
                if node is None:
                    return hashed
                cursor = node.id
                comps = path_components(db, node)
                expect = (node.size, node.mtime_ns, node.id, node.name)
                relative = root.relative_dir
            digest = self._hash_one(relative, comps, expect, rate)
            if digest is None:
                continue
            with self.sessions() as db:
                done = db.execute(
                    update(Node)
                    .where(
                        Node.id == expect[2],
                        Node.mtime_ns == expect[1],
                        Node.content_hash.is_(None),
                    )
                    .values(content_hash=digest)
                )
                db.commit()
                hashed += int(done.rowcount or 0)  # type: ignore[attr-defined]
        return hashed

    def _hash_one(
        self, relative: str, comps: list[str], expect: tuple[Any, ...], rate: int
    ) -> str | None:
        size, mtime_ns, _, name = expect
        try:
            with open_dir(self.settings.library_path, relative, comps[:-1]) as dir_fd:
                fd, info = files.open_regular(dir_fd, name)
        except (StorageError, OSError):
            return None
        try:
            if info.st_size != size or info.st_mtime_ns != mtime_ns:
                return None
            digest = hashlib.sha256()
            while chunk := os.read(fd, files.CHUNK):
                digest.update(chunk)
                time.sleep(len(chunk) / rate)
                if self.stop.is_set():
                    return None
            after = os.fstat(fd)
            if after.st_size != size or after.st_mtime_ns != mtime_ns:
                return None
            return digest.hexdigest()
        finally:
            os.close(fd)
