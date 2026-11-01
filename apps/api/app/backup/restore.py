"""Restoring from a backup chain (ADR-015 section 5).

A restore never overwrites: it creates a *new* folder at the top of the root and rebuilds the tree
there from the full archive and the changed-only archives after it, up to the chosen restore
point. Authorization is decided by the caller (the owner of a personal root, the administrator
for the shared tree); this module only needs the recovery key, which it never stores or logs.
"""

import errno
import gzip
import hashlib
import json
import logging
import os
import secrets
import shutil
import stat
import tarfile
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth import clock
from app.backup import crypto
from app.backup.engine import DirCache, open_tar
from app.backup.volume import backups_root
from app.config import Settings
from app.db.models import BackupRun, Node, StorageRoot
from app.domain.nodes import root_node
from app.policy import privacy
from app.storage.files import TMP_PREFIX
from app.storage.names import InvalidName, existing_name, name_key
from app.storage.resolver import DIR_FLAGS, StorageError, StorageMissing, open_dir

log = logging.getLogger("filecanopy.backup")

FILE_MODE = 0o640
DIR_MODE = 0o750
RESTORE_TMP = f"{TMP_PREFIX}restore-"  # a restore is built under this name, then renamed
MAX_DELETED_BYTES = 128 * 1024 * 1024  # meta/deleted.txt: one short path per removed entry
MAX_RUN_JSON_BYTES = 64 * 1024


class RestoreError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class RestoreResult:
    folder_name: str
    files: int
    bytes_written: int
    run_id: uuid.UUID


def restore_points(db: Session, root_id: uuid.UUID) -> list[BackupRun]:
    return list(
        db.scalars(
            select(BackupRun)
            .where(
                BackupRun.root_id == root_id,
                BackupRun.status == "ok",
                BackupRun.pruned_at.is_(None),
            )
            .order_by(BackupRun.finished_at.desc(), BackupRun.seq.desc())
        )
    )


def chain_for(db: Session, root_id: uuid.UUID, target: BackupRun) -> list[BackupRun]:
    runs = list(
        db.scalars(
            select(BackupRun)
            .where(
                BackupRun.root_id == root_id,
                BackupRun.chain_id == target.chain_id,
                BackupRun.status == "ok",
                BackupRun.pruned_at.is_(None),
                BackupRun.seq <= target.seq,
            )
            .order_by(BackupRun.seq)
        )
    )
    if [r.seq for r in runs] != list(range(1, target.seq + 1)) or runs[0].kind != "full":
        raise RestoreError("BACKUP_CHAIN_INCOMPLETE")
    return runs


def _valid_parts(rel: str) -> list[str]:
    parts = rel.split("/")
    try:
        for part in parts:
            if part in ("", ".", "..") or part.startswith(TMP_PREFIX):
                raise InvalidName(part)
            existing_name(part)
    except InvalidName as exc:
        raise RestoreError("BACKUP_ARCHIVE_UNSAFE") from exc
    return parts


def _rmtree_at(dir_fd: int, name: str) -> None:
    try:
        info = os.lstat(name, dir_fd=dir_fd)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(info.st_mode):
        os.unlink(name, dir_fd=dir_fd)
        return
    child = os.open(name, DIR_FLAGS, dir_fd=dir_fd)
    try:
        for entry in os.listdir(child):
            _rmtree_at(child, entry)
    finally:
        os.close(child)
    os.rmdir(name, dir_fd=dir_fd)


class _Writer:
    """Writes into the new restore folder through directory descriptors; nothing outside it."""

    def __init__(self, settings: Settings, root: StorageRoot, folder: str) -> None:
        self.settings, self.root, self.folder = settings, root, folder
        self.files = 0
        self.bytes = 0
        self._dirs = DirCache(
            settings.library_path, root.relative_dir, prefix=(folder,), create=True
        )

    def dir_fd(self, parts: list[str]) -> int:
        return self._dirs.get(tuple(parts))

    def close(self) -> None:
        self._dirs.close()

    def delete(self, line: str) -> None:
        rel = line.rstrip("/")
        if not rel:
            return
        parts = _valid_parts(rel)
        try:
            parent_fd = self._dirs.get(tuple(parts[:-1]), create=False)
        except StorageMissing:
            return  # the parent was never restored (or is already gone): nothing to remove
        _rmtree_at(parent_fd, parts[-1])
        self._dirs.forget()  # a removed directory may be cached

    def make_dir(self, rel: str) -> None:
        self.dir_fd(_valid_parts(rel))

    def write_file(self, rel: str, source: IO[bytes], mtime: int) -> None:
        parts = _valid_parts(rel)
        parent_fd = self.dir_fd(parts[:-1])
        try:
            if stat.S_ISDIR(os.lstat(parts[-1], dir_fd=parent_fd).st_mode):
                _rmtree_at(parent_fd, parts[-1])  # a directory became a file in a later run
                self._dirs.forget()
                parent_fd = self.dir_fd(parts[:-1])
        except FileNotFoundError:
            pass
        reserve = self.settings.import_free_space_reserve_bytes
        tmp = f"{TMP_PREFIX}{secrets.token_hex(8)}"
        fd = os.open(
            tmp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            FILE_MODE,
            dir_fd=parent_fd,
        )
        try:
            with os.fdopen(fd, "wb", closefd=False) as out:
                while block := source.read(1024 * 1024):
                    if shutil.disk_usage(self.settings.library_path).free < reserve + len(block):
                        raise RestoreError("RESTORE_NO_SPACE")
                    out.write(block)
                    self.bytes += len(block)
                out.flush()
            os.fsync(fd)
            os.utime(fd, ns=(mtime * 1_000_000_000, mtime * 1_000_000_000))
        except BaseException:
            os.close(fd)
            try:
                os.unlink(tmp, dir_fd=parent_fd)
            except OSError:
                pass
            raise
        os.close(fd)
        os.replace(tmp, parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        self.files += 1


def _check_archive_file(path: Path, run: BackupRun) -> None:
    """The file on the volume must be the one the database recorded: same size, same SHA-256.
    Done for the whole chain before anything is extracted."""
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise RestoreError("BACKUP_ARCHIVE_MISSING") from exc
    if not stat.S_ISREG(info.st_mode):
        raise RestoreError("BACKUP_ARCHIVE_MISSING")
    if run.size_bytes is None or run.archive_sha256 is None or info.st_size != run.size_bytes:
        raise RestoreError("BACKUP_ARCHIVE_DAMAGED")
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
    except OSError as exc:
        raise RestoreError("BACKUP_ARCHIVE_DAMAGED") from exc
    if digest.hexdigest() != run.archive_sha256:
        raise RestoreError("BACKUP_ARCHIVE_DAMAGED")


def _check_run_json(
    tar: tarfile.TarFile, member: tarfile.TarInfo, root: StorageRoot, run: BackupRun
) -> str:
    """`meta/run.json` is the first member and must name this root, chain and sequence."""
    if member.name != "meta/run.json" or not member.isfile() or member.size > MAX_RUN_JSON_BYTES:
        raise RestoreError("BACKUP_ARCHIVE_DAMAGED")
    data = tar.extractfile(member)
    try:
        meta = json.loads(data.read() if data else b"")
    except ValueError as exc:
        raise RestoreError("BACKUP_ARCHIVE_DAMAGED") from exc
    if (
        not isinstance(meta, dict)
        or meta.get("root_id") != str(root.id)
        or meta.get("chain_id") != run.chain_id
        or meta.get("seq") != run.seq
        or meta.get("kind") != run.kind
    ):
        raise RestoreError("BACKUP_ARCHIVE_DAMAGED")
    recorded = meta.get("privacy_label")  # absent in backups made before M09
    return recorded if recorded in privacy.RANK else "normal"


def required_label(db: Session, root: StorageRoot, recorded: str = "normal") -> str:
    """The label a restored folder must carry: the strictest of what the backup recorded and what
    the root holds now. A pre-M09 backup records nothing, so the root's current labels decide
    (Normal when it has none)."""
    label = recorded
    for own in db.scalars(
        select(Node.privacy_label)
        .where(Node.root_id == root.id, Node.status.not_in(("purged", "dismissed")))
        .distinct()
    ):
        label = privacy.stricter(label, own)
    return label


def restore(
    db: Session,
    settings: Settings,
    *,
    root: StorageRoot,
    run_id: uuid.UUID | None,
    recovery_key: str | None,
    unlocked: bool = False,
) -> RestoreResult:
    if root.status != "active" or root.scan_state is not None or root.scan_paused_since is not None:
        # a disabled, missing, suspended or import-busy root is not a place to write into
        raise RestoreError("ROOT_NOT_ACTIVE")
    points = restore_points(db, root.id)
    if run_id is None:
        target = points[0] if points else None
    else:
        target = next((p for p in points if p.id == run_id), None)
    if target is None:
        raise RestoreError("BACKUP_NOT_FOUND")
    chain = chain_for(db, root.id, target)
    if target.encrypted:
        try:
            if recovery_key is None:
                raise crypto.BadKey("missing")
            fp = crypto.recovery_key_fingerprint(recovery_key)
        except crypto.BadKey as exc:
            raise RestoreError("BACKUP_KEY_INVALID") from exc
        if any(r.key_fingerprint != fp for r in chain):
            raise RestoreError("BACKUP_KEY_MISMATCH")
    base = backups_root(settings)
    for r in chain:
        if not r.archive_name:
            raise RestoreError("BACKUP_ARCHIVE_MISSING")
        _check_archive_file(base / r.archive_name, r)

    # a name nothing on disk or in the database uses (including missing nodes' names)
    taken = {
        n.name_key
        for n in db.scalars(select(Node).where(Node.parent_id == root_node(db, root.id).id))
    }
    stamp = f"{clock.utcnow():%Y-%m-%d %H%M}"
    work = f"{RESTORE_TMP}{secrets.token_hex(8)}"  # invisible to the app until it is complete
    try:
        with open_dir(settings.library_path, root.relative_dir) as root_fd:
            os.mkdir(work, DIR_MODE, dir_fd=root_fd)
    except (OSError, StorageError) as exc:
        raise RestoreError("RESTORE_FAILED") from exc

    if not unlocked and required_label(db, root) == "confidential":
        raise RestoreError("LOCKED")  # unlock first; the recorded labels are re-checked below
    recorded_label = "normal"
    writer = _Writer(settings, root, work)
    published = False
    folder = ""
    try:
        for run in chain:
            archive = base / (run.archive_name or "")
            context = crypto.archive_context(root.id, run.chain_id, run.seq)
            with open_tar(
                archive, encrypted=run.encrypted, recovery_key=recovery_key, context=context
            ) as tar:
                first = True
                for member in tar:
                    if first:
                        recorded_label = privacy.stricter(
                            recorded_label, _check_run_json(tar, member, root, run)
                        )
                        first = False
                        continue
                    name = member.name
                    if name == "meta/deleted.txt":
                        if member.size > MAX_DELETED_BYTES:
                            raise RestoreError("BACKUP_ARCHIVE_DAMAGED")
                        data = tar.extractfile(member)
                        text = data.read().decode("utf-8") if data else ""
                        for line in text.splitlines():
                            writer.delete(line)
                    elif name.startswith("files/") and member.isdir():
                        writer.make_dir(name[len("files/") :])
                    elif name.startswith("files/") and member.isfile():
                        data = tar.extractfile(member)
                        if data is not None:
                            writer.write_file(name[len("files/") :], data, int(member.mtime))
                if first:  # an archive without any member
                    raise RestoreError("BACKUP_ARCHIVE_DAMAGED")
        writer.close()
        label = required_label(db, root, recorded_label)
        if label == "confidential" and not unlocked:
            raise RestoreError("LOCKED")
        folder, node_id = _publish(db, settings, root, work, taken, stamp, label)
        published = True
        node = db.get(Node, node_id)
        if node is not None:
            node.status = "active"
            db.commit()
    except BaseException as exc:
        writer.close()
        _discard(settings, root, folder if published else work)
        if published:
            _forget_name(db, root, folder)
        if isinstance(exc, (RestoreError, KeyboardInterrupt, SystemExit)):
            raise
        log.error("restore failed: %s", type(exc).__name__)  # no paths in the log
        # the key matched the chain, so a decryption or decompression error means damage
        if isinstance(
            exc, (crypto.DecryptionError, tarfile.TarError, EOFError, zlib.error, gzip.BadGzipFile)
        ):
            raise RestoreError("BACKUP_ARCHIVE_DAMAGED") from exc
        if isinstance(exc, (OSError, ValueError, StorageError)):  # includes StorageEscape
            raise RestoreError("RESTORE_FAILED") from exc
        raise
    return RestoreResult(folder, writer.files, writer.bytes, target.id)


def _register_folder(
    db: Session, settings: Settings, root: StorageRoot, work: str, name: str, label: str
) -> uuid.UUID:
    """Creates the folder's node with its label, still `busy`, before the folder becomes visible:
    the scanner skips busy nodes, so it can never find the folder first and make it Normal."""
    from app.domain.fileops import _new_node, _stamp  # deferred: fileops builds on storage

    top = root_node(db, root.id)
    with open_dir(settings.library_path, root.relative_dir) as root_fd:
        info = os.stat(work, dir_fd=root_fd, follow_symlinks=False)  # same inode after the rename
    node = _new_node(top, name, "dir", None)
    _stamp(node, info)
    node.privacy_label = label
    node.status = "busy"
    db.add(node)
    db.commit()
    return node.id


def _forget_name(db: Session, root: StorageRoot, folder: str) -> None:
    db.rollback()
    top = root_node(db, root.id)
    node = db.scalar(select(Node).where(Node.parent_id == top.id, Node.name == folder))
    if node is not None:
        db.delete(node)
        db.commit()


def _forget_node(db: Session, node_id: uuid.UUID) -> None:
    db.rollback()
    node = db.get(Node, node_id)
    if node is not None:
        db.delete(node)
        db.commit()


def _publish(
    db: Session,
    settings: Settings,
    root: StorageRoot,
    work: str,
    taken: set[str],
    stamp: str,
    label: str,
) -> tuple[str, uuid.UUID]:
    """Renames the finished folder to its final, visible name and registers its node (with the
    label) around the rename. A name already in use is never taken (a directory renamed onto an
    empty one would replace it, so the name is checked first)."""
    folder = f"Restored from backup {stamp}"
    with open_dir(settings.library_path, root.relative_dir) as root_fd:
        counter = 1
        while True:
            candidate = folder if counter == 1 else f"{folder} ({counter})"
            counter += 1
            if name_key(candidate) in taken:
                continue
            try:
                os.lstat(candidate, dir_fd=root_fd)
                continue
            except FileNotFoundError:
                pass
            node_id = _register_folder(db, settings, root, work, candidate, label)
            try:
                os.rename(work, candidate, src_dir_fd=root_fd, dst_dir_fd=root_fd)
            except (FileExistsError, IsADirectoryError):
                _forget_node(db, node_id)
                continue
            except OSError as exc:
                _forget_node(db, node_id)
                if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
                    continue
                raise
            return candidate, node_id


def sweep_stale(sessions: "sessionmaker[Session]", settings: Settings) -> int:
    """Removes restore folders a crash left half-built. Run at startup, before restores can start
    (one process); they carry the staging prefix, so the app never showed them."""
    removed = 0
    with sessions() as db:
        dirs = [r.relative_dir for r in db.scalars(select(StorageRoot))]
    for relative_dir in dirs:
        try:
            with open_dir(settings.library_path, relative_dir) as root_fd:
                for entry in os.listdir(root_fd):
                    if entry.startswith(RESTORE_TMP):
                        _rmtree_at(root_fd, entry)
                        removed += 1
        except (OSError, StorageError):
            continue
    return removed


def _discard(settings: Settings, root: StorageRoot, folder: str) -> None:
    """Removes a half-restored folder this call created."""
    try:
        with open_dir(settings.library_path, root.relative_dir) as root_fd:
            _rmtree_at(root_fd, folder)
    except OSError:
        pass
