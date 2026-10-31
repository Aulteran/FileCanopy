"""One backup run of one root (ADR-015 sections 3 and 5).

Layout of an archive (`tar.gz`, optionally encrypted, one per root and run):

    meta/run.json        chain, sequence, kind, time
    meta/deleted.txt     paths removed since the previous run (directories end with "/")
    meta/missing.txt     paths that went missing outside the app (their last copy is kept)
    meta/metadata.json   nodes, grants, the users they name and events (no secrets)
    files/...            the files and directories of this run (all of them in a full run)
    meta/manifest.json   size, mtime and SHA-256 of every member of `files/`, written last

Deletions come before the files so a chain restores with plain `tar`: for each archive in order,
apply `meta/deleted.txt`, then extract. The list of what the previous run contained is kept in the
database (`backup_chain_state`); the archives are never read to plan a run.

Files are read only through the no-symlink resolver; symlinks, special files, hardlinked files,
mounted drives, unsupported entries and staging files are never read. A root whose scan is
suspended or that is not active is not backed up. Logs and audit rows carry ids and counts only.
"""

import contextlib
import errno
import hashlib
import io
import json
import logging
import os
import secrets
import stat
import tarfile
import threading
import time
import uuid
import zlib
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth import clock
from app.auth.service import audit
from app.backup import crypto, retention
from app.backup.store import BackupConfig, KeyInfo, load_config, load_key
from app.backup.volume import backups_root, inspect_volume
from app.config import Settings
from app.db.models import (
    BackupChainState,
    BackupRun,
    FileEvent,
    Node,
    NodeGrant,
    StorageRoot,
    User,
)
from app.domain import nodes as domain_nodes
from app.storage.files import TMP_PREFIX, UnsafeEntry, open_regular
from app.storage.names import InvalidName, existing_name
from app.storage.resolver import StorageError, open_dir

log = logging.getLogger("filecanopy.backup")

EVENT_CAP = 100_000
Fingerprint = tuple[int, int, int]  # size, mtime_ns, inode
TMP_SUFFIX = ".part"


@dataclass
class RunResult:
    status: str  # ok | failed | refused | skipped
    run_id: uuid.UUID | None = None
    code: str | None = None


def root_label(root: StorageRoot) -> str:
    return root.relative_dir.replace("/", "-")


# ------------------------------------------------------------------ small helpers


class Throttle:
    """Keeps the average read rate under `rate` bytes per second (0 = unlimited)."""

    def __init__(self, rate: int) -> None:
        self.rate, self.start, self.total = rate, time.monotonic(), 0

    def consume(self, n: int) -> None:
        if self.rate <= 0:
            return
        self.total += n
        ahead = self.total / self.rate - (time.monotonic() - self.start)
        if ahead > 0.05:
            time.sleep(min(ahead, 1.0))


class HashingSink:
    """Counts and hashes what reaches the disk, so the written file can be re-checked."""

    def __init__(self, target: BinaryIO) -> None:
        self.target, self.size, self.sha = target, 0, hashlib.sha256()
        self.discard = False

    def write(self, data: bytes | bytearray | memoryview) -> int:
        if self.discard:
            return len(data)
        self.target.write(data)
        self.sha.update(data)
        self.size += len(data)
        return len(data)

    def flush(self) -> None:
        self.target.flush()


class DirCache:
    """A few open directory descriptors for the files being read (sorted order keeps it cheap)."""

    def __init__(
        self,
        library: Path,
        relative_dir: str,
        limit: int = 16,
        *,
        prefix: tuple[str, ...] = (),
        create: bool = False,
    ) -> None:
        self.library, self.relative_dir, self.limit = library, relative_dir, limit
        self.prefix, self.create = prefix, create
        self._open: dict[tuple[str, ...], tuple[contextlib.ExitStack, int]] = {}

    def get(self, comps: tuple[str, ...]) -> int:
        hit = self._open.pop(comps, None)
        if hit is None:
            stack = contextlib.ExitStack()
            fd = stack.enter_context(
                open_dir(
                    self.library, self.relative_dir, (*self.prefix, *comps), create=self.create
                )
            )
            hit = (stack, fd)
            if len(self._open) >= self.limit:
                oldest = next(iter(self._open))
                self._open.pop(oldest)[0].close()
        self._open[comps] = hit
        return hit[1]

    def forget(self) -> None:
        """Closes every cached descriptor (after directories were removed)."""
        self.close()

    def close(self) -> None:
        for stack, _ in self._open.values():
            stack.close()
        self._open.clear()


def encode_snapshot(files: dict[str, Fingerprint], dirs: set[str]) -> bytes:
    body = {"f": {p: list(fp) for p, fp in files.items()}, "d": sorted(dirs)}
    return zlib.compress(json.dumps(body, separators=(",", ":")).encode(), 6)


def decode_snapshot(blob: bytes) -> tuple[dict[str, Fingerprint], set[str]]:
    body = json.loads(zlib.decompress(blob))
    files = {p: (int(v[0]), int(v[1]), int(v[2])) for p, v in body["f"].items()}
    return files, set(body["d"])


def path_key(path: str) -> list[str]:
    return path.split("/")


def _ancestors(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]


def _under(path: str, prefixes: set[str]) -> bool:
    return any(a in prefixes for a in _ancestors(path))


# ------------------------------------------------------------------ the tree to back up


@dataclass
class Tree:
    dirs: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)
    alive_prefixes: set[str] = field(default_factory=set)  # busy or reconcile_needed
    missing_prefixes: set[str] = field(default_factory=set)  # status missing


def collect_tree(db: Session, root: StorageRoot) -> Tree:
    """Eligible entries from the database. Names are checked again: a corrupted row cannot
    become a traversal. Unsupported entries, special files and staging files are not eligible."""
    rows = db.execute(
        select(
            Node.id, Node.parent_id, Node.name, Node.kind, Node.status, Node.unsupported_reason
        ).where(Node.root_id == root.id)
    ).all()
    children: dict[uuid.UUID | None, list[Any]] = defaultdict(list)
    for r in rows:
        children[r.parent_id].append(r)
    tree = Tree()
    start = children.get(None, [])
    stack: list[tuple[Any, str]] = [(child, "") for r in start for child in children[r.id]]
    while stack:
        r, parent = stack.pop()
        try:
            name = existing_name(r.name)
        except InvalidName:
            continue
        if name.startswith(TMP_PREFIX) or name == ".":
            continue
        path = f"{parent}/{name}" if parent else name
        if r.status in ("busy", "reconcile_needed"):
            tree.alive_prefixes.add(path)
            continue
        if r.status == "missing":
            tree.missing_prefixes.add(path)
            continue
        if r.status != "active" or r.unsupported_reason is not None:
            continue
        if r.kind == "dir":
            tree.dirs.add(path)
            stack.extend((child, path) for child in children.get(r.id, []))
        elif r.kind == "file":
            tree.files.add(path)
    return tree


# ------------------------------------------------------------------ reading files


class UnstableFile(Exception):
    """The file changed while it was being read."""


class StreamSource:
    """A file object for `tarfile.addfile` that reads exactly `size` bytes from a descriptor,
    hashing and throttling as it goes. A file that shrinks is zero-padded and flagged."""

    def __init__(self, fd: int, size: int, throttle: Throttle) -> None:
        self.fd, self.remaining, self.throttle = fd, size, throttle
        self.sha = hashlib.sha256()
        self.torn = False

    def read(self, n: int = -1) -> bytes:
        if self.remaining <= 0:
            return b""
        want = self.remaining if n < 0 else min(n, self.remaining, 1024 * 1024)
        pieces: list[bytes] = []
        got = 0
        while got < want:  # tarfile needs full blocks: keep reading until `want` or end of file
            piece = os.read(self.fd, want - got)
            if not piece:  # shrank under us
                self.torn = True
                pieces.append(b"\0" * (want - got))
                break
            pieces.append(piece)
            got += len(piece)
        data = b"".join(pieces)
        self.remaining -= len(data)
        self.sha.update(data)
        self.throttle.consume(len(data))
        return data


def read_stable(fd: int, info: os.stat_result, throttle: Throttle) -> bytes:
    """Whole content of a small file, or UnstableFile if it changed during the read."""
    size = info.st_size
    chunks: list[bytes] = []
    got = 0
    while got < size + 1:
        piece = os.read(fd, min(1024 * 1024, size + 1 - got))
        if not piece:
            break
        chunks.append(piece)
        got += len(piece)
    after = os.fstat(fd)
    if got != size or after.st_size != size or after.st_mtime_ns != info.st_mtime_ns:
        raise UnstableFile
    throttle.consume(got)
    return b"".join(chunks)


# ------------------------------------------------------------------ the engine


def fingerprint_of(info: os.stat_result) -> Fingerprint:
    return (info.st_size, info.st_mtime_ns, info.st_ino)


def _tar_file_info(path: str, size: int, mtime_ns: int) -> tarfile.TarInfo:
    ti = tarfile.TarInfo("files/" + path)
    ti.size, ti.mtime, ti.mode = size, mtime_ns // 1_000_000_000, 0o644
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    return ti


def _tar_dir_info(path: str) -> tarfile.TarInfo:
    ti = tarfile.TarInfo("files/" + path)
    ti.type, ti.mode, ti.mtime = tarfile.DIRTYPE, 0o755, int(time.time())
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    return ti


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    ti = tarfile.TarInfo(name)
    ti.size, ti.mtime, ti.mode = len(data), int(time.time()), 0o644
    tar.addfile(ti, io.BytesIO(data))


class BackupEngine:
    def __init__(self, sessions: "sessionmaker[Session]", settings: Settings) -> None:
        self.sessions, self.settings = sessions, settings
        self.stop = threading.Event()
        self._root_locks: dict[uuid.UUID, threading.Lock] = defaultdict(threading.Lock)

    # -- housekeeping
    def recover_interrupted(self) -> int:
        """Runs left `running` by a crash become `failed`; their partial files are removed."""
        count = 0
        with self.sessions() as db:
            try:
                base = backups_root(self.settings)
            except RuntimeError:
                return 0
            for run in db.scalars(select(BackupRun).where(BackupRun.status == "running")):
                if run.archive_name:
                    archive = base / run.archive_name
                    for victim in (archive, archive.with_name(archive.name + ".json")):
                        victim.unlink(missing_ok=True)
                    self._sweep_tmp(archive.parent)
                run.status, run.error_code, run.finished_at = (
                    "failed",
                    "INTERRUPTED",
                    clock.utcnow(),
                )
                count += 1
            db.commit()
            if base.exists():  # stray temporary files of runs that never recorded a row
                for chain_dir in base.glob("*/*"):
                    if chain_dir.is_dir():
                        self._sweep_tmp(chain_dir)
        return count

    @staticmethod
    def _sweep_tmp(directory: Path) -> None:
        if not directory.is_dir():
            return
        for entry in directory.iterdir():
            if entry.name.startswith(".tmp-"):
                entry.unlink(missing_ok=True)
        try:
            directory.rmdir()  # a chain directory that never got an archive
        except OSError:
            pass

    # -- the run
    def run_root(self, root_id: uuid.UUID) -> RunResult:
        lock = self._root_locks[root_id]
        if not lock.acquire(blocking=False):
            return RunResult("skipped", code="RUN_IN_PROGRESS")
        try:
            return self._run(root_id)
        finally:
            lock.release()

    def _refuse(
        self, root_id: uuid.UUID, kind: str, code: str, cfg: BackupConfig, key: KeyInfo | None
    ) -> RunResult:
        now = clock.utcnow()
        with self.sessions() as db:
            run = BackupRun(
                root_id=root_id,
                chain_id="-",
                seq=0,
                kind=kind,
                status="refused",
                started_at=now,
                finished_at=now,
                error_code=code,
                encrypted=cfg.encrypt,
                key_fingerprint=key.fingerprint if key and cfg.encrypt else None,
            )
            db.add(run)
            audit(db, "backup.refused", root_id=str(root_id), code=code)
            db.commit()
            log.warning("backup of root %s refused: %s", root_id, code)
            return RunResult("refused", run.id, code)

    def _run(self, root_id: uuid.UUID) -> RunResult:
        settings = self.settings
        with self.sessions() as db:
            cfg, key = load_config(db), load_key(db)
            root = db.get(StorageRoot, root_id)
            if root is None or root.status != "active" or root.scan_state is not None:
                return RunResult("skipped", code="ROOT_NOT_ACTIVE")
            if root.scan_paused_since is not None:
                return RunResult("skipped", code="IMPORT_WRITING")
            state = db.get(BackupChainState, root_id)
            prior_files: dict[str, Fingerprint] = {}
            prior_dirs: set[str] = set()
            kind = "full"
            if state is not None:
                try:
                    exists = (backups_root(settings) / state.last_archive).is_file()
                except RuntimeError:
                    exists = False
                wanted_fp = key.fingerprint if cfg.encrypt and key else None
                if (
                    exists
                    and state.encrypted == cfg.encrypt
                    and state.key_fingerprint == wanted_fp
                    and state.seq < cfg.full_every
                ):
                    kind = "incremental"
                    prior_files, prior_dirs = decode_snapshot(state.snapshot)
            vol = inspect_volume(settings)
            if not vol.usable:
                return self._refuse(root_id, kind, "BACKUP_VOLUME_UNAVAILABLE", cfg, key)
            if cfg.encrypt and key is None:
                return self._refuse(root_id, kind, "BACKUP_KEY_MISSING", cfg, key)
            tree = collect_tree(db, root)
            relative_dir, label = root.relative_dir, root_label(root)

        # -- plan: fingerprints of what exists now (read only, no content)
        library = settings.library_path
        cache = DirCache(library, relative_dir)
        current: dict[str, Fingerprint] = {}
        unreadable: set[str] = set()
        try:
            for path in sorted(tree.files, key=path_key):
                *dirs, name = path.split("/")
                try:
                    dir_fd = cache.get(tuple(dirs))
                    info = os.stat(existing_name(name), dir_fd=dir_fd, follow_symlinks=False)
                except (OSError, StorageError, InvalidName):
                    unreadable.add(path)
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                    unreadable.add(path)
                    continue
                current[path] = fingerprint_of(info)
        finally:
            cache.close()

        carried_files = {
            p: fp
            for p, fp in prior_files.items()
            if p not in current
            and (
                p in unreadable
                or _under(p, tree.alive_prefixes)
                or _under(p, tree.missing_prefixes)
            )
        }
        carried_dirs = {
            d
            for d in prior_dirs
            if d not in tree.dirs and _under(d, tree.alive_prefixes | tree.missing_prefixes)
        }
        missing = sorted(
            p
            for p in set(prior_files) | prior_dirs
            if p not in current
            and p not in tree.dirs
            and (p in unreadable or _under(p, tree.missing_prefixes))
        )
        if kind == "full":
            include = sorted(current, key=path_key)
            added, changed = len(include), 0
            include_dirs = sorted(tree.dirs)
            deleted: list[str] = []
        else:
            include = sorted(
                (p for p, fp in current.items() if prior_files.get(p) != fp), key=path_key
            )
            added = sum(1 for p in include if p not in prior_files)
            changed = len(include) - added
            include_dirs = sorted(tree.dirs - prior_dirs)
            gone_files = [p for p in prior_files if p not in current and p not in carried_files]
            gone_dirs = [
                d + "/" for d in prior_dirs if d not in tree.dirs and d not in carried_dirs
            ]
            deleted = sorted(gone_files + gone_dirs)

        raw_bytes = sum(current[p][0] for p in include)
        now = clock.utcnow()
        with self.sessions() as db:
            cfg = load_config(db)
            estimate = retention.estimate_run_bytes(
                db, root_id, raw_bytes, len(include) + len(include_dirs)
            )
            verdict = retention.check_feasible(
                db, settings, cfg, root_id=root_id, kind=kind, estimate_bytes=estimate
            )
            if not verdict.ok:
                return self._refuse(root_id, kind, verdict.code or "BACKUP_REFUSED", cfg, key)
            seq = 1 if kind == "full" or state is None else state.seq + 1
            chain_id = (
                f"{now:%Y%m%dT%H%M%SZ}-{secrets.token_hex(2)}"
                if kind == "full" or state is None
                else state.chain_id
            )
            ext = ".tar.gz.fcb" if cfg.encrypt else ".tar.gz"
            archive_rel = f"{label}/{chain_id}/{seq:04d}-{kind}{ext}"
            run = BackupRun(
                root_id=root_id,
                chain_id=chain_id,
                seq=seq,
                kind=kind,
                status="running",
                started_at=now,
                archive_name=archive_rel,
                encrypted=cfg.encrypt,
                key_fingerprint=key.fingerprint if key and cfg.encrypt else None,
            )
            db.add(run)
            db.commit()
            run_id = run.id

        try:
            outcome = self._write(
                run_id=run_id,
                root_id=root_id,
                relative_dir=relative_dir,
                cfg=cfg,
                key=key,
                kind=kind,
                chain_id=chain_id,
                seq=seq,
                archive_rel=archive_rel,
                include=include,
                include_dirs=include_dirs,
                current=current,
                deleted=deleted,
                missing=missing,
                added=added,
                changed=changed,
                prior_files=prior_files,
                carried_files=carried_files,
                carried_dirs=carried_dirs,
                tree=tree,
                state=state,
            )
        except Exception as exc:  # never leaves a partial archive or a half-recorded run
            code = (
                "VOLUME_FULL"
                if isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT)
                else "BACKUP_FAILED"
            )
            self._fail(run_id, archive_rel, code)
            log.error("backup run failed (%s): %s", code, type(exc).__name__)  # no paths
            return RunResult("failed", run_id, code)
        return outcome

    def _fail(self, run_id: uuid.UUID, archive_rel: str, code: str) -> None:
        try:
            base = backups_root(self.settings)
            archive = base / archive_rel
            archive.unlink(missing_ok=True)
            archive.with_name(archive.name + ".json").unlink(missing_ok=True)
            self._sweep_tmp(archive.parent)
        except (OSError, RuntimeError):
            pass
        with self.sessions() as db:
            run = db.get(BackupRun, run_id)
            if run is not None:
                run.status, run.error_code, run.finished_at = "failed", code, clock.utcnow()
                audit(db, "backup.failed", root_id=str(run.root_id), code=code)
                db.commit()

    def _metadata(self, db: Session, root_id: uuid.UUID, after_event: int) -> tuple[bytes, int]:
        node_rows = db.execute(
            select(
                Node.id, Node.parent_id, Node.name, Node.kind, Node.status, Node.size, Node.revision
            ).where(Node.root_id == root_id)
        ).all()
        node_ids = [r.id for r in node_rows]
        grants = (
            db.execute(
                select(
                    NodeGrant.node_id,
                    NodeGrant.grantee_user_id,
                    NodeGrant.level,
                    NodeGrant.granted_by,
                ).where(NodeGrant.node_id.in_(node_ids))
            ).all()
            if node_ids
            else []
        )
        root = db.get(StorageRoot, root_id)
        user_ids = {g.grantee_user_id for g in grants} | {
            g.granted_by for g in grants if g.granted_by
        }
        if root is not None and root.owner_user_id:
            user_ids.add(root.owner_user_id)
        users = db.execute(
            select(User.id, User.email, User.name, User.role, User.status).where(
                User.id.in_(user_ids)
            )
        ).all()  # never the password hash, sessions or tokens
        events = (
            db.execute(
                select(FileEvent)
                .join(Node, Node.id == FileEvent.node_id)
                .where(Node.root_id == root_id, FileEvent.id > after_event)
                .order_by(FileEvent.id)
                .limit(EVENT_CAP)
            )
            .scalars()
            .all()
        )
        high = events[-1].id if events else after_event
        body = {
            "format": 1,
            "root": {"id": str(root_id), "kind": root.kind if root else None},
            "nodes": [
                {
                    "id": str(r.id),
                    "parent_id": str(r.parent_id) if r.parent_id else None,
                    "name": r.name,
                    "kind": r.kind,
                    "status": r.status,
                    "size": r.size,
                    "revision": r.revision,
                }
                for r in node_rows
            ],
            "grants": [
                {
                    "node_id": str(g.node_id),
                    "grantee_user_id": str(g.grantee_user_id),
                    "level": g.level,
                    "granted_by": str(g.granted_by) if g.granted_by else None,
                }
                for g in grants
            ],
            "users": [
                {
                    "id": str(u.id),
                    "email": u.email,
                    "name": u.name,
                    "role": u.role,
                    "status": u.status,
                }
                for u in users
            ],
            "events": [
                {
                    "id": e.id,
                    "node_id": str(e.node_id),
                    "at": e.at.isoformat(),
                    "actor_kind": e.actor_kind,
                    "actor_user_id": str(e.actor_user_id) if e.actor_user_id else None,
                    "action": e.action,
                    "source": e.source,
                    "details": e.details,
                }
                for e in events
            ],
            "settings": {"shared_default_access": domain_nodes.shared_default_access(db)},
        }
        return json.dumps(body, separators=(",", ":")).encode(), high

    def _write(
        self,
        *,
        run_id: uuid.UUID,
        root_id: uuid.UUID,
        relative_dir: str,
        cfg: BackupConfig,
        key: KeyInfo | None,
        kind: str,
        chain_id: str,
        seq: int,
        archive_rel: str,
        include: list[str],
        include_dirs: list[str],
        current: dict[str, Fingerprint],
        deleted: list[str],
        missing: list[str],
        added: int,
        changed: int,
        prior_files: dict[str, Fingerprint],
        carried_files: dict[str, Fingerprint],
        carried_dirs: set[str],
        tree: Tree,
        state: BackupChainState | None,
    ) -> RunResult:
        settings = self.settings
        base = backups_root(settings, create=True)
        archive = base / archive_rel
        archive.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = archive.parent / f".tmp-{secrets.token_hex(8)}{TMP_SUFFIX}"
        with self.sessions() as db:
            after_event = state.event_high_water if state and kind == "incremental" else 0
            metadata, high_water = self._metadata(db, root_id, after_event)
        created = clock.utcnow()
        throttle = Throttle(settings.backup_rate_bytes)
        manifest: dict[str, dict[str, Any]] = {}
        unstable: list[str] = []
        unreadable_now: list[str] = []
        stored: dict[str, Fingerprint] = {}
        uncompressed = 0

        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        raw = os.fdopen(fd, "wb")
        sink = HashingSink(raw)
        writer: crypto.EncryptingWriter | None = None
        file_key: bytes | None = None
        try:
            target: Any = sink
            if cfg.encrypt:
                if key is None:
                    raise RuntimeError("no key")
                writer = crypto.EncryptingWriter(sink, crypto.parse_public_key(key.public))
                file_key = writer.file_key
                target = writer
            tar = tarfile.open(
                fileobj=target, mode="w:gz", compresslevel=6, format=tarfile.PAX_FORMAT
            )
            run_meta = {
                "format": 1,
                "root_id": str(root_id),
                "chain_id": chain_id,
                "seq": seq,
                "kind": kind,
                "created_at": created.isoformat(),
                "encrypted": cfg.encrypt,
            }
            _add_bytes(tar, "meta/run.json", json.dumps(run_meta).encode())
            _add_bytes(tar, "meta/deleted.txt", "".join(p + "\n" for p in deleted).encode())
            _add_bytes(tar, "meta/missing.txt", "".join(p + "\n" for p in missing).encode())
            _add_bytes(tar, "meta/metadata.json", metadata)
            for d in include_dirs:
                tar.addfile(_tar_dir_info(d))
            cache = DirCache(settings.library_path, relative_dir)
            try:
                for path in include:
                    if self.stop.is_set():
                        raise RuntimeError("stopped")
                    self._add_file(
                        tar, cache, path, throttle, manifest, stored, unstable, unreadable_now
                    )
            finally:
                cache.close()
            uncompressed = sum(m["size"] for m in manifest.values())
            manifest_doc = {
                "format": 1,
                "kind": kind,
                "files": manifest,
                "dirs": include_dirs,
                "changed_during_backup": sorted(unstable),
            }
            manifest_bytes = json.dumps(manifest_doc, separators=(",", ":")).encode()
            _add_bytes(tar, "meta/manifest.json", manifest_bytes)
            tar.close()
            if writer is not None:
                writer.close()
            raw.flush()
            os.fsync(raw.fileno())
        except BaseException:
            sink.discard = (
                True  # a compressor flushed later by the garbage collector writes nowhere
            )
            raw.close()
            tmp.unlink(missing_ok=True)
            raise
        raw.close()

        # -- verify: the bytes on disk are what was written, and the archive reads back
        digest = hashlib.sha256()
        with open(tmp, "rb") as check:
            while block := check.read(1024 * 1024):
                digest.update(block)
        if digest.hexdigest() != sink.sha.hexdigest():
            tmp.unlink(missing_ok=True)
            raise OSError("written archive does not match")
        verify_archive(tmp, manifest_bytes, manifest, file_key)

        # -- publish, unless the root changed state while we were reading it
        with self.sessions() as db:
            root = db.get(StorageRoot, root_id)
            if (
                root is None
                or root.status != "active"
                or root.scan_state is not None
                or root.scan_paused_since is not None
            ):
                tmp.unlink(missing_ok=True)
                self._fail(run_id, archive_rel, "ROOT_CHANGED_DURING_BACKUP")
                return RunResult("failed", run_id, "ROOT_CHANGED_DURING_BACKUP")
        if archive.exists():
            tmp.unlink(missing_ok=True)
            raise FileExistsError("archive name taken")
        os.rename(tmp, archive)
        sidecar = {
            "format": 1,
            "run_id": str(run_id),
            "root_id": str(root_id),
            "chain_id": chain_id,
            "seq": seq,
            "kind": kind,
            "created_at": created.isoformat(),
            "encrypted": cfg.encrypt,
            "key_fingerprint": key.fingerprint if key and cfg.encrypt else None,
            "size_bytes": sink.size,
            "sha256": sink.sha.hexdigest(),
            "files": len(manifest),
            "deleted": len(deleted),
            "missing": len(missing),
        }
        side_tmp = archive.parent / f".tmp-{secrets.token_hex(8)}{TMP_SUFFIX}"
        side_tmp.write_text(json.dumps(sidecar), encoding="utf-8")
        os.rename(side_tmp, archive.with_name(archive.name + ".json"))
        self._fsync_dir(archive.parent)

        # -- record: the new snapshot replaces the old one only now
        new_files: dict[str, Fingerprint] = dict(carried_files)
        for p, fp in current.items():
            if p in stored:
                new_files[p] = stored[p]
            elif p in prior_files and (
                p in unstable or p in unreadable_now or prior_files[p] == fp
            ):
                # unchanged, or not captured this time: keep the old fingerprint so that the next
                # run takes the file again (a file never captured has no entry at all)
                new_files[p] = prior_files[p]
        new_dirs = tree.dirs | carried_dirs
        finished = clock.utcnow()
        with self.sessions() as db:
            run = db.get(BackupRun, run_id)
            if run is None:
                raise RuntimeError("run row vanished")
            run.status, run.finished_at = "ok", finished
            run.size_bytes, run.uncompressed_bytes = sink.size, uncompressed
            run.archive_sha256 = sink.sha.hexdigest()
            run.files_added, run.files_changed = added, changed
            run.files_deleted, run.files_missing = len(deleted), len(missing)
            run.files_unstable = len(unstable)
            run.event_high_water = high_water
            snapshot = encode_snapshot(new_files, set(new_dirs))
            st = db.get(BackupChainState, root_id)
            if st is None:
                st = BackupChainState(root_id=root_id)
                db.add(st)
            st.chain_id, st.seq, st.snapshot = chain_id, seq, snapshot
            st.encrypted = cfg.encrypt
            st.key_fingerprint = key.fingerprint if key and cfg.encrypt else None
            st.last_archive, st.event_high_water = archive_rel, high_water
            st.updated_at = finished
            audit(
                db,
                "backup.run",
                root_id=str(root_id),
                run_id=str(run_id),
                kind=kind,
                seq=seq,
                files=len(manifest),
                deleted=len(deleted),
                missing=len(missing),
                unstable=len(unstable),
                size_bytes=sink.size,
            )
            db.commit()
            retention.prune_to_limit(db, settings, load_config(db))
        return RunResult("ok", run_id)

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        try:
            dfd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        except OSError:
            return
        try:
            os.fsync(dfd)
        except OSError:
            pass
        finally:
            os.close(dfd)

    def _add_file(
        self,
        tar: tarfile.TarFile,
        cache: DirCache,
        path: str,
        throttle: Throttle,
        manifest: dict[str, dict[str, Any]],
        stored: dict[str, Fingerprint],
        unstable: list[str],
        unreadable: list[str],
    ) -> None:
        *dirs, name = path.split("/")
        try:
            dir_fd = cache.get(tuple(dirs))
            fd, info = open_regular(dir_fd, name)
        except (OSError, StorageError, UnsafeEntry, InvalidName):
            unreadable.append(path)
            return
        try:
            if info.st_size <= self.settings.backup_buffer_file_bytes:
                data: bytes | None = None
                for _attempt in range(2):  # a change during the read is retried once
                    try:
                        data = read_stable(fd, info, throttle)
                        break
                    except UnstableFile:
                        os.lseek(fd, 0, os.SEEK_SET)
                        info = os.fstat(fd)
                if data is None:
                    unstable.append(path)
                    return
                ti = _tar_file_info(path, len(data), info.st_mtime_ns)
                tar.addfile(ti, io.BytesIO(data))
                manifest[path] = {
                    "size": len(data),
                    "mtime_ns": info.st_mtime_ns,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
                stored[path] = fingerprint_of(info)
                return
            source = StreamSource(fd, info.st_size, throttle)
            tar.addfile(_tar_file_info(path, info.st_size, info.st_mtime_ns), source)
            after = os.fstat(fd)
            manifest[path] = {
                "size": info.st_size,
                "mtime_ns": info.st_mtime_ns,
                "sha256": source.sha.hexdigest(),
            }
            if source.torn or fingerprint_of(after) != fingerprint_of(info):
                unstable.append(path)  # recorded in the manifest; the next run takes it again
                return
            stored[path] = fingerprint_of(info)
        finally:
            os.close(fd)


@contextlib.contextmanager
def open_tar(
    path: Path,
    *,
    encrypted: bool,
    recovery_key: str | None = None,
    file_key: bytes | None = None,
) -> Iterator[tarfile.TarFile]:
    """The archive as a sequential tar stream, decrypted when it was written encrypted."""
    with open(path, "rb") as handle:
        stream: BinaryIO | io.BufferedReader = handle
        if encrypted:
            stream = io.BufferedReader(
                crypto.DecryptingReader(handle, recovery_key=recovery_key, file_key=file_key)
            )
        with tarfile.open(fileobj=stream, mode="r|gz") as tar:
            yield tar


def verify_archive(
    path: Path,
    manifest_bytes: bytes | None,
    manifest: dict[str, dict[str, Any]] | None,
    file_key: bytes | None,
    *,
    recovery_key: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Reads the whole archive back and checks every member of `files/` against the manifest
    stored in it (and against the expected manifest when given). Returns the stored manifest.
    Raises on any difference."""
    seen: dict[str, str] = {}
    stored_manifest: dict[str, Any] | None = None
    stored_manifest_bytes = b""
    encrypted = file_key is not None or recovery_key is not None
    with open_tar(path, encrypted=encrypted, recovery_key=recovery_key, file_key=file_key) as tar:
        for member in tar:
            if member.name == "meta/manifest.json":
                extracted = tar.extractfile(member)
                stored_manifest_bytes = extracted.read() if extracted else b""
                stored_manifest = json.loads(stored_manifest_bytes)
            elif member.isfile() and member.name.startswith("files/"):
                extracted = tar.extractfile(member)
                sha = hashlib.sha256()
                while block := (extracted.read(1024 * 1024) if extracted else b""):
                    sha.update(block)
                seen[member.name[len("files/") :]] = sha.hexdigest()
            else:
                extracted = tar.extractfile(member) if member.isfile() else None
                if extracted:
                    while extracted.read(1024 * 1024):
                        pass
    if stored_manifest is None:
        raise ValueError("archive has no manifest")
    if manifest_bytes is not None and stored_manifest_bytes != manifest_bytes:
        raise ValueError("stored manifest differs")
    files: dict[str, dict[str, Any]] = stored_manifest["files"]
    if set(files) != set(seen):
        raise ValueError("archive members differ from the manifest")
    for p, entry in files.items():
        if seen[p] != entry["sha256"]:
            raise ValueError("a member does not match its checksum")
    if manifest is not None and set(manifest) != set(files):
        raise ValueError("manifest differs from the run")
    return files
