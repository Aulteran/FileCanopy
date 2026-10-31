"""Safe directory resolution beneath the library mount (ADR-004 §7).

Paths are never built by string concatenation and then resolved. Each component is opened with
`O_NOFOLLOW | O_DIRECTORY` relative to its parent's descriptor, so a symlink at any depth (the
storage root directory included) is refused rather than followed. Names are re-validated at use
time. Crossing onto another filesystem below a root is refused (no mount points in the MVP).
Requires a POSIX platform; the API container is Linux.
"""

import errno
import os
import stat
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from app.storage.names import InvalidName, validate_name

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
DIR_MODE = 0o750


class StorageError(Exception):
    """A storage location could not be used safely."""


class StorageEscape(StorageError):
    """A symlink, bad name or filesystem boundary would leave the allowed tree."""


class StorageMissing(StorageError):
    """A directory on the path does not exist."""


def _open_child(parent_fd: int, name: str, *, create: bool = False) -> int:
    try:
        validate_name(name)
    except InvalidName as exc:
        raise StorageEscape("unsafe path component") from exc
    if create:
        try:
            os.mkdir(name, DIR_MODE, dir_fd=parent_fd)
        except FileExistsError:
            pass
    try:
        return os.open(name, DIR_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        try:
            info = os.lstat(name, dir_fd=parent_fd)
        except FileNotFoundError:
            raise StorageMissing("directory does not exist") from exc
        except OSError:
            raise StorageError("directory is not accessible") from exc
        if stat.S_ISLNK(info.st_mode):
            raise StorageEscape("symlink refused") from exc
        if exc.errno in (errno.ELOOP,):
            raise StorageEscape("symlink refused") from exc
        raise StorageError("not an accessible directory") from exc


def _descend(
    library: Path, root_parts: Sequence[str], components: Sequence[str], *, create: bool
) -> int:
    """Opens each directory in turn, closing the previous one; returns the final fd."""
    try:
        fd = os.open(library, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)  # the mount itself
    except FileNotFoundError as exc:
        raise StorageMissing("library mount is missing") from exc
    except OSError as exc:
        raise StorageError("library mount is not accessible") from exc
    root_dev: int | None = None
    try:
        for index, name in enumerate([*root_parts, *components]):
            child = _open_child(fd, name, create=create)
            os.close(fd)
            fd = child
            dev = os.fstat(fd).st_dev
            if index == len(root_parts) - 1:
                root_dev = dev
            elif index >= len(root_parts) and dev != root_dev:
                raise StorageEscape("filesystem boundary refused")
    except BaseException:
        os.close(fd)
        raise
    return fd


@contextmanager
def open_dir(
    library: Path, root_dir: str, components: Sequence[str] = (), *, create: bool = False
) -> Iterator[int]:
    """Yields a directory fd for `<library>/<root_dir>/<components...>`.

    `root_dir` is the storage root's relative_dir ('shared' or 'personal/<uuid>'); `components`
    are the node names below the root. `create=True` makes missing directories (mode 0o750)
    beneath the library mount, which itself must exist. Raises StorageEscape / StorageMissing /
    StorageError; never follows a link.
    """
    root_parts = root_dir.split("/")
    if any(p == "" for p in root_parts):
        raise StorageEscape("bad root directory")
    names = [*root_parts, *components]
    try:
        for name in names:  # reject every unsafe name before touching the filesystem
            validate_name(name)
    except InvalidName as exc:
        raise StorageEscape("unsafe path component") from exc
    fd = _descend(library, root_parts, list(components), create=create)
    try:
        yield fd
    finally:
        os.close(fd)


def ensure_root_dir(library: Path, root_dir: str) -> None:
    """Creates (mode 0o750) and verifies a storage root directory."""
    with open_dir(library, root_dir, create=True):
        pass
