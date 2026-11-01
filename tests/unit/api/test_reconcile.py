"""M06a external-change reconciliation (ADR-011): identity across outside moves and renames,
modifications, missing instead of deleted, the mass-change guard, mount refusal, unsupported
entries, case collisions, network-filesystem fallback, the watcher, and authorization.

In-memory SQLite plus a per-test temp library on a Linux container, so inodes, `O_NOFOLLOW`, FIFOs
and inotify are real. Mounted drives and network filesystems are simulated (they need privileges).
"""

import os
import shutil
import stat
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from test_acl import Fx
from test_auth import World
from test_files import Api, code, disk, nid

from app.db.models import FileEvent, FileOperation, Node, NodeGrant, ScanRun, StorageRoot
from app.storage import reconcile, roots
from app.storage.reconcile import PassResult
from app.storage.service import ReconcilerService
from app.storage.watcher import Watcher


@pytest.fixture
def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., World]:
    def factory(**overrides: Any) -> World:
        return World(tmp_path, monkeypatch, **overrides)

    return factory


@pytest.fixture
def fx(make_world: Callable[..., World]) -> Fx:
    return Fx(make_world())


# ------------------------------------------------------------------ helpers


def reconciler(fx: Fx) -> reconcile.Reconciler:
    service: ReconcilerService = fx.w.app.state.reconciler
    return service.reconciler


def root_id(fx: Fx, node_id: uuid.UUID) -> uuid.UUID:
    with fx.w.ctx.sessions() as db:
        node = db.get(Node, node_id)
        assert node is not None
        return node.root_id


def scan(fx: Fx, anchor: uuid.UUID | None = None, **kw: Any) -> PassResult:
    """One pass over the root that contains `anchor` (alice's personal root by default)."""
    result = reconciler(fx).run_root(root_id(fx, anchor or fx.alice_root), "manual", **kw)
    assert result.outcome != "skipped", result
    return result


def row(fx: Fx, node_id: uuid.UUID) -> Node:
    with fx.w.ctx.sessions() as db:
        node = db.get(Node, node_id)
        assert node is not None
        db.expunge(node)
        return node


def child(fx: Fx, parent: uuid.UUID, name: str, *, status: str | None = None) -> Node | None:
    with fx.w.ctx.sessions() as db:
        query = select(Node).where(Node.parent_id == parent, Node.name == name)
        if status is not None:
            query = query.where(Node.status == status)
        found = db.scalars(query).first()
        if found is not None:
            db.expunge(found)
        return found


def events(fx: Fx, node_id: uuid.UUID) -> list[FileEvent]:
    with fx.w.ctx.sessions() as db:
        rows = list(db.scalars(select(FileEvent).where(FileEvent.node_id == node_id)))
        for r in rows:
            db.expunge(r)
        return sorted(rows, key=lambda e: e.id)


def actions(fx: Fx, node_id: uuid.UUID) -> list[str]:
    return [e.action for e in events(fx, node_id)]


def all_events(fx: Fx) -> list[FileEvent]:
    with fx.w.ctx.sessions() as db:
        return list(db.scalars(select(FileEvent)))


def count_nodes(fx: Fx, root: uuid.UUID) -> int:
    with fx.w.ctx.sessions() as db:
        return len(list(db.scalars(select(Node.id).where(Node.root_id == root))))


@pytest.fixture
def alice(fx: Fx) -> Api:
    return Api(fx, "alice")


def settle(fx: Fx) -> None:
    """First scan after the fixture folders were created: fills fingerprints without events."""
    scan(fx)
    assert all_events(fx) == []


# ------------------------------------------------------------------ fingerprints and events


def test_first_scan_fills_fingerprints_silently(fx: Fx) -> None:
    before = row(fx, fx.taxes)
    assert before.fs_ino is None
    result = scan(fx)
    assert result.outcome == "ok"
    after = row(fx, fx.taxes)
    assert after.fs_ino is not None and after.fs_dev is not None and after.mtime_ns is not None
    assert after.revision == before.revision
    assert all_events(fx) == []


def test_app_writes_are_not_external_changes(fx: Fx, alice: Api) -> None:
    folder = nid(alice.mkdir(fx.taxes, "Receipts"))
    f = nid(alice.upload(folder, "r.txt", b"receipt"))
    moved = nid(alice.upload(fx.private, "m.txt", b"to move"))
    assert alice.move(moved, folder, row(fx, moved).revision).status_code == 200
    settle_events = [(e.action, e.actor_kind) for e in all_events(fx)]
    result = scan(fx)
    assert result.outcome == "ok"
    assert result.counts.get("modified", 0) == 0 and result.counts.get("created", 0) == 0
    assert [(e.action, e.actor_kind) for e in all_events(fx)] == settle_events  # nothing new
    created = events(fx, f)
    assert [e.action for e in created] == ["uploaded"]
    assert created[0].actor_kind == "user" and created[0].actor_user_id == alice.uid
    assert row(fx, f).content_hash is not None  # uploads store the hash they computed


def test_external_file_is_created_by_external_filesystem(fx: Fx, alice: Api) -> None:
    settle(fx)
    (disk(fx, fx.taxes) / "from-host.txt").write_bytes(b"hello")
    scan(fx)
    node = child(fx, fx.taxes, "from-host.txt")
    assert node is not None and node.kind == "file" and node.size == 5
    [event] = events(fx, node.id)
    assert (event.action, event.actor_kind, event.actor_user_id) == (
        "external_created",
        "external_filesystem",
        None,
    )
    assert event.source == "scan"
    names = [i["name"] for i in alice.children(fx.taxes)]
    assert "from-host.txt" in names
    assert alice.download(node.id).content == b"hello"  # readable like any other file


def test_external_modification_bumps_revision_and_clears_hash(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.taxes, "doc.txt", b"one"))
    before = row(fx, f)
    assert before.content_hash is not None
    path = disk(fx, f)
    path.write_bytes(b"three!!")
    scan(fx)
    after = row(fx, f)
    assert after.id == before.id and after.size == 7
    assert after.revision == before.revision + 1
    assert after.content_hash is None
    assert actions(fx, f) == ["uploaded", "external_modified"]
    assert events(fx, f)[-1].actor_kind == "external_filesystem"
    # same size, newer mtime is also a modification
    path.write_bytes(b"THREE!!")
    os.utime(path, ns=(before.mtime_ns or 0, (before.mtime_ns or 0) + 5_000_000_000))
    scan(fx)
    assert actions(fx, f).count("external_modified") == 2


def test_editor_save_by_rename_over_original_keeps_identity(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.taxes, "doc.txt", b"original"))
    path = disk(fx, f)
    tmp = path.with_name("doc.txt.swp-copy")
    tmp.write_bytes(b"rewritten by an editor")
    os.replace(tmp, path)  # the original inode is replaced; the name stays
    scan(fx)
    node = row(fx, f)
    assert node.status == "active" and node.size == len(b"rewritten by an editor")
    assert "external_modified" in actions(fx, f)
    assert alice.download(f).content == b"rewritten by an editor"


# ------------------------------------------------------------------ moves and renames


def test_out_of_band_file_rename_keeps_identity(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.taxes, "old.txt", b"same bytes"))
    src = disk(fx, f)
    src.rename(src.with_name("new.txt"))
    scan(fx)
    node = row(fx, f)
    assert node.name == "new.txt" and node.status == "active"
    assert node.revision == 2
    assert child(fx, fx.taxes, "old.txt") is None
    moved = [e for e in events(fx, f) if e.action == "external_moved"]
    assert len(moved) == 1 and moved[0].details["uncertain"] is False
    assert moved[0].details["old_name"] == "old.txt"
    assert alice.download(f).content == b"same bytes"


def test_out_of_band_move_to_other_folder(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.taxes, "a.txt", b"aaa"))
    shutil.move(disk(fx, f), disk(fx, fx.private) / "a.txt")
    scan(fx)
    node = row(fx, f)
    assert node.parent_id == fx.private and node.name == "a.txt"
    assert node.lineage.startswith(row(fx, fx.private).lineage)
    assert row(fx, f).status == "active"


def test_out_of_band_folder_rename_and_move_carry_children(fx: Fx, alice: Api) -> None:
    sub = nid(alice.mkdir(fx.taxes, "Sub"))
    inner = nid(alice.mkdir(sub, "Inner"))
    f = nid(alice.upload(inner, "deep.txt", b"deep"))
    scan(fx)
    old = disk(fx, sub)
    old.rename(old.with_name("Renamed"))
    scan(fx)
    assert row(fx, sub).name == "Renamed" and row(fx, sub).status == "active"
    assert row(fx, inner).status == "active" and row(fx, f).status == "active"
    assert alice.download(f).content == b"deep"  # path resolves under the new name
    shutil.move(disk(fx, sub), disk(fx, fx.private) / "Renamed")
    scan(fx)
    assert row(fx, sub).parent_id == fx.private
    assert row(fx, f).lineage.startswith(row(fx, fx.private).lineage)
    assert alice.download(f).content == b"deep"
    moved = [e for e in events(fx, sub) if e.action == "external_moved"]
    assert [m.details["uncertain"] for m in moved] == [False, False]


def test_move_into_a_new_folder_keeps_file_identity(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.taxes, "x.txt", b"content"))
    newdir = disk(fx, fx.taxes) / "Archive"
    newdir.mkdir()
    shutil.move(disk(fx, f), newdir / "x.txt")
    scan(fx)
    node = row(fx, f)
    archive = child(fx, fx.taxes, "Archive")
    assert archive is not None and node.parent_id == archive.id and node.status == "active"


def test_copy_and_delete_is_an_uncertain_inferred_move(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.taxes, "orig.bin", b"identical content" * 100))
    src = disk(fx, f)
    copy = disk(fx, fx.private) / "copy.bin"
    shutil.copyfile(src, copy)  # a new inode, new mtime
    src.unlink()
    scan(fx)
    node = row(fx, f)
    assert node.parent_id == fx.private and node.name == "copy.bin"
    moved = [e for e in events(fx, f) if e.action == "external_moved"]
    assert len(moved) == 1 and moved[0].details["uncertain"] is True


def test_ambiguous_identical_files_are_not_linked(fx: Fx, alice: Api) -> None:
    a = nid(alice.upload(fx.taxes, "a.bin", b"twin" * 400))
    b = nid(alice.upload(fx.taxes, "b.bin", b"twin" * 400))
    paths = [disk(fx, n) for n in (a, b)]
    for p in paths:  # copy both before deleting either, so no inode number is reused
        shutil.copyfile(p, disk(fx, fx.private) / ("moved-" + p.name))
    for p in paths:
        p.unlink()
    scan(fx)
    assert row(fx, a).status == "missing" and row(fx, b).status == "missing"
    assert child(fx, fx.private, "moved-a.bin") is not None  # new nodes, no guess
    assert child(fx, fx.private, "moved-b.bin") is not None


def test_moves_never_cross_roots(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.taxes, "mine.txt", b"private notes"))
    shutil.move(disk(fx, f), disk(fx, fx.shared_sub) / "mine.txt")
    scan(fx)
    scan(fx, fx.shared_root)
    assert row(fx, f).status == "missing"
    appeared = child(fx, fx.shared_sub, "mine.txt")
    assert appeared is not None and appeared.id != f
    assert actions(fx, appeared.id) == ["external_created"]


# ------------------------------------------------------------------ missing, never deleted


def test_vanished_file_is_missing_and_can_reappear(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.taxes, "gone.txt", b"keep me"))
    path = disk(fx, f)
    saved = path.read_bytes()
    path.unlink()
    scan(fx)
    assert row(fx, f).status == "missing"
    assert actions(fx, f) == ["uploaded", "missing"]
    assert alice.download(f).status_code == 409 and code(alice.download(f)) == "CONTENT_MISSING"
    items = {i["name"]: i for i in alice.children(fx.taxes)}
    assert items["gone.txt"]["status"] == "missing"  # shown, not hidden
    path.write_bytes(saved)  # restored from a backup
    scan(fx)
    assert row(fx, f).status == "active"
    assert actions(fx, f)[-1] == "reappeared"
    assert alice.download(f).content == b"keep me"


def test_vanished_folder_marks_the_subtree_missing(fx: Fx, alice: Api) -> None:
    sub = nid(alice.mkdir(fx.taxes, "Gone"))
    f = nid(alice.upload(sub, "inside.txt", b"x"))
    shutil.rmtree(disk(fx, sub))
    scan(fx)
    assert row(fx, sub).status == "missing" and row(fx, f).status == "missing"
    [event] = [e for e in events(fx, sub) if e.action == "missing"]
    assert event.details["affected"] == 2


def test_missing_file_found_elsewhere_by_a_later_scan_links_again(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.taxes, "wanderer.txt", b"found again"))
    stash = fx.w.settings.library_path.parent / "stash.tmp"  # outside every root
    shutil.move(disk(fx, f), stash)
    scan(fx)
    assert row(fx, f).status == "missing"
    shutil.move(stash, disk(fx, fx.private) / "wanderer.txt")
    scan(fx)
    node = row(fx, f)
    assert node.status == "active" and node.parent_id == fx.private
    assert "reappeared" in actions(fx, f)


def test_dismiss_requires_write_and_hides_the_node(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.taxes, "dismiss-me.txt", b"x"))
    disk(fx, f).unlink()
    scan(fx)
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "viewer")
    fx.grant(fx.alice, fx.taxes, "carol@example.com", "editor")
    bob, carol = Api(fx, "bob"), Api(fx, "carol")
    assert bob.post(f"/nodes/{f}/dismiss").status_code == 403
    assert Api(fx, "dave").post(f"/nodes/{f}/dismiss").status_code == 403
    assert Api(fx, "admin").post(f"/nodes/{f}/dismiss").status_code == 403
    live = nid(alice.upload(fx.taxes, "still-here.txt", b"y"))
    r = alice.post(f"/nodes/{live}/dismiss")
    assert (r.status_code, code(r)) == (409, "NOT_MISSING")
    assert carol.post(f"/nodes/{f}/dismiss").status_code == 200
    assert row(fx, f).status == "dismissed"  # the row stays for audit
    assert "dismissed" in actions(fx, f)
    assert "dismiss-me.txt" not in [i["name"] for i in alice.children(fx.taxes)]
    assert alice.get(f"/nodes/{f}").status_code == 403  # now behaves as nonexistent
    # the name is free again for new content
    assert alice.upload(fx.taxes, "dismiss-me.txt", b"new").status_code == 201


def test_dismiss_removes_grants_beneath(fx: Fx, alice: Api) -> None:
    sub = nid(alice.mkdir(fx.taxes, "Shared"))
    fx.grant(fx.alice, sub, "bob@example.com", "viewer")
    shutil.rmtree(disk(fx, sub))
    scan(fx)
    assert alice.post(f"/nodes/{sub}/dismiss").status_code == 200
    with fx.w.ctx.sessions() as db:
        assert list(db.scalars(select(NodeGrant).where(NodeGrant.node_id == sub))) == []


# ------------------------------------------------------------------ mass-change guard


def make_tree(fx: Fx, parent: uuid.UUID, count: int = 120) -> Path:
    base = disk(fx, parent) / "bulk"
    base.mkdir()
    for i in range(count):
        (base / f"f{i:03}.txt").write_bytes(f"file {i}".encode())
    return base


def test_mass_disappearance_suspends_the_root(fx: Fx, alice: Api) -> None:
    base = make_tree(fx, fx.taxes)
    scan(fx)
    root = root_id(fx, fx.alice_root)
    total = count_nodes(fx, root)
    shutil.rmtree(base)  # looks exactly like an unmounted bind mount
    result = scan(fx)
    assert result.outcome == "suspended" and result.error == "MASS_CHANGE"
    with fx.w.ctx.sessions() as db:
        assert db.get(StorageRoot, root).scan_state == "suspended"  # type: ignore[union-attr]
        assert not list(db.scalars(select(Node).where(Node.status == "missing")))
    assert count_nodes(fx, root) == total
    # later passes do nothing while it waits for a person; app writes still work
    assert reconciler(fx).run_root(root, "periodic").outcome == "skipped"
    assert alice.upload(fx.taxes, "still-works.txt", b"ok").status_code == 201


def test_suspended_root_is_resolved_by_its_owner_not_the_admin(fx: Fx, alice: Api) -> None:
    base = make_tree(fx, fx.taxes)
    scan(fx)
    shutil.rmtree(base)
    assert scan(fx).outcome == "suspended"
    admin, bob = Api(fx, "admin"), Api(fx, "bob")
    body = {"action": "apply"}
    assert admin.post(f"/nodes/{fx.alice_root}/scan/resolve", json=body).status_code == 403
    assert bob.post(f"/nodes/{fx.alice_root}/scan/resolve", json=body).status_code == 403
    r = alice.post(f"/nodes/{fx.taxes}/scan/resolve", json=body)
    assert (r.status_code, code(r)) == (422, "NOT_A_ROOT")
    assert (
        alice.post(f"/nodes/{fx.alice_root}/scan/resolve", json={"action": "keep"}).status_code
        == 200
    )
    with fx.w.ctx.sessions() as db:
        root = db.get(StorageRoot, root_id(fx, fx.alice_root))
        assert root is not None and root.scan_state == "suspended"
    assert alice.post(f"/nodes/{fx.alice_root}/scan/resolve", json=body).status_code == 200
    service: ReconcilerService = fx.w.app.state.reconciler
    assert service.process_requests() == 1  # the forced pass
    with fx.w.ctx.sessions() as db:
        root = db.get(StorageRoot, root_id(fx, fx.alice_root))
        assert root is not None and root.scan_state is None
        assert len(list(db.scalars(select(Node).where(Node.status == "missing")))) >= 120
    r = alice.post(f"/nodes/{fx.alice_root}/scan/resolve", json=body)
    assert (r.status_code, code(r)) == (409, "NOT_SUSPENDED")


def test_shared_root_suspension_is_an_admin_decision(fx: Fx) -> None:
    base = make_tree(fx, fx.shared_sub)
    scan(fx, fx.shared_root)
    shutil.rmtree(base)
    assert scan(fx, fx.shared_root).outcome == "suspended"
    alice = Api(fx, "alice")
    body = {"action": "apply"}
    assert alice.post(f"/nodes/{fx.shared_root}/scan/resolve", json=body).status_code == 403
    assert (
        Api(fx, "admin").post(f"/nodes/{fx.shared_root}/scan/resolve", json=body).status_code == 200
    )


def test_admin_overview_shows_only_a_status_code_for_a_suspended_personal_root(fx: Fx) -> None:
    base = make_tree(fx, fx.taxes)
    scan(fx)
    shutil.rmtree(base)
    scan(fx)
    admin = Api(fx, "admin")
    body = admin.get("/admin/storage").json()
    statuses = [p["status"] for p in body["personal_roots"]]
    assert "scan_suspended" in statuses
    assert all(
        set(p) == {"user_id", "status", "trash_items", "trash_bytes"}
        for p in body["personal_roots"]
    )


def test_library_relocation_with_preserved_mtimes_is_silent(fx: Fx, alice: Api) -> None:
    ids = [nid(alice.upload(fx.taxes, f"r{i}.txt", f"data {i}".encode())) for i in range(5)]
    base = make_tree(fx, fx.private)
    scan(fx)
    n_events = len(all_events(fx))
    root_dir = disk(fx, fx.alice_root)
    copy = root_dir.with_name("relocated")
    shutil.copytree(root_dir, copy)  # copy2 preserves mtimes; every inode is new
    shutil.rmtree(root_dir)
    copy.rename(root_dir)
    assert base.exists()
    old_inodes = {i: row(fx, i).fs_ino for i in ids}
    result = scan(fx)
    assert result.outcome == "ok"
    assert len(all_events(fx)) == n_events  # nothing recorded: names matched, identity kept
    assert all(row(fx, i).status == "active" for i in ids)
    assert {i: row(fx, i).fs_ino for i in ids} != old_inodes  # fingerprints were refreshed


def test_library_relocation_without_mtimes_asks_before_recording(fx: Fx, alice: Api) -> None:
    make_tree(fx, fx.private)
    scan(fx)
    n_events = len(all_events(fx))
    root_dir = disk(fx, fx.alice_root)
    copy = root_dir.with_name("relocated")
    shutil.copytree(root_dir, copy, copy_function=shutil.copyfile)  # fresh mtimes everywhere
    shutil.rmtree(root_dir)
    copy.rename(root_dir)
    result = scan(fx)
    assert result.outcome == "suspended"
    assert len(all_events(fx)) == n_events


def test_restart_scan_picks_up_changes_made_while_down(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.taxes, "before.txt", b"v1"))
    scan(fx)
    disk(fx, f).write_bytes(b"v2 changed while the app was down")
    (disk(fx, fx.taxes) / "added-while-down.txt").write_bytes(b"new")
    fresh = reconcile.Reconciler(fx.w.ctx.sessions, fx.w.settings)  # a new process, no memory
    result = fresh.run_root(root_id(fx, fx.alice_root), "startup")
    assert result.outcome == "ok"
    assert row(fx, f).revision == 2
    assert child(fx, fx.taxes, "added-while-down.txt") is not None
    with fx.w.ctx.sessions() as db:
        assert "startup" in set(db.scalars(select(ScanRun.trigger)))


def test_large_tree_scans_once_then_reports_no_changes(fx: Fx) -> None:
    base = disk(fx, fx.shared_sub) / "big"
    for d in range(40):
        (base / f"d{d:02}").mkdir(parents=True)
        for i in range(50):
            (base / f"d{d:02}" / f"f{i:02}.dat").write_bytes(b"x" * (i + 1))
    first = scan(fx, fx.shared_root)
    assert first.counts["created"] == 1 + 40 + 2000
    second = scan(fx, fx.shared_root)
    assert second.outcome == "ok" and not any(
        second.counts.get(k) for k in ("created", "modified", "missing", "moved")
    )
    with fx.w.ctx.sessions() as db:
        runs = list(db.scalars(select(ScanRun.counts)))
    assert {"deferred": 0, "unreadable": 0} in runs  # the quiet second pass


# ------------------------------------------------------------------ app operations are not external


def test_planned_operation_defers_changes_in_its_folder(fx: Fx) -> None:
    settle(fx)
    (disk(fx, fx.taxes) / "mid-upload.txt").write_bytes(b"x")
    now = fx.w.ctx.sessions
    with now() as db:
        db.add(
            FileOperation(
                actor_user_id=fx.uid("alice@example.com"),
                action="upload",
                status="planned",
                request_hash="h",
                target_parent_id=fx.taxes,
                created_at=reconcile.clock.utcnow(),
                updated_at=reconcile.clock.utcnow(),
            )
        )
        db.commit()
    result = scan(fx)
    assert child(fx, fx.taxes, "mid-upload.txt") is None and result.counts["deferred"] == 1
    with now() as db:
        for op in db.scalars(select(FileOperation)):
            op.status = "failed"
        db.commit()
    scan(fx)
    assert child(fx, fx.taxes, "mid-upload.txt") is not None


def test_busy_nodes_and_their_subtrees_are_left_alone(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.taxes, "claimed.txt", b"x"))
    sub = nid(alice.mkdir(fx.taxes, "Claimed"))
    inner = nid(alice.upload(sub, "i.txt", b"i"))
    scan(fx)
    with fx.w.ctx.sessions() as db:
        for n in (f, sub):
            node = db.get(Node, n)
            assert node is not None
            node.status = "busy"
        db.commit()
    disk(fx, f).unlink()
    disk(fx, inner).unlink()
    scan(fx)
    assert row(fx, f).status == "busy" and row(fx, inner).status == "active"


def test_staging_files_are_never_imported(fx: Fx) -> None:
    settle(fx)
    (disk(fx, fx.taxes) / ".filecanopy-tmp-abc123").write_bytes(b"half an upload")
    scan(fx)
    assert child(fx, fx.taxes, ".filecanopy-tmp-abc123") is None


def test_reconcile_needed_node_resolves_when_disk_matches(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.taxes, "flagged.txt", b"x"))
    with fx.w.ctx.sessions() as db:
        node = db.get(Node, f)
        assert node is not None
        node.status = "reconcile_needed"
        db.commit()
    scan(fx)
    assert row(fx, f).status == "active"


# ------------------------------------------------------------------ unsupported entries and mounts


def test_symlinks_and_special_files_are_listed_greyed_out_never_followed(
    fx: Fx, alice: Api, tmp_path: Path
) -> None:
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("host secret")
    base = disk(fx, fx.taxes)
    os.symlink(outside, base / "link.txt")
    os.symlink(tmp_path, base / "dirlink")
    os.mkfifo(base / "pipe")
    scan(fx)
    items = {i["name"]: i for i in alice.children(fx.taxes)}
    assert (
        items["link.txt"]["unsupported_reason"] == "symlink"
        and items["link.txt"]["kind"] == "other"
    )
    assert items["dirlink"]["unsupported_reason"] == "symlink"
    assert items["pipe"]["unsupported_reason"] == "special"
    link = uuid.UUID(items["link.txt"]["id"])
    for r in (alice.download(link), alice.get(f"/nodes/{link}/children")):
        assert r.status_code in (409, 422), r.text
    assert code(alice.download(link)) == "UNSUPPORTED_ENTRY"
    assert alice.copy(link, fx.private).status_code == 409
    assert alice.rename(link, "x", 1).status_code == 409
    assert child(fx, link, "anything") is None  # the target was never entered
    scan(fx)  # stable on the next pass
    assert len([n for n in alice.children(fx.taxes) if n["name"] == "link.txt"]) == 1


def test_nested_mounted_drive_is_listed_unsupported_and_never_entered(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    mount = disk(fx, fx.taxes) / "usb-drive"
    mount.mkdir()
    (mount / "secret-on-other-drive.txt").write_text("not ours")
    real = reconcile.is_mount_point
    monkeypatch.setattr(
        reconcile,
        "is_mount_point",
        lambda st, dev: st.st_ino == mount.stat().st_ino or real(st, dev),
    )
    scan(fx)
    items = {i["name"]: i for i in alice.children(fx.taxes)}
    assert items["usb-drive"]["unsupported_reason"] == "mounted_drive"
    assert items["usb-drive"]["kind"] == "other"
    node = uuid.UUID(items["usb-drive"]["id"])
    assert alice.get(f"/nodes/{node}/children").status_code in (409, 422)
    with fx.w.ctx.sessions() as db:
        assert not list(db.scalars(select(Node).where(Node.name == "secret-on-other-drive.txt")))
    assert alice.mkdir(node, "x").status_code in (409, 422)


def test_root_on_another_device_is_refused(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = root_id(fx, fx.alice_root)
    with fx.w.ctx.sessions() as db:
        rel = db.get(StorageRoot, root).relative_dir  # type: ignore[union-attr]
    real = roots.root_device
    monkeypatch.setattr(
        roots, "root_device", lambda lib, r: real(lib, r) + 1 if r == rel else real(lib, r)
    )
    counts = roots.ensure_roots(fx.w.ctx.sessions, fx.w.settings)
    assert counts["unsupported_mount"] == 1
    with fx.w.ctx.sessions() as db:
        assert db.get(StorageRoot, root).status == "unsupported_mount"  # type: ignore[union-attr]
    assert alice.get(f"/nodes/{fx.taxes}/children").status_code == 403  # unavailable
    assert alice.upload(fx.taxes, "nope.txt", b"x").status_code == 403
    assert fx.alice_root not in [uuid.UUID(r["node_id"]) for r in alice.get("/roots").json()]
    assert reconciler(fx).run_root(root, "periodic").outcome == "skipped"  # never scanned
    assert not (disk(fx, fx.taxes) / "nope.txt").exists()  # never written
    statuses = Api(fx, "admin").get("/admin/storage").json()["personal_roots"]
    assert "unsupported_mount" in [p["status"] for p in statuses]
    monkeypatch.setattr(roots, "root_device", real)  # the drive is gone again
    roots.ensure_roots(fx.w.ctx.sessions, fx.w.settings)
    assert alice.get(f"/nodes/{fx.taxes}/children").status_code == 200


# ------------------------------------------------------------------ names


def test_case_collision_tree_keeps_both_and_warns(fx: Fx, alice: Api) -> None:
    base = disk(fx, fx.taxes)
    (base / "Report.pdf").write_bytes(b"upper")
    (base / "report.pdf").write_bytes(b"lower")
    (base / "Unique.pdf").write_bytes(b"u")
    scan(fx)
    items = {i["name"]: i for i in alice.children(fx.taxes)}
    assert items["Report.pdf"]["name_collision"] and items["report.pdf"]["name_collision"]
    assert not items["Unique.pdf"]["name_collision"]
    assert alice.download(uuid.UUID(items["Report.pdf"]["id"])).content == b"upper"
    assert alice.download(uuid.UUID(items["report.pdf"]["id"])).content == b"lower"
    scan(fx)  # never merged on later passes
    assert len([i for i in alice.children(fx.taxes) if i["name"].lower() == "report.pdf"]) == 2


def test_app_refuses_to_create_a_name_that_differs_only_by_case(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.taxes, "Report.pdf", b"one"))
    r = alice.upload(fx.taxes, "REPORT.PDF", b"two")
    assert (r.status_code, code(r)) == (409, "NAME_CONFLICT")
    kept = alice.upload(fx.taxes, "REPORT.PDF", b"two", mode="keep_both")
    assert kept.status_code == 201 and kept.json()["node"]["name"] == "REPORT (2).PDF"
    r = alice.mkdir(fx.taxes, "report.PDF")
    assert (r.status_code, code(r)) == (409, "NAME_CONFLICT")
    other = nid(alice.upload(fx.taxes, "other.pdf", b"3"))
    r = alice.rename(other, "REPORT.pdf", row(fx, other).revision)
    assert (r.status_code, code(r)) == (409, "NAME_CONFLICT")
    # changing only the case of a node's own name is allowed
    assert alice.rename(f, "report.pdf", row(fx, f).revision).status_code == 200


def test_nfd_name_from_the_host_is_opened_as_it_is(fx: Fx, alice: Api) -> None:
    nfd = unicodedata.normalize("NFD", "café.txt")
    assert nfd != unicodedata.normalize("NFC", nfd)
    (disk(fx, fx.taxes) / nfd).write_bytes(b"decomposed")
    scan(fx)
    node = child(fx, fx.taxes, nfd)
    assert node is not None and node.name == nfd  # stored exactly as on disk
    assert alice.download(node.id).content == b"decomposed"
    assert alice.rename(node.id, "cafe.txt", node.revision).status_code == 200
    assert (disk(fx, fx.taxes) / "cafe.txt").read_bytes() == b"decomposed"
    # the app still normalizes names it creates itself
    made = alice.upload(fx.taxes, nfd, b"x", mode="keep_both")
    assert made.status_code == 201
    assert made.json()["node"]["name"] == unicodedata.normalize("NFC", "café.txt")


def test_undecodable_name_is_listed_stably_and_unsupported(fx: Fx, alice: Api) -> None:
    raw = os.fsencode(disk(fx, fx.taxes)) + b"/bad-\xff-name.txt"
    fd = os.open(raw, os.O_WRONLY | os.O_CREAT, 0o640)
    os.close(fd)
    scan(fx)
    scan(fx)
    items = [i for i in alice.children(fx.taxes) if i["unsupported_reason"] == "undecodable_name"]
    assert len(items) == 1 and "�" in items[0]["name"] and items[0]["kind"] == "other"
    node = row(fx, uuid.UUID(items[0]["id"]))
    assert node.raw_name_digest is not None and len(node.raw_name_digest) == 64
    assert alice.download(node.id).status_code == 409


def test_control_character_name_is_unsupported(fx: Fx, alice: Api) -> None:
    (disk(fx, fx.taxes) / "line\nbreak.txt").write_bytes(b"x")
    scan(fx)
    items = [i for i in alice.children(fx.taxes) if i["unsupported_reason"] == "invalid_name"]
    assert len(items) == 1


# ------------------------------------------------------------------ network filesystems and watcher


MOUNTINFO = (
    "22 1 0:21 / / rw - overlay overlay rw\n"
    "40 22 0:35 / /library rw,relatime shared:5 - nfs4 nas:/export rw,vers=4.2\n"
    "41 22 0:36 / /app\\040data rw - ext4 /dev/sda1 rw\n"
)


def test_network_filesystem_is_detected_from_mountinfo(tmp_path: Path) -> None:
    info = tmp_path / "mountinfo"
    info.write_text(MOUNTINFO)
    assert roots.filesystem_type(Path("/library/shared"), info) == "nfs4"
    assert roots.filesystem_type(Path("/library"), info) == "nfs4"
    assert roots.filesystem_type(Path("/app data/x"), info) == "ext4"
    assert roots.filesystem_type(Path("/elsewhere"), info) == "overlay"
    assert roots.is_network_fs("nfs4", "auto") and roots.is_network_fs("cifs", "auto")
    assert not roots.is_network_fs("ext4", "auto") and not roots.is_network_fs("overlay", "auto")
    assert roots.is_network_fs("ext4", "yes") and not roots.is_network_fs("nfs4", "no")


def test_network_fs_setting_is_recorded_and_the_watcher_stays_off(
    make_world: Callable[..., World], monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = Fx(make_world(network_fs="yes"))
    roots.ensure_roots(fx.w.ctx.sessions, fx.w.settings)
    with fx.w.ctx.sessions() as db:
        assert all(r.network_fs for r in db.scalars(select(StorageRoot)))
    admin = Api(fx, "admin")
    body = admin.get("/admin/storage").json()
    assert body["network_fs"] is True and body["network_acknowledged"] is False
    assert admin.post("/admin/storage/network-ack").status_code == 204
    assert admin.post("/admin/storage/network-ack").status_code == 204  # idempotent
    assert admin.get("/admin/storage").json()["network_acknowledged"] is True
    assert Api(fx, "alice").post("/admin/storage/network-ack").status_code == 403
    service: ReconcilerService = fx.w.app.state.reconciler
    if service.watcher is not None and service.watcher.start():
        service.sync_watches(root_id(fx, fx.alice_root))
        assert not service.watcher.watching(root_id(fx, fx.alice_root))
        service.watcher.stop()


def test_periodic_rescan_finds_changes_without_any_watcher(
    make_world: Callable[..., World],
) -> None:
    """Simulates a network share: no events ever arrive, the interval pass is the guarantee."""
    fx = Fx(make_world(network_fs="yes", watcher_enabled=False, rescan_interval_seconds=1))
    assert fx.w.app.state.reconciler.watcher is None
    scan(fx)
    (disk(fx, fx.taxes) / "arrives-silently.txt").write_bytes(b"x")
    service: ReconcilerService = fx.w.app.state.reconciler
    service.start()
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and child(fx, fx.taxes, "arrives-silently.txt") is None:
            time.sleep(0.2)
    finally:
        service.stop()
    assert child(fx, fx.taxes, "arrives-silently.txt") is not None


def test_inotify_watcher_reports_changed_directories(fx: Fx) -> None:
    hits: list[tuple[uuid.UUID, uuid.UUID]] = []
    overflow: list[bool] = []
    watcher = Watcher(lambda r, n: hits.append((r, n)), lambda: overflow.append(True))
    if not watcher.available or not watcher.start():
        pytest.skip("inotify is not available here")
    try:
        rid = root_id(fx, fx.alice_root)
        with fx.w.ctx.sessions() as db:
            rel = db.get(StorageRoot, rid).relative_dir  # type: ignore[union-attr]
        assert watcher.sync(fx.w.settings.library_path, rid, rel, [(fx.taxes, ["Taxes"])])
        assert watcher.watching(rid)
        (disk(fx, fx.taxes) / "event.txt").write_bytes(b"x")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not hits:
            time.sleep(0.05)
        assert (rid, fx.taxes) in hits
        watcher.forget(rid)
        assert not watcher.watching(rid)
    finally:
        watcher.stop()


def test_hints_are_debounced_then_rescan_the_directory(fx: Fx) -> None:
    settle(fx)
    service: ReconcilerService = fx.w.app.state.reconciler
    rid = root_id(fx, fx.alice_root)
    (disk(fx, fx.taxes) / "hinted.txt").write_bytes(b"x")
    service._dirty(rid, fx.taxes)
    assert service.process_requests() == 0  # too fresh
    assert child(fx, fx.taxes, "hinted.txt") is None
    assert service.process_requests(now=time.monotonic() + 3) == 1
    assert child(fx, fx.taxes, "hinted.txt") is not None
    with fx.w.ctx.sessions() as db:
        assert "hint" in set(db.scalars(select(ScanRun.trigger)))


# ------------------------------------------------------------------ hashing


def test_background_hasher_fills_hashes_and_skips_changed_files(fx: Fx, alice: Api) -> None:
    fx.w.settings.hash_rate_bytes = 10**9
    (disk(fx, fx.taxes) / "plain.txt").write_bytes(b"hash me")
    (disk(fx, fx.taxes) / "racy.txt").write_bytes(b"will change")
    scan(fx)
    plain = child(fx, fx.taxes, "plain.txt")
    racy = child(fx, fx.taxes, "racy.txt")
    assert plain is not None and racy is not None and plain.content_hash is None
    (disk(fx, fx.taxes) / "racy.txt").write_bytes(b"changed after the scan, longer")
    assert reconciler(fx).hash_pending(root_id(fx, fx.alice_root)) == 1
    import hashlib

    assert row(fx, plain.id).content_hash == hashlib.sha256(b"hash me").hexdigest()
    assert row(fx, racy.id).content_hash is None  # the fingerprint no longer matches


def test_hasher_can_be_turned_off(fx: Fx) -> None:
    fx.w.settings.hash_rate_bytes = 0
    (disk(fx, fx.taxes) / "p.txt").write_bytes(b"x")
    scan(fx)
    assert reconciler(fx).hash_pending(root_id(fx, fx.alice_root)) == 0


# ------------------------------------------------------------------ authorization


def test_rescan_needs_list_and_is_rate_limited(fx: Fx, alice: Api) -> None:
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "viewer")
    bob = Api(fx, "bob")
    assert Api(fx, "dave").post(f"/nodes/{fx.taxes}/rescan").status_code == 403
    assert Api(fx, "admin").post(f"/nodes/{fx.taxes}/rescan").status_code == 403
    assert bob.post(f"/nodes/{fx.taxes}/rescan").status_code == 202
    again = alice.post(f"/nodes/{fx.taxes}/rescan")
    assert (again.status_code, code(again)) == (429, "RESCAN_TOO_SOON")
    other = alice.post(f"/nodes/{fx.private}/rescan")  # a different folder is not limited
    assert other.status_code == 202
    f = nid(alice.upload(fx.taxes, "f.txt", b"x"))
    assert alice.post(f"/nodes/{f}/rescan").status_code == 422
    service: ReconcilerService = fx.w.app.state.reconciler
    assert service.process_requests() == 2


def test_new_routes_are_all_policy_guarded(fx: Fx) -> None:
    from test_acl import _api_routes, _guards

    routes = {
        (m, r.path): r
        for r in _api_routes(fx.w.app.routes)
        for m in r.methods
        if r.path.startswith("/api/v1/nodes/{node_id}/")
    }
    for key in [
        ("POST", "/api/v1/nodes/{node_id}/rescan"),
        ("POST", "/api/v1/nodes/{node_id}/dismiss"),
        ("POST", "/api/v1/nodes/{node_id}/scan/resolve"),
    ]:
        assert key in routes and _guards(routes[key].dependant), key


def test_events_never_contain_host_paths(fx: Fx, alice: Api) -> None:
    (disk(fx, fx.taxes) / "n.txt").write_bytes(b"x")
    scan(fx)
    lib = str(fx.w.settings.library_path)
    for e in all_events(fx):
        assert lib not in repr(e.details)
    assert stat.S_ISREG(os.stat(disk(fx, fx.taxes) / "n.txt").st_mode)


def test_scan_thread_survives_and_stops(fx: Fx) -> None:
    service: ReconcilerService = fx.w.app.state.reconciler
    service.start()
    service.start()  # idempotent
    time.sleep(0.5)
    service.stop()
    assert threading.active_count() >= 1


# ------------------------------------------------------------------ review fixes (Opus, M06a)


def set_inode(fx: Fx, node_id: uuid.UUID, ino: int) -> None:
    with fx.w.ctx.sessions() as db:
        node = db.get(Node, node_id)
        assert node is not None
        node.fs_ino = ino
        db.commit()


def test_reused_inode_does_not_hand_a_missing_folders_grants_to_a_new_folder(
    fx: Fx, alice: Api
) -> None:
    old = nid(alice.mkdir(fx.taxes, "Old"))
    nid(alice.upload(old, "kept.txt", b"secret ledger"))
    fx.grant(fx.alice, old, "bob@example.com", "viewer")
    shutil.rmtree(disk(fx, old))
    scan(fx)
    assert row(fx, old).status == "missing"
    payroll = disk(fx, fx.taxes) / "Payroll"
    payroll.mkdir()
    (payroll / "salaries.txt").write_bytes(b"unrelated")
    set_inode(fx, old, payroll.stat().st_ino)  # the kernel reused the freed inode number
    scan(fx)
    assert row(fx, old).status == "missing"  # not linked to the unrelated folder
    new = child(fx, fx.taxes, "Payroll")
    assert new is not None and new.id != old
    bob = Api(fx, "bob")
    assert bob.get(f"/nodes/{new.id}/children").status_code == 403
    assert not [e for e in events(fx, old) if e.action == "external_moved"]


def test_missing_file_with_a_reused_inode_and_other_content_is_not_linked(
    fx: Fx, alice: Api
) -> None:
    f = nid(alice.upload(fx.taxes, "old.txt", b"old content"))
    disk(fx, f).unlink()
    scan(fx)
    fresh = disk(fx, fx.taxes) / "unrelated.txt"
    fresh.write_bytes(b"completely different bytes")
    set_inode(fx, f, fresh.stat().st_ino)
    scan(fx)
    assert row(fx, f).status == "missing"
    assert child(fx, fx.taxes, "unrelated.txt") is not None


def test_tiny_identical_files_are_never_linked_by_hash(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.taxes, "tiny.txt", b"x"))
    src = disk(fx, f)
    shutil.copyfile(src, disk(fx, fx.private) / "other-tiny.txt")
    src.unlink()
    scan(fx)
    assert row(fx, f).status == "missing"


def test_file_and_folder_trading_a_name_do_not_stall_the_root(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.taxes, "swap", b"i was a file"))
    path = disk(fx, f)
    saved = path.read_bytes()
    path.unlink()
    scan(fx)
    path.mkdir()  # a folder now has the name
    scan(fx)
    folder = child(fx, fx.taxes, "swap", status="active")
    assert folder is not None and folder.kind == "dir"
    path.rmdir()
    path.write_bytes(saved)  # and the file is back
    result = scan(fx)
    assert result.outcome == "ok"
    assert row(fx, f).status == "active" and row(fx, folder.id).status == "missing"
    with fx.w.ctx.sessions() as db:
        assert not list(db.scalars(select(ScanRun).where(ScanRun.outcome == "running")))


def test_a_crashing_pass_never_leaves_a_running_scan_run(
    fx: Fx, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: Any, plan: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(reconcile.Applier, "apply", boom)
    result = scan(fx)
    assert result.outcome == "failed"
    with fx.w.ctx.sessions() as db:
        runs = list(db.scalars(select(ScanRun.outcome)))
        assert "running" not in runs and "failed" in runs


def test_dismissing_grants_needs_a_grant_administrator_and_audits_them(fx: Fx, alice: Api) -> None:
    sub = nid(alice.mkdir(fx.taxes, "Granted"))
    fx.grant(fx.alice, fx.taxes, "carol@example.com", "editor")
    fx.grant(fx.alice, sub, "bob@example.com", "viewer")
    shutil.rmtree(disk(fx, sub))
    scan(fx)
    carol = Api(fx, "carol")
    assert carol.post(f"/nodes/{sub}/dismiss").status_code == 403  # editors cannot drop grants
    assert row(fx, sub).status == "missing"
    assert alice.post(f"/nodes/{sub}/dismiss").status_code == 200
    from app.db.models import AuthAudit

    with fx.w.ctx.sessions() as db:
        rec = [a for a in db.scalars(select(AuthAudit)) if a.action == "file.dismissed"]
        assert rec and rec[-1].details["grants_removed"][0]["level"] == "viewer"


def test_hashing_skips_a_suspended_root(fx: Fx) -> None:
    fx.w.settings.hash_rate_bytes = 10**9
    (disk(fx, fx.taxes) / "h.txt").write_bytes(b"hash me")
    scan(fx)
    with fx.w.ctx.sessions() as db:
        root = db.get(StorageRoot, root_id(fx, fx.alice_root))
        assert root is not None
        root.scan_state = "suspended"
        db.commit()
    assert reconciler(fx).hash_pending(root_id(fx, fx.alice_root)) == 0


def test_device_is_rechecked_on_every_pass(fx: Fx, monkeypatch: pytest.MonkeyPatch) -> None:
    rid = root_id(fx, fx.alice_root)
    with fx.w.ctx.sessions() as db:
        rel = db.get(StorageRoot, rid).relative_dir  # type: ignore[union-attr]
    real = roots.root_device
    monkeypatch.setattr(
        roots, "root_device", lambda lib, r: real(lib, r) + 1 if r == rel else real(lib, r)
    )
    assert reconciler(fx).run_root(rid, "periodic").outcome == "skipped"
    with fx.w.ctx.sessions() as db:
        assert db.get(StorageRoot, rid).status == "unsupported_mount"  # type: ignore[union-attr]


# ------------------------------------------------------------------ apply is never blind


def test_suspended_root_reports_what_it_is_waiting_on(fx: Fx, alice: Api) -> None:
    base = make_tree(fx, fx.taxes)
    scan(fx)
    shutil.rmtree(base)
    scan(fx)
    status = alice.get(f"/nodes/{fx.alice_root}/scan").json()
    assert status["scan_state"] == "suspended" and status["reason"] == "MASS_CHANGE"
    assert status["counts"]["affected"] >= 121 and status["counts"]["missing"] >= 1
    assert "path" not in repr(status) and "bulk" not in repr(status)  # counts only
    assert Api(fx, "admin").get(f"/nodes/{fx.alice_root}/scan").status_code == 403
    r = alice.get(f"/nodes/{fx.taxes}/scan")
    assert (r.status_code, code(r)) == (422, "NOT_A_ROOT")


def test_apply_is_refused_when_the_plan_grew_since_it_was_reviewed(fx: Fx, alice: Api) -> None:
    first = make_tree(fx, fx.taxes, 120)
    second = make_tree(fx, fx.private, 100)
    scan(fx)
    shutil.rmtree(first)
    assert scan(fx).outcome == "suspended"
    reviewed = alice.get(f"/nodes/{fx.alice_root}/scan").json()["counts"]["affected"]
    shutil.rmtree(second)  # more disappears while the owner is looking
    resolve = {"action": "apply"}
    assert alice.post(f"/nodes/{fx.alice_root}/scan/resolve", json=resolve).status_code == 200
    service: ReconcilerService = fx.w.app.state.reconciler
    fx.w.advance(seconds=5)  # distinct run timestamps, as in real life
    service.process_requests()
    again = alice.get(f"/nodes/{fx.alice_root}/scan").json()
    assert again["scan_state"] == "suspended" and again["reason"] == "CHANGED_SINCE_REVIEW"
    assert again["counts"]["affected"] > reviewed
    with fx.w.ctx.sessions() as db:
        assert not list(db.scalars(select(Node).where(Node.status == "missing")))  # nothing applied
    # having seen the new numbers, applying again goes through
    assert alice.post(f"/nodes/{fx.alice_root}/scan/resolve", json=resolve).status_code == 200
    fx.w.advance(seconds=5)
    service.process_requests()
    assert alice.get(f"/nodes/{fx.alice_root}/scan").json()["scan_state"] is None
    with fx.w.ctx.sessions() as db:
        assert len(list(db.scalars(select(Node).where(Node.status == "missing")))) >= 220
