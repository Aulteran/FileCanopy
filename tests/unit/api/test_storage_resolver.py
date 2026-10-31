"""M03 path traversal and symlink escape: name validation and the fd-based resolver.

Needs POSIX `O_NOFOLLOW`/`dir_fd`, so it runs in the pinned Linux Python image (npm run check:api).
"""

import os
import stat
import types
from pathlib import Path
from typing import Any

import pytest

from app.storage import resolver
from app.storage.names import InvalidName, validate_name
from app.storage.resolver import (
    StorageError,
    StorageEscape,
    StorageMissing,
    ensure_root_dir,
    open_dir,
)

pytestmark = pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="POSIX only")

BAD_NAMES = [
    "",
    ".",
    "..",
    "a/b",
    "a/../b",
    "/etc",
    "/",
    "..\\x",
    "a\\b",
    "a\x00b",
    "a\nb",
    "a\tb",
    "a\x7fb",
    "a\x85b",  # C1 control
    "x" * 256,
    "é" * 128,  # 256 UTF-8 bytes
]


@pytest.fixture
def lib(tmp_path: Path) -> Path:
    library = tmp_path / "library"
    library.mkdir()
    return library


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    target = tmp_path / "outside"
    target.mkdir()
    (target / "secret.txt").write_text("not yours")
    return target


@pytest.mark.parametrize("name", BAD_NAMES)
def test_validate_name_rejects_unsafe(name: str) -> None:
    with pytest.raises(InvalidName):
        validate_name(name)


def test_validate_name_accepts_and_normalizes() -> None:
    assert validate_name("Taxes 2026") == "Taxes 2026"
    assert validate_name("é") == "é"  # NFC
    assert validate_name("x" * 255) == "x" * 255
    assert validate_name("..hidden") == "..hidden"  # only the exact reserved names are refused


def test_ensure_root_creates_private_directories(lib: Path) -> None:
    ensure_root_dir(lib, "personal/11111111-1111-1111-1111-111111111111")
    made = lib / "personal" / "11111111-1111-1111-1111-111111111111"
    assert made.is_dir()
    assert stat.S_IMODE(made.stat().st_mode) & 0o007 == 0  # never world-accessible
    ensure_root_dir(lib, "personal/11111111-1111-1111-1111-111111111111")  # idempotent


@pytest.mark.parametrize("name", BAD_NAMES)
def test_component_traversal_refused_at_use_time(lib: Path, outside: Path, name: str) -> None:
    ensure_root_dir(lib, "shared")
    with pytest.raises(StorageEscape), open_dir(lib, "shared", [name]):
        pass
    assert (outside / "secret.txt").read_text() == "not yours"


@pytest.mark.parametrize("root_dir", ["", "shared//x", "/shared", "../shared", "shared/..", "."])
def test_bad_root_dir_refused(lib: Path, root_dir: str) -> None:
    with pytest.raises(StorageEscape), open_dir(lib, root_dir):
        pass


def test_walks_nested_directories(lib: Path) -> None:
    ensure_root_dir(lib, "shared")
    (lib / "shared" / "a" / "b").mkdir(parents=True)
    with open_dir(lib, "shared", ["a", "b"]) as fd:
        assert os.fstat(fd).st_ino == (lib / "shared" / "a" / "b").stat().st_ino


def test_symlink_as_root_directory_is_refused(lib: Path, outside: Path) -> None:
    (lib / "personal").mkdir()
    (lib / "personal" / "u").symlink_to(outside, target_is_directory=True)
    with pytest.raises(StorageEscape), open_dir(lib, "personal/u"):
        pass
    with pytest.raises(StorageEscape):
        ensure_root_dir(lib, "personal/u")


def test_symlinked_parent_of_root_is_refused(lib: Path, outside: Path) -> None:
    (lib / "personal").symlink_to(outside, target_is_directory=True)
    with pytest.raises(StorageEscape):
        ensure_root_dir(lib, "personal/u")
    assert not (outside / "u").exists()  # nothing was created through the link


def test_symlink_to_system_directory_is_refused(lib: Path) -> None:
    ensure_root_dir(lib, "shared")
    (lib / "shared" / "etc").symlink_to("/etc", target_is_directory=True)
    with pytest.raises(StorageEscape), open_dir(lib, "shared", ["etc"]):
        pass


def test_symlink_to_another_users_root_is_refused(lib: Path) -> None:
    ensure_root_dir(lib, "personal/alice")
    ensure_root_dir(lib, "personal/bob")
    (lib / "personal" / "bob" / "peek").symlink_to(lib / "personal" / "alice")
    with pytest.raises(StorageEscape), open_dir(lib, "personal/bob", ["peek"]):
        pass


def test_symlink_as_intermediate_component_is_refused(lib: Path, outside: Path) -> None:
    ensure_root_dir(lib, "shared")
    (outside / "b").mkdir()
    (lib / "shared" / "a").symlink_to(outside, target_is_directory=True)
    with pytest.raises(StorageEscape), open_dir(lib, "shared", ["a", "b"]):
        pass


def test_symlink_as_final_component_is_refused(lib: Path, outside: Path) -> None:
    ensure_root_dir(lib, "shared")
    (lib / "shared" / "a").symlink_to(outside, target_is_directory=True)
    with pytest.raises(StorageEscape), open_dir(lib, "shared", ["a"]):
        pass


def test_dangling_symlink_is_escape_not_missing(lib: Path, tmp_path: Path) -> None:
    ensure_root_dir(lib, "shared")
    (lib / "shared" / "a").symlink_to(tmp_path / "does-not-exist")
    with pytest.raises(StorageEscape), open_dir(lib, "shared", ["a"]):
        pass


def test_symlink_swapped_in_after_validation_is_caught_on_next_use(
    lib: Path, outside: Path
) -> None:
    ensure_root_dir(lib, "shared")
    (lib / "shared" / "a").mkdir()
    with open_dir(lib, "shared", ["a"]):
        pass  # fine while it is a real directory
    (lib / "shared" / "a").rmdir()
    (lib / "shared" / "a").symlink_to(outside, target_is_directory=True)
    with pytest.raises(StorageEscape), open_dir(lib, "shared", ["a"]):
        pass


def test_file_in_place_of_directory_is_an_error_not_an_escape(lib: Path) -> None:
    ensure_root_dir(lib, "shared")
    (lib / "shared" / "a").write_text("x")
    with pytest.raises(StorageError) as info, open_dir(lib, "shared", ["a"]):
        pass
    assert not isinstance(info.value, StorageEscape)


def test_missing_paths_report_missing(lib: Path, tmp_path: Path) -> None:
    ensure_root_dir(lib, "shared")
    with pytest.raises(StorageMissing), open_dir(lib, "shared", ["nope"]):
        pass
    with pytest.raises(StorageMissing), open_dir(tmp_path / "no-mount", "shared"):
        pass


def test_filesystem_boundary_is_refused(lib: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ensure_root_dir(lib, "shared")
    (lib / "shared" / "a").mkdir()
    calls: list[int] = []

    def fstat(fd: int) -> os.stat_result:
        r = os.fstat(fd)
        calls.append(fd)
        dev = r.st_dev + (len(calls) - 1)  # every directory below the root "is" a new device
        return os.stat_result(
            (r.st_mode, r.st_ino, dev, r.st_nlink, r.st_uid, r.st_gid, r.st_size, 0, 0, 0)
        )

    proxy: Any = types.SimpleNamespace(**{k: getattr(os, k) for k in dir(os)})
    proxy.fstat = fstat
    monkeypatch.setattr(resolver, "os", proxy)
    with pytest.raises(StorageEscape, match="boundary"), open_dir(lib, "shared", ["a"]):
        pass


def test_corrupted_database_name_cannot_become_traversal(lib: Path, outside: Path) -> None:
    # A node row whose name was altered to '..' must be refused by the resolver itself.
    ensure_root_dir(lib, "personal/u")
    with pytest.raises(StorageEscape), open_dir(lib, "personal/u", ["..", "outside"]):
        pass
