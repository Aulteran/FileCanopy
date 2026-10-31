"""Filesystem primitives for file operations (ADR-013). Everything works on directory file
descriptors obtained from `resolver.open_dir`; no function here takes or builds a path string.

Guarantees: no call replaces an existing entry (`rename_noreplace`, link-based publish); every
open uses `O_NOFOLLOW`; only regular, single-link files are read (hardlink policy); writes go to a
temporary file in the destination directory and appear under their final name atomically.
"""

import ctypes
import errno
import hashlib
import os
import secrets
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO

from app.storage.names import InvalidName, existing_name, validate_name
from app.storage.resolver import DIR_FLAGS, DIR_MODE, StorageError, StorageEscape

TMP_PREFIX = ".filecanopy-tmp-"
CHUNK = 1024 * 1024
FILE_MODE = 0o640
RENAME_NOREPLACE = 1


class NameExists(StorageError):
    """The destination name is already taken (never overwritten)."""


class UnsafeEntry(StorageError):
    """Symlink, special file or hardlinked file: refused for reading and copying."""


class CrossDevice(StorageError):
    """A rename would cross filesystems; the caller falls back to copy, verify, remove."""


class SourceChanged(StorageError):
    """The source was modified while it was being read."""


class TreeTooLarge(StorageError):
    """A tree exceeds the configured entry or depth limit."""


def reserved_name(name: str) -> bool:
    return name.startswith(TMP_PREFIX)


def check_name(name: str) -> str:
    clean = validate_name(name)
    if reserved_name(clean):
        raise InvalidName("reserved prefix")
    return clean


_libc = ctypes.CDLL(None, use_errno=True)
_renameat2 = getattr(_libc, "renameat2", None)


def rename_noreplace(src_dir: int, src: str, dst_dir: int, dst: str) -> None:
    """Atomic rename that fails with NameExists instead of replacing. Uses renameat2 where the
    kernel and filesystem support it; otherwise links files (atomic) and refuses directory moves
    that cannot be made race-free without it."""
    if _renameat2 is not None:
        rc = _renameat2(src_dir, src.encode(), dst_dir, dst.encode(), RENAME_NOREPLACE)
        if rc == 0:
            return
        err = ctypes.get_errno()
        if err == errno.EEXIST:
            raise NameExists(dst)
        if err == errno.EXDEV:
            raise CrossDevice(dst)
        if err not in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP):
            raise OSError(err, os.strerror(err))
    info = os.lstat(src, dir_fd=src_dir)
    if stat.S_ISDIR(info.st_mode):
        # Without RENAME_NOREPLACE an existing empty directory would be replaced silently, so
        # the best available check-then-rename is used; it is documented as best effort.
        try:
            os.lstat(dst, dir_fd=dst_dir)
        except FileNotFoundError:
            pass
        else:
            raise NameExists(dst)
        try:
            os.rename(src, dst, src_dir_fd=src_dir, dst_dir_fd=dst_dir)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                raise CrossDevice(dst) from exc
            raise
        return
    if not _link_noreplace(src_dir, src, dst_dir, dst):
        os.unlink(src, dir_fd=src_dir)


def _link_noreplace(src_dir: int, src: str, dst_dir: int, dst: str) -> bool:
    """Gives `src` the new name without replacing anything. Returns True when the no-hardlink
    fallback already moved `src` (nothing left to unlink), False when `src` still exists."""
    try:
        os.link(src, dst, src_dir_fd=src_dir, dst_dir_fd=dst_dir, follow_symlinks=False)
    except FileExistsError as exc:
        raise NameExists(dst) from exc
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            raise CrossDevice(dst) from exc
        if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP):
            raise
        # Filesystem without hardlinks: reserve the name exclusively, then rename over our own
        # placeholder.
        try:
            os.close(os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE, dir_fd=dst_dir))
        except FileExistsError as dup:
            raise NameExists(dst) from dup
        os.rename(src, dst, src_dir_fd=src_dir, dst_dir_fd=dst_dir)
        return True
    return False


def fsync_dir(dir_fd: int) -> None:
    try:
        os.fsync(dir_fd)
    except OSError:  # some network filesystems refuse directory fsync
        pass


def lstat_entry(dir_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.lstat(existing_name(name), dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except InvalidName as exc:
        raise StorageEscape("unsafe path component") from exc


def make_dir(dir_fd: int, name: str) -> os.stat_result:
    name = check_name(name)
    try:
        os.mkdir(name, DIR_MODE, dir_fd=dir_fd)
    except FileExistsError as exc:
        raise NameExists(name) from exc
    fsync_dir(dir_fd)
    return os.lstat(name, dir_fd=dir_fd)


def open_dir_child(dir_fd: int, name: str) -> int:
    try:
        return os.open(existing_name(name), DIR_FLAGS, dir_fd=dir_fd)
    except InvalidName as exc:
        raise StorageEscape("unsafe path component") from exc
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise UnsafeEntry("not a plain directory") from exc
        if exc.errno == errno.ENOENT:
            raise FileNotFoundError(name) from exc
        raise


def open_regular(dir_fd: int, name: str) -> tuple[int, os.stat_result]:
    """Opens an existing regular file for reading, race-safely. Symlinks, FIFOs, devices and
    hardlinked files (`st_nlink > 1`, ADR-013 §5) raise UnsafeEntry."""
    try:
        name = existing_name(name)
    except InvalidName as exc:
        raise StorageEscape("unsafe path component") from exc
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)  # O_NONBLOCK: a FIFO cannot hang the worker
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise UnsafeEntry("symlink refused") from exc
        if exc.errno == errno.ENOENT:
            raise FileNotFoundError(name) from exc
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise UnsafeEntry("not a regular file")
        if info.st_nlink > 1:
            raise UnsafeEntry("hardlinked file")
    except BaseException:
        os.close(fd)
        raise
    return fd, info


def hash_file(dir_fd: int, name: str) -> tuple[str, int]:
    fd, _ = open_regular(dir_fd, name)
    digest, total = hashlib.sha256(), 0
    try:
        while chunk := os.read(fd, CHUNK):
            digest.update(chunk)
            total += len(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest(), total


class StagedWrite:
    """A temporary file in the destination directory. `publish(name)` makes it appear under its
    final name atomically and never replaces an existing entry; `abort()` removes it."""

    def __init__(self, dir_fd: int) -> None:
        self.dir_fd = dir_fd
        self.tmp = f"{TMP_PREFIX}{secrets.token_hex(12)}"
        self.fd: int | None = os.open(
            self.tmp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            FILE_MODE,
            dir_fd=dir_fd,
        )
        self.size = 0
        self._sha = hashlib.sha256()
        self.published = False

    def write(self, data: bytes) -> None:
        if self.fd is None:
            raise ValueError("closed")
        view = memoryview(data)
        while view:
            written = os.write(self.fd, view)
            view = view[written:]
        self.size += len(data)
        self._sha.update(data)

    @property
    def sha256(self) -> str:
        return self._sha.hexdigest()

    def seal(self) -> None:
        """Flushes the data to disk and closes the temporary file (idempotent)."""
        if self.fd is not None:
            os.fsync(self.fd)
            os.close(self.fd)
            self.fd = None

    def publish(self, name: str) -> os.stat_result:
        """May be retried with another name after NameExists."""
        name = check_name(name)
        self.seal()
        consumed = _link_noreplace(self.dir_fd, self.tmp, self.dir_fd, name)
        self.published = True
        if not consumed:
            try:
                os.unlink(self.tmp, dir_fd=self.dir_fd)
            except FileNotFoundError:
                pass
        fsync_dir(self.dir_fd)
        return os.lstat(name, dir_fd=self.dir_fd)

    def abort(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if not self.published:
            try:
                os.unlink(self.tmp, dir_fd=self.dir_fd)
            except FileNotFoundError:
                pass


def copy_file(
    src_dir: int, src: str, dst_dir: int, dst: str, *, expect: os.stat_result | None = None
) -> tuple[os.stat_result, str]:
    """Copies a regular file to a new name (never overwriting). Returns the new file's stat and
    the sha256 of the bytes written. Fails with SourceChanged if the source was modified while
    being read (size/mtime differ before and after, or from `expect`)."""
    fd, before = open_regular(src_dir, src)
    stage: StagedWrite | None = None
    try:
        if expect is not None and (expect.st_ino, expect.st_size) != (
            before.st_ino,
            before.st_size,
        ):
            raise SourceChanged(src)
        stage = StagedWrite(dst_dir)
        while chunk := os.read(fd, CHUNK):
            stage.write(chunk)
        after = os.fstat(fd)
        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            raise SourceChanged(src)
        if stage.size != before.st_size:
            raise SourceChanged(src)
        info = stage.publish(dst)
        return info, stage.sha256
    except BaseException:
        if stage is not None:
            stage.abort()
        raise
    finally:
        os.close(fd)


@dataclass
class TreeEntry:
    """One entry of a copied tree, relative to the tree root. `src_stat` is the source state at
    copy time (used to remove the source only if it is unchanged)."""

    rel: tuple[str, ...]
    kind: str  # dir | file
    size: int
    src_stat: os.stat_result
    sha256: str | None = None


def scan_tree(
    dir_fd: int, name: str, *, max_entries: int, max_depth: int
) -> list[tuple[tuple[str, ...], str, os.stat_result]]:
    """Walks `name` under `dir_fd` without following links. Raises UnsafeEntry on the first
    symlink, special file or hardlinked file, TreeTooLarge past the limits. Pre-flight for copy and
    cross-device move: nothing is written yet."""
    out: list[tuple[tuple[str, ...], str, os.stat_result]] = []

    def visit(parent_fd: int, rel: tuple[str, ...], depth: int) -> None:
        if depth > max_depth:
            raise TreeTooLarge("depth")
        with os.scandir(parent_fd) as it:
            entries = sorted(it, key=lambda e: e.name)
        for entry in entries:
            if reserved_name(entry.name):
                continue  # another operation's staging file
            try:
                validate_name(entry.name)
            except InvalidName as exc:
                raise UnsafeEntry("unsupported name in tree") from exc
            info = entry.stat(follow_symlinks=False)
            if len(out) >= max_entries:
                raise TreeTooLarge("entries")
            child_rel = (*rel, entry.name)
            if stat.S_ISDIR(info.st_mode):
                out.append((child_rel, "dir", info))
                child_fd = open_dir_child(parent_fd, entry.name)
                try:
                    visit(child_fd, child_rel, depth + 1)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                out.append((child_rel, "file", info))
            else:
                raise UnsafeEntry("unsupported entry in tree")

    top = os.lstat(existing_name(name), dir_fd=dir_fd)
    if stat.S_ISREG(top.st_mode):
        if top.st_nlink != 1:
            raise UnsafeEntry("hardlinked file")
        return [((name,), "file", top)]
    if not stat.S_ISDIR(top.st_mode):
        raise UnsafeEntry("unsupported entry")
    root_fd = open_dir_child(dir_fd, name)
    try:
        out.append(((name,), "dir", top))
        visit(root_fd, (name,), 1)
    finally:
        os.close(root_fd)
    return out


def _walk_fd(root_fd: int, rel: tuple[str, ...]) -> int:
    fd = os.dup(root_fd)
    try:
        for part in rel:
            nxt = open_dir_child(fd, part)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def copy_tree(
    src_dir: int,
    src: str,
    dst_dir: int,
    dst: str,
    plan: list[tuple[tuple[str, ...], str, os.stat_result]],
) -> list[TreeEntry]:
    """Copies the scanned `plan` (from `scan_tree`) to `dst` under `dst_dir`. On any failure
    every entry this call created is removed again (deepest first) before re-raising. Returns the
    created entries with relative paths (first component is `src`, replaced by `dst` on disk)."""
    created: list[TreeEntry] = []
    dst = check_name(dst)

    def dst_rel(rel: tuple[str, ...]) -> tuple[str, ...]:
        return (dst, *rel[1:])

    try:
        for rel, kind, info in plan:
            target = dst_rel(rel)
            parent_fd = _walk_fd(dst_dir, target[:-1])
            try:
                src_parent = _walk_fd(src_dir, rel[:-1])
                try:
                    if kind == "dir":
                        make_dir(parent_fd, target[-1])
                        created.append(TreeEntry(target, "dir", 0, info))
                    else:
                        new, sha = copy_file(
                            src_parent, rel[-1], parent_fd, target[-1], expect=info
                        )
                        created.append(TreeEntry(target, "file", new.st_size, info, sha))
                finally:
                    os.close(src_parent)
            finally:
                os.close(parent_fd)
    except BaseException:
        remove_created(dst_dir, created)
        raise
    return created


def remove_created(dst_dir: int, created: list[TreeEntry]) -> list[tuple[str, ...]]:
    """Removes entries this process created, deepest first. Returns what could not be removed."""
    leftover: list[tuple[str, ...]] = []
    for entry in sorted(created, key=lambda e: len(e.rel), reverse=True):
        try:
            parent_fd = _walk_fd(dst_dir, entry.rel[:-1])
        except (OSError, StorageError):
            leftover.append(entry.rel)
            continue
        try:
            if entry.kind == "dir":
                os.rmdir(entry.rel[-1], dir_fd=parent_fd)
            else:
                os.unlink(entry.rel[-1], dir_fd=parent_fd)
        except OSError:
            leftover.append(entry.rel)
        finally:
            os.close(parent_fd)
    return leftover


def remove_verified_source(
    src_dir: int,
    src: str,
    copied: list[TreeEntry],
    plan: list[tuple[tuple[str, ...], str, os.stat_result]],
) -> list[tuple[str, ...]]:
    """Removes the source tree after a verified copy. A file is unlinked only if it is still the
    same file (inode, size, mtime) as when it was copied; a directory only if empty. Anything else
    stays and is reported, so a change made during the move is never destroyed."""
    leftover: list[tuple[str, ...]] = []
    originals = {rel: (kind, info) for rel, kind, info in plan}
    for rel in sorted(originals, key=len, reverse=True):
        kind, info = originals[rel]
        try:
            parent_fd = _walk_fd(src_dir, rel[:-1])
        except (OSError, StorageError):
            leftover.append(rel)
            continue
        try:
            if kind == "file":
                now = os.lstat(rel[-1], dir_fd=parent_fd)
                if (now.st_ino, now.st_size, now.st_mtime_ns, now.st_ctime_ns) != (
                    info.st_ino,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                ):
                    leftover.append(rel)
                else:
                    os.unlink(rel[-1], dir_fd=parent_fd)
            else:
                os.rmdir(rel[-1], dir_fd=parent_fd)
        except OSError:
            leftover.append(rel)
        finally:
            os.close(parent_fd)
    fsync_dir(src_dir)
    return leftover


def read_chunks(fileobj: BinaryIO, limit: int) -> Iterator[bytes]:
    """Streams at most `limit` bytes (the size promised in Content-Length) and always closes the
    file. The file object also closes itself when garbage collected, so a response that is never
    iterated (client gone before the first chunk) does not leak the descriptor."""
    remaining = limit
    try:
        while remaining > 0:
            chunk = fileobj.read(min(CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        fileobj.close()
