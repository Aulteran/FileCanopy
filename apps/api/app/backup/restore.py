"""Restoring from a backup chain (ADR-015 section 5).

A restore never overwrites: it creates a *new* folder at the top of the root and rebuilds the tree
there from the full archive and the changed-only archives after it, up to the chosen restore
point. Authorization is decided by the caller (the owner of a personal root, the administrator
for the shared tree); this module only needs the recovery key, which it never stores or logs.
"""

import gzip
import logging
import os
import secrets
import shutil
import stat
import tarfile
import uuid
import zlib
from dataclasses import dataclass
from typing import IO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import clock
from app.backup import crypto
from app.backup.engine import DirCache, open_tar
from app.backup.volume import backups_root
from app.config import Settings
from app.db.models import BackupRun, Node, StorageRoot
from app.domain.nodes import root_node
from app.storage.files import TMP_PREFIX
from app.storage.names import InvalidName, existing_name, name_key
from app.storage.resolver import DIR_FLAGS, open_dir

log = logging.getLogger("filecanopy.backup")

FILE_MODE = 0o640
DIR_MODE = 0o750


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
        parent_fd = self.dir_fd(parts[:-1])
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


def restore(
    db: Session,
    settings: Settings,
    *,
    root: StorageRoot,
    run_id: uuid.UUID | None,
    recovery_key: str | None,
) -> RestoreResult:
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
        if not r.archive_name or not (base / r.archive_name).is_file():
            raise RestoreError("BACKUP_ARCHIVE_MISSING")

    # a name nothing on disk or in the database uses (including missing nodes' names)
    taken = {
        n.name_key
        for n in db.scalars(select(Node).where(Node.parent_id == root_node(db, root.id).id))
    }
    stamp = f"{clock.utcnow():%Y-%m-%d %H%M}"
    folder = f"Restored from backup {stamp}"
    with open_dir(settings.library_path, root.relative_dir) as root_fd:
        counter = 1
        while True:
            candidate = folder if counter == 1 else f"{folder} ({counter})"
            if name_key(candidate) not in taken:
                try:
                    os.mkdir(candidate, DIR_MODE, dir_fd=root_fd)
                    folder = candidate
                    break
                except FileExistsError:
                    pass
            counter += 1

    writer = _Writer(settings, root, folder)
    try:
        for run in chain:
            archive = base / (run.archive_name or "")
            with open_tar(archive, encrypted=run.encrypted, recovery_key=recovery_key) as tar:
                for member in tar:
                    name = member.name
                    if name == "meta/deleted.txt":
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
        writer.close()
    except BaseException as exc:
        writer.close()
        _discard(settings, root, folder)
        if isinstance(exc, (RestoreError, KeyboardInterrupt, SystemExit)):
            raise
        log.error("restore failed: %s", type(exc).__name__)  # no paths in the log
        # the key matched the chain, so a decryption or decompression error means damage
        if isinstance(
            exc, (crypto.DecryptionError, tarfile.TarError, EOFError, zlib.error, gzip.BadGzipFile)
        ):
            raise RestoreError("BACKUP_ARCHIVE_DAMAGED") from exc
        if isinstance(exc, (OSError, ValueError)):
            raise RestoreError("RESTORE_FAILED") from exc
        raise
    return RestoreResult(folder, writer.files, writer.bytes, target.id)


def _discard(settings: Settings, root: StorageRoot, folder: str) -> None:
    """Removes a half-restored folder this call created."""
    try:
        with open_dir(settings.library_path, root.relative_dir) as root_fd:
            _rmtree_at(root_fd, folder)
    except OSError:
        pass
