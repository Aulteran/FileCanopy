"""File operations: list, create folder, upload, download, copy, rename, move (ADR-013).

Follows the action contract of `04_DATA_FILESYSTEM.md`: authorize every involved node through the
policy engine, validate, journal, claim, act on the filesystem, then reconcile the database in one
transaction. The filesystem action is atomic and never replaces an existing entry; if the database
step fails the action is undone, and if it cannot be undone the node and journal row say
`reconcile_needed` instead of claiming success. Callers pass an `OpCtx`; routes never build paths.
"""

import errno
import hashlib
import json
import logging
import os
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from sqlalchemy import func, literal, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.auth import clock
from app.auth.service import audit
from app.config import Settings
from app.db.models import HIDDEN_NODE_STATUSES, FileOperation, Node, StorageRoot
from app.domain import events, setup
from app.domain.nodes import path_components
from app.policy import engine, privacy
from app.policy.model import Actor, Privilege
from app.storage import files, viewable
from app.storage.identity import fingerprint
from app.storage.names import InvalidName, name_key
from app.storage.resolver import (
    StorageError,
    StorageEscape,
    StorageMissing,
    ensure_root_dir,
    open_dir,
)

log = logging.getLogger("filecanopy.fileops")

MAX_KEY = 128
KEEP_BOTH_ATTEMPTS = 100


class OpError(Exception):
    """A refused or failed operation with a stable machine-readable code."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass
class OpCtx:
    """Who acts. `actor` is always an account: an AI agent acts for a user under that user's
    privileges (`actor_kind="ai_agent"` with the approved `proposal_id`), so it passes the same
    policy checks and its events name both."""

    db: Session
    settings: Settings
    actor: uuid.UUID
    ip: str | None = None
    actor_kind: str = "user"
    proposal_id: str | None = None
    # The login session of the person at the browser; None in background work, which therefore
    # never counts as unlocked for Confidential folders (M09).
    session_id: uuid.UUID | None = None

    def event_actor(self) -> dict[str, Any]:
        """Keyword arguments naming the actor for `events.record`."""
        if self.actor_kind == events.AI_AGENT:
            return {
                "actor_kind": events.AI_AGENT,
                "on_behalf_of": self.actor,
                "proposal_id": self.proposal_id,
                "source": "ai",
            }
        return {"actor_kind": "user", "actor_user_id": self.actor, "source": "ui"}


@dataclass
class Outcome:
    node: Node
    op: FileOperation | None
    replay: bool = False


# ------------------------------------------------------------------ authorization / helpers


def _need(ctx: OpCtx, target: uuid.UUID | Node, privilege: Privilege) -> Node:
    node = ctx.db.get(Node, target) if isinstance(target, uuid.UUID) else target
    if node is None or privilege not in engine.effective(
        ctx.db, Actor(ctx.actor, session_id=ctx.session_id), node
    ):
        node_id = target if isinstance(target, uuid.UUID) else target.id
        audit(
            ctx.db,
            "access.denied",
            actor=ctx.actor,
            ip=ctx.ip,
            node_id=str(node_id),
            required=privilege.value,
        )
        ctx.db.commit()
        raise OpError(403, "FORBIDDEN")
    return node


def _unlocked_subtree(ctx: OpCtx, node: Node) -> None:
    """Refuses an action on a folder that holds a locked Confidential folder (M09)."""
    if privacy.locked_scopes_in(
        ctx.db,
        ctx.actor,
        ctx.session_id if ctx.actor_kind == "user" else None,
        node,
        kind=ctx.actor_kind,
    ):
        audit(ctx.db, "privacy.locked_denied", actor=ctx.actor, ip=ctx.ip, node_id=str(node.id))
        ctx.db.commit()
        raise OpError(403, "LOCKED")


def _carry(ctx: OpCtx, src: Node, new_parent: Node) -> str | None:
    try:
        return privacy.carried_label(ctx.db, src, new_parent)
    except ValueError as exc:
        raise OpError(409, "PRIVACY_DOWNGRADE") from exc


def _usable(node: Node, *, kind: str | None = None) -> None:
    if node.kind == "other":
        raise OpError(409, "UNSUPPORTED_ENTRY")  # symlink, special file or mounted drive (ADR-011)
    if kind is not None and node.kind != kind:
        raise OpError(422, "NOT_A_DIRECTORY" if kind == "dir" else "NOT_A_FILE")
    if node.status == "busy":
        raise OpError(409, "NODE_BUSY")
    if node.status == "missing":
        raise OpError(409, "CONTENT_MISSING")  # gone outside the app: never "deleted"
    if node.status != "active":
        raise OpError(409, "RECONCILE_NEEDED")


def _clean(name: str) -> str:
    try:
        return files.check_name(name)
    except InvalidName as exc:
        raise OpError(422, "INVALID_NAME") from exc


def _root(ctx: OpCtx, node: Node) -> StorageRoot:
    root = ctx.db.get(StorageRoot, node.root_id)
    if root is None or root.status in engine.UNUSABLE_ROOT_STATUSES:
        raise OpError(403, "FORBIDDEN")
    return root


def _map_error(exc: BaseException) -> OpError:
    if isinstance(exc, OpError):
        return exc
    table: list[tuple[type[BaseException], int, str]] = [
        (files.NameExists, 409, "NAME_CONFLICT"),
        (files.UnsafeEntry, 409, "UNSUPPORTED_ENTRY"),
        (files.SourceChanged, 409, "SOURCE_CHANGED"),
        (files.TreeTooLarge, 413, "TREE_TOO_LARGE"),
        (StorageEscape, 409, "STORAGE_UNSAFE_PATH"),
        (StorageMissing, 409, "CONTENT_MISSING"),
        (FileNotFoundError, 409, "CONTENT_MISSING"),
        (InvalidName, 422, "INVALID_NAME"),
        (StorageError, 503, "STORAGE_UNAVAILABLE"),
    ]
    for kind, status, code in table:
        if isinstance(exc, kind):
            return OpError(status, code)
    if isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT):
        return OpError(507, "INSUFFICIENT_STORAGE")
    if isinstance(exc, OSError):
        return OpError(503, "STORAGE_IO_ERROR")
    return OpError(500, "OPERATION_FAILED")


@contextmanager
def _fs(ctx: OpCtx, root: StorageRoot, comps: list[str], *, write: bool) -> Iterator[int]:
    """Opens a directory beneath a root. Writes first make sure the root directory exists (accounts
    created while the API runs have none until restart)."""
    if write and root.status != "active":
        raise OpError(503, "STORAGE_UNAVAILABLE")  # an unusable root is not silently recreated
    try:
        if write:
            ensure_root_dir(ctx.settings.library_path, root.relative_dir)
        with open_dir(ctx.settings.library_path, root.relative_dir, comps) as fd:
            yield fd
    except (StorageError, FileNotFoundError, InvalidName) as exc:
        raise _map_error(exc) from exc


def _db_has(
    db: Session,
    parent_id: uuid.UUID,
    name: str,
    *,
    exact: bool = False,
    exclude: uuid.UUID | None = None,
) -> bool:
    """True if a sibling holds the name. New names also collide on `name_key` (case and Unicode
    form), so content stays safe for case-insensitive clients (ADR-011 §6); dismissed nodes
    release their name."""
    column = Node.name == name if exact else Node.name_key == name_key(name)
    query = select(Node.id).where(
        Node.parent_id == parent_id, Node.status.not_in(HIDDEN_NODE_STATUSES), column
    )
    if exclude is not None:
        query = query.where(Node.id != exclude)
    return db.scalar(query.limit(1)) is not None


def _candidates(name: str, kind: str, mode: str) -> Iterator[str]:
    yield name
    if mode == "keep_both":
        stem, ext = (name, "") if kind == "dir" else os.path.splitext(name)
        for i in range(2, KEEP_BOTH_ATTEMPTS + 2):
            cand = f"{stem} ({i}){ext}"
            if len(cand.encode()) <= 255:
                yield cand


def _new_node(parent: Node, name: str, kind: str, size: int | None) -> Node:
    now = clock.utcnow()
    node_id = uuid.uuid4()
    return Node(
        id=node_id,
        root_id=parent.root_id,
        parent_id=parent.id,
        name=name,
        kind=kind,
        lineage=f"{parent.lineage}{node_id}/",
        status="active",
        revision=1,
        size=size,
        created_at=now,
        updated_at=now,
    )


def _stamp(node: Node, info: os.stat_result, sha256: str | None = None) -> None:
    """Records the filesystem identity the app itself just created, so the next scan sees no
    change (and a later outside edit is detected against this fingerprint)."""
    node.fs_dev, node.fs_ino, node.mtime_ns = fingerprint(info)
    if sha256 is not None:
        node.content_hash = sha256


def _parent_unchanged(db: Session, parent: Node, revision: int) -> None:
    """Final-transaction check for create-type operations: the parent was not renamed, moved or
    claimed while we worked, so the entry we made is still where the database will say it is.
    Takes the tree lock and re-reads the parent, so the caller builds the new node from a lineage
    that no concurrent move can rewrite before this transaction ends."""
    serialize_tree_changes(db)
    db.expire_all()
    db.refresh(parent)
    if parent.status != "active" or parent.revision != revision or not _chain_idle(db, parent):
        raise OpError(409, "PARENT_CHANGED")


TREE_LOCK_KEY = 0x46435F54524545  # arbitrary constant: "FC_TREE"


def serialize_tree_changes(db: Session) -> None:
    """Final transactions that change lineage (moves) or insert nodes under a lineage take one
    transaction-scoped advisory lock, so a subtree rewrite can never miss an uncommitted insert
    and no transaction builds a lineage from a stale ancestor. DB-only and short; file I/O never
    happens while it is held. SQLite (unit tests) has a single writer already."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": TREE_LOCK_KEY})


def _chain_idle(db: Session, node: Node) -> bool:
    """False if the node or any ancestor is claimed by an in-flight move."""
    busy = db.scalar(
        select(func.count())
        .select_from(Node)
        .where(Node.id.in_(engine.lineage_ids(node)), Node.status == "busy")
    )
    return not busy


# ------------------------------------------------------------------ journal


def _hash(**params: Any) -> str:
    blob = json.dumps(params, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def _begin(
    ctx: OpCtx,
    action: str,
    key: str | None,
    *,
    node_id: uuid.UUID | None,
    target_parent_id: uuid.UUID | None,
    params: dict[str, Any],
    detail: dict[str, Any],
) -> tuple[FileOperation, bool]:
    """Records a `planned` row. With an idempotency key, a retry of a committed operation returns
    the original result and a different request under the same key is refused."""
    if key is not None and not (0 < len(key) <= MAX_KEY and key.isprintable()):
        raise OpError(422, "INVALID_IDEMPOTENCY_KEY")
    digest = _hash(action=action, **params)
    now = clock.utcnow()
    if key is not None:
        prior = ctx.db.scalar(
            select(FileOperation).where(
                FileOperation.actor_user_id == ctx.actor, FileOperation.idempotency_key == key
            )
        )
        if prior is not None:
            if prior.request_hash != digest:
                raise OpError(422, "IDEMPOTENCY_KEY_REUSED")
            if prior.status == "committed":
                return prior, True
            if prior.status in ("planned", "reconcile_needed"):
                raise OpError(409, "OPERATION_IN_PROGRESS")
            retried = ctx.db.execute(  # compare-and-swap: only one concurrent retry may proceed
                update(FileOperation)
                .where(
                    FileOperation.id == prior.id,
                    FileOperation.status.in_(("failed", "rolled_back")),
                )
                .values(status="planned", detail=detail, updated_at=now)
            )
            if retried.rowcount != 1:  # type: ignore[attr-defined]
                ctx.db.rollback()
                raise OpError(409, "OPERATION_IN_PROGRESS")
            ctx.db.commit()
            ctx.db.refresh(prior)
            return prior, False
    op = FileOperation(
        actor_user_id=ctx.actor,
        action=action,
        status="planned",
        idempotency_key=key,
        request_hash=digest,
        node_id=node_id,
        target_parent_id=target_parent_id,
        detail=detail,
        created_at=now,
        updated_at=now,
    )
    ctx.db.add(op)
    try:
        ctx.db.commit()
    except IntegrityError as exc:  # a concurrent request with the same key won
        ctx.db.rollback()
        raise OpError(409, "OPERATION_IN_PROGRESS") from exc
    return op, False


def _peek_replay(
    ctx: OpCtx, action: str, key: str | None, params: dict[str, Any]
) -> FileOperation | None:
    """A retry of an already committed keyed operation is answered from the journal before any
    precondition (such as the revision, which the first run advanced) is evaluated."""
    if key is None:
        return None
    prior = ctx.db.scalar(
        select(FileOperation).where(
            FileOperation.actor_user_id == ctx.actor, FileOperation.idempotency_key == key
        )
    )
    if prior is None or prior.status != "committed":
        return None
    if prior.request_hash != _hash(action=action, **params):
        return None  # `_begin` reports the reuse
    return prior


def _set(op: FileOperation, status: str, **detail: Any) -> None:
    op.status = status
    op.detail = {**(op.detail or {}), **detail}
    op.updated_at = clock.utcnow()


def _record_failure(ctx: OpCtx, op: FileOperation, err: OpError) -> None:
    ctx.db.rollback()
    if op.status in ("planned",):
        _set(op, "failed", error=err.code)
    ctx.db.commit()


@contextmanager
def _journaled(ctx: OpCtx, op: FileOperation) -> Iterator[None]:
    """Any exception inside becomes a coded OpError and a `failed` journal row (unless the body
    already recorded `reconcile_needed`)."""
    try:
        yield
    except BaseException as exc:
        err = _map_error(exc)
        if err.status == 500:
            log.exception("file operation failed unexpectedly")
        _record_failure(ctx, op, err)
        raise err from exc


# ------------------------------------------------------------------ reads


def list_children(
    ctx: OpCtx, node_id: uuid.UUID, *, limit: int, after: str | None
) -> tuple[list[Node], str | None]:
    node = _need(ctx, node_id, Privilege.LIST)
    _usable_for_read(node, "dir")
    query = select(Node).where(Node.parent_id == node.id, Node.status.not_in(HIDDEN_NODE_STATUSES))
    if after is not None:
        query = query.where(Node.name > after)
    rows = list(ctx.db.scalars(query.order_by(Node.name).limit(limit + 1)))
    next_cursor = rows[limit - 1].name if len(rows) > limit else None
    return rows[:limit], next_cursor


def _usable_for_read(node: Node, kind: str) -> None:
    if node.kind == "other":
        raise OpError(409, "UNSUPPORTED_ENTRY")
    if kind == "file" and node.status == "missing":
        raise OpError(409, "CONTENT_MISSING")
    if node.kind != kind:
        raise OpError(422, "NOT_A_DIRECTORY" if kind == "dir" else "NOT_A_FILE")


@dataclass
class Download:
    file: BinaryIO
    size: int
    name: str
    revision: int
    mtime_ns: int


def open_download(ctx: OpCtx, node_id: uuid.UUID) -> Download:
    node = _need(ctx, node_id, Privilege.READ)
    _usable_for_read(node, "file")
    if node.parent_id is None:
        raise OpError(422, "NOT_A_FILE")
    root = _root(ctx, node)
    comps = path_components(ctx.db, node)
    try:
        with _fs(ctx, root, comps[:-1], write=False) as dir_fd:
            fd, info = files.open_regular(dir_fd, comps[-1])
    except (StorageError, FileNotFoundError) as exc:
        raise _map_error(exc) from exc
    return Download(
        os.fdopen(fd, "rb", buffering=0), info.st_size, node.name, node.revision, info.st_mtime_ns
    )


def open_view(ctx: OpCtx, node_id: uuid.UUID) -> tuple[Download, viewable.ViewType]:
    """Opens a file for the in-app viewer (ADR-019): same access and path safety as a download,
    plus an allow-listed type whose first bytes match."""
    node = _need(ctx, node_id, Privilege.READ)
    _usable_for_read(node, "file")
    vt = viewable.classify(node.name)
    if vt is None:
        raise OpError(415, "NOT_VIEWABLE")
    dl = open_download(ctx, node_id)
    try:
        head = dl.file.read(viewable.SNIFF_BYTES) or b""
        dl.file.seek(0)
        if not viewable.matches_content(vt.kind, vt.mime, head):
            raise OpError(415, "TYPE_MISMATCH")
    except BaseException:
        dl.file.close()
        raise
    return dl, vt


# ------------------------------------------------------------------ create folder


def create_folder(ctx: OpCtx, parent_id: uuid.UUID, name: str, key: str | None) -> Outcome:
    parent = _need(ctx, parent_id, Privilege.WRITE)
    _usable(parent, kind="dir")
    clean = _clean(name)
    root = _root(ctx, parent)
    op, replay = _begin(
        ctx,
        "mkdir",
        key,
        node_id=None,
        target_parent_id=parent.id,
        params={"parent": str(parent.id), "name": clean},
        detail={"parent": str(parent.id), "name": clean},
    )
    if replay:
        return _replayed(ctx, op)
    with _journaled(ctx, op):
        comps = path_components(ctx.db, parent)
        revision = parent.revision
        if _db_has(ctx.db, parent.id, clean):
            raise OpError(409, "NAME_CONFLICT")
        with _fs(ctx, root, comps, write=True) as dir_fd:
            made = files.make_dir(dir_fd, clean)
            try:
                _parent_unchanged(ctx.db, parent, revision)
                node = _new_node(parent, clean, "dir", None)
                _stamp(node, made)
                ctx.db.add(node)
                setup.close_window(ctx.db, parent.root_id, "content", actor=ctx.actor, ip=ctx.ip)
                events.record(
                    ctx.db,
                    node.id,
                    "folder_created",
                    correlation_id=op.id,
                    **ctx.event_actor(),
                    name=clean,
                )
                op.node_id = node.id
                _set(op, "committed")
                audit(ctx.db, "file.mkdir", actor=ctx.actor, ip=ctx.ip, node_id=str(node.id))
                ctx.db.commit()
            except BaseException:
                ctx.db.rollback()
                _undo_or_flag(ctx, op, dir_fd, clean, "dir")
                raise
    return Outcome(node, op)


def _undo_entry(dir_fd: int, name: str, kind: str) -> bool:
    try:
        if kind == "dir":
            os.rmdir(name, dir_fd=dir_fd)
        else:
            os.unlink(name, dir_fd=dir_fd)
    except OSError:
        log.warning("could not undo a partially created entry")
        return False
    return True


def _undo_or_flag(ctx: OpCtx, op: FileOperation, dir_fd: int, name: str, kind: str) -> None:
    """Removes an entry created by a failed operation; if that is impossible the operation says
    `reconcile_needed` (an orphan exists on disk) instead of a plain failure."""
    if not _undo_entry(dir_fd, name, kind):
        ctx.db.rollback()
        _set(op, "reconcile_needed", step="undo_incomplete")
        ctx.db.commit()


def _note(ctx: OpCtx, op: FileOperation, **detail: Any) -> None:
    """Commits journal detail before the filesystem step it describes, so recovery sees it."""
    _set(op, "planned", **detail)
    ctx.db.commit()


def _replayed(ctx: OpCtx, op: FileOperation) -> Outcome:
    node = ctx.db.get(Node, op.node_id) if op.node_id else None
    if node is None:
        raise OpError(409, "OPERATION_IN_PROGRESS")
    if Privilege.TRAVERSE not in engine.effective(
        ctx.db, Actor(ctx.actor, session_id=ctx.session_id), node
    ):
        raise OpError(403, "FORBIDDEN")  # the result moved somewhere this actor cannot see
    return Outcome(node, op, replay=True)


# ------------------------------------------------------------------ upload


class Upload:
    """Streaming upload in three steps so the route can run each blocking part in a worker
    thread: `begin()`, repeated `write()`, then `finish()` (or `abort()`)."""

    def __init__(
        self, ctx: OpCtx, parent_id: uuid.UUID, name: str, mode: str, key: str | None
    ) -> None:
        self.ctx = ctx
        self.parent_id = parent_id
        self.name = name
        self.mode = mode
        self.key = key
        self.op: FileOperation | None = None
        self.replayed: Outcome | None = None
        self._stack = ExitStack()
        self._stage: files.StagedWrite | None = None
        self._dir_fd = -1
        self._parent: Node | None = None
        self._revision = 0

    def begin(self) -> None:
        ctx = self.ctx
        parent = _need(ctx, self.parent_id, Privilege.WRITE)
        _usable(parent, kind="dir")
        self.name = _clean(self.name)
        if self.mode not in ("reject", "keep_both"):
            raise OpError(422, "INVALID_CONFLICT_MODE")
        root = _root(ctx, parent)
        op, replay = _begin(
            ctx,
            "upload",
            self.key,
            node_id=None,
            target_parent_id=parent.id,
            params={"parent": str(parent.id), "name": self.name, "mode": self.mode},
            detail={"parent": str(parent.id), "name": self.name},
        )
        if replay:
            self.replayed = _replayed(ctx, op)
            return
        self.op = op
        with _journaled(ctx, op):
            self._parent, self._revision = parent, parent.revision
            comps = path_components(ctx.db, parent)
            self._dir_fd = self._stack.enter_context(_fs(ctx, root, comps, write=True))
            self._stage = files.StagedWrite(self._dir_fd)
            _set(op, "planned", tmp=self._stage.tmp)
            ctx.db.commit()

    def write(self, chunk: bytes) -> None:
        stage = self._stage
        if stage is None:
            raise RuntimeError("upload not started")
        if stage.size + len(chunk) > self.ctx.settings.max_upload_bytes:
            raise OpError(413, "UPLOAD_TOO_LARGE")
        stage.write(chunk)

    def finish(self) -> Outcome:
        ctx, op, stage, parent = self.ctx, self.op, self._stage, self._parent
        if op is None or stage is None or parent is None:
            raise RuntimeError("upload not started")
        try:
            with _journaled(ctx, op):
                chosen: str | None = None
                info: os.stat_result | None = None
                for cand in _candidates(self.name, "file", self.mode):
                    if _db_has(ctx.db, parent.id, cand):
                        continue
                    if cand != self.name:
                        _note(ctx, op, name=cand)
                    try:
                        info = stage.publish(cand)
                        chosen = cand
                        break
                    except files.NameExists:
                        continue  # taken on disk: try the next candidate
                if chosen is None or info is None:
                    raise OpError(409, "NAME_CONFLICT")
                try:
                    _parent_unchanged(ctx.db, parent, self._revision)
                    node = _new_node(parent, chosen, "file", info.st_size)
                    _stamp(node, info, stage.sha256)
                    ctx.db.add(node)
                    setup.close_window(
                        ctx.db, parent.root_id, "content", actor=ctx.actor, ip=ctx.ip
                    )
                    events.record(
                        ctx.db,
                        node.id,
                        "uploaded",
                        correlation_id=op.id,
                        **ctx.event_actor(),
                        name=chosen,
                        size=info.st_size,
                    )
                    op.node_id = node.id
                    _set(op, "committed", size=info.st_size, sha256=stage.sha256)
                    audit(ctx.db, "file.upload", actor=ctx.actor, ip=ctx.ip, node_id=str(node.id))
                    ctx.db.commit()
                except BaseException:
                    ctx.db.rollback()
                    _undo_or_flag(ctx, op, self._dir_fd, chosen, "file")
                    raise
            return Outcome(node, op)
        finally:
            self.close()

    def abort(self, code: str = "UPLOAD_ABORTED") -> None:
        try:
            if self.op is not None and self.op.status == "planned":
                self.ctx.db.rollback()
                _set(self.op, "failed", error=code)
                self.ctx.db.commit()
        finally:
            self.close()

    def close(self) -> None:
        if self._stage is not None:
            self._stage.abort()
            self._stage = None
        self._stack.close()


# ------------------------------------------------------------------ rename / move / copy


def _apply_move(
    ctx: OpCtx,
    node: Node,
    new_parent: Node,
    new_name: str,
    *,
    old_lineage: str,
    old_root: uuid.UUID,
) -> None:
    """Database half of a move, in the caller's transaction: the node, every descendant lineage
    and root id, and (across roots) the grants held on the moved folders."""
    db = ctx.db
    new_lineage = f"{new_parent.lineage}{node.id}/"
    db.execute(
        update(Node)
        .where(Node.lineage.startswith(old_lineage, autoescape=True), Node.id != node.id)
        .values(
            lineage=literal(new_lineage) + func.substr(Node.lineage, len(old_lineage) + 1),
            root_id=new_parent.root_id,
        )
        .execution_options(synchronize_session=False)
    )
    db.execute(
        update(Node)
        .where(Node.id == node.id)
        .values(
            parent_id=new_parent.id,
            name=new_name,
            name_key=name_key(new_name),
            root_id=new_parent.root_id,
            lineage=new_lineage,
            status="active",
            revision=Node.revision + 1,
            updated_at=clock.utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    db.flush()
    db.expire_all()


def _release_claim(ctx: OpCtx, node_id: uuid.UUID) -> None:
    ctx.db.execute(
        update(Node).where(Node.id == node_id, Node.status == "busy").values(status="active")
    )
    ctx.db.commit()


def relocate(
    ctx: OpCtx,
    node_id: uuid.UUID,
    *,
    action: str,
    expected_revision: int,
    target_parent_id: uuid.UUID | None,
    name: str | None,
    on_conflict: str,
    key: str | None,
    undo_of: uuid.UUID | None = None,
) -> Outcome:
    """Rename (same folder) or move. Needs write access on the old parent and the new parent.
    Across roots the move is a verified copy plus the source going to its owner's trash
    (ADR-013 §7, ADR-017). `undo_of` marks the inverse of an earlier operation (the undo module)."""
    node = _need(ctx, node_id, Privilege.WRITE)
    if node.parent_id is None:
        raise OpError(422, "CANNOT_MOVE_ROOT")
    old_parent = _need(ctx, node.parent_id, Privilege.WRITE)  # grant roots cannot be renamed
    new_parent = (
        old_parent
        if target_parent_id is None or target_parent_id == old_parent.id
        else _need(ctx, target_parent_id, Privilege.WRITE)
    )
    if new_parent.kind != "dir":
        raise OpError(422, "NOT_A_DIRECTORY")
    _unlocked_subtree(ctx, node)  # a Confidential folder inside must be open to be moved along
    carry = _carry(ctx, node, new_parent) if new_parent.id != old_parent.id else None
    if new_parent.root_id != node.root_id:
        from app.domain import trash  # deferred: trash builds on this module

        return trash.cross_root_move(
            ctx,
            node,
            old_parent,
            new_parent,
            action=action,
            expected_revision=expected_revision,
            name=name,
            on_conflict=on_conflict,
            key=key,
            undo_of=undo_of,
        )
    if new_parent.lineage.startswith(node.lineage):
        raise OpError(422, "INVALID_TARGET")  # into itself or its own subtree
    if on_conflict not in ("reject", "keep_both"):
        raise OpError(422, "INVALID_CONFLICT_MODE")
    new_name = _clean(name) if name is not None else node.name
    params = {
        "node": str(node.id),
        "rev": expected_revision,
        "parent": str(new_parent.id),
        "name": new_name,
        "mode": on_conflict,
    }
    done_before = _peek_replay(ctx, action, key, params)
    if done_before is not None:
        return _replayed(ctx, done_before)
    if node.revision != expected_revision:
        raise OpError(409, "REVISION_CONFLICT")
    if new_parent.id == old_parent.id and new_name == node.name:
        return Outcome(node, None)  # nothing to do
    for parent in {old_parent.id: old_parent, new_parent.id: new_parent}.values():
        _usable(parent, kind="dir")
    _usable(node)
    old_root, new_root = _root(ctx, old_parent), _root(ctx, new_parent)
    op, replay = _begin(
        ctx,
        action,
        key,
        node_id=node.id,
        target_parent_id=new_parent.id,
        params=params,
        detail={
            "src_parent": str(old_parent.id),
            "src_name": node.name,
            "dst_parent": str(new_parent.id),
            "dst_name": new_name,
            **({"undo_of": str(undo_of)} if undo_of is not None else {}),
        },
    )
    if replay:
        return _replayed(ctx, op)

    src_names = path_components(ctx.db, node)
    dst_parent_comps = path_components(ctx.db, new_parent)
    old_lineage, old_root_id, old_name = node.lineage, node.root_id, node.name
    claimed = ctx.db.execute(
        update(Node)
        .where(Node.id == node.id, Node.revision == expected_revision, Node.status == "active")
        .values(status="busy")
    )
    if claimed.rowcount != 1:  # type: ignore[attr-defined]
        err = OpError(409, "REVISION_CONFLICT")
        _record_failure(ctx, op, err)
        raise err
    ctx.db.commit()

    mode = on_conflict if action == "move" else "reject"
    leftover_check: (
        tuple[int, str, list[tuple[tuple[str, ...], str, os.stat_result]], list[files.TreeEntry]]
        | None
    ) = None
    done = False
    try:
        with ExitStack() as stack:
            src_fd = stack.enter_context(_fs(ctx, old_root, src_names[:-1], write=True))
            same = new_parent.id == old_parent.id
            dst_fd = (
                src_fd
                if same
                else stack.enter_context(_fs(ctx, new_root, dst_parent_comps, write=True))
            )
            info = files.lstat_entry(src_fd, old_name)
            if info is None:
                _set(op, "reconcile_needed", error="CONTENT_MISSING")
                ctx.db.execute(
                    update(Node).where(Node.id == node.id).values(status="reconcile_needed")
                )
                ctx.db.commit()
                raise OpError(409, "CONTENT_MISSING")
            _set(op, "planned", src_ino=info.st_ino, src_dev=info.st_dev)
            ctx.db.commit()

            chosen: str | None = None
            via_copy = False
            for cand in _candidates(new_name, node.kind, mode):
                if _db_has(ctx.db, new_parent.id, cand, exclude=node.id):
                    continue
                _note(ctx, op, dst_name=cand, step="renaming")
                try:
                    files.rename_noreplace(src_fd, old_name, dst_fd, cand)
                    chosen = cand
                    break
                except files.NameExists:
                    continue
                except files.CrossDevice:
                    _note(ctx, op, via_copy=True, step="copying")
                    try:
                        plan, copied = _copy_for_move(ctx, src_fd, old_name, dst_fd, cand)
                    except files.NameExists:
                        continue
                    chosen, via_copy = cand, True
                    leftover_check = (src_fd, old_name, plan, copied)
                    break
            if chosen is None:
                raise OpError(409, "NAME_CONFLICT")
            _set(op, "planned", dst_name=chosen, step="filesystem_done", via_copy=via_copy)

            try:
                # Final transaction: serialize with other lineage changes, then trust only fresh
                # rows. An ancestor of the target or of the node may have moved while the
                # filesystem step ran (possibly minutes for a cross-device copy).
                serialize_tree_changes(ctx.db)
                ctx.db.expire_all()
                fresh = ctx.db.get(Node, node.id)
                target = ctx.db.get(Node, new_parent.id)
                if fresh is None or target is None:
                    raise OpError(409, "PARENT_CHANGED")
                if target.status != "active" or not _chain_idle(ctx.db, target):
                    raise OpError(409, "PARENT_CHANGED")
                if target.lineage.startswith(fresh.lineage):
                    raise OpError(409, "INVALID_TARGET")  # a concurrent move made a cycle
                old_lineage, old_root_id = fresh.lineage, fresh.root_id
                _apply_move(
                    ctx, fresh, target, chosen, old_lineage=old_lineage, old_root=old_root_id
                )
                if carry is not None:  # a folder keeps the protection it had where it came from
                    ctx.db.execute(
                        update(Node).where(Node.id == node.id).values(privacy_label=carry)
                    )
                    ctx.db.expire_all()
                events.record(
                    ctx.db,
                    node.id,
                    "undone"
                    if undo_of is not None
                    else ("renamed" if action == "rename" else "moved"),
                    correlation_id=op.id,
                    **ctx.event_actor(),
                    old_name=old_name,
                    name=chosen,
                    from_parent_id=str(old_parent.id),
                    to_parent_id=str(target.id),
                    **_undo_marker(undo_of),
                )
                fresh = ctx.db.get(Node, node.id)
                post = {} if undo_of is not None or fresh is None else {
                    "undo": {
                        "kind": "relocate",
                        "revision": fresh.revision,
                        "src_parent": str(old_parent.id),
                        "src_name": old_name,
                        "dst_parent": str(target.id),
                        "dst_name": chosen,
                        "action": action,
                    }
                }  # fmt: skip
                if via_copy:
                    _set(op, "reconcile_needed", step="remove_source", **post)
                else:
                    _set(op, "committed", **post)
                audit(
                    ctx.db,
                    f"file.{action}",
                    actor=ctx.actor,
                    ip=ctx.ip,
                    node_id=str(node.id),
                    cross_root=old_root_id != target.root_id,
                )
                ctx.db.commit()
            except BaseException:
                ctx.db.rollback()
                _undo_move(ctx, op, node.id, src_fd, old_name, dst_fd, chosen, leftover_check)
                raise
            done = True

            if leftover_check is not None:
                s_fd, s_name, plan, copied = leftover_check
                left = files.remove_verified_source(s_fd, s_name, copied, plan)
                if left:
                    _set(op, "reconcile_needed", step="source_remnants", remnants=len(left))
                else:
                    _set(op, "committed", step="complete")
                ctx.db.commit()
    except BaseException as exc:
        err = _map_error(exc)
        if not done:
            ctx.db.rollback()
            current = ctx.db.get(FileOperation, op.id)
            if current is not None and current.status == "planned":
                _set(current, "failed", error=err.code)
            ctx.db.commit()
            _release_claim(ctx, node.id)
        if err.status == 500:
            log.exception("file operation failed unexpectedly")
        raise err from exc
    ctx.db.refresh(node)
    return Outcome(node, op)


def _undo_move(
    ctx: OpCtx,
    op: FileOperation,
    node_id: uuid.UUID,
    src_fd: int,
    src_name: str,
    dst_fd: int,
    dst_name: str,
    copy_state: tuple[
        int, str, list[tuple[tuple[str, ...], str, os.stat_result]], list[files.TreeEntry]
    ]
    | None,
) -> None:
    """The database step failed after the filesystem step. Put the filesystem back; if that is not
    possible, say so on the node and the journal instead of pretending."""
    ok = False
    try:
        if copy_state is None:
            files.rename_noreplace(dst_fd, dst_name, src_fd, src_name)
            ok = True
        else:
            ok = not files.remove_created(dst_fd, copy_state[3])
    except Exception:
        ok = False
    ctx.db.rollback()
    current = ctx.db.get(FileOperation, op.id)
    if current is not None:
        if ok:
            _set(current, "rolled_back", step="database_failed")
        else:
            _set(current, "reconcile_needed", step="database_failed_filesystem_changed")
    if not ok:
        ctx.db.execute(update(Node).where(Node.id == node_id).values(status="reconcile_needed"))
    ctx.db.commit()


def _copy_for_move(
    ctx: OpCtx, src_fd: int, src_name: str, dst_fd: int, dst_name: str
) -> tuple[list[tuple[tuple[str, ...], str, os.stat_result]], list[files.TreeEntry]]:
    """Cross-device fallback: scan, copy, then re-read every copied file and compare its hash with
    what was written. The source is untouched until the database points at the copy."""
    s = ctx.settings
    plan = files.scan_tree(
        src_fd, src_name, max_entries=s.max_tree_entries, max_depth=s.max_tree_depth
    )
    copied = files.copy_tree(src_fd, src_name, dst_fd, dst_name, plan)
    try:
        for entry in copied:
            if entry.kind != "file":
                continue
            parent_fd = _open_rel(dst_fd, entry.rel[:-1])
            try:
                digest, size = files.hash_file(parent_fd, entry.rel[-1])
            finally:
                os.close(parent_fd)
            if digest != entry.sha256 or size != entry.size:
                raise OpError(500, "COPY_VERIFY_FAILED")
    except BaseException:
        files.remove_created(dst_fd, copied)
        raise
    return plan, copied


def _open_rel(dir_fd: int, rel: tuple[str, ...]) -> int:
    fd = os.dup(dir_fd)
    try:
        for part in rel:
            nxt = files.open_dir_child(fd, part)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def copy(
    ctx: OpCtx,
    node_id: uuid.UUID,
    *,
    target_parent_id: uuid.UUID,
    name: str | None,
    on_conflict: str,
    expected_revision: int | None,
    key: str | None,
) -> Outcome:
    """Copy a file or folder tree (read access on the source, write access on the target)."""
    src = _need(ctx, node_id, Privilege.READ)
    if src.parent_id is None:
        raise OpError(422, "CANNOT_COPY_ROOT")
    target = _need(ctx, target_parent_id, Privilege.WRITE)
    _unlocked_subtree(ctx, src)
    _carry(ctx, src, target)  # a file cannot take its protection along: refused (409)
    _usable(target, kind="dir")
    _usable(src)
    if src.kind == "dir" and target.lineage.startswith(src.lineage):
        raise OpError(422, "INVALID_TARGET")
    if on_conflict not in ("reject", "keep_both"):
        raise OpError(422, "INVALID_CONFLICT_MODE")
    if expected_revision is not None and src.revision != expected_revision:
        raise OpError(409, "REVISION_CONFLICT")
    new_name = _clean(name) if name is not None else src.name
    src_root, dst_root = _root(ctx, src), _root(ctx, target)
    op, replay = _begin(
        ctx,
        "copy",
        key,
        node_id=None,
        target_parent_id=target.id,
        params={
            "node": str(src.id),
            "parent": str(target.id),
            "name": new_name,
            "mode": on_conflict,
        },
        detail={"source": str(src.id), "parent": str(target.id), "name": new_name},
    )
    if replay:
        return _replayed(ctx, op)
    with _journaled(ctx, op):
        src_names = path_components(ctx.db, src)
        dst_comps = path_components(ctx.db, target)
        revision = target.revision
        s = ctx.settings
        with ExitStack() as stack:
            src_fd = stack.enter_context(_fs(ctx, src_root, src_names[:-1], write=False))
            dst_fd = stack.enter_context(_fs(ctx, dst_root, dst_comps, write=True))
            plan = files.scan_tree(
                src_fd, src.name, max_entries=s.max_tree_entries, max_depth=s.max_tree_depth
            )
            created: list[files.TreeEntry] | None = None
            chosen: str | None = None
            for cand in _candidates(new_name, src.kind, on_conflict):
                if _db_has(ctx.db, target.id, cand):
                    continue
                _note(ctx, op, name=cand, step="copying")
                try:
                    created = files.copy_tree(src_fd, src.name, dst_fd, cand, plan)
                    chosen = cand
                    break
                except files.NameExists:
                    continue
            if created is None or chosen is None:
                raise OpError(409, "NAME_CONFLICT")
            try:
                _parent_unchanged(ctx.db, target, revision)
                top = _insert_copied(ctx, target, created, op, src)
                op.node_id = top.id
                _set(
                    op,
                    "committed",
                    entries=len(created),
                    undo={"kind": "copy", "top": str(top.id), "revision": top.revision},
                )
                audit(
                    ctx.db,
                    "file.copy",
                    actor=ctx.actor,
                    ip=ctx.ip,
                    node_id=str(top.id),
                    source_id=str(src.id),
                )
                ctx.db.commit()
            except BaseException:
                ctx.db.rollback()
                if files.remove_created(dst_fd, created):
                    current = ctx.db.get(FileOperation, op.id)
                    if current is not None:
                        _set(current, "reconcile_needed", step="copy_cleanup_incomplete")
                        ctx.db.commit()
                raise
    return Outcome(top, op)


def _undo_marker(undo_of: uuid.UUID | None) -> dict[str, Any]:
    return {"operation_id": str(undo_of)} if undo_of is not None else {}


def _copy_marker(moved: bool, src: Node, entries: int, is_top: bool) -> dict[str, Any]:
    if not is_top:
        return {}
    return {("from_node_id" if moved else "copied_from"): str(src.id), "entries": entries}


def _insert_copied(
    ctx: OpCtx,
    target: Node,
    created: list[files.TreeEntry],
    op: FileOperation,
    src: Node,
    *,
    moved: bool = False,
) -> Node:
    """Creates fresh node identities for every copied entry, parents before children. The top
    entry's history says where it came from (`copied`, or `moved_in` for a cross-root move whose
    source records `moved_out`); the rest are plain `created`."""
    by_rel: dict[tuple[str, ...], Node] = {}
    top: Node | None = None
    labels = privacy.labels_below(ctx.db, src)  # copies keep the protection of what they copy
    for entry in sorted(created, key=lambda e: (len(e.rel), e.rel)):
        parent = target if len(entry.rel) == 1 else by_rel[entry.rel[:-1]]
        node = _new_node(
            parent, entry.rel[-1], entry.kind, entry.size if entry.kind == "file" else None
        )
        if entry.new_stat is not None:
            _stamp(node, entry.new_stat, entry.sha256)
        is_top = len(entry.rel) == 1
        if entry.kind == "dir":
            own = src.privacy_label if is_top else labels.get(entry.rel[1:], "normal")
            if is_top:
                carried = privacy.carried_label(ctx.db, src, target) if src.kind == "dir" else None
                own = carried or own
            node.privacy_label = own
        ctx.db.add(node)
        events.record(
            ctx.db,
            node.id,
            ("moved_in" if moved else "copied") if is_top else "created",
            correlation_id=op.id,
            **ctx.event_actor(),
            name=entry.rel[-1],
            **_copy_marker(moved, src, len(created), is_top),
        )
        by_rel[entry.rel] = node
        if is_top:
            top = node
    ctx.db.flush()
    if top is None:
        raise OpError(500, "OPERATION_FAILED")
    setup.close_window(ctx.db, target.root_id, "content", actor=ctx.actor, ip=ctx.ip)
    return top


# ------------------------------------------------------------------ journal queries / recovery


def get_operation(db: Session, actor: uuid.UUID, operation_id: uuid.UUID) -> FileOperation | None:
    """Journal rows are visible to the actor who started them only."""
    op = db.get(FileOperation, operation_id)
    return op if op is not None and op.actor_user_id == actor else None


def recover_operations(sessions: "sessionmaker[Session]", settings: Settings) -> dict[str, int]:
    """Startup pass over operations left `planned` by a crash. The filesystem is inspected through
    the safe resolver and the journal's recorded inode: complete what is provably complete, undo
    what is provably untouched, and flag everything else `reconcile_needed`."""
    counts = {"rolled_back": 0, "committed": 0, "failed": 0, "reconcile_needed": 0}
    cutoff = clock.utcnow().timestamp() - settings.operation_stale_seconds
    with sessions() as db:
        stale = [
            op
            for op in db.scalars(select(FileOperation).where(FileOperation.status == "planned"))
            if op.updated_at.timestamp() <= cutoff
        ]
        for op in stale:
            ctx = OpCtx(db, settings, op.actor_user_id)
            try:
                outcome = _recover_one(ctx, op)
            except Exception:  # never crash startup on one bad row
                log.exception("recovery of an operation failed")
                db.rollback()
                outcome = "reconcile_needed"
                _set(op, "reconcile_needed", step="recovery_error")
            counts[outcome] += 1
            db.commit()
        from app.domain import trash  # deferred: trash builds on this module

        trash.release_orphan_items(db)
        # A claim whose journal row is gone or finished (for example the release itself failed)
        # would block the node forever.
        in_flight = select(FileOperation.node_id).where(
            FileOperation.status == "planned", FileOperation.node_id.is_not(None)
        )
        db.execute(
            update(Node)
            .where(Node.status == "busy", Node.id.not_in(in_flight))
            .values(status="active")
        )
        db.commit()
    return counts


def _recover_one(ctx: OpCtx, op: FileOperation) -> str:
    d = op.detail or {}
    db = ctx.db
    if op.action in ("trash", "restore", "purge", "xmove"):
        from app.domain import trash  # deferred: trash builds on this module

        return trash.recover_one(ctx, op)
    if op.action in ("rename", "move"):
        node = db.get(Node, op.node_id) if op.node_id else None
        if node is None or node.status != "busy" or "src_ino" not in d:
            if node is not None and node.status == "busy":
                db.execute(update(Node).where(Node.id == node.id).values(status="active"))
            _set(op, "failed", error="INTERRUPTED")
            return "failed"
        src_parent, dst_parent = (
            db.get(Node, uuid.UUID(d["src_parent"])),
            db.get(Node, uuid.UUID(d["dst_parent"])),
        )
        if src_parent is None or dst_parent is None:
            return _flag(db, op, node)
        here = _stat_at(ctx, src_parent, d["src_name"])
        there = _stat_at(ctx, dst_parent, d["dst_name"])
        ino = d["src_ino"]
        if here is not None and here.st_ino == ino and (there is None or there.st_ino != ino):
            if there is not None and d.get("via_copy"):
                return _flag(db, op, node)  # partial copy at the destination: a human decides
            db.execute(update(Node).where(Node.id == node.id).values(status="active"))
            _set(op, "rolled_back", step="recovered_untouched")
            return "rolled_back"
        if here is None and there is not None and there.st_ino == ino and not d.get("via_copy"):
            _apply_move(
                ctx,
                node,
                dst_parent,
                d["dst_name"],
                old_lineage=node.lineage,
                old_root=node.root_id,
            )
            events.record(
                db,
                node.id,
                "renamed" if op.action == "rename" else "moved",
                correlation_id=op.id,
                **ctx.event_actor(),
                old_name=d["src_name"],
                name=d["dst_name"],
                from_parent_id=d["src_parent"],
                to_parent_id=d["dst_parent"],
                recovered=True,
            )
            _set(op, "committed", step="recovered_forward")
            return "committed"
        return _flag(db, op, node)
    # mkdir / upload / copy: nothing is recorded in the database until the final commit, so there
    # is nothing to undo there; remove a leftover staging file and look for an orphaned result.
    parent = db.get(Node, uuid.UUID(d["parent"])) if "parent" in d else None
    name = d.get("name") or d.get("dst_name")
    if parent is not None and d.get("tmp"):
        _remove_leftover_tmp(ctx, parent, d["tmp"])
    if parent is not None and name and _stat_at(ctx, parent, name) is not None:
        if not _db_has(db, parent.id, name, exact=True):
            _set(op, "reconcile_needed", step="orphan_entry")
            return "reconcile_needed"
    _set(op, "failed", error="INTERRUPTED")
    return "failed"


def _flag(db: Session, op: FileOperation, node: Node) -> str:
    db.execute(update(Node).where(Node.id == node.id).values(status="reconcile_needed"))
    _set(op, "reconcile_needed", step="recovery_undecided")
    return "reconcile_needed"


def _stat_at(ctx: OpCtx, parent: Node, name: str) -> os.stat_result | None:
    root = ctx.db.get(StorageRoot, parent.root_id)
    if root is None:
        return None
    try:
        with open_dir(
            Path(ctx.settings.library_path), root.relative_dir, path_components(ctx.db, parent)
        ) as fd:
            return files.lstat_entry(fd, name)
    except (StorageError, FileNotFoundError):
        return None


def _remove_leftover_tmp(ctx: OpCtx, parent: Node, tmp: str) -> None:
    root = ctx.db.get(StorageRoot, parent.root_id)
    if root is None or not tmp.startswith(files.TMP_PREFIX):
        return
    try:
        with open_dir(
            Path(ctx.settings.library_path), root.relative_dir, path_components(ctx.db, parent)
        ) as fd:
            os.unlink(tmp, dir_fd=fd)
    except (StorageError, OSError):
        pass
