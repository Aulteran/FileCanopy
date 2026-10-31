"""M06c optional backups (ADR-015): settings and volume validation, full and changed-only runs,
chains that restore by hand with `tar`, per-root archives, temporary names and failed runs,
pruning of whole chains with the minimum-chains floor, size estimation and refusal, suspended
roots, encryption and key handling, restore authorization, interrupted-run cleanup and logs.

In-memory SQLite plus per-test temp library and backup volume on a Linux container; all content is
generated. Nothing here calls a cloud service.
"""

import logging
import os
import random
import shutil
import stat
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from test_acl import Fx
from test_auth import World
from test_files import Api, code, disk, nid
from test_reconcile import root_id, row, scan

from app.backup import cli, crypto, retention
from app.backup import engine as engine_mod
from app.backup import restore as restore_mod
from app.backup.engine import BackupEngine, RunResult, open_tar
from app.backup.service import BackupService
from app.backup.store import KEY_KEY, SETTINGS_KEY, load_config
from app.backup.volume import BACKUP_DIR, inspect_volume
from app.config import Settings
from app.db.models import (
    AppSetting,
    AuthAudit,
    BackupChainState,
    BackupRun,
    Node,
    StorageRoot,
)

MARKER = b"TOP-SECRET-MARKER-CONTENT"
MARKER_NAME = "secret-marker-name"


@pytest.fixture
def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., World]:
    def factory(**overrides: Any) -> World:
        volume = tmp_path / "backups"
        volume.mkdir(exist_ok=True)
        values: dict[str, Any] = {
            "backup_path": str(volume),
            "backup_rate_bytes": 0,
            "backup_free_space_reserve_bytes": 0,
            "import_free_space_reserve_bytes": 0,
            "backup_scheduler_enabled": False,
            **overrides,
        }
        return World(tmp_path, monkeypatch, **values)

    return factory


@pytest.fixture
def fx(make_world: Callable[..., World]) -> Fx:
    return Fx(make_world())


@pytest.fixture
def alice(fx: Fx) -> Api:
    return Api(fx, "alice")


# ------------------------------------------------------------------ helpers

PLAIN: dict[str, Any] = {
    "enabled": True,
    "interval_minutes": 60,
    "full_every": 3,
    "min_chains_kept": 2,
    "max_size_bytes": None,
    "encrypt": False,
    "acknowledge_unencrypted": True,
}


def put_settings(fx: Fx, who: tuple[Any, dict[str, str]] | None = None, **over: Any) -> Any:
    c, h = who or fx.admin
    return c.put("/api/v1/admin/backups/settings", json={**PLAIN, **over}, headers=h)


def enable(fx: Fx, **over: Any) -> dict[str, Any]:
    r = put_settings(fx, **over)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def service(fx: Fx) -> BackupService:
    svc: BackupService = fx.w.app.state.backup
    return svc


def lib_root(fx: Fx, anchor: uuid.UUID | None = None) -> Path:
    with fx.w.ctx.sessions() as db:
        root = db.get(StorageRoot, root_id(fx, anchor or fx.alice_root))
        assert root is not None
        return fx.w.settings.library_path / root.relative_dir


def volume_dir(fx: Fx) -> Path:
    return Path(fx.w.settings.backup_path) / BACKUP_DIR


def run(fx: Fx, anchor: uuid.UUID | None = None) -> RunResult:
    return service(fx).engine.run_root(root_id(fx, anchor or fx.alice_root))


def runs(fx: Fx, anchor: uuid.UUID | None = None, status: str | None = "ok") -> list[BackupRun]:
    with fx.w.ctx.sessions() as db:
        q = select(BackupRun).where(BackupRun.root_id == root_id(fx, anchor or fx.alice_root))
        if status:
            q = q.where(BackupRun.status == status)
        rows = list(db.scalars(q.order_by(BackupRun.started_at, BackupRun.seq)))
        for r in rows:
            db.expunge(r)
        return rows


def archives(fx: Fx, anchor: uuid.UUID | None = None) -> list[Path]:
    return [
        volume_dir(fx) / r.archive_name
        for r in runs(fx, anchor)
        if r.archive_name and r.pruned_at is None
    ]


def run_ok(fx: Fx, anchor: uuid.UUID | None = None) -> BackupRun:
    result = run(fx, anchor)
    assert result.status == "ok", result
    fx.w.advance(minutes=61)  # distinct timestamps; makes the next run due
    return next(r for r in runs(fx, anchor) if r.id == result.run_id)


def members(path: Path, key: str | None = None) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with open_tar(path, encrypted=key is not None, recovery_key=key) as tar:
        for m in tar:
            f = tar.extractfile(m) if m.isfile() else None
            out[m.name] = f.read() if f else b""
    return out


def file_names(path: Path, key: str | None = None) -> set[str]:
    return {n[len("files/") :] for n, _ in members(path, key).items() if n.startswith("files/")}


def tree(base: Path) -> dict[str, bytes | None]:
    out: dict[str, bytes | None] = {}
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(base).as_posix()
        out[rel] = p.read_bytes() if p.is_file() else None
    return out


def manual_restore(chain: list[Path], dest: Path, key: str | None = None) -> None:
    """The documented procedure, with the `tar` and `rm` of the host: for each archive in order,
    apply meta/deleted.txt, then extract."""
    scratch = dest.parent / f"{dest.name}-scratch"
    scratch.mkdir(exist_ok=True)
    dest.mkdir(exist_ok=True)
    for a in chain:
        plain = a
        if a.name.endswith(".fcb"):
            plain = scratch / (a.name + ".tar.gz")
            assert key is not None
            with open(a, "rb") as src, open(plain, "wb") as dst:
                reader = crypto.DecryptingReader(src, recovery_key=key)
                while block := reader.read(1024 * 1024):
                    dst.write(block)
        listed = subprocess.run(  # noqa: S603
            ["tar", "-xzOf", str(plain), "meta/deleted.txt"], capture_output=True, check=True
        ).stdout.decode()
        for line in listed.splitlines():
            subprocess.run(["rm", "-rf", "--", str(dest / "files" / line)], check=True)  # noqa: S603
        subprocess.run(["tar", "-xzf", str(plain), "-C", str(dest)], check=True)  # noqa: S603


def blob(n: int, seed: int = 1) -> bytes:
    return random.Random(seed).randbytes(n)  # noqa: S311  (test data, not a key)


def set_root(fx: Fx, anchor: uuid.UUID, **values: Any) -> None:
    with fx.w.ctx.sessions() as db:
        root = db.get(StorageRoot, root_id(fx, anchor))
        assert root is not None
        for k, v in values.items():
            setattr(root, k, v)
        db.commit()


def audit_text(fx: Fx) -> str:
    return " ".join(repr(a.details) for a in fx.w.db_rows(AuthAudit))


# ------------------------------------------------------------------ settings and volume


def test_off_by_default_and_the_app_works_without_a_volume(
    make_world: Callable[..., World],
) -> None:
    fx = Fx(make_world(backup_path=""))
    alice = Api(fx, "alice")
    status = fx.admin[0].get("/api/v1/admin/backups", headers=fx.admin[1]).json()
    assert status["settings"]["enabled"] is False and status["settings"]["encrypt"] is True
    assert status["volume"]["configured"] is False
    assert "volume_not_configured" in status["warnings"]
    r = put_settings(fx)
    assert (r.status_code, code(r)) == (409, "BACKUP_VOLUME_UNAVAILABLE")
    assert nid(alice.upload(fx.taxes, "a.txt", b"still works")) is not None  # CRUD unaffected
    assert service(fx).tick() == []
    service(fx).start()  # no volume: no thread
    service(fx).stop()


def test_off_by_default_with_a_volume(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "a.txt", b"x")
    assert service(fx).tick(force=True) == []  # disabled: nothing runs, nothing is written
    assert not volume_dir(fx).exists()
    assert fx.w.db_rows(BackupRun) == []


@pytest.mark.parametrize("where", ["equal", "inside", "contains"])
@pytest.mark.parametrize("other", ["library", "app-data"])
def test_backup_path_may_not_nest_with_library_or_app_data(
    tmp_path: Path, where: str, other: str
) -> None:
    lib, data = tmp_path / "library", tmp_path / "app-data"
    target = lib if other == "library" else data
    path = {"equal": target, "inside": target / "b", "contains": tmp_path}[where]
    with pytest.raises(ValueError, match="backup_path"):
        Settings(  # type: ignore[call-arg]
            database_url="postgresql+psycopg://u:p@h/db",
            library_path=lib,
            app_data_path=data,
            backup_path=str(path),
        )


def test_separate_backup_path_is_accepted(tmp_path: Path) -> None:
    s = Settings(  # type: ignore[call-arg]
        database_url="postgresql+psycopg://u:p@h/db",
        library_path=tmp_path / "library",
        app_data_path=tmp_path / "app-data",
        backup_path=str(tmp_path / "backups"),
    )
    assert s.backup_path


def test_volume_states_and_same_device_warning(fx: Fx) -> None:
    s = fx.w.settings
    vol = inspect_volume(s)
    assert vol.usable and vol.same_device is True  # a copy on the same disk is not a backup
    status = fx.admin[0].get("/api/v1/admin/backups", headers=fx.admin[1]).json()
    assert "same_device" in status["warnings"]
    path = Path(s.backup_path)
    shutil.rmtree(path)
    assert inspect_volume(s).reason == "missing"
    path.write_text("a file")
    assert inspect_volume(s).reason == "not_a_directory"
    path.unlink()
    target = path.parent / "elsewhere"
    target.mkdir()
    path.symlink_to(target, target_is_directory=True)
    assert inspect_volume(s).reason == "symlink"
    r = put_settings(fx)  # enabling on an unusable volume is refused
    assert (r.status_code, code(r)) == (409, "BACKUP_VOLUME_UNAVAILABLE")


def test_settings_are_validated_audited_and_admin_only(fx: Fx) -> None:
    for bad in (
        {"interval_minutes": 1},
        {"full_every": 0},
        {"min_chains_kept": 0},
        {"max_size_bytes": 0},
        {"interval_minutes": True},
    ):
        assert put_settings(fx, **bad).status_code == 422, bad
    r = put_settings(fx, encrypt=False, acknowledge_unencrypted=False)
    assert (r.status_code, code(r)) == (422, "UNENCRYPTED_NOT_ACKNOWLEDGED")
    ok = enable(fx, interval_minutes=120, full_every=4, max_size_bytes=10**9)
    assert ok["settings"]["interval_minutes"] == 120
    assert "unencrypted" in ok["warnings"]
    row = [a for a in fx.w.db_rows(AuthAudit) if a.action == "backup.settings_changed"][-1]
    assert row.details["before"]["enabled"] is False and row.details["after"]["full_every"] == 4
    alice = Api(fx, "alice")
    assert alice.client.get("/api/v1/admin/backups").status_code == 403
    assert (
        alice.client.put(
            "/api/v1/admin/backups/settings", json=PLAIN, headers=alice.headers
        ).status_code
        == 403
    )
    anon = fx.w.browser()
    assert anon.get("/api/v1/admin/backups").status_code == 401
    no_csrf = fx.admin[0].put("/api/v1/admin/backups/settings", json=PLAIN)
    assert no_csrf.status_code == 403 and code(no_csrf) == "CSRF_TOKEN_INVALID"
    assert load_config_of(fx).full_every == 4


def load_config_of(fx: Fx) -> Any:
    with fx.w.ctx.sessions() as db:
        return load_config(db)


def test_single_chain_warning_and_default_of_two(fx: Fx) -> None:
    assert load_config_of(fx).min_chains_kept == 2
    assert "single_chain" not in enable(fx)["warnings"]
    assert "single_chain" in enable(fx, min_chains_kept=1)["warnings"]


def test_damaged_settings_fail_closed(fx: Fx) -> None:
    enable(fx)
    with fx.w.ctx.sessions() as db:
        row = db.get(AppSetting, SETTINGS_KEY)
        assert row is not None
        row.value = {"enabled": "yes", "interval_minutes": -4, "full_every": "x"}
        db.commit()
    cfg = load_config_of(fx)
    assert cfg.enabled is False and cfg.interval_minutes == 1440 and cfg.full_every == 7


# ------------------------------------------------------------------ full and incremental runs


def test_full_run_contains_everything_and_is_per_root(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "a.txt", b"alpha")
    alice.upload(fx.y2026, "b.txt", b"beta")
    alice.upload(fx.private, "c.txt", b"gamma")
    shared = Api(fx, "bob")
    shared.upload(fx.shared_sub, "s.txt", b"shared one")
    enable(fx)
    r = run_ok(fx)
    assert r.kind == "full" and r.seq == 1 and r.files_added == 3
    a = archives(fx)[0]
    assert a.name == "0001-full.tar.gz" and a.with_name(a.name + ".json").is_file()
    assert a.parent.parent.name.startswith("personal-")  # one folder per root
    got = members(a)
    assert got["files/Taxes/a.txt"] == b"alpha" and got["files/Taxes/2026/b.txt"] == b"beta"
    assert got["files/Private/c.txt"] == b"gamma"
    assert {"files/Taxes", "files/Taxes/Other", "files/Private"} <= set(got)  # empty dirs too
    assert "files/Family/s.txt" not in got  # another root's content is not in this archive
    run_ok(fx, fx.shared_root)
    assert file_names(archives(fx, fx.shared_root)[0]) == {"Family", "Family/s.txt"}
    assert archives(fx)[0].parent.parent != archives(fx, fx.shared_root)[0].parent.parent
    meta = members(a)["meta/metadata.json"]
    assert b"password" not in meta and b"token" not in meta


def test_incremental_add_change_rename_delete_and_chain_restores_by_hand(
    fx: Fx, alice: Api, tmp_path: Path
) -> None:
    a = nid(alice.upload(fx.taxes, "a.txt", b"alpha"))
    b = nid(alice.upload(fx.y2026, "b.txt", b"beta"))
    c = nid(alice.upload(fx.private, "c.txt", b"gamma"))
    alice.upload(fx.private, "keep.txt", b"unchanged")
    enable(fx, full_every=5)
    assert run_ok(fx).kind == "full"

    time.sleep(0.01)
    (disk(fx, a)).write_bytes(b"alpha changed and longer")  # change (outside the app)
    scan(fx)
    alice.upload(fx.other, "d.txt", b"delta")  # add
    assert alice.rename(c, "c2.txt", row(fx, c).revision).status_code == 200  # rename
    disk(fx, b).unlink()  # goes missing, then is dismissed: a deletion
    scan(fx)
    assert alice.post(f"/nodes/{b}/dismiss").status_code == 200
    inc = run_ok(fx)
    assert inc.kind == "incremental" and inc.seq == 2
    names = file_names(archives(fx)[1])
    assert names == {"Taxes/a.txt", "Taxes/Other/d.txt", "Private/c2.txt"}
    deleted = members(archives(fx)[1])["meta/deleted.txt"].decode().split()
    assert sorted(deleted) == ["Private/c.txt", "Taxes/2026/b.txt"]
    assert inc.files_added == 2 and inc.files_changed == 1 and inc.files_deleted == 2

    third = run_ok(fx)  # nothing changed
    assert third.seq == 3 and file_names(archives(fx)[2]) == set()

    dest = tmp_path / "restored"
    manual_restore(archives(fx), dest)
    assert tree(dest / "files") == tree(lib_root(fx))


def test_missing_files_are_recorded_not_deleted_and_stay_in_the_restore(
    fx: Fx, alice: Api, tmp_path: Path
) -> None:
    f = nid(alice.upload(fx.taxes, "gone.txt", b"was here"))
    alice.upload(fx.taxes, "stay.txt", b"stay")
    enable(fx, full_every=5)
    run_ok(fx)
    disk(fx, f).unlink()
    scan(fx)  # becomes `missing`, never deleted
    second = run_ok(fx)
    assert second.files_missing == 1 and second.files_deleted == 0
    got = members(archives(fx)[1])
    assert got["meta/missing.txt"].decode().split() == ["Taxes/gone.txt"]
    assert got["meta/deleted.txt"] == b""
    dest = tmp_path / "r1"
    manual_restore(archives(fx), dest)
    assert (dest / "files/Taxes/gone.txt").read_bytes() == b"was here"  # last copy is kept
    assert alice.post(f"/nodes/{f}/dismiss").status_code == 200
    third = run_ok(fx)
    assert third.files_deleted == 1
    dest2 = tmp_path / "r2"
    manual_restore(archives(fx), dest2)
    assert (
        not (dest2 / "files/Taxes/gone.txt").exists() and (dest2 / "files/Taxes/stay.txt").exists()
    )


def test_a_file_replaced_by_a_directory_restores(fx: Fx, alice: Api, tmp_path: Path) -> None:
    x = nid(alice.upload(fx.taxes, "x", b"a file"))
    enable(fx, full_every=5)
    run_ok(fx)
    disk(fx, x).unlink()
    scan(fx)
    assert alice.post(f"/nodes/{x}/dismiss").status_code == 200
    (lib_root(fx) / "Taxes" / "x").mkdir()
    (lib_root(fx) / "Taxes" / "x" / "inside.txt").write_bytes(b"now a folder")
    scan(fx)
    run_ok(fx)
    dest = tmp_path / "r"
    manual_restore(archives(fx), dest)
    assert tree(dest / "files") == tree(lib_root(fx))


def test_chain_rolls_over_after_full_every_runs(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "a.txt", b"a")
    enable(fx, full_every=3)
    kinds = [run_ok(fx).kind for _ in range(7)]
    assert kinds == [
        "full",
        "incremental",
        "incremental",
        "full",
        "incremental",
        "incremental",
        "full",
    ]
    chains = {r.chain_id for r in runs(fx)}
    assert len(chains) == 3


def test_unchanged_files_are_not_rewritten_and_hashing_is_not_needed(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "a.txt", b"a")
    enable(fx, full_every=5)
    run_ok(fx)
    run_ok(fx)
    assert file_names(archives(fx)[1]) == set()


def test_unsupported_entries_symlinks_and_staging_files_are_skipped(fx: Fx, alice: Api) -> None:
    root = lib_root(fx)
    alice.upload(fx.taxes, "good.txt", b"good")
    alice.upload(fx.taxes, "h1.txt", b"hardlinked")
    os.link(root / "Taxes" / "h1.txt", root / "Taxes" / "h2.txt")
    os.symlink("good.txt", root / "Taxes" / "link")
    os.mkfifo(root / "Taxes" / "pipe")
    (root / "Taxes" / ".filecanopy-tmp-abc").write_bytes(b"staging")
    fd = os.open(os.fsencode(root) + b"/Taxes/\xff-bad.txt", os.O_CREAT | os.O_WRONLY, 0o640)
    os.write(fd, b"undecodable name")
    os.close(fd)
    mounted = nid(alice.mkdir(fx.taxes, "Mounted"))
    alice.upload(mounted, "inside.txt", b"behind a mounted drive")
    scan(fx)
    with fx.w.ctx.sessions() as db:  # the scanner marks a mounted drive like this
        node = db.get(Node, mounted)
        assert node is not None
        node.unsupported_reason = "mounted_drive"
        db.commit()
    enable(fx)
    r = run_ok(fx)
    assert r.status == "ok"
    names = file_names(archives(fx)[0])
    assert "Taxes/good.txt" in names
    forbidden = {"Taxes/h1.txt", "Taxes/h2.txt", "Taxes/link", "Taxes/pipe", "Taxes/Mounted"}
    assert not forbidden & names
    assert not any("tmp" in n or "inside" in n or "bad" in n for n in names)


def test_suspended_or_inactive_roots_are_never_backed_up(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "a.txt", b"a")
    enable(fx)
    for values in (
        {"scan_state": "suspended"},
        {"status": "unsupported_mount"},
        {"status": "missing"},
        {"scan_paused_since": datetime.now(UTC)},
    ):
        set_root(fx, fx.alice_root, scan_state=None, status="active", scan_paused_since=None)
        set_root(fx, fx.alice_root, **values)
        result = run(fx)
        assert result.status == "skipped", values
        assert runs(fx, status=None) == []  # nothing recorded, so nothing counts toward retention
        assert not volume_dir(fx).exists() or not any(volume_dir(fx).rglob("*.tar.gz"))
    set_root(fx, fx.alice_root, scan_state=None, status="active", scan_paused_since=None)
    assert run(fx).status == "ok"
    set_root(fx, fx.alice_root, scan_state="suspended")
    ticks = service(fx).tick(force=True)
    assert all(res.status in ("skipped", "ok") for _, res in ticks)
    status = fx.admin[0].get("/api/v1/admin/backups", headers=fx.admin[1]).json()
    assert any(r["status"] == "scan_suspended" for r in status["roots"])


def test_root_suspended_while_running_discards_the_run(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice.upload(fx.taxes, "a.txt", b"a")
    enable(fx)
    real = engine_mod.verify_archive

    def suspend_then_verify(*a: Any, **k: Any) -> Any:
        set_root(fx, fx.alice_root, scan_state="suspended")
        return real(*a, **k)

    monkeypatch.setattr(engine_mod, "verify_archive", suspend_then_verify)
    result = run(fx)
    assert (result.status, result.code) == ("failed", "ROOT_CHANGED_DURING_BACKUP")
    assert not list(volume_dir(fx).rglob("*.tar.gz")) and not list(volume_dir(fx).rglob(".tmp-*"))
    with fx.w.ctx.sessions() as db:
        assert db.get(BackupChainState, root_id(fx, fx.alice_root)) is None


# ------------------------------------------------------------------ files that change while read


def test_file_changing_during_the_read_is_retried_once_then_deferred(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice.upload(fx.taxes, "busy.txt", b"busy file")
    alice.upload(fx.taxes, "calm.txt", b"calm file")
    enable(fx, full_every=5)
    real = engine_mod.read_stable
    calls = {"busy": 0}

    def flaky(fd: int, info: os.stat_result, throttle: Any) -> bytes:
        data = real(fd, info, throttle)
        if data == b"busy file":
            calls["busy"] += 1
            if calls["busy"] <= 1:
                raise engine_mod.UnstableFile
        return data

    monkeypatch.setattr(engine_mod, "read_stable", flaky)
    first = run_ok(fx)  # fails once, succeeds on the retry
    assert first.files_unstable == 0 and "Taxes/busy.txt" in file_names(archives(fx)[0])

    def always(fd: int, info: os.stat_result, throttle: Any) -> bytes:
        data = real(fd, info, throttle)
        if data == b"busy file changed":
            raise engine_mod.UnstableFile
        return data

    monkeypatch.setattr(engine_mod, "read_stable", always)
    (lib_root(fx) / "Taxes" / "busy.txt").write_bytes(b"busy file changed")
    scan(fx)
    second = run_ok(fx)
    assert second.files_unstable == 1 and file_names(archives(fx)[1]) == set()
    manifest = members(archives(fx)[1])["meta/manifest.json"]
    assert b"changed_during_backup" in manifest and b"Taxes/busy.txt" in manifest
    monkeypatch.setattr(engine_mod, "read_stable", real)
    third = run_ok(fx)  # picked up by the next run
    assert file_names(archives(fx)[2]) == {"Taxes/busy.txt"} and third.files_changed == 1


def test_large_file_that_shrinks_while_streamed_is_flagged(
    make_world: Callable[..., World], monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = Fx(make_world(backup_buffer_file_bytes=10))
    alice = Api(fx, "alice")
    big = nid(alice.upload(fx.taxes, "big.bin", blob(200_000)))
    enable(fx, full_every=5)
    real = engine_mod.StreamSource.read
    state = {"cut": False}

    def shrinking(self: Any, n: int = -1) -> bytes:
        if not state["cut"]:
            state["cut"] = True
            os.truncate(disk(fx, big), 1000)
        return real(self, n)

    monkeypatch.setattr(engine_mod.StreamSource, "read", shrinking)
    r = run_ok(fx)
    assert r.files_unstable == 1
    monkeypatch.setattr(engine_mod.StreamSource, "read", real)
    scan(fx)
    again = run_ok(fx)  # the next run captures the real file
    assert again.files_added == 1 and again.files_unstable == 0  # never captured: added


# ------------------------------------------------------------------ temporary names and failures


def test_failed_run_leaves_no_partial_archive_and_does_not_advance_the_chain(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice.upload(fx.taxes, "a.txt", b"a")
    enable(fx, full_every=5)
    run_ok(fx)
    alice.upload(fx.taxes, "b.txt", b"b")
    with fx.w.ctx.sessions() as db:
        before = db.get(BackupChainState, root_id(fx, fx.alice_root))
        assert before is not None
        snapshot_before = bytes(before.snapshot)

    def boom(*a: Any, **k: Any) -> Any:
        raise ValueError("archive failed verification")

    real_verify = engine_mod.verify_archive
    monkeypatch.setattr(engine_mod, "verify_archive", boom)
    result = run(fx)
    assert (result.status, result.code) == ("failed", "BACKUP_FAILED")
    leftovers = [p for p in volume_dir(fx).rglob("*") if p.is_file()]
    assert sorted(p.name for p in leftovers) == [
        "0001-full.tar.gz",
        "0001-full.tar.gz.json",
    ]  # no temporary file, no half-named archive, no second archive
    with fx.w.ctx.sessions() as db:
        after = db.get(BackupChainState, root_id(fx, fx.alice_root))
        assert after is not None and bytes(after.snapshot) == snapshot_before and after.seq == 1
    monkeypatch.setattr(engine_mod, "verify_archive", real_verify)
    fx.w.advance(minutes=61)
    nxt = run_ok(fx)
    assert nxt.seq == 2 and file_names(archives(fx)[1]) == {"Taxes/b.txt"}  # nothing was lost


def test_disk_full_is_reported_and_cleaned_up(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno

    alice.upload(fx.taxes, "a.txt", blob(300_000))
    enable(fx)

    def full(self: Any, data: Any) -> int:
        raise OSError(errno.ENOSPC, "no space")

    monkeypatch.setattr(engine_mod.HashingSink, "write", full)
    result = run(fx)
    assert (result.status, result.code) == ("failed", "VOLUME_FULL")
    assert not [p for p in volume_dir(fx).rglob("*") if p.is_file()]
    status = fx.admin[0].get("/api/v1/admin/backups", headers=fx.admin[1]).json()
    assert "last_run_failed" in status["warnings"]


def test_interrupted_run_is_cleaned_up_at_start(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "a.txt", b"a")
    enable(fx)
    run_ok(fx)
    chain_dir = archives(fx)[0].parent
    other = chain_dir.parent / "20260101T000000Z-dead"
    other.mkdir()
    partial = other / "0001-full.tar.gz"
    partial.write_bytes(b"half")
    (other / ".tmp-1234.part").write_bytes(b"half")
    (chain_dir / ".tmp-9999.part").write_bytes(b"stray")
    with fx.w.ctx.sessions() as db:
        db.add(
            BackupRun(
                root_id=root_id(fx, fx.alice_root),
                chain_id="20260101T000000Z-dead",
                seq=1,
                kind="full",
                status="running",
                started_at=engine_mod.clock.utcnow(),
                archive_name=f"{chain_dir.parent.name}/20260101T000000Z-dead/0001-full.tar.gz",
            )
        )
        db.commit()
    svc = service(fx)
    svc.start()  # recovery runs at start
    svc.stop()
    assert not other.exists() and not (chain_dir / ".tmp-9999.part").exists()
    assert archives(fx)[0].is_file()  # the good chain is untouched
    failed = runs(fx, status="failed")
    assert [r.error_code for r in failed] == ["INTERRUPTED"]


# ------------------------------------------------------------------ pruning, floor and size


def populate(fx: Fx, alice: Api, size: int = 100_000) -> None:
    alice.upload(fx.taxes, "big.bin", blob(size))


def chains_on_disk(fx: Fx, anchor: uuid.UUID | None = None) -> list[str]:
    return sorted({r.chain_id for r in runs(fx, anchor) if r.pruned_at is None})


def test_pruning_removes_whole_oldest_chains_after_a_verified_run(fx: Fx, alice: Api) -> None:
    populate(fx, alice)
    enable(fx, full_every=2, min_chains_kept=2, max_size_bytes=10**9)
    for _ in range(4):  # two chains of two runs
        run_ok(fx)
    one_chain = sum(r.size_bytes or 0 for r in runs(fx) if r.chain_id == runs(fx)[0].chain_id)
    assert len(chains_on_disk(fx)) == 2
    enable(fx, full_every=2, min_chains_kept=2, max_size_bytes=int(one_chain * 2.5))
    for _ in range(4):
        run_ok(fx)
    live = runs(fx)
    chains = chains_on_disk(fx)
    assert len(chains) == 2  # never fewer than the floor, never a partial chain
    for c in chains:
        assert sorted(r.seq for r in live if r.chain_id == c) == [1, 2]
    pruned_rows = [a for a in fx.w.db_rows(AuthAudit) if a.action == "backup.pruned"]
    assert pruned_rows and all(
        set(a.details) >= {"chain_id", "reason", "usage_before", "usage_after", "free_before"}
        for a in pruned_rows
    )
    assert not any("big.bin" in repr(a.details) for a in pruned_rows)
    on_disk = {p.parent.name for p in volume_dir(fx).rglob("*.tar.gz")}
    assert on_disk == set(chains)


def test_minimum_chains_are_never_pruned_and_over_limit_is_reported(fx: Fx, alice: Api) -> None:
    populate(fx, alice)
    enable(fx, full_every=1, min_chains_kept=3, max_size_bytes=10**9)
    for _ in range(3):
        run_ok(fx)
    enable(fx, full_every=1, min_chains_kept=3, max_size_bytes=1000)  # far too small
    assert run(fx).status == "refused"  # honouring the floor leaves no room: it does not start
    assert len(chains_on_disk(fx)) == 3
    status = fx.admin[0].get("/api/v1/admin/backups", headers=fx.admin[1]).json()
    assert "over_limit" in status["warnings"]


def test_run_that_cannot_fit_without_breaking_the_floor_does_not_start(fx: Fx, alice: Api) -> None:
    populate(fx, alice, 50_000)
    enable(fx, full_every=1, min_chains_kept=2, max_size_bytes=10**9)
    run_ok(fx)
    run_ok(fx)
    size = sum(r.size_bytes or 0 for r in runs(fx))
    enable(fx, full_every=1, min_chains_kept=2, max_size_bytes=size)  # no room for a third
    before = {p for p in volume_dir(fx).rglob("*") if p.is_file()}
    result = run(fx)
    assert (result.status, result.code) == ("refused", "BACKUP_VOLUME_TOO_SMALL")
    assert {p for p in volume_dir(fx).rglob("*") if p.is_file()} == before  # nothing pruned
    assert len(chains_on_disk(fx)) == 2
    refused = runs(fx, status="refused")
    assert len(refused) == 1 and refused[0].archive_name is None
    status = fx.admin[0].get("/api/v1/admin/backups", headers=fx.admin[1]).json()
    assert "volume_too_small" in status["warnings"]
    fx.w.advance(minutes=61)
    assert run(fx).status == "refused"  # still too small: retried each interval, never forced


def test_a_failed_run_never_prunes(fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    populate(fx, alice, 40_000)
    enable(fx, full_every=1, min_chains_kept=2, max_size_bytes=10**9)
    for _ in range(3):
        run_ok(fx)
    # feasible (old chains could go) but a success would exceed the limit and have to prune
    enable(fx, full_every=1, min_chains_kept=2, max_size_bytes=150_000)
    monkeypatch.setattr(engine_mod, "verify_archive", lambda *a, **k: 1 / 0)
    assert run(fx).status == "failed"
    assert len(chains_on_disk(fx)) == 3  # the old chains are still all there


def test_estimate_uses_the_compression_of_the_last_run(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "text.txt", b"abc " * 600_000)  # compresses very well
    enable(fx, full_every=5)
    run_ok(fx)
    with fx.w.ctx.sessions() as db:
        raw = 5_000_000
        est = retention.estimate_run_bytes(db, root_id(fx, fx.alice_root), raw)
        assert est < raw // 4
        assert retention.estimate_run_bytes(db, uuid.uuid4(), raw) == raw + 16 * 1024  # 1:1


def test_volume_free_space_is_checked_before_starting(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    populate(fx, alice)
    enable(fx)
    real = retention.inspect_volume
    from app.backup.volume import VolumeStatus

    def tiny(settings: Settings) -> VolumeStatus:
        v = real(settings)
        return VolumeStatus(v.configured, v.usable, v.reason, v.same_device, 1000, v.total_bytes)

    monkeypatch.setattr(retention, "inspect_volume", tiny)
    result = run(fx)
    assert (result.status, result.code) == ("refused", "BACKUP_VOLUME_TOO_SMALL")


def test_chains_of_an_inactive_root_are_never_pruned(fx: Fx, alice: Api) -> None:
    populate(fx, alice, 40_000)
    Api(fx, "bob").upload(fx.shared_sub, "s.bin", blob(40_000, 2))
    enable(fx, full_every=1, min_chains_kept=1, max_size_bytes=10**9)
    for _ in range(3):
        run_ok(fx, fx.shared_root)  # three old shared-tree chains
    set_root(fx, fx.shared_root, scan_state="suspended")  # its scan is suspended now
    enable(fx, full_every=1, min_chains_kept=1, max_size_bytes=190_000)
    for _ in range(5):
        run_ok(fx)  # alice's runs need room: only her own old chains may go
    assert len(chains_on_disk(fx, fx.shared_root)) == 3  # the frozen root keeps its history
    assert len(chains_on_disk(fx)) == 1
    pruned = [a for a in fx.w.db_rows(AuthAudit) if a.action == "backup.pruned"]
    assert pruned and all(a.details["root_id"] == str(root_id(fx, fx.alice_root)) for a in pruned)


# ------------------------------------------------------------------ scheduling


def test_scheduler_runs_due_roots_only(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "a.txt", b"a")
    enable(fx, interval_minutes=60)
    svc = service(fx)
    first = svc.tick()
    assert {res.status for _, res in first} == {"ok"} and len(first) >= 2  # shared + personal
    assert svc.tick() == []  # nothing is due yet
    fx.w.advance(minutes=59)
    assert svc.tick() == []
    fx.w.advance(minutes=2)
    assert len(svc.tick()) == len(first)
    enable(fx, enabled=False)
    fx.w.advance(minutes=600)
    assert svc.tick(force=True) == []


def test_run_now_and_rate_limit(fx: Fx) -> None:
    svc = service(fx)
    c, h = fx.admin
    assert c.post("/api/v1/admin/backups/run", headers=h).status_code == 409  # not enabled
    enable(fx)
    svc._last_manual = 0.0  # the settings change asked for a run
    assert c.post("/api/v1/admin/backups/run", headers=h).status_code == 202
    again = c.post("/api/v1/admin/backups/run", headers=h)
    assert (again.status_code, code(again)) == (429, "BACKUP_RUN_TOO_SOON")
    assert (
        Api(fx, "alice")
        .client.post("/api/v1/admin/backups/run", headers=Api(fx, "alice").headers)
        .status_code
        == 403
    )


def test_scheduler_thread_starts_and_stops(make_world: Callable[..., World]) -> None:
    fx = Fx(make_world(backup_scheduler_enabled=True, backup_check_seconds=1))
    svc = service(fx)
    svc.start()
    svc.start()  # idempotent
    time.sleep(0.3)
    svc.stop()
    assert threading.active_count() >= 1


# ------------------------------------------------------------------ encryption and keys


def test_archives_are_encrypted_by_default_with_a_one_time_key(
    fx: Fx, alice: Api, tmp_path: Path
) -> None:
    alice.upload(fx.taxes, MARKER_NAME + ".txt", MARKER)
    first = enable(fx, encrypt=True, acknowledge_unencrypted=False)
    key = first["recovery_key"]
    assert key.startswith("fcbk1-") and first["key"]["present"] is True
    assert enable(fx, encrypt=True)["recovery_key"] is None  # shown once
    status = fx.admin[0].get("/api/v1/admin/backups", headers=fx.admin[1]).json()
    assert status["recovery_key"] is None and status["key"]["fingerprint"]
    r = run_ok(fx)
    assert r.encrypted and r.key_fingerprint == crypto.recovery_key_fingerprint(key)
    a = archives(fx)[0]
    raw = a.read_bytes()
    assert a.name == "0001-full.tar.gz.fcb" and raw[:4] == b"FCB2"
    assert MARKER not in raw and MARKER_NAME.encode() not in raw  # not even in file names
    assert b"\x1f\x8b" not in raw[:8]
    assert members(a, key)[f"files/Taxes/{MARKER_NAME}.txt"] == MARKER
    # the recovery key is nowhere on the instance
    needle = key.removeprefix("fcbk1-")
    stored = " ".join(repr(s.value) for s in fx.w.db_rows(AppSetting))
    assert needle not in stored and "fcbk1" not in stored
    assert needle not in audit_text(fx)
    dumped = " ".join(repr((x.chain_id, x.error_code)) for x in fx.w.db_rows(BackupRun))
    assert needle not in dumped
    for f in volume_dir(fx).rglob("*"):
        if f.is_file():
            assert needle.encode() not in f.read_bytes()
    # by hand: decrypt, then tar
    dest = tmp_path / "hand"
    manual_restore(archives(fx), dest, key)
    assert (dest / f"files/Taxes/{MARKER_NAME}.txt").read_bytes() == MARKER


def test_wrong_truncated_and_damaged_archives_fail_cleanly(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "a.txt", blob(150_000))
    key = enable(fx, encrypt=True, acknowledge_unencrypted=False)["recovery_key"]
    run_ok(fx)
    a = archives(fx)[0]
    other, _, _ = crypto.generate_keypair()
    with pytest.raises(crypto.DecryptionError):
        members(a, other)
    raw = a.read_bytes()
    cut = a.with_name("cut.fcb")
    cut.write_bytes(raw[: len(raw) // 2])
    with pytest.raises(crypto.DecryptionError):
        members(cut, key)
    flipped = bytearray(raw)
    flipped[len(raw) // 2] ^= 0x40
    bad = a.with_name("bad.fcb")
    bad.write_bytes(bytes(flipped))
    with pytest.raises(crypto.DecryptionError):
        members(bad, key)
    with pytest.raises(crypto.BadKey):
        crypto.parse_recovery_key("not-a-key")


def test_crypto_round_trips_for_every_chunk_boundary() -> None:
    import io

    recovery, public, _ = crypto.generate_keypair()
    for size in (0, 1, crypto.CHUNK - 1, crypto.CHUNK, crypto.CHUNK + 1, 3 * crypto.CHUNK + 7):
        data = blob(size, size)
        sink = io.BytesIO()
        w = crypto.EncryptingWriter(sink, crypto.parse_public_key(public))
        w.write(data)
        w.close()
        out = crypto.DecryptingReader(io.BytesIO(sink.getvalue()), recovery_key=recovery)
        assert out.readall() == data
        # the writer's own key reads it too (used to verify a run without the recovery key)
        again = crypto.DecryptingReader(io.BytesIO(sink.getvalue()), file_key=w.file_key)
        assert again.readall() == data


def test_key_loss_rotation_starts_a_new_chain_and_old_chains_need_the_old_key(
    fx: Fx, alice: Api
) -> None:
    alice.upload(fx.taxes, "a.txt", b"original")
    old = enable(fx, encrypt=True, acknowledge_unencrypted=False, full_every=5)["recovery_key"]
    run_ok(fx)
    fx.w.advance(minutes=1)
    c, h = fx.admin
    rotated = c.post("/api/v1/admin/backups/key/rotate", headers=h).json()
    new = rotated["recovery_key"]
    assert new and new != old
    r2 = run_ok(fx)
    assert r2.kind == "full" and r2.seq == 1  # a new chain for the new key
    old_run, new_run = runs(fx)
    assert old_run.key_fingerprint != new_run.key_fingerprint
    assert members(archives(fx)[1], new)
    with pytest.raises(crypto.DecryptionError):
        members(archives(fx)[1], old)
    row = [a for a in fx.w.db_rows(AuthAudit) if a.action == "backup.key_rotated"][0]
    assert new not in repr(row.details) and old not in repr(row.details)


def test_losing_the_stored_key_refuses_runs_instead_of_writing_plain_archives(
    fx: Fx, alice: Api
) -> None:
    alice.upload(fx.taxes, "a.txt", b"a")
    enable(fx, encrypt=True, acknowledge_unencrypted=False)
    with fx.w.ctx.sessions() as db:
        row = db.get(AppSetting, KEY_KEY)
        assert row is not None
        db.delete(row)
        db.commit()
    result = run(fx)
    assert (result.status, result.code) == ("refused", "BACKUP_KEY_MISSING")
    assert not list(volume_dir(fx).rglob("*.tar.gz*"))
    status = fx.admin[0].get("/api/v1/admin/backups", headers=fx.admin[1]).json()
    assert "key_missing" in status["warnings"]
    rotated = fx.admin[0].post("/api/v1/admin/backups/key/rotate", headers=fx.admin[1]).json()
    assert rotated["recovery_key"]  # recovery path: make a new key, back up again
    fx.w.advance(minutes=61)
    assert run(fx).status == "ok"


def test_changing_encryption_starts_a_new_chain(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "a.txt", b"a")
    enable(fx, full_every=5)
    assert run_ok(fx).kind == "full"
    assert run_ok(fx).kind == "incremental"
    enable(fx, encrypt=True, acknowledge_unencrypted=False, full_every=5)
    third = run_ok(fx)
    assert third.kind == "full" and third.encrypted


def test_missing_previous_archive_forces_a_full_run(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "a.txt", b"a")
    enable(fx, full_every=5)
    run_ok(fx)
    archives(fx)[0].unlink()  # the volume was tampered with or replaced
    assert run_ok(fx).kind == "full"


# ------------------------------------------------------------------ command line


def test_cli_verify_list_and_decrypt(
    fx: Fx, alice: Api, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    alice.upload(fx.taxes, "a.txt", blob(50_000))
    key = enable(fx, encrypt=True, acknowledge_unencrypted=False)["recovery_key"]
    run_ok(fx)
    a = archives(fx)[0]
    key_file = tmp_path / "key.txt"
    key_file.write_text(key)
    assert cli.main(["verify", str(a), "--key-file", str(key_file)]) == 0
    assert cli.main(["verify", str(a)]) == 0  # without the key: checksum against the index
    wrong = tmp_path / "wrong.txt"
    wrong.write_text(crypto.generate_keypair()[0])
    assert cli.main(["verify", str(a), "--key-file", str(wrong)]) == 1
    damaged = a.with_name("damaged.fcb")
    damaged.write_bytes(a.read_bytes()[:-5])
    assert cli.main(["verify", str(damaged)]) == 0  # no index file for it: structure unreadable
    assert cli.main(["verify", str(damaged), "--key-file", str(key_file)]) == 1
    flipped = bytearray(a.read_bytes())
    flipped[-3] ^= 1
    a.write_bytes(bytes(flipped))
    assert cli.main(["verify", str(a)]) == 1  # checksum differs from the index
    out = tmp_path / "plain.tar.gz"
    a.write_bytes(bytes(flipped[:-3]) + bytes([flipped[-3] ^ 1]) + bytes(flipped[-2:]))
    assert cli.main(["decrypt", str(a), str(out), "--key-file", str(key_file)]) == 0
    assert out.read_bytes()[:2] == b"\x1f\x8b"
    assert cli.main(["decrypt", str(a), str(out), "--key-file", str(key_file)]) == 2  # no overwrite
    assert cli.main(["list", str(volume_dir(fx))]) == 0
    text = capsys.readouterr().out
    assert "full" in text and "encrypted" in text and "a.txt" not in text


# ------------------------------------------------------------------ restore


def make_restore_fixture(fx: Fx, alice: Api, encrypt: bool = True) -> str | None:
    alice.upload(fx.taxes, "a.txt", b"alpha")
    alice.upload(fx.y2026, "b.txt", b"beta")
    body = enable(fx, encrypt=encrypt, acknowledge_unencrypted=not encrypt, full_every=5)
    run_ok(fx)
    key: str | None = body["recovery_key"]
    return key


def restore(who: Api | tuple[Any, dict[str, str]], node: uuid.UUID, **body: Any) -> Any:
    c, h = (who.client, who.headers) if isinstance(who, Api) else who
    return c.post(f"/api/v1/nodes/{node}/backups/restore", json=body, headers=h)


def test_owner_restores_into_a_new_folder_and_nothing_is_overwritten(fx: Fx, alice: Api) -> None:
    key = make_restore_fixture(fx, alice)
    (lib_root(fx) / "Taxes" / "a.txt").write_bytes(b"alpha edited after the backup")
    before = tree(lib_root(fx))
    points = alice.get(f"/nodes/{fx.alice_root}/backups").json()
    assert len(points) == 1 and "path" not in points[0] and points[0]["kind"] == "full"
    r = restore(alice, fx.alice_root, recovery_key=key)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["files"] == 2 and body["folder_name"].startswith("Restored from backup ")
    folder = lib_root(fx) / body["folder_name"]
    assert (folder / "Taxes" / "a.txt").read_bytes() == b"alpha"
    assert (folder / "Taxes" / "2026" / "b.txt").read_bytes() == b"beta"
    after = tree(lib_root(fx))
    live = {k: v for k, v in after.items() if not k.startswith(body["folder_name"])}
    assert live == before  # the existing files were not touched
    assert body["folder_node_id"] is not None
    kids = alice.children(uuid.UUID(body["folder_node_id"]))
    assert sorted(k["name"] for k in kids) == ["Private", "Taxes"]  # registered with the app
    second = restore(alice, fx.alice_root, recovery_key=key).json()
    assert second["folder_name"] != body["folder_name"]  # never reuses a name
    assert (lib_root(fx) / second["folder_name"] / "Taxes" / "a.txt").read_bytes() == b"alpha"
    ok = [a for a in fx.w.db_rows(AuthAudit) if a.action == "backup.restored"]
    assert len(ok) == 2 and key not in repr(ok[0].details)


def test_restore_points_can_be_chosen_through_a_chain(fx: Fx, alice: Api) -> None:
    key = make_restore_fixture(fx, alice)
    alice.upload(fx.taxes, "later.txt", b"later")
    run_ok(fx)
    points = alice.get(f"/nodes/{fx.alice_root}/backups").json()
    assert [p["kind"] for p in points] == ["incremental", "full"]
    old = next(p for p in points if p["kind"] == "full")
    r = restore(alice, fx.alice_root, recovery_key=key, run_id=old["id"]).json()
    assert not (lib_root(fx) / r["folder_name"] / "Taxes" / "later.txt").exists()
    r2 = restore(alice, fx.alice_root, recovery_key=key).json()
    assert (lib_root(fx) / r2["folder_name"] / "Taxes" / "later.txt").read_bytes() == b"later"
    assert (
        restore(alice, fx.alice_root, recovery_key=key, run_id=str(uuid.uuid4())).status_code == 404
    )


def test_web_administrator_cannot_restore_or_list_a_personal_root(fx: Fx, alice: Api) -> None:
    key = make_restore_fixture(fx, alice)
    before = tree(lib_root(fx))
    admin2 = signed_in_pair(fx, "admin2@example.com")
    for who in (fx.admin, admin2, Api(fx, "bob"), Api(fx, "dave")):
        r = restore(who, fx.alice_root, recovery_key=key)
        assert (r.status_code, code(r)) == (403, "FORBIDDEN")
        c, h = (who.client, who.headers) if isinstance(who, Api) else who
        assert c.get(f"/api/v1/nodes/{fx.alice_root}/backups", headers=h).status_code == 403
    assert tree(lib_root(fx)) == before
    anon = fx.w.browser()
    assert (
        anon.post(
            f"/api/v1/nodes/{fx.alice_root}/backups/restore", json={"recovery_key": key}
        ).status_code
        == 401
    )
    no_csrf = alice.client.post(
        f"/api/v1/nodes/{fx.alice_root}/backups/restore", json={"recovery_key": key}
    )
    assert no_csrf.status_code == 403 and code(no_csrf) == "CSRF_TOKEN_INVALID"
    denied = [a for a in fx.w.db_rows(AuthAudit) if a.action == "access.denied"]
    assert len(denied) >= 8


def signed_in_pair(fx: Fx, email: str) -> tuple[Any, dict[str, str]]:
    from test_auth import signed_in

    return signed_in(fx.w, email)


def test_shared_tree_is_restored_by_an_administrator_only(fx: Fx, alice: Api) -> None:
    Api(fx, "bob").upload(fx.shared_sub, "s.txt", b"shared")
    key = enable(fx)["recovery_key"]
    assert key is None  # plain archives need no key
    run_ok(fx, fx.shared_root)
    assert restore(alice, fx.shared_root).status_code == 403  # an editor of the tree is not enough
    admin2 = signed_in_pair(fx, "admin2@example.com")
    r = restore(admin2, fx.shared_root)
    assert r.status_code == 200, r.text
    assert (
        lib_root(fx, fx.shared_root) / r.json()["folder_name"] / "Family" / "s.txt"
    ).read_bytes() == b"shared"


def test_restore_checks_the_key_and_refuses_a_wrong_one(fx: Fx, alice: Api) -> None:
    key = make_restore_fixture(fx, alice)
    before = tree(lib_root(fx))
    other = crypto.generate_keypair()[0]
    for bad, expected in (
        (other, "BACKUP_KEY_MISMATCH"),
        ("garbage", "BACKUP_KEY_INVALID"),
        (None, "BACKUP_KEY_INVALID"),
    ):
        r = restore(alice, fx.alice_root, recovery_key=bad)
        assert (r.status_code, code(r)) == (422, expected)
    assert tree(lib_root(fx)) == before  # no folder left behind
    assert key
    failures = [a for a in fx.w.db_rows(AuthAudit) if a.action == "backup.restore_failed"]
    assert len(failures) == 3 and "garbage" not in repr([f.details for f in failures])


def test_damaged_archive_leaves_no_half_restored_folder(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "a.bin", blob(300_000))
    key = enable(fx, encrypt=True, acknowledge_unencrypted=False)["recovery_key"]
    run_ok(fx)
    a = archives(fx)[0]
    data = bytearray(a.read_bytes())
    data[len(data) * 3 // 4] ^= 0x10
    a.write_bytes(bytes(data))
    before = tree(lib_root(fx))
    r = restore(alice, fx.alice_root, recovery_key=key)
    assert (r.status_code, code(r)) == (409, "BACKUP_ARCHIVE_DAMAGED")
    assert tree(lib_root(fx)) == before
    a.unlink()
    r2 = restore(alice, fx.alice_root, recovery_key=key)
    assert (r2.status_code, code(r2)) == (409, "BACKUP_ARCHIVE_MISSING")
    assert tree(lib_root(fx)) == before


def swap_archive(fx: Fx, path: Path, data: bytes) -> None:
    """Replaces an archive on the volume and makes the database agree with the new bytes, as if
    the file had been the one the run wrote (what a forger who also reached the database would
    do; the checks that remain are the ones inside the archive)."""
    import hashlib

    path.write_bytes(data)
    with fx.w.ctx.sessions() as db:
        for r in db.scalars(select(BackupRun)):
            if r.archive_name and (volume_dir(fx) / r.archive_name) == path:
                r.size_bytes, r.archive_sha256 = len(data), hashlib.sha256(data).hexdigest()
        db.commit()


def forged_tar(fx: Fx, run: BackupRun, members: list[tuple[str, bytes]], **meta: Any) -> bytes:
    import io
    import json
    import tarfile

    head = {
        "format": 1,
        "root_id": str(run.root_id),
        "chain_id": run.chain_id,
        "seq": run.seq,
        "kind": run.kind,
        **meta,
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in [("meta/run.json", json.dumps(head).encode()), *members]:
            ti = tarfile.TarInfo(name)
            ti.size = len(content)
            tar.addfile(ti, io.BytesIO(content))
    return buf.getvalue()


def test_unsafe_archive_members_are_refused(fx: Fx, alice: Api, tmp_path: Path) -> None:
    alice.upload(fx.taxes, "a.txt", b"a")
    enable(fx)
    run_ok(fx)
    a = archives(fx)[0]
    evil = forged_tar(
        fx,
        runs(fx)[0],
        [(n, b"bad") for n in ("files/../../escape.txt", "files/ok/../../x", "files//abs")],
    )
    swap_archive(fx, a, evil)
    r = restore(alice, fx.alice_root)
    assert (r.status_code, code(r)) == (422, "BACKUP_ARCHIVE_UNSAFE")
    assert not (tmp_path / "escape.txt").exists() and not (tmp_path / "library" / "x").exists()
    assert not any(p.name.startswith("Restored") for p in lib_root(fx).iterdir())


def test_restore_routes_are_policy_guarded(fx: Fx) -> None:
    from test_acl import _api_routes, _guards

    routes = {
        (m, r.path): r
        for r in _api_routes(fx.w.app.routes)
        for m in r.methods
        if "backups" in r.path
    }
    assert ("GET", "/api/v1/nodes/{node_id}/backups") in routes
    assert ("POST", "/api/v1/nodes/{node_id}/backups/restore") in routes
    for (_m, path), route in routes.items():
        if "{node_id}" in path:
            assert _guards(route.dependant), path


def test_restore_of_a_non_root_node_is_refused(fx: Fx, alice: Api) -> None:
    make_restore_fixture(fx, alice)
    r = alice.get(f"/nodes/{fx.taxes}/backups")
    assert (r.status_code, code(r)) == (422, "NOT_A_ROOT")
    r2 = restore(alice, fx.taxes)
    assert (r2.status_code, code(r2)) == (422, "NOT_A_ROOT")


# ------------------------------------------------------------------ logs and audit


def test_no_content_paths_or_keys_in_logs_or_audit(
    fx: Fx, alice: Api, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.DEBUG)
    alice.upload(fx.taxes, MARKER_NAME + ".txt", MARKER)
    alice.upload(fx.taxes, "big.bin", blob(100_000))
    body = enable(
        fx, encrypt=True, acknowledge_unencrypted=False, full_every=1, max_size_bytes=10**9
    )
    key = body["recovery_key"]
    for _ in range(3):
        run_ok(fx)
    real_verify = engine_mod.verify_archive
    monkeypatch.setattr(engine_mod, "verify_archive", lambda *a, **k: 1 / 0)
    run(fx)  # a failed run logs only the error class
    monkeypatch.setattr(engine_mod, "verify_archive", real_verify)
    one = sum(r.size_bytes or 0 for r in runs(fx)) // 3
    enable(
        fx,
        encrypt=True,
        acknowledge_unencrypted=False,
        full_every=1,
        max_size_bytes=int(one * 2.5),
    )
    run_ok(fx)  # prunes
    restore(alice, fx.alice_root, recovery_key=key)
    app_logs = " | ".join(
        r.getMessage() + repr(r.exc_text) for r in caplog.records if r.name.startswith("filecanopy")
    )
    assert "backup run failed" in app_logs and "ZeroDivisionError" in app_logs
    haystack = app_logs + audit_text(fx)
    for secret in (
        MARKER.decode(),
        MARKER_NAME,
        key.removeprefix("fcbk1-"),
        str(fx.w.settings.library_path),
        str(fx.w.settings.backup_path),
        "Taxes",
    ):
        assert secret not in haystack, secret
    assert {"backup.pruned", "backup.run", "backup.failed"} <= {
        x.action for x in fx.w.db_rows(AuthAudit)
    }


def test_status_reports_sizes_and_estimates_without_names(fx: Fx, alice: Api) -> None:
    populate(fx, alice, 60_000)
    enable(fx, full_every=3, max_size_bytes=10**9)
    run_ok(fx)
    alice.upload(fx.taxes, "small.txt", b"x")
    run_ok(fx)
    status = fx.admin[0].get("/api/v1/admin/backups", headers=fx.admin[1]).json()
    est = status["estimates"]
    assert est["last_full_bytes"] > 50_000 and est["avg_incremental_bytes"] > 0
    assert est["chains_held"] and est["history_days"]
    mine = next(r for r in status["roots"] if r["user_id"] == str(fx.uid("alice@example.com")))
    assert mine["last_run"]["kind"] == "incremental" and mine["chains"] == 1
    flat = repr(status)
    assert "big.bin" not in flat and "Taxes" not in flat
    assert status["volume"]["used_bytes"] > 0


def test_stat_modes_of_backup_files_are_private(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "a.txt", b"a")
    enable(fx)
    run_ok(fx)
    a = archives(fx)[0]
    assert stat.S_IMODE(a.stat().st_mode) & 0o077 == 0  # not readable by other users
    assert stat.S_IMODE(volume_dir(fx).stat().st_mode) & 0o077 == 0


def test_engine_stop_event_ends_a_run(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "a.txt", b"a")
    enable(fx)
    engine: BackupEngine = service(fx).engine
    engine.stop.set()
    result = engine.run_root(root_id(fx, fx.alice_root))
    assert result.status == "failed"
    assert not [p for p in volume_dir(fx).rglob("*") if p.is_file()]


# ------------------------------------------------------------------ M06c review fixes


def audit_actions(fx: Fx, action: str) -> list[AuthAudit]:
    return [a for a in fx.w.db_rows(AuthAudit) if a.action == action]


def status_of(fx: Fx) -> dict[str, Any]:
    body: dict[str, Any] = fx.admin[0].get("/api/v1/admin/backups", headers=fx.admin[1]).json()
    return body


def test_a_prune_failure_never_destroys_the_runs_own_verified_archive(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    populate(fx, alice, 40_000)
    enable(fx, full_every=1, min_chains_kept=2, max_size_bytes=10**9)
    for _ in range(2):
        run_ok(fx)

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("database went away while pruning")

    real_prune = retention.prune_to_limit
    monkeypatch.setattr(retention, "prune_to_limit", boom)
    result = run(fx)
    assert result.status == "ok"  # the run was committed; pruning is a separate step
    newest = next(r for r in runs(fx) if r.id == result.run_id)
    assert newest.status == "ok" and newest.pruned_at is None
    assert (volume_dir(fx) / (newest.archive_name or "")).is_file()
    assert runs(fx, status="failed") == []
    failures = audit_actions(fx, "backup.prune_failed")
    assert len(failures) == 1 and failures[0].details["error"] == "RuntimeError"
    assert "prune_failed" in status_of(fx)["warnings"]
    monkeypatch.setattr(retention, "prune_to_limit", real_prune)
    fx.w.advance(minutes=61)
    assert run(fx).status == "ok"
    assert "prune_failed" not in status_of(fx)["warnings"]  # cleared by the next good run


def test_pruning_marks_rows_before_unlinking_and_survives_file_errors(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    populate(fx, alice, 60_000)
    enable(fx, full_every=1, min_chains_kept=2, max_size_bytes=10**9)
    for _ in range(3):
        run_ok(fx)
    oldest = runs(fx)[0]
    one = sum(r.size_bytes or 0 for r in runs(fx)) // 3
    enable(fx, full_every=1, min_chains_kept=2, max_size_bytes=int(one * 3.5))
    seen_marked: list[bool] = []
    real_unlink = Path.unlink

    def refusing(self: Path, *a: Any, **k: Any) -> None:
        if oldest.chain_id in str(self):
            with fx.w.ctx.sessions() as db:
                row = db.get(BackupRun, oldest.id)
                assert row is not None
                seen_marked.append(row.pruned_at is not None)
            raise PermissionError("read-only volume")  # an OSError that is not FileNotFound
        real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", refusing)
    result = run(fx)
    assert result.status == "ok"
    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert seen_marked and all(seen_marked)  # committed as pruned before the first unlink
    newest = next(r for r in runs(fx, status=None) if r.id == result.run_id)
    assert newest.status == "ok" and (volume_dir(fx) / (newest.archive_name or "")).is_file()
    failures = audit_actions(fx, "backup.prune_failed")
    assert failures and failures[0].details["error"] == "FILES_NOT_REMOVED"
    assert failures[0].details["files"] >= 1
    assert "prune_failed" in status_of(fx)["warnings"]
    assert oldest.chain_id not in chains_on_disk(fx)  # no row promises the stuck archive


def test_restore_verifies_each_archive_against_the_database_before_extracting(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = make_restore_fixture(fx, alice, encrypt=False)
    assert key is None
    a = archives(fx)[0]
    original = a.read_bytes()

    def no_writer(*args: Any, **kw: Any) -> Any:
        raise AssertionError("extraction started before the archive was verified")

    monkeypatch.setattr(restore_mod, "_Writer", no_writer)
    before = tree(lib_root(fx))
    a.write_bytes(original + b"x")  # size differs
    r = restore(alice, fx.alice_root)
    assert (r.status_code, code(r)) == (409, "BACKUP_ARCHIVE_DAMAGED")
    flipped = bytearray(original)
    flipped[len(flipped) // 2] ^= 0x01  # same size, different bytes
    a.write_bytes(bytes(flipped))
    r = restore(alice, fx.alice_root)
    assert (r.status_code, code(r)) == (409, "BACKUP_ARCHIVE_DAMAGED")
    assert tree(lib_root(fx)) == before


def test_restore_refuses_an_archive_that_names_another_run(fx: Fx, alice: Api) -> None:
    make_restore_fixture(fx, alice, encrypt=False)
    a = archives(fx)[0]
    run_row = runs(fx)[0]
    good = [("files/x.txt", b"x")]
    for wrong in ({"seq": 7}, {"chain_id": "other-chain"}, {"root_id": str(uuid.uuid4())}):
        swap_archive(fx, a, forged_tar(fx, run_row, good, **wrong))
        r = restore(alice, fx.alice_root)
        assert (r.status_code, code(r)) == (409, "BACKUP_ARCHIVE_DAMAGED"), wrong
    swap_archive(fx, a, forged_tar(fx, run_row, good))  # the same bytes with the right names
    assert restore(alice, fx.alice_root).status_code == 200
    assert not any(p.name.startswith(restore_mod.RESTORE_TMP) for p in lib_root(fx).iterdir())


def test_encrypted_archive_is_bound_to_its_run_and_cannot_be_moved(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "a.txt", b"a")
    key = enable(fx, encrypt=True, acknowledge_unencrypted=False, full_every=5)["recovery_key"]
    run_ok(fx)
    alice.upload(fx.taxes, "b.txt", b"b")
    run_ok(fx)
    first, second = archives(fx)
    row = runs(fx)[1]
    ctx_ok = crypto.archive_context(row.root_id, row.chain_id, 2)
    with open(second, "rb") as fh:
        assert crypto.DecryptingReader(fh, recovery_key=key, context=ctx_ok).context == ctx_ok
    wrong = crypto.archive_context(row.root_id, row.chain_id, 1)
    with open(second, "rb") as fh, pytest.raises(crypto.DecryptionError):
        crypto.DecryptingReader(fh, recovery_key=key, context=wrong)
    # an old, genuine archive of the same chain put in the place of the newest one
    swap_archive(fx, second, first.read_bytes())
    before = tree(lib_root(fx))
    r = restore(alice, fx.alice_root, recovery_key=key)
    assert (r.status_code, code(r)) == (409, "BACKUP_ARCHIVE_DAMAGED")
    assert tree(lib_root(fx)) == before


def test_a_header_that_changes_the_context_fails_authentication() -> None:
    import io

    recovery, public, _ = crypto.generate_keypair()
    sink = io.BytesIO()
    w = crypto.EncryptingWriter(sink, crypto.parse_public_key(public), b"ctx-A")
    w.write(b"payload")
    w.close()
    raw = bytearray(sink.getvalue())
    assert raw[:4] == b"FCB2"
    raw[42:47] = b"ctx-B"
    with pytest.raises(crypto.DecryptionError):
        crypto.DecryptingReader(io.BytesIO(bytes(raw)), recovery_key=recovery).readall()


def test_a_low_order_ephemeral_key_is_a_decryption_error_not_a_crash() -> None:
    import io
    import struct

    recovery = crypto.generate_keypair()[0]
    header = crypto.MAGIC + bytes(32) + bytes(4) + struct.pack(">H", 0)
    with pytest.raises(crypto.DecryptionError):
        crypto.DecryptingReader(io.BytesIO(header + b"\0" * 40), recovery_key=recovery)


def test_a_run_on_an_emptied_or_unreadable_mount_is_refused_and_does_not_count(
    fx: Fx, alice: Api
) -> None:
    for i in range(14):
        alice.upload(fx.taxes, f"f{i}.txt", b"file %d" % i)
    enable(fx, full_every=1, min_chains_kept=1)
    run_ok(fx)
    assert len(chains_on_disk(fx)) == 1
    good_archive = archives(fx)[0]
    for item in (lib_root(fx) / "Taxes").iterdir():  # the mount came back empty
        if item.is_file():
            item.unlink()
    result = run(fx)
    assert (result.status, result.code) == ("refused", "BACKUP_SOURCE_UNREADABLE")
    assert len(chains_on_disk(fx)) == 1 and good_archive.is_file()  # nothing new, nothing lost
    assert not [p for p in volume_dir(fx).rglob(".tmp-*")]
    assert "source_unreadable" in status_of(fx)["warnings"]
    fx.w.advance(minutes=61)
    assert run(fx).status == "refused"  # retried each interval, never forced


def test_a_few_unreadable_files_do_not_stop_a_backup(fx: Fx, alice: Api) -> None:
    for i in range(14):
        alice.upload(fx.taxes, f"f{i}.txt", b"file %d" % i)
    enable(fx)
    for i in range(3):
        (lib_root(fx) / "Taxes" / f"f{i}.txt").unlink()
    assert run(fx).status == "ok"


def test_a_replaced_root_directory_is_refused(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "a.txt", b"a")
    enable(fx)
    with fx.w.ctx.sessions() as db:
        top = db.scalars(
            select(Node).where(Node.root_id == root_id(fx, fx.alice_root), Node.parent_id.is_(None))
        ).one()
        top.fs_dev, top.fs_ino = 4242, 4242  # what the reconciler recorded is another directory
        db.commit()
    result = run(fx)
    assert (result.status, result.code) == ("refused", "BACKUP_SOURCE_CHANGED")
    assert not list(volume_dir(fx).rglob("*.tar.gz"))


def test_restore_needs_an_active_idle_root(fx: Fx, alice: Api) -> None:
    key = make_restore_fixture(fx, alice)
    before = tree(lib_root(fx))
    for values in ({"scan_state": "suspended"}, {"scan_paused_since": datetime.now(UTC)}):
        set_root(fx, fx.alice_root, scan_state=None, status="active", scan_paused_since=None)
        set_root(fx, fx.alice_root, **values)
        r = restore(alice, fx.alice_root, recovery_key=key)
        assert (r.status_code, code(r)) == (409, "ROOT_NOT_ACTIVE"), values
    for status, expected in (("disabled", 403), ("missing", 409)):  # disabled: policy says no
        set_root(fx, fx.alice_root, scan_state=None, status=status, scan_paused_since=None)
        assert restore(alice, fx.alice_root, recovery_key=key).status_code == expected, status
    set_root(fx, fx.alice_root, scan_state=None, status="active", scan_paused_since=None)
    assert tree(lib_root(fx)) == before


def test_deletions_for_parents_that_were_never_restored_create_nothing(fx: Fx, alice: Api) -> None:
    make_restore_fixture(fx, alice, encrypt=False)
    run_row = runs(fx)[0]
    deleted = b"ghost/deep/file.txt\nnope/sub/\nTaxes\n"
    members_in = [("meta/deleted.txt", deleted), ("files/keep.txt", b"k")]
    swap_archive(fx, archives(fx)[0], forged_tar(fx, run_row, members_in))
    r = restore(alice, fx.alice_root)
    assert r.status_code == 200, r.text
    folder = lib_root(fx) / r.json()["folder_name"]
    assert sorted(p.name for p in folder.rglob("*")) == ["keep.txt"]  # no ghost/ or nope/ folders


def test_key_creation_and_rotation_are_serialised_and_report_the_fingerprint(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.backup import store

    first = enable(fx, encrypt=True, acknowledge_unencrypted=False)
    assert first["recovery_key"]
    assert first["recovery_key_fingerprint"] == first["key"]["fingerprint"]
    again = enable(fx, encrypt=True, acknowledge_unencrypted=False)
    assert again["recovery_key"] is None  # the one-time key is never made twice
    assert again["key"]["fingerprint"] == first["key"]["fingerprint"]
    c, h = fx.admin
    stale = c.post(
        "/api/v1/admin/backups/key/rotate",
        json={"expected_fingerprint": "0123456789abcdef"},
        headers=h,
    )
    assert (stale.status_code, code(stale)) == (409, "KEY_CHANGED")
    assert status_of(fx)["key"]["fingerprint"] == first["key"]["fingerprint"]
    rotated = c.post(
        "/api/v1/admin/backups/key/rotate",
        json={"expected_fingerprint": first["key"]["fingerprint"]},
        headers=h,
    ).json()
    assert rotated["recovery_key"]
    assert rotated["recovery_key_fingerprint"] == rotated["key"]["fingerprint"]
    assert rotated["key"]["fingerprint"] != first["key"]["fingerprint"]
    # the request that lost a creation race gets no key and changes nothing
    with fx.w.ctx.sessions() as db:
        assert store.create_key_if_absent(db) is None
    with fx.w.ctx.sessions() as db:
        monkeypatch.setattr(store, "_lock_key_row", lambda _db: None)
        assert store.create_key_if_absent(db) is None  # the primary key refuses a second insert
    assert status_of(fx)["key"]["fingerprint"] == rotated["key"]["fingerprint"]


def test_administrator_status_hides_per_user_counts_of_personal_roots(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "a.txt", b"a")
    Api(fx, "bob").upload(fx.shared_sub, "s.txt", b"s")
    enable(fx)
    run_ok(fx)
    run_ok(fx, fx.shared_root)
    status = status_of(fx)
    personal = next(r for r in status["roots"] if r["kind"] == "personal" and r["last_run"])
    shared = next(r for r in status["roots"] if r["kind"] == "shared")
    counted = ("files_added", "files_changed", "files_deleted", "files_missing", "files_unstable")
    assert personal["last_run"]["status"] == "ok" and personal["last_run"]["size_bytes"]
    assert all(personal["last_run"][k] is None for k in counted)
    assert shared["last_run"]["files_added"] >= 1  # the shared tree is the administrator's own
    points = alice.get(f"/nodes/{fx.alice_root}/backups").json()
    assert points[0]["files_added"] >= 1  # the owner still sees their own counts


def test_backup_directory_that_is_the_library_by_another_path_is_refused(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app import config
    from app.backup import volume

    backup = Path(fx.w.settings.backup_path)
    real = volume.path_identity
    shared_dir = fx.w.settings.library_path / "shared"

    def same_inode(p: Path) -> tuple[int, int] | None:
        return (77, 77) if p in (backup, shared_dir) else real(p)

    alice.upload(fx.taxes, "a.txt", b"a")
    enable(fx)
    monkeypatch.setattr(volume, "path_identity", same_inode)
    vol = inspect_volume(fx.w.settings)
    assert (vol.usable, vol.reason) == (False, "nested")
    result = run(fx)
    assert (result.status, result.code) == ("refused", "BACKUP_VOLUME_UNAVAILABLE")
    assert "volume_nested" in status_of(fx)["warnings"]
    r = put_settings(fx)
    assert (r.status_code, code(r)) == (409, "BACKUP_VOLUME_UNAVAILABLE")
    # the same check against the library and the app data, when the application starts
    library = fx.w.settings.library_path.resolve()
    monkeypatch.setattr(
        config,
        "path_identity",
        lambda p: (77, 77) if p.resolve() in (backup.resolve(), library) else None,
    )
    with pytest.raises(ValueError, match="same directory"):
        Settings(  # type: ignore[call-arg]
            database_url="postgresql+psycopg://u:p@h/db",
            library_path=fx.w.settings.library_path,
            app_data_path=tmp_path / "other-data",
            backup_path=str(backup),
        )


def test_restore_is_built_under_a_staging_name_and_published_at_the_end(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = make_restore_fixture(fx, alice)
    listings: list[list[str]] = []
    real = restore_mod._Writer.write_file

    def spy(self: Any, rel: str, source: Any, mtime: int) -> None:
        listings.append(sorted(p.name for p in lib_root(fx).iterdir()))
        real(self, rel, source, mtime)

    monkeypatch.setattr(restore_mod._Writer, "write_file", spy)
    r = restore(alice, fx.alice_root, recovery_key=key)
    assert r.status_code == 200
    assert listings
    for names in listings:  # while files are being written the final name does not exist yet
        assert not any(n.startswith("Restored from backup") for n in names)
        assert any(n.startswith(restore_mod.RESTORE_TMP) for n in names)
    final = sorted(p.name for p in lib_root(fx).iterdir())
    assert r.json()["folder_name"] in final
    assert not any(n.startswith(restore_mod.RESTORE_TMP) for n in final)


def test_stale_restore_staging_folders_are_swept_at_startup(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "a.txt", b"a")
    enable(fx)
    stale = lib_root(fx) / f"{restore_mod.RESTORE_TMP}deadbeef"
    (stale / "Taxes" / "deep").mkdir(parents=True)
    (stale / "Taxes" / "deep" / "x.txt").write_bytes(b"half")
    svc = service(fx)
    svc.start()
    svc.stop()
    assert not stale.exists()
    assert (lib_root(fx) / "Taxes" / "a.txt").is_file()  # real data untouched


def test_storage_errors_during_restore_become_a_clean_failure(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.storage.resolver import StorageEscape

    key = make_restore_fixture(fx, alice)
    before = tree(lib_root(fx))

    def escape(self: Any, *a: Any, **k: Any) -> None:
        raise StorageEscape("unsafe path component")

    monkeypatch.setattr(restore_mod._Writer, "write_file", escape)
    r = restore(alice, fx.alice_root, recovery_key=key)
    assert (r.status_code, code(r)) == (500, "RESTORE_FAILED")
    assert tree(lib_root(fx)) == before
    assert audit_actions(fx, "backup.restore_failed")[-1].details["code"] == "RESTORE_FAILED"


def test_oversized_deletion_list_and_manifest_are_refused(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_restore_fixture(fx, alice, encrypt=False)
    a = archives(fx)[0]
    swap_archive(fx, a, forged_tar(fx, runs(fx)[0], [("meta/deleted.txt", b"x/\n" * 100)]))
    monkeypatch.setattr(restore_mod, "MAX_DELETED_BYTES", 50)
    r = restore(alice, fx.alice_root)
    assert (r.status_code, code(r)) == (409, "BACKUP_ARCHIVE_DAMAGED")
    monkeypatch.setattr(restore_mod, "MAX_DELETED_BYTES", 128 * 1024 * 1024)
    # an honest archive whose manifest is above the cap is not read into memory
    fx.w.advance(minutes=61)
    alice.upload(fx.taxes, "again.txt", b"again")
    newest = run_ok(fx)
    path = volume_dir(fx) / (newest.archive_name or "")
    monkeypatch.setattr(engine_mod, "MAX_MANIFEST_BYTES", 10)
    with pytest.raises(ValueError, match="manifest is larger"):
        engine_mod.verify_archive(path, None, None, None)


def test_archive_metadata_names_user_ids_and_roles_only(fx: Fx, alice: Api) -> None:
    import json

    alice.upload(fx.taxes, "a.txt", b"a")
    enable(fx)
    run_ok(fx)
    raw = members(archives(fx)[0])["meta/metadata.json"]
    meta = json.loads(raw)
    assert meta["users"] and all(set(u) == {"id", "role"} for u in meta["users"])
    flat = raw.decode()
    assert "@example.com" not in flat and "email" not in flat


def test_first_verified_run_records_the_root_identity_and_a_swap_is_then_refused(
    fx: Fx, alice: Api
) -> None:
    alice.upload(fx.taxes, "a.txt", b"a")
    enable(fx, full_every=5)
    run_ok(fx)
    with fx.w.ctx.sessions() as db:
        top = db.scalars(
            select(Node).where(Node.root_id == root_id(fx, fx.alice_root), Node.parent_id.is_(None))
        ).one()
        info = os.stat(lib_root(fx))
        assert (top.fs_dev, top.fs_ino) == (info.st_dev, info.st_ino)
    top_dir = lib_root(fx)
    moved = top_dir.with_name(top_dir.name + "-old")
    top_dir.rename(moved)
    shutil.copytree(moved, top_dir)  # same content, a different directory (new inode)
    result = run(fx)
    assert (result.status, result.code) == ("refused", "BACKUP_SOURCE_CHANGED")


def test_a_root_directory_that_vanished_with_files_in_it_is_refused(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "a.txt", b"a")
    enable(fx)
    run_ok(fx)
    shutil.rmtree(lib_root(fx))
    result = run(fx)
    assert (result.status, result.code) == ("refused", "BACKUP_SOURCE_UNREADABLE")
    assert len(chains_on_disk(fx)) == 1
