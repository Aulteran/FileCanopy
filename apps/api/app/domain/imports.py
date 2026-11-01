"""Setup import and folder upload (ADR-011 §1, §2, §4).

One engine serves three kinds of batch:

- `setup_folder` and `setup_zip` fill an empty shared tree (administrator) or personal folder (its
  owner) once, while the root's setup window is open. They are planned first: nothing is written
  until the importer has seen the plan and started it.
- `folder_upload` adds a folder to any folder the person can write to. It starts at once.

Every file goes through the same path as an ordinary upload (ADR-013): a temporary file in the
destination directory, an fsync, a no-replace publish with keep-both on a name clash, then the node
and its journal row in one transaction. Nothing is ever overwritten, folders of the same name merge,
and every entry re-checks the actor's authorization through the policy engine before it commits.
Zip names pass through the same per-component validation as folder names, so a traversal path is
rejected at planning time and again by the safe resolver at write time.
"""

import logging
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from collections import Counter
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, exists, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.auth import clock
from app.auth.service import audit
from app.config import Settings
from app.db.models import HIDDEN_NODE_STATUSES, ImportBatch, ImportEntry, Node, StorageRoot, User
from app.domain import events, setup
from app.domain.fileops import (
    OpCtx,
    OpError,
    _candidates,
    _chain_idle,
    _db_has,
    _fs,
    _map_error,
    _new_node,
    _root,
    _stamp,
    _undo_entry,
    _usable,
    serialize_tree_changes,
)
from app.domain.nodes import path_components
from app.policy import engine
from app.policy.model import Actor, Privilege
from app.storage import files
from app.storage.names import InvalidName, name_key
from app.storage.resolver import StorageError
from app.storage.ziparchive import EntryLimit, ZipRefused, inspect_zip, limited_chunks

log = logging.getLogger("filecanopy.imports")

SYSTEM_NAMES = frozenset({"__macosx", ".ds_store", "thumbs.db", "desktop.ini"})
DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
SETUP_KINDS = ("setup_folder", "setup_zip")
ALL_KINDS = (*SETUP_KINDS, "folder_upload")
OPEN_STATUSES = ("planned", "running", "stopped")
FINAL_ENTRY = ("done", "renamed", "skipped", "failed")
STAGING_NAME = re.compile(r"^[0-9a-f]{32}\.zip$")
EXAMPLES = 20
# A manifest body may weigh this much per allowed entry (see api.imports.manifest_byte_cap).
MANIFEST_BYTES_PER_ENTRY = 320
# While a zip streams in, free space on app-data is re-checked every this many bytes.
SPACE_RECHECK_BYTES = 64 * 1024 * 1024
INSERT_CHUNK = 2000
STALE_STAGING_SECONDS = 24 * 3600
# Authorization failures stop the whole batch; anything else only fails the one entry.
STOP_CODES = frozenset({"FORBIDDEN", "SETUP_CLOSED", "STORAGE_UNAVAILABLE"})
# Refusals that say nothing about the entry itself: it keeps its state.
UNTOUCHED = STOP_CODES | {"BATCH_NOT_RUNNING", "ENTRY_BUSY", "ENTRY_NOT_FOUND", "NOT_A_FILE"}


# ------------------------------------------------------------------ names and planning


class PathReject(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class RawEntry:
    path: str
    kind: str  # dir | file
    size: int | None
    mtime_ms: int | None
    src_index: int | None = None
    skip: str | None = None  # decided before planning (zip: encrypted, symlink, ...)


@dataclass
class PlannedEntry:
    idx: int
    parent_idx: int | None
    name: str
    path: str
    kind: str
    size: int | None
    mtime_ms: int | None
    src_index: int | None
    status: str  # pending | skipped
    reason: str | None = None


@dataclass
class Planned:
    entries: list[PlannedEntry]
    summary: dict[str, Any]


def split_path(raw: str, *, zip_style: bool) -> list[str]:
    """A relative path as validated, NFC-normalized components, or PathReject. Absolute paths,
    drive prefixes and empty, `.` or `..` components are refused (zip-slip). In a zip a backslash is
    a separator; elsewhere it is an invalid name character."""
    text = raw.replace("\\", "/") if zip_style else raw
    if text.endswith("/"):
        text = text[:-1]  # a directory entry
    if not text or text.startswith("/"):
        raise PathReject("INVALID_PATH")
    parts = text.split("/")
    if DRIVE_PREFIX.match(parts[0]) or any(p in ("", ".", "..") for p in parts):
        raise PathReject("INVALID_PATH")
    try:
        return [files.check_name(p) for p in parts]
    except (InvalidName, ValueError) as exc:  # ValueError: a lone surrogate cannot be encoded
        raise PathReject("INVALID_NAME") from exc


def _display(path: str) -> str:
    """The path as shown back to its sender: storable text without control characters."""
    text = path.encode("utf-8", "replace").decode("utf-8")
    return "".join("�" if ord(c) < 32 or ord(c) == 127 else c for c in text)[:1024]


def build_plan(
    raws: list[RawEntry],
    *,
    max_depth: int,
    max_file_bytes: int,
    include_system: bool,
    zip_style: bool,
) -> Planned:
    """Turns a manifest into entries. Folders are deduplicated by name key (case and Unicode form)
    so they merge; a file whose name key is taken is predicted as a keep-both rename. Missing
    parent folders get an implicit entry, so every folder has exactly one row. Pure: no I/O."""
    entries: list[PlannedEntry] = []
    dirs: dict[tuple[str, ...], int] = {}
    file_keys: set[tuple[str, ...]] = set()
    seen_paths: set[str] = set()
    skipped: Counter[str] = Counter()
    skipped_examples: list[dict[str, str]] = []
    collisions: list[dict[str, str]] = []
    collision_count = 0
    merged = 0

    def skip(raw: RawEntry, reason: str) -> None:
        skipped[reason] += 1
        shown = _display(raw.path)
        if len(skipped_examples) < EXAMPLES:
            skipped_examples.append({"path": shown, "reason": reason})
        entries.append(
            PlannedEntry(
                len(entries), None, "", shown, raw.kind, raw.size, raw.mtime_ms, raw.src_index,
                "skipped", reason,
            )
        )  # fmt: skip

    def collide(path: str, kind: str) -> None:
        nonlocal collision_count
        collision_count += 1
        if len(collisions) < EXAMPLES:
            collisions.append({"path": _display(path), "kind": kind})

    def ensure_dir(comps: list[str]) -> int:
        keys = tuple(name_key(c) for c in comps)
        if keys in dirs:
            return dirs[keys]
        parent = ensure_dir(comps[:-1]) if len(comps) > 1 else None
        idx = len(entries)
        path = "/".join(comps)
        entries.append(
            PlannedEntry(idx, parent, comps[-1], path, "dir", None, None, None, "pending")
        )
        dirs[keys] = idx
        if keys in file_keys:
            collide(path, "folder_vs_file")
        return idx

    for raw in raws:
        if raw.skip:
            skip(raw, raw.skip)
            continue
        try:
            comps = split_path(raw.path, zip_style=zip_style)
        except PathReject as err:
            skip(raw, err.code)
            continue
        if len(comps) > max_depth:
            skip(raw, "TOO_DEEP")
            continue
        if not include_system and any(c.casefold() in SYSTEM_NAMES for c in comps):
            skip(raw, "SYSTEM_FILE")
            continue
        path = "/".join(comps)
        keys = tuple(name_key(c) for c in comps)
        if raw.kind == "dir":
            known = keys in dirs
            idx = ensure_dir(comps)
            if known and entries[idx].path != path:
                merged += 1
                collide(path, "folder_merged_by_name_key")
            if entries[idx].mtime_ms is None:
                entries[idx].mtime_ms = raw.mtime_ms
            continue
        size = raw.size or 0
        if size > max_file_bytes:
            skip(raw, "TOO_LARGE")
            continue
        parent = ensure_dir(comps[:-1]) if len(comps) > 1 else None
        if keys in file_keys or keys in dirs:
            collide(path, "duplicate" if path in seen_paths else "name_key")
        seen_paths.add(path)
        file_keys.add(keys)
        entries.append(
            PlannedEntry(
                len(entries), parent, comps[-1], path, "file", size, raw.mtime_ms, raw.src_index,
                "pending",
            )
        )  # fmt: skip

    pending_files = [e for e in entries if e.kind == "file" and e.status == "pending"]
    summary: dict[str, Any] = {
        "files": len(pending_files),
        "folders": sum(1 for e in entries if e.kind == "dir" and e.status == "pending"),
        "bytes": sum(e.size or 0 for e in pending_files),
        "skipped": dict(skipped),
        "skipped_examples": skipped_examples,
        "collisions": {"count": collision_count, "examples": collisions},
        "merged_folders": merged,
    }
    return Planned(entries, summary)


def _space(settings: Settings, needed: int) -> dict[str, Any]:
    free = shutil.disk_usage(settings.library_path).free
    required = needed + settings.import_free_space_reserve_bytes
    return {"free_bytes": free, "required_bytes": required, "fits": required <= free}


# ------------------------------------------------------------------ authorization


def _deny(ctx: OpCtx, node_id: uuid.UUID, what: str) -> OpError:
    ctx.db.rollback()
    audit(ctx.db, "access.denied", actor=ctx.actor, ip=ctx.ip, node_id=str(node_id), required=what)
    ctx.db.commit()
    return OpError(403, "FORBIDDEN")


def authorize(ctx: OpCtx, batch: ImportBatch) -> tuple[StorageRoot, Node]:
    """The policy check made before every entry commit. Setup batches need the root's open window
    and whoever may administer it (an administrator for the shared tree, the owner for a personal
    folder: an administrator has no route into a personal one). Folder uploads need `write`."""
    db = ctx.db
    root = db.get(StorageRoot, batch.root_id)
    target = db.get(Node, batch.target_node_id)
    user = db.get(User, batch.actor_user_id)
    if (
        root is None
        or target is None
        or user is None
        or user.status != "active"
        or user.id != ctx.actor
        or root.status != "active"
        or target.root_id != root.id
    ):
        raise _deny(ctx, batch.target_node_id, "import")
    actor = Actor(user.id, session_id=ctx.session_id)
    if batch.kind in SETUP_KINDS:
        if target.parent_id is not None or not engine.can_administer_grants(db, actor, target):
            raise _deny(ctx, target.id, "setup")
        if root.setup_state != "open":
            raise OpError(409, "SETUP_CLOSED")
    else:
        if Privilege.WRITE not in engine.effective(db, actor, target):
            raise _deny(ctx, target.id, "write")
        _usable(target, kind="dir")
    return root, target


def check_destination(ctx: OpCtx, batch: ImportBatch, target: Node, parent: Node) -> None:
    """The folder an entry is about to be written into must still be the batch's target or lie
    below it, and (folder uploads) the actor must still hold `write` on that folder itself. A
    folder moved out of the uploader's reach while the batch was open is therefore never written
    into. Fails with PARENT_CHANGED and nothing else: no ids, names or paths are revealed."""
    db = ctx.db
    try:
        db.refresh(parent)
    except Exception as exc:  # the folder is gone
        raise OpError(409, "PARENT_CHANGED") from exc
    if parent.status != "active" or target.id not in engine.lineage_ids(parent):
        raise OpError(409, "PARENT_CHANGED")
    if batch.kind not in SETUP_KINDS:
        if Privilege.WRITE not in engine.effective(db, Actor(ctx.actor, session_id=ctx.session_id), parent):
            raise OpError(409, "PARENT_CHANGED")


def close_window_for_upload(ctx: OpCtx, batch: ImportBatch, root: StorageRoot) -> None:
    """A folder upload creates content by a path other than the setup import, so it closes the
    root's setup window (ADR-011 §1) in the same transaction that creates the node. Setup batches
    never close it themselves."""
    if batch.kind not in SETUP_KINDS:
        setup.close_window(ctx.db, root.id, "folder_upload", actor=ctx.actor, ip=ctx.ip)


# ------------------------------------------------------------------ batches


def _entry(db: Session, batch_id: uuid.UUID, idx: int) -> ImportEntry | None:
    return db.scalar(
        select(ImportEntry)
        .where(ImportEntry.batch_id == batch_id, ImportEntry.idx == idx)
        .execution_options(populate_existing=True)
    )


def _touch(db: Session, batch_id: uuid.UUID) -> None:
    db.execute(
        update(ImportBatch).where(ImportBatch.id == batch_id).values(updated_at=clock.utcnow())
    )
    db.commit()


def _insert_batch(
    ctx: OpCtx,
    *,
    root: StorageRoot,
    target: Node,
    kind: str,
    status: str,
    planned: Planned,
    include_system: bool,
    needed_bytes: int,
    batch_id: uuid.UUID | None = None,
    staged_zip: str | None = None,
) -> ImportBatch:
    db, now = ctx.db, clock.utcnow()
    plan = {**planned.summary, **_space(ctx.settings, needed_bytes)}
    batch = ImportBatch(
        id=batch_id or uuid.uuid4(),
        root_id=root.id,
        target_node_id=target.id,
        actor_user_id=ctx.actor,
        kind=kind,
        status=status,
        include_system=include_system,
        plan=plan,
        staged_zip=staged_zip,
        created_at=now,
        updated_at=now,
    )
    db.add(batch)
    try:
        db.flush()
    except IntegrityError as exc:  # the partial unique index: another unfinished setup import
        db.rollback()
        raise OpError(409, "IMPORT_IN_PROGRESS") from exc
    rows = [
        {
            "batch_id": batch.id,
            "idx": e.idx,
            "parent_idx": e.parent_idx,
            "name": e.name,
            "path": e.path,
            "kind": e.kind,
            "size": e.size,
            "mtime_ms": e.mtime_ms,
            "src_index": e.src_index,
            "status": e.status,
            "reason": e.reason,
        }
        for e in planned.entries
    ]
    for start in range(0, len(rows), INSERT_CHUNK):
        db.execute(insert(ImportEntry), rows[start : start + INSERT_CHUNK])
    audit(
        db,
        "import.planned" if status == "planned" else "import.started",
        actor=ctx.actor,
        ip=ctx.ip,
        batch_id=str(batch.id),
        kind=kind,
        files=plan["files"],
        bytes=plan["bytes"],
    )
    db.commit()
    return batch


def setup_target(ctx: OpCtx, node_id: uuid.UUID) -> tuple[StorageRoot, Node]:
    """A root node the actor may set up while its window is open; else 403 or 409."""
    node = ctx.db.get(Node, node_id)
    root = ctx.db.get(StorageRoot, node.root_id) if node is not None else None
    if (
        node is None
        or root is None
        or node.parent_id is not None
        or root.status != "active"
        or not engine.can_administer_grants(ctx.db, Actor(ctx.actor, session_id=ctx.session_id), node)
    ):
        raise _deny(ctx, node_id, "setup")
    if root.setup_state != "open":
        raise OpError(409, "SETUP_CLOSED")
    return root, node


def precheck_setup(ctx: OpCtx, node_id: uuid.UUID) -> None:
    """Everything a setup import needs before a byte is read: authority, an open window, and no
    other unfinished setup import of this root."""
    root, _ = setup_target(ctx, node_id)
    _no_open_setup_batch(ctx, root)


def _no_open_setup_batch(ctx: OpCtx, root: StorageRoot) -> None:
    open_batches = ctx.db.scalar(
        select(func.count())
        .select_from(ImportBatch)
        .where(
            ImportBatch.root_id == root.id,
            ImportBatch.kind.in_(SETUP_KINDS),
            ImportBatch.status.in_(OPEN_STATUSES),
        )
    )
    if open_batches:
        raise OpError(409, "IMPORT_IN_PROGRESS")


def _check_count(ctx: OpCtx, count: int) -> None:
    if count > ctx.settings.max_import_entries:
        raise OpError(413, "TOO_MANY_ENTRIES")


def _plan_args(settings: Settings, include_system: bool, zip_style: bool) -> dict[str, Any]:
    return {
        "max_depth": settings.max_tree_depth,
        "max_file_bytes": settings.max_upload_bytes,
        "include_system": include_system,
        "zip_style": zip_style,
    }


def plan_setup_folder(
    ctx: OpCtx, node_id: uuid.UUID, raws: list[RawEntry], include_system: bool
) -> ImportBatch:
    root, node = setup_target(ctx, node_id)
    _no_open_setup_batch(ctx, root)
    _check_count(ctx, len(raws))
    planned = build_plan(raws, **_plan_args(ctx.settings, include_system, False))
    return _insert_batch(
        ctx,
        root=root,
        target=node,
        kind="setup_folder",
        status="planned",
        planned=planned,
        include_system=include_system,
        needed_bytes=planned.summary["bytes"],
    )


class UploadSlots:
    """Zip uploads in flight: at most one per root and one per person at a time (the API is one
    process, ADR-013 §6a). The unique index on open setup batches is the database-level guard;
    this keeps a second archive from being streamed to disk at all."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._roots: set[uuid.UUID] = set()
        self._users: set[uuid.UUID] = set()

    def acquire(self, root_id: uuid.UUID, user_id: uuid.UUID) -> None:
        with self._lock:
            if root_id in self._roots:
                raise OpError(409, "IMPORT_IN_PROGRESS")
            if user_id in self._users:
                raise OpError(429, "UPLOAD_IN_PROGRESS")
            self._roots.add(root_id)
            self._users.add(user_id)

    def release(self, root_id: uuid.UUID, user_id: uuid.UUID) -> None:
        with self._lock:
            self._roots.discard(root_id)
            self._users.discard(user_id)


zip_slots = UploadSlots()


def check_staging_space(settings: Settings, incoming: int) -> None:
    """Free space on the app-data volume (where archives are staged) must cover the archive and
    the reserve."""
    staging = settings.app_data_path
    free = shutil.disk_usage(staging).free
    if incoming + settings.import_free_space_reserve_bytes > free:
        raise OpError(507, "INSUFFICIENT_SPACE")


def staging_dir(settings: Settings) -> Path:
    return settings.app_data_path / "staging" / "imports"


def staged_path(settings: Settings, name: str) -> Path:
    if not STAGING_NAME.match(name):
        raise OpError(500, "OPERATION_FAILED")
    return staging_dir(settings) / name


def new_staging_file(settings: Settings, batch_id: uuid.UUID) -> tuple[int, str]:
    """Creates the empty staged archive file (never following a link) and returns its descriptor
    and name. The caller streams the upload into it."""
    directory = staging_dir(settings)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = f"{batch_id.hex}.zip"
    fd = os.open(
        directory / name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
    )
    return fd, name


def delete_staged(settings: Settings, name: str | None) -> None:
    if name and STAGING_NAME.match(name):
        try:
            (staging_dir(settings) / name).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            log.warning("could not remove a staged archive")


def plan_setup_zip(
    ctx: OpCtx, node_id: uuid.UUID, batch_id: uuid.UUID, staged_name: str, include_system: bool
) -> ImportBatch:
    """Reads the staged archive's central directory and plans it. Raises OpError for a bad
    archive; the caller removes the staged file on any error."""
    root, node = setup_target(ctx, node_id)
    _no_open_setup_batch(ctx, root)
    try:
        items = inspect_zip(
            staged_path(ctx.settings, staged_name), max_entries=ctx.settings.max_import_entries
        )
    except ZipRefused as err:
        raise OpError(413 if err.code == "TOO_MANY_ENTRIES" else 422, err.code) from err
    raws = [
        RawEntry(i.filename, "dir" if i.is_dir else "file", i.size, i.mtime_ms, i.index, i.skip)
        for i in items
    ]
    planned = build_plan(raws, **_plan_args(ctx.settings, include_system, True))
    if planned.summary["bytes"] > ctx.settings.max_zip_uncompressed_bytes:
        raise OpError(413, "ZIP_TOO_LARGE")
    return _insert_batch(
        ctx,
        root=root,
        target=node,
        kind="setup_zip",
        status="planned",
        planned=planned,
        include_system=include_system,
        needed_bytes=planned.summary["bytes"],
        batch_id=batch_id,
        staged_zip=staged_name,
    )


def plan_folder_upload(
    ctx: OpCtx, node_id: uuid.UUID, raws: list[RawEntry], include_system: bool
) -> ImportBatch:
    node = ctx.db.get(Node, node_id)
    if node is None or Privilege.WRITE not in engine.effective(ctx.db, Actor(ctx.actor, session_id=ctx.session_id), node):
        raise _deny(ctx, node_id, "write")
    _usable(node, kind="dir")
    root = _root(ctx, node)
    _check_count(ctx, len(raws))
    # The count and the insert are serialized per person (one API process, ADR-013 §6a), so
    # concurrent requests cannot all pass the limit while each one is still being planned.
    with _user_lock(ctx.actor):
        return _plan_folder_upload_locked(ctx, root, node, raws, include_system)


_user_locks: dict[uuid.UUID, threading.Lock] = {}


def _user_lock(user_id: uuid.UUID) -> threading.Lock:
    with _locks_guard:
        return _user_locks.setdefault(user_id, threading.Lock())


def _plan_folder_upload_locked(
    ctx: OpCtx, root: StorageRoot, node: Node, raws: list[RawEntry], include_system: bool
) -> ImportBatch:
    unfinished = ctx.db.scalar(
        select(func.count())
        .select_from(ImportBatch)
        .where(
            ImportBatch.actor_user_id == ctx.actor,
            ImportBatch.kind == "folder_upload",
            ImportBatch.status.in_(OPEN_STATUSES),
        )
    )
    if (unfinished or 0) >= ctx.settings.max_open_folder_uploads:
        raise OpError(429, "TOO_MANY_OPEN_UPLOADS")
    planned = build_plan(raws, **_plan_args(ctx.settings, include_system, False))
    plan_space = _space(ctx.settings, planned.summary["bytes"])
    if not plan_space["fits"]:
        raise OpError(507, "INSUFFICIENT_SPACE")  # refused before the first byte is written
    batch = _insert_batch(
        ctx,
        root=root,
        target=node,
        kind="folder_upload",
        status="running",
        planned=planned,
        include_system=include_system,
        needed_bytes=planned.summary["bytes"],
    )
    maybe_finalize(ctx, batch.id)
    return batch


def start_batch(ctx: OpCtx, batch: ImportBatch) -> ImportBatch:
    """Confirms a planned setup import. The free-space check is repeated: time has passed."""
    if batch.status not in ("planned", "stopped"):
        raise OpError(409, "NOT_STARTABLE")
    authorize(ctx, batch)
    pending = (
        ctx.db.scalar(
            select(func.coalesce(func.sum(ImportEntry.size), 0)).where(
                ImportEntry.batch_id == batch.id,
                ImportEntry.kind == "file",
                ImportEntry.status.in_(("pending", "failed")),
            )
        )
        or 0
    )
    if not _space(ctx.settings, int(pending))["fits"]:
        raise OpError(507, "INSUFFICIENT_SPACE")
    now = clock.utcnow()
    ctx.db.execute(
        update(ImportEntry)
        .where(ImportEntry.batch_id == batch.id, ImportEntry.status == "failed")
        .values(status="pending", reason=None)
    )
    batch.status, batch.error_code, batch.updated_at = "running", None, now
    audit(
        ctx.db,
        "import.started",
        actor=ctx.actor,
        ip=ctx.ip,
        batch_id=str(batch.id),
        kind=batch.kind,
    )
    ctx.db.commit()
    if batch.kind == "setup_folder":
        maybe_finalize(ctx, batch.id)
    return batch


def cancel_batch(ctx: OpCtx, batch: ImportBatch) -> ImportBatch:
    """Stops a batch. Entries already committed stay where they are."""
    if batch.status in ("planned", "running", "stopped"):
        batch.status, batch.finished_at, batch.updated_at = (
            "cancelled",
            clock.utcnow(),
            clock.utcnow(),
        )
        audit(ctx.db, "import.cancelled", actor=ctx.actor, ip=ctx.ip, batch_id=str(batch.id))
        ctx.db.commit()
        delete_staged(ctx.settings, batch.staged_zip)
    return batch


def _stop_batch(db: Session, batch_id: uuid.UUID, code: str) -> None:
    db.rollback()
    db.execute(
        update(ImportEntry)
        .where(ImportEntry.batch_id == batch_id, ImportEntry.status.in_(("pending", "writing")))
        .values(status="failed", reason=code)
    )
    db.execute(
        update(ImportBatch)
        .where(ImportBatch.id == batch_id, ImportBatch.status.in_(OPEN_STATUSES))
        .values(status="stopped", error_code=code, updated_at=clock.utcnow())
    )
    db.commit()


def fail_batch(db: Session, settings: Settings, batch_id: uuid.UUID, code: str) -> None:
    """A batch-level limit was breached: committed entries stay, the rest is not attempted."""
    db.rollback()
    db.execute(
        update(ImportEntry)
        .where(ImportEntry.batch_id == batch_id, ImportEntry.status.in_(("pending", "writing")))
        .values(status="failed", reason=code)
    )
    batch = db.get(ImportBatch, batch_id)
    if batch is not None and batch.status in OPEN_STATUSES:
        batch.status, batch.error_code = "failed", code
        batch.updated_at = batch.finished_at = clock.utcnow()
        audit(db, "import.failed", actor=batch.actor_user_id, batch_id=str(batch_id), code=code)
        delete_staged(settings, batch.staged_zip)
    db.commit()


def counts_of(db: Session, batch_id: uuid.UUID) -> dict[str, int]:
    rows = db.execute(
        select(
            ImportEntry.kind,
            ImportEntry.status,
            func.count(),
            func.coalesce(func.sum(ImportEntry.size), 0),
        )
        .where(ImportEntry.batch_id == batch_id)
        .group_by(ImportEntry.kind, ImportEntry.status)
    ).all()
    out = {
        "pending": 0, "writing": 0, "done": 0, "renamed": 0, "skipped": 0, "failed": 0,
        "folders_done": 0, "bytes_done": 0, "bytes_total": 0,
    }  # fmt: skip
    for kind, status, count, size in rows:
        if kind == "dir":
            if status in ("done", "renamed"):
                out["folders_done"] += count
            continue
        out[status] += count
        if status in ("done", "renamed"):
            out["bytes_done"] += int(size or 0)
        if status != "skipped":
            out["bytes_total"] += int(size or 0)
    return out


def list_entries(
    db: Session, batch_id: uuid.UUID, *, status: str | None, after: int, limit: int
) -> list[ImportEntry]:
    query = select(ImportEntry).where(ImportEntry.batch_id == batch_id, ImportEntry.idx > after)
    if status is not None:
        query = query.where(ImportEntry.status == status)
    return list(db.scalars(query.order_by(ImportEntry.idx).limit(limit)))


def expire_stale(db: Session, settings: Settings, root_id: uuid.UUID | None = None) -> int:
    """Batches idle for `import_expiry_seconds` become `expired` and lose their staged archive."""
    cutoff = clock.utcnow().timestamp() - settings.import_expiry_seconds
    query = select(ImportBatch).where(ImportBatch.status.in_(OPEN_STATUSES))
    if root_id is not None:
        query = query.where(ImportBatch.root_id == root_id)
    count = 0
    for batch in db.scalars(query):
        if batch.updated_at.timestamp() < cutoff:
            batch.status, batch.finished_at = "expired", clock.utcnow()
            delete_staged(settings, batch.staged_zip)
            audit(db, "import.expired", actor=batch.actor_user_id, batch_id=str(batch.id))
            count += 1
    db.commit()
    return count


def resume_with_manifest(ctx: OpCtx, batch: ImportBatch, raws: list[RawEntry]) -> dict[str, Any]:
    """Matches a re-selected folder against the batch by (path, size, mtime) and returns only the
    files still to send. Files that changed since the first selection are not sent."""
    if batch.kind == "setup_zip":
        raise OpError(409, "NOT_RESUMABLE_BY_MANIFEST")
    if batch.status not in ("running", "stopped"):
        raise OpError(409, "NOT_RESUMABLE")  # a finished batch is never re-opened
    authorize(ctx, batch)
    _check_count(ctx, len(raws))
    planned = build_plan(raws, **_plan_args(ctx.settings, batch.include_system, False))
    stored: dict[str, list[ImportEntry]] = {}
    for entry in ctx.db.scalars(
        select(ImportEntry).where(ImportEntry.batch_id == batch.id, ImportEntry.kind == "file")
    ):
        stored.setdefault(entry.path, []).append(entry)
    pending: list[dict[str, Any]] = []
    unmatched = 0
    for wanted in planned.entries:
        if wanted.kind != "file" or wanted.status != "pending":
            continue
        candidates = stored.get(wanted.path) or []
        match = candidates.pop(0) if candidates else None
        if match is None or match.size != wanted.size or match.mtime_ms != wanted.mtime_ms:
            unmatched += 1
            continue
        if match.status in ("pending", "failed"):
            match.status, match.reason = "pending", None
            pending.append({"index": match.idx, "path": match.path, "size": match.size})
    batch.status, batch.error_code, batch.finished_at = "running", None, None
    batch.updated_at = clock.utcnow()
    audit(
        ctx.db,
        "import.resumed",
        actor=ctx.actor,
        ip=ctx.ip,
        batch_id=str(batch.id),
        kind=batch.kind,
        pending=len(pending),
        unmatched=unmatched,
    )
    ctx.db.commit()
    maybe_finalize(ctx, batch.id)
    return {"pending": pending, "unmatched": unmatched}


# ------------------------------------------------------------------ folders and files


_locks: dict[uuid.UUID, threading.RLock] = {}
_locks_guard = threading.Lock()


def _batch_lock(batch_id: uuid.UUID) -> threading.RLock:
    """Folder creation is serialized per batch (the API is one process, ADR-013 §6a), so parallel
    uploads into a new folder cannot each create it."""
    with _locks_guard:
        return _locks.setdefault(batch_id, threading.RLock())


def _mtime_ns(mtime_ms: int | None) -> int | None:
    if mtime_ms is None or mtime_ms <= 0:
        return None
    if mtime_ms > int(time.time() * 1000) + 24 * 3600 * 1000:
        return None  # a date in the future is not worth preserving
    return mtime_ms * 1_000_000


def _ensure_dir_node(ctx: OpCtx, batch: ImportBatch, entry_idx: int | None, target: Node) -> Node:
    """The folder node for a plan entry, merging into an existing active folder of the same name
    key or creating it. A name held by anything else (a file, a missing node) gets a keep-both
    name."""
    db = ctx.db
    if entry_idx is None:
        return target
    with _batch_lock(batch.id):
        entry = _entry(db, batch.id, entry_idx)
        if entry is None:
            raise OpError(409, "PARENT_CHANGED")
        if entry.node_id is not None:
            node = db.get(Node, entry.node_id)
            if node is None or node.status != "active" or node.kind != "dir":
                raise OpError(409, "PARENT_CHANGED")
            return node
        parent = _ensure_dir_node(ctx, batch, entry.parent_idx, target)
        _usable(parent, kind="dir")
        check_destination(ctx, batch, target, parent)
        root = _root(ctx, parent)
        key = name_key(entry.name)
        for existing in db.scalars(
            select(Node)
            .where(
                Node.parent_id == parent.id,
                Node.name_key == key,
                Node.status.not_in(HIDDEN_NODE_STATUSES),
            )
            .order_by(Node.created_at)
        ):
            if existing.kind == "dir" and existing.status == "active":
                entry.node_id, entry.result_name, entry.status = existing.id, existing.name, "done"
                db.commit()
                return existing
        comps = path_components(db, parent)
        for cand in _candidates(entry.name, "dir", "keep_both"):
            if _db_has(db, parent.id, cand):
                continue
            with _fs(ctx, root, comps, write=True) as dir_fd:
                try:
                    made = files.make_dir(dir_fd, cand)
                except files.NameExists:
                    continue  # taken on disk by something the database does not know yet
                try:
                    _parent_unchanged(ctx, parent)
                    node = _new_node(parent, cand, "dir", None)
                    _stamp(node, made)
                    db.add(node)
                    events.record(
                        db, node.id, "imported", source="import", actor_user_id=ctx.actor,
                        correlation_id=batch.id, name=cand, kind="dir",
                    )  # fmt: skip
                    fresh = _entry(db, batch.id, entry_idx)
                    if fresh is None:
                        raise OpError(409, "PARENT_CHANGED")
                    fresh.node_id, fresh.result_name = node.id, cand
                    fresh.status = "done" if cand == entry.name else "renamed"
                    close_window_for_upload(ctx, batch, root)
                    db.commit()
                except BaseException:
                    db.rollback()
                    _undo_entry(dir_fd, cand, "dir")
                    raise
                return node
        raise OpError(409, "NAME_CONFLICT")


def _parent_unchanged(ctx: OpCtx, parent: Node) -> None:
    """Final-transaction check shared by folder and file creation: the parent is still an active,
    idle folder, and nothing can rewrite its lineage before this transaction ends."""
    revision = parent.revision
    serialize_tree_changes(ctx.db)
    ctx.db.expire_all()
    ctx.db.refresh(parent)
    if parent.status != "active" or parent.revision != revision or not _chain_idle(ctx.db, parent):
        raise OpError(409, "PARENT_CHANGED")


@dataclass
class EntryResult:
    index: int
    status: str
    result_name: str | None
    node_id: uuid.UUID | None
    reason: str | None = None


def _result(entry: ImportEntry) -> EntryResult:
    return EntryResult(entry.idx, entry.status, entry.result_name, entry.node_id, entry.reason)


class EntryUpload:
    """Writes one file entry in three steps so a route can run each blocking part in a worker
    thread: `begin()`, repeated `write()`, then `finish()` (or `abort()`). `begin()` may leave
    `result` set when the entry was already complete, which makes a re-sent entry idempotent."""

    def __init__(
        self,
        ctx: OpCtx,
        batch_id: uuid.UUID,
        index: int,
        *,
        declared_length: int | None = None,
    ) -> None:
        self.ctx, self.batch_id, self.index = ctx, batch_id, index
        self.declared_length = declared_length
        self.result: EntryResult | None = None
        self._stack = ExitStack()
        self._stage: files.StagedWrite | None = None
        self._dir_fd = -1
        self._parent: Node | None = None
        self._owned = False
        self._expected: int | None = None
        self._mtime_ms: int | None = None
        self._name = ""

    # -- steps
    def begin(self) -> None:
        ctx, db = self.ctx, self.ctx.db
        batch = db.get(ImportBatch, self.batch_id)
        if batch is None or batch.actor_user_id != ctx.actor:
            raise OpError(403, "FORBIDDEN")
        entry = _entry(db, batch.id, self.index)
        if entry is None:
            raise OpError(404, "ENTRY_NOT_FOUND")
        if entry.kind != "file":
            raise OpError(422, "NOT_A_FILE")
        if entry.status in ("done", "renamed", "skipped"):
            self.result = _result(entry)  # a re-sent entry gets its result, even after the end
            return
        if batch.status != "running":
            raise OpError(409, "BATCH_NOT_RUNNING")
        if entry.status == "writing":
            raise OpError(409, "ENTRY_BUSY")
        try:
            root, target = authorize(ctx, batch)
        except OpError as err:
            if err.code in STOP_CODES:
                _stop_batch(db, batch.id, err.code)
            raise
        if (
            self.declared_length is not None
            and entry.size is not None
            and self.declared_length != entry.size
        ):
            self._fail_entry("SIZE_MISMATCH")
            raise OpError(409, "SIZE_MISMATCH")
        self._expected, self._mtime_ms, self._name = entry.size, entry.mtime_ms, entry.name
        _touch(db, batch.id)
        # Claimed before any folder is created, so the reconciler (which pauses a root only while
        # an entry is `writing`) never sees a folder that has no node yet.
        # A conditional update, so two requests sending the same entry cannot both claim it.
        claim = db.execute(
            update(ImportEntry)
            .where(
                ImportEntry.batch_id == batch.id,
                ImportEntry.idx == self.index,
                ImportEntry.status.in_(("pending", "failed")),
            )
            .values(status="writing", reason=None, dir_node_id=None, tmp_name=None, dst_name=None)
            .execution_options(synchronize_session=False)
        )
        db.commit()
        if not claim.rowcount:  # type: ignore[attr-defined]
            raise OpError(409, "ENTRY_BUSY")
        self._owned = True
        parent = _ensure_dir_node(ctx, batch, entry.parent_idx, target)
        _usable(parent, kind="dir")
        check_destination(ctx, batch, target, parent)
        comps = path_components(db, parent)
        self._dir_fd = self._stack.enter_context(_fs(ctx, _root(ctx, parent), comps, write=True))
        self._stage = files.StagedWrite(self._dir_fd)
        self._parent = parent
        claimed = _entry(db, batch.id, self.index)
        if claimed is None:
            raise OpError(404, "ENTRY_NOT_FOUND")
        claimed.dir_node_id, claimed.tmp_name = parent.id, self._stage.tmp
        db.commit()

    def write(self, chunk: bytes) -> None:
        stage = self._stage
        if stage is None:
            raise RuntimeError("entry not started")
        total = stage.size + len(chunk)
        if total > self.ctx.settings.max_upload_bytes:
            raise OpError(413, "UPLOAD_TOO_LARGE")
        if self._expected is not None and total > self._expected:
            raise OpError(409, "SIZE_MISMATCH")  # the file grew while it was being read
        try:
            stage.write(chunk)
        except OSError as exc:
            raise _map_error(exc) from exc

    def finish(self) -> EntryResult:
        ctx, db, stage, parent = self.ctx, self.ctx.db, self._stage, self._parent
        if stage is None or parent is None:
            raise RuntimeError("entry not started")
        try:
            if self._expected is not None and stage.size != self._expected:
                raise OpError(409, "SIZE_MISMATCH")  # the file changed while it was being read
            mtime = _mtime_ns(self._mtime_ms)
            if mtime is not None and stage.fd is not None:
                try:
                    os.utime(stage.fd, ns=(mtime, mtime))
                except OSError:
                    pass  # a timestamp is nice to keep, never worth failing the file
            batch = db.get(ImportBatch, self.batch_id)
            if batch is None:
                raise OpError(403, "FORBIDDEN")
            try:
                root, target = authorize(ctx, batch)  # the last look before the file is visible
            except OpError as err:
                if err.code in STOP_CODES:
                    _stop_batch(db, batch.id, err.code)
                raise
            check_destination(ctx, batch, target, parent)
            _touch(db, batch.id)
            chosen, info = self._publish(stage, parent)
            try:
                _parent_unchanged(ctx, parent)
                node = _new_node(parent, chosen, "file", info.st_size)
                _stamp(node, info, stage.sha256)
                db.add(node)
                events.record(
                    db, node.id, "imported", source="import", actor_user_id=ctx.actor,
                    correlation_id=self.batch_id, name=chosen, kind="file",
                )  # fmt: skip
                entry = _entry(db, self.batch_id, self.index)
                if entry is None:
                    raise OpError(404, "ENTRY_NOT_FOUND")
                entry.status = "done" if chosen == self._name else "renamed"
                entry.node_id, entry.result_name, entry.reason = node.id, chosen, None
                entry.tmp_name = entry.dst_name = None
                close_window_for_upload(ctx, batch, root)
                db.commit()
            except BaseException:
                db.rollback()
                _undo_entry(self._dir_fd, chosen, "file")
                raise
            result = _result(entry)
            self._owned = False
        finally:
            self.close()
        try:
            maybe_finalize(ctx, self.batch_id)
        except Exception:
            log.exception("finalizing an import failed")
        return result

    def _publish(self, stage: files.StagedWrite, parent: Node) -> tuple[str, os.stat_result]:
        db = self.ctx.db
        for cand in _candidates(self._name, "file", "keep_both"):
            if _db_has(db, parent.id, cand):
                continue
            entry = _entry(db, self.batch_id, self.index)
            if entry is not None:
                entry.dst_name = cand  # recorded first, so recovery can find a published file
                db.commit()
            try:
                return cand, stage.publish(cand)
            except files.NameExists:
                continue
        raise OpError(409, "NAME_CONFLICT")

    def _fail_entry(self, code: str) -> None:
        self.ctx.db.rollback()
        self.ctx.db.execute(
            update(ImportEntry)
            .where(
                ImportEntry.batch_id == self.batch_id,
                ImportEntry.idx == self.index,
                ImportEntry.status.in_(("pending", "failed")),
            )
            .values(status="failed", reason=code)
        )
        self.ctx.db.commit()

    def abort(self, code: str | None = None) -> None:
        """`None` puts the entry back to pending (the sender went away); a code records a failure
        the sender can retry on resume."""
        try:
            if not self._owned:
                if code is not None and code not in UNTOUCHED:
                    self._fail_entry(code)  # refused before it was claimed: say why, once
            else:
                self.ctx.db.rollback()
                self.ctx.db.execute(
                    update(ImportEntry)
                    .where(
                        ImportEntry.batch_id == self.batch_id,
                        ImportEntry.idx == self.index,
                        ImportEntry.status == "writing",
                    )
                    .values(
                        status="pending" if code is None else "failed",
                        reason=code,
                        tmp_name=None,
                        dst_name=None,
                    )
                )
                self.ctx.db.commit()
        finally:
            self.close()

    def close(self) -> None:
        if self._stage is not None:
            self._stage.abort()
            self._stage = None
        self._stack.close()


def maybe_finalize(ctx: OpCtx, batch_id: uuid.UUID) -> bool:
    """When no file is left to send: create the folders that stayed empty, then mark the batch
    done. Returns True if it is done."""
    db = ctx.db
    db.expire_all()
    batch = db.get(ImportBatch, batch_id)
    if batch is None or batch.status != "running":
        return False
    waiting = db.scalar(
        select(func.count())
        .select_from(ImportEntry)
        .where(
            ImportEntry.batch_id == batch_id,
            ImportEntry.kind == "file",
            ImportEntry.status.in_(("pending", "writing")),
        )
    )
    if waiting:
        return False
    try:
        _, target = authorize(ctx, batch)
    except OpError as err:
        if err.code in STOP_CODES:
            _stop_batch(db, batch_id, err.code)
        return False
    leftovers = list(
        db.scalars(
            select(ImportEntry.idx)
            .where(
                ImportEntry.batch_id == batch_id,
                ImportEntry.kind == "dir",
                ImportEntry.status == "pending",
            )
            .order_by(ImportEntry.idx)
        )
    )
    for idx in leftovers:  # the empty folders of the manifest, created at the end
        try:
            _ensure_dir_node(ctx, batch, idx, target)
        except OpError as err:
            db.rollback()
            db.execute(
                update(ImportEntry)
                .where(ImportEntry.batch_id == batch_id, ImportEntry.idx == idx)
                .values(status="failed", reason=err.code)
            )
            db.commit()
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        return False
    batch.status, batch.error_code = "done", None
    batch.updated_at = batch.finished_at = clock.utcnow()
    audit(db, "import.finished", actor=batch.actor_user_id, batch_id=str(batch_id), kind=batch.kind)
    db.commit()
    delete_staged(ctx.settings, batch.staged_zip)
    with _locks_guard:
        _locks.pop(batch_id, None)
    return True


# ------------------------------------------------------------------ zip extraction


class _BatchLimit(Exception):
    pass


ZIP_ENTRY_ERRORS: tuple[type[BaseException], ...] = (
    zipfile.BadZipFile,
    NotImplementedError,
    EOFError,
    RuntimeError,
    ValueError,
)


class ImportRunner:
    """Extracts staged archives in background threads of the API process (workers are optional,
    ADR-003). One thread per batch; progress is the `import_entry` rows."""

    def __init__(self, sessions: "sessionmaker[Session]", settings: Settings) -> None:
        self.sessions, self.settings = sessions, settings
        self.stop = threading.Event()
        self._threads: dict[uuid.UUID, threading.Thread] = {}
        self._lock = threading.Lock()

    def start_zip(self, batch_id: uuid.UUID) -> bool:
        with self._lock:
            thread = self._threads.get(batch_id)
            if thread is not None and thread.is_alive():
                return False
            thread = threading.Thread(
                target=self._run, args=(batch_id,), name="fc-zip-import", daemon=True
            )
            self._threads[batch_id] = thread
            thread.start()
            return True

    def wait(self, batch_id: uuid.UUID, timeout: float = 60.0) -> bool:
        thread = self._threads.get(batch_id)
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def shutdown(self) -> None:
        self.stop.set()
        for thread in list(self._threads.values()):
            thread.join(timeout=5)

    def _run(self, batch_id: uuid.UUID) -> None:
        try:
            self._extract(batch_id)
        except Exception:
            log.exception("zip import crashed")
            with self.sessions() as db:
                _stop_batch(db, batch_id, "INTERRUPTED")

    def _next_entry(self, batch_id: uuid.UUID, after: int) -> tuple[int, int | None, int] | None:
        with self.sessions() as db:
            batch = db.get(ImportBatch, batch_id)
            if batch is None or batch.status != "running":
                return None
            row = db.execute(
                select(ImportEntry.idx, ImportEntry.src_index, ImportEntry.size)
                .where(
                    ImportEntry.batch_id == batch_id,
                    ImportEntry.kind == "file",
                    ImportEntry.status == "pending",
                    ImportEntry.idx > after,
                )
                .order_by(ImportEntry.idx)
                .limit(1)
            ).first()
            return (row[0], row[1], row[2] or 0) if row else (-1, None, 0)

    def _extract(self, batch_id: uuid.UUID) -> None:
        settings = self.settings
        with self.sessions() as db:
            batch = db.get(ImportBatch, batch_id)
            if batch is None or batch.status != "running" or batch.staged_zip is None:
                return
            actor, staged = batch.actor_user_id, staged_path(settings, batch.staged_zip)
            total = counts_of(db, batch_id)["bytes_done"]
        try:
            archive = zipfile.ZipFile(staged)
        except (zipfile.BadZipFile, OSError):
            with self.sessions() as db:
                fail_batch(db, settings, batch_id, "INVALID_ZIP")
            return
        with archive:
            infos = archive.infolist()
            cursor = -1
            while not self.stop.is_set():
                nxt = self._next_entry(batch_id, cursor)
                if nxt is None:
                    return  # cancelled, stopped or finished elsewhere
                idx, src, _ = nxt
                if idx < 0:  # no more pending files
                    break
                cursor = idx
                with self.sessions() as db:
                    ctx = OpCtx(db, settings, actor)
                    try:
                        total = self._one(ctx, archive, infos, batch_id, idx, src, total)
                    except _BatchLimit:
                        fail_batch(db, settings, batch_id, "ZIP_TOO_LARGE")
                        return
                    stopped = db.get(ImportBatch, batch_id)
                    if stopped is not None and stopped.status != "running":
                        return
            if self.stop.is_set():
                return
        with self.sessions() as db:
            maybe_finalize(OpCtx(db, settings, actor), batch_id)

    def _one(
        self,
        ctx: OpCtx,
        archive: zipfile.ZipFile,
        infos: list[zipfile.ZipInfo],
        batch_id: uuid.UUID,
        idx: int,
        src: int | None,
        total: int,
    ) -> int:
        settings = self.settings
        up = EntryUpload(ctx, batch_id, idx)
        try:
            if src is None or src >= len(infos):
                raise OpError(422, "ZIP_CORRUPT")
            info = infos[src]
            up.begin()
            if up.result is None:
                with archive.open(info) as stream:
                    for chunk in limited_chunks(
                        stream,
                        compress_size=info.compress_size,
                        max_bytes=settings.max_upload_bytes,
                        max_ratio=settings.max_zip_ratio,
                    ):
                        total += len(chunk)
                        if total > settings.max_zip_uncompressed_bytes:
                            raise _BatchLimit
                        up.write(chunk)
                up.finish()
        except _BatchLimit:
            up.abort("ZIP_TOO_LARGE")
            raise
        except EntryLimit as err:
            up.abort(err.code)
        except OpError as err:
            up.abort(err.code if err.code not in STOP_CODES else None)
        except ZIP_ENTRY_ERRORS:
            up.abort("ZIP_CORRUPT")
        except BaseException:
            up.abort(None)
            raise
        return total


# ------------------------------------------------------------------ listing and retention

PRUNABLE = ("done", "cancelled", "expired")


def unfinished_batches(db: Session, actor_id: uuid.UUID, limit: int = 50) -> list[ImportBatch]:
    """The caller's own batches that are not finished, newest first."""
    return list(
        db.scalars(
            select(ImportBatch)
            .where(ImportBatch.actor_user_id == actor_id, ImportBatch.status.in_(OPEN_STATUSES))
            .order_by(ImportBatch.created_at.desc())
            .limit(limit)
        )
    )


def prune_import_reports(db: Session, settings: Settings, limit: int = 100) -> int:
    """Deletes the `import_entry` rows (the per-file report) of batches that completed more than
    `import_entry_retention_days` ago. The batch summary row stays indefinitely. A batch that is
    stopped, failed or still open is never pruned, nor is one with an unresolved failed entry.
    Returns the number of batches pruned."""
    cutoff = clock.utcnow() - timedelta(days=settings.import_entry_retention_days)
    failed = exists().where(ImportEntry.batch_id == ImportBatch.id, ImportEntry.status == "failed")
    ids = list(
        db.scalars(
            select(ImportBatch.id)
            .where(
                ImportBatch.status.in_(PRUNABLE),
                ImportBatch.entries_pruned_at.is_(None),
                ImportBatch.finished_at.is_not(None),
                ImportBatch.finished_at < cutoff,
                ~failed,
            )
            .order_by(ImportBatch.finished_at)
            .limit(limit)
        )
    )
    for batch_id in ids:
        removed = db.execute(delete(ImportEntry).where(ImportEntry.batch_id == batch_id))
        db.execute(
            update(ImportBatch)
            .where(ImportBatch.id == batch_id)
            .values(entries_pruned_at=clock.utcnow())
        )
        audit(
            db,
            "import.reports_pruned",
            batch_id=str(batch_id),
            entries=int(removed.rowcount or 0),  # type: ignore[attr-defined]
        )
        db.commit()
    return len(ids)


# ------------------------------------------------------------------ startup recovery


def _recover_entry(db: Session, settings: Settings, entry: ImportEntry) -> str:
    """An entry left `writing` by a crash: remove its temporary file, and adopt the file if it was
    already published under the recorded name; otherwise it goes back to `pending`."""
    parent = db.get(Node, entry.dir_node_id) if entry.dir_node_id else None
    root = db.get(StorageRoot, parent.root_id) if parent is not None else None
    outcome = "requeued"
    if parent is not None and root is not None:
        try:
            with files_dir(settings, root, db, parent) as dir_fd:
                if entry.tmp_name and entry.tmp_name.startswith(files.TMP_PREFIX):
                    try:
                        os.unlink(entry.tmp_name, dir_fd=dir_fd)
                    except FileNotFoundError:
                        pass
                outcome = _adopt(db, entry, parent, dir_fd)
        except Exception as exc:
            log.warning("could not inspect an interrupted import entry: %s", type(exc).__name__)
    if outcome == "requeued":
        entry.status = "pending"
    entry.tmp_name = entry.dst_name = None
    return outcome


def files_dir(settings: Settings, root: StorageRoot, db: Session, parent: Node) -> Any:
    from app.storage.resolver import open_dir

    return open_dir(settings.library_path, root.relative_dir, path_components(db, parent))


def _adopt(db: Session, entry: ImportEntry, parent: Node, dir_fd: int) -> str:
    name = entry.dst_name
    if not name or entry.size is None:
        return "requeued"
    info = files.lstat_entry(dir_fd, name)
    if info is None or info.st_size != entry.size or info.st_nlink != 1:
        return "requeued"
    if db.scalar(select(Node.id).where(Node.parent_id == parent.id, Node.name == name)):
        return "requeued"  # a node already holds that name: not ours
    try:
        digest, size = files.hash_file(dir_fd, name)
    except (OSError, StorageError):
        return "requeued"
    if size != entry.size:
        return "requeued"
    node = _new_node(parent, name, "file", size)
    _stamp(node, os.stat(name, dir_fd=dir_fd, follow_symlinks=False), digest)
    db.add(node)
    batch = db.get(ImportBatch, entry.batch_id)
    events.record(
        db, node.id, "imported", source="import",
        actor_user_id=batch.actor_user_id if batch else None,
        actor_kind="user" if batch else "system", correlation_id=entry.batch_id,
        name=name, kind="file",
    )  # fmt: skip
    entry.status = "done" if name == entry.name else "renamed"
    entry.node_id, entry.result_name = node.id, name
    if batch is not None and batch.kind not in SETUP_KINDS:
        setup.close_window(db, parent.root_id, "folder_upload", actor=batch.actor_user_id)
    return "adopted"


def recover_imports(sessions: "sessionmaker[Session]", settings: Settings) -> dict[str, int]:
    """Startup pass: interrupted entries are requeued (or adopted), interrupted archive
    extractions stop so they can be resumed, stale batches expire, and old staged archives that no
    batch uses are deleted."""
    counts = {"requeued": 0, "adopted": 0, "interrupted": 0, "expired": 0, "staged_removed": 0}
    with sessions() as db:
        counts["expired"] = expire_stale(db, settings)
        prune_import_reports(db, settings)
        for entry in list(db.scalars(select(ImportEntry).where(ImportEntry.status == "writing"))):
            try:
                counts[_recover_entry(db, settings, entry)] += 1
                db.commit()
            except Exception:
                db.rollback()
                log.exception("recovery of an import entry failed")
        interrupted = db.execute(
            update(ImportBatch)
            .where(ImportBatch.status == "running", ImportBatch.kind == "setup_zip")
            .values(status="stopped", error_code="INTERRUPTED", updated_at=clock.utcnow())
        )
        counts["interrupted"] = int(interrupted.rowcount or 0)  # type: ignore[attr-defined]
        db.commit()
        used = set(
            db.scalars(
                select(ImportBatch.staged_zip).where(
                    ImportBatch.status.in_(OPEN_STATUSES), ImportBatch.staged_zip.is_not(None)
                )
            )
        )
    directory = staging_dir(settings)
    if directory.is_dir():
        limit = time.time() - STALE_STAGING_SECONDS
        for path in directory.iterdir():
            try:
                if path.name not in used and path.stat().st_mtime < limit:
                    path.unlink()
                    counts["staged_removed"] += 1
            except OSError:
                continue
    return counts


def iter_manifest(entries: Any) -> Iterator[RawEntry]:
    for item in entries:
        yield RawEntry(item.path, item.kind, item.size, item.mtime)
