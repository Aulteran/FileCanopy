# ruff: noqa: F811  (fixtures are imported from test_imports and used as arguments)
"""Regression tests for the M06b security review (Opus 5.5): every finding has a test here that
fails on the code as it was reviewed. Built from the reviewer's proofs of concept.

Findings: a zip declaring one entry but holding millions of records; large JSON bodies parsed
before authentication; folder uploads that left the setup window open; uploads that kept writing
after the destination moved out of the uploader's reach; an import holding the reconciler off a
root indefinitely. Plus the lower-confidence items (one open setup import per root in the
database, a finished batch is never re-opened, staging space and concurrency limits, a per-person
cap on open folder uploads) and the owner's retention and recovery decisions.

All content is generated; no real documents.
"""

import struct
import tracemalloc
import uuid
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from test_imports import (  # noqa: F401  (fixtures are re-exported for pytest)
    Env,
    Person,
    _root_id,
    children,
    code,
    entries,
    env,
    make_world,
    make_zip,
    manifest,
    plan_folder,
    plan_zip,
    put,
    set_entry_status,
    state,
)

from app.auth import clock
from app.db.models import AuthAudit, ImportBatch, ImportEntry, StorageRoot
from app.domain import imports
from app.domain.fileops import OpError
from app.storage import ziparchive
from app.storage.reconcile import Reconciler
from app.storage.service import ReconcilerService
from app.storage.ziparchive import ZipRefused, inspect_zip

# ------------------------------------------------------------------ 1. a lying zip entry count


def lying_zip(path: Path, records: int, declared: int = 1) -> None:
    """One stored empty file plus `records` central-directory records, all pointing at it, under an
    end record that declares only `declared` entries (the reviewer's cdbomb.py)."""
    local = struct.pack("<4sHHHHHIIIHH", b"PK\x03\x04", 20, 0, 0, 0, 0x21, 0, 0, 0, 1, 0) + b"a"
    record = struct.pack(
        "<4sHHHHHHIIIHHHHHII", b"PK\x01\x02", 20, 20, 0, 0, 0, 0x21, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0
    )
    directory = (record + b"a") * records
    end = struct.pack(
        "<4sHHHHIIH", b"PK\x05\x06", 0, 0, declared, declared, len(directory), len(local), 0
    )
    path.write_bytes(local + directory + end)


def test_a_zip_declaring_one_entry_but_holding_many_records_is_refused_unread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bomb = tmp_path / "bomb.zip"
    lying_zip(bomb, records=250_000)

    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("zipfile must not load a directory that was not validated")

    monkeypatch.setattr(ziparchive.zipfile, "ZipFile", forbidden)
    with pytest.raises(ZipRefused) as err:
        inspect_zip(bomb, max_entries=100_000)
    assert err.value.code == "TOO_MANY_ENTRIES"


def test_records_that_do_not_match_the_declared_count_or_size_are_invalid(tmp_path: Path) -> None:
    few = tmp_path / "few.zip"
    lying_zip(few, records=5, declared=1)  # fewer than the limit, still not what it declares
    with pytest.raises(ZipRefused) as err:
        inspect_zip(few, max_entries=100)
    assert err.value.code == "INVALID_ZIP"
    # a directory far larger than any plausible record budget is refused without being read
    huge = tmp_path / "huge.zip"
    lying_zip(huge, records=3000, declared=3000)
    with pytest.raises(ZipRefused) as big:
        inspect_zip(huge, max_entries=2)
    assert big.value.code == "TOO_MANY_ENTRIES"
    # a directory cut short in the middle of a record
    broken = tmp_path / "broken.zip"
    data = make_zip({"a": b"1", "b": b"2"})
    at = data.rfind(b"PK\x05\x06")
    cut = bytearray(data)
    cut[at + 12 : at + 16] = struct.pack("<I", struct.unpack_from("<I", data, at + 12)[0] - 7)
    broken.write_bytes(bytes(cut))
    with pytest.raises(ZipRefused):
        inspect_zip(broken, max_entries=10)


def test_a_zip64_archive_with_many_entries_is_still_listed(tmp_path: Path) -> None:
    path = tmp_path / "many.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        for i in range(70_000):  # more than 65535: the end record needs zip64
            zf.writestr(f"d/{i}", b"")
    assert len(inspect_zip(path, max_entries=100_000)) == 70_000
    with pytest.raises(ZipRefused) as err:
        inspect_zip(path, max_entries=69_999)
    assert err.value.code == "TOO_MANY_ENTRIES"


def test_a_zip64_locator_hidden_behind_a_plain_end_record_is_walked_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second review: the end record declares one entry and no zip64 fields, but a zip64 record
    and locator sit right before it, inside the comment of the one record a walk of the plain end
    record would see. CPython always follows such a locator, so the walk must follow it as well."""
    records = 250_000
    local = struct.pack("<4sHHHHHIIIHH", b"PK\x03\x04", 20, 0, 0, 0, 0x21, 0, 0, 0, 1, 0) + b"a"
    head = "<4sHHHHHHIIIHHHHHII"
    record = struct.pack(head, b"PK\x01\x02", 20, 20, 0, 0, 0, 0x21, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0)
    directory = (record + b"a") * records
    hidden_at = len(local) + len(directory)
    hidden = struct.pack(head, b"PK\x01\x02", 20, 20, 0, 0, 0, 0x21, 0, 0, 0, 0, 0, 76, 0, 0, 0, 0)
    reloff = hidden_at + len(hidden)  # the zip64 record starts in the hidden record's comment
    count, size = records + 1, reloff - len(local)
    z64 = struct.pack(
        "<4sQHHIIQQQQ", b"PK\x06\x06", 44, 45, 45, 0, 0, count, count, size, len(local)
    )
    locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, reloff, 1)
    end = struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, 1, 1, 46 + 76, hidden_at, 0)
    bomb = tmp_path / "hidden64.zip"
    bomb.write_bytes(local + directory + hidden + z64 + locator + end)

    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("zipfile must not load a directory that was not validated")

    monkeypatch.setattr(ziparchive.zipfile, "ZipFile", forbidden)
    with pytest.raises(ZipRefused) as err:
        inspect_zip(bomb, max_entries=100_000)
    assert err.value.code == "TOO_MANY_ENTRIES"


def test_uploading_a_lying_zip_is_refused_and_nothing_stays_staged(
    make_world: Any, tmp_path: Path
) -> None:
    env = Env(make_world(max_import_entries=1000))
    lying = tmp_path / "lying.zip"
    lying_zip(lying, records=20_000)
    a = env.alice
    r = plan_zip(a, a.personal_root(), lying.read_bytes())
    assert r.status_code == 413 and code(r) == "TOO_MANY_ENTRIES"
    staging = env.w.settings.app_data_path / "staging" / "imports"
    assert not staging.exists() or list(staging.iterdir()) == []
    assert state(a, a.personal_root())["batch"] is None


# ------------------------------------------------------------------ 2. auth before the body


def chunks(total: int, size: int = 1 << 20) -> Iterator[bytes]:
    item = b'{"path":"a","kind":"dir"},'
    block = item * (size // len(item))
    sent = 0
    yield b'{"entries":['
    while sent < total:
        yield block
        sent += len(block)
    yield b'{"path":"a","kind":"dir"}]}'


def peak_mib(call: Any) -> tuple[Any, float]:
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        out = call()
        return out, tracemalloc.get_traced_memory()[1] / (1 << 20)
    finally:
        tracemalloc.stop()


@pytest.mark.parametrize("route", ["folder-uploads", "setup/import"])
def test_an_anonymous_manifest_is_refused_before_its_body_is_read(env: Env, route: str) -> None:
    anon = env.w.browser()
    url = f"/api/v1/nodes/{uuid.uuid4()}/{route}"
    r, peak = peak_mib(
        lambda: anon.post(
            url, content=chunks(24 << 20), headers={"content-type": "application/json"}
        )
    )
    assert r.status_code == 401, r.text
    assert peak < 8, f"{peak:.1f} MiB were allocated for an unauthenticated request"


def test_a_manifest_without_the_csrf_token_is_refused_before_its_body_is_read(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    r, peak = peak_mib(
        lambda: a.client.post(
            f"/api/v1/nodes/{root}/setup/import",
            content=chunks(24 << 20),
            headers={"content-type": "application/json"},
        )
    )
    assert r.status_code == 403 and code(r) == "CSRF_TOKEN_INVALID"
    assert peak < 8


def test_a_signed_in_manifest_over_the_byte_cap_is_refused_while_it_is_read(
    make_world: Any,
) -> None:
    env = Env(make_world(max_import_entries=1000))
    a = env.alice
    cap = 1000 * imports.MANIFEST_BYTES_PER_ENTRY + 64 * 1024
    root = a.personal_root()
    r, peak = peak_mib(
        lambda: a.req("POST", f"/nodes/{root}/setup/import", content=chunks(cap * 3))
    )
    assert r.status_code == 413 and code(r) == "REQUEST_TOO_LARGE"
    # What was buffered stays near the cap, not the 3x body. The slack covers the test client's own
    # 2 MiB chunk and framework code first imported during the request (it grew by ~50 KiB in M10).
    assert peak < cap * 2 / (1 << 20) + 5


def test_the_entry_limit_comes_from_the_setting_and_a_bad_body_is_a_plain_422(
    make_world: Any,
) -> None:
    env = Env(make_world(max_import_entries=2))
    a = env.alice
    root = a.personal_root()
    many = a.post(
        f"/nodes/{root}/setup/import", json={"entries": manifest({"1": b"", "2": b"", "3": b""})}
    )
    assert many.status_code == 413 and code(many) == "TOO_MANY_ENTRIES"
    bad = a.post(f"/nodes/{root}/setup/import", content=b"{not json")
    assert bad.status_code == 422 and code(bad) == "VALIDATION_ERROR"
    odd = a.post(
        f"/nodes/{root}/folder-uploads", json={"entries": [{"path": "x", "kind": "dir"}], "x": 1}
    )
    assert odd.status_code == 422


# ---------------------------------------------------------------- 3. folder upload closes it


def upload_folder(p: Person, node: str, files: dict[str, bytes]) -> dict[str, Any]:
    r = p.post(f"/nodes/{node}/folder-uploads", json={"entries": manifest(files)})
    assert r.status_code == 201, r.text
    batch: dict[str, Any] = r.json()
    for e in entries(p, batch["id"], status="pending"):
        if e["kind"] != "file":
            continue
        assert put(p, batch["id"], e["index"], files[e["path"]]).status_code == 200
    return batch


def test_a_folder_upload_into_an_open_personal_root_closes_the_setup_window(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    assert state(a, root)["state"] == "open"
    batch = upload_folder(a, root, {"Docs/a.txt": b"hello"})
    assert a.get(f"/imports/{batch['id']}").json()["status"] == "done"
    assert state(a, root)["state"] == "closed"
    late = plan_folder(a, root, {"Docs/a.txt": b"x"})
    assert late.status_code == 409 and code(late) == "SETUP_CLOSED"
    reasons = [
        r.details["reason"] for r in env.w.db_rows(AuthAudit) if r.action == "storage.setup_closed"
    ]
    assert reasons == ["folder_upload"]


def test_a_folder_upload_into_the_open_shared_root_closes_the_window(env: Env) -> None:
    admin = env.admin
    shared = admin.shared_root()
    assert state(admin, shared)["state"] == "open"
    upload_folder(admin, shared, {"Team/plan.txt": b"plan"})
    assert state(admin, shared)["state"] == "closed"
    assert code(plan_folder(admin, shared, {"x.txt": b"x"})) == "SETUP_CLOSED"


def test_empty_folders_of_an_upload_also_close_the_window(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    r = a.post(
        f"/nodes/{root}/folder-uploads", json={"entries": manifest({}, dirs=("Only/Empty",))}
    )
    assert r.status_code == 201, r.text
    assert (a.personal_disk() / "Only" / "Empty").is_dir()
    assert state(a, root)["state"] == "closed"


def test_a_setup_import_still_does_not_close_its_own_window(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    batch = plan_folder(a, root, {"a.txt": b"a"}).json()
    a.post(f"/imports/{batch['id']}/start")
    assert put(a, batch["id"], 0, b"a").status_code == 200
    assert state(a, root)["state"] == "open"


def test_a_recovered_folder_upload_file_closes_the_window_too(env: Env) -> None:
    """A file published just before a crash is adopted at startup: that creates a node as well."""
    a = env.alice
    root = a.personal_root()
    files = {"x.txt": b"data"}
    batch = a.post(f"/nodes/{root}/folder-uploads", json={"entries": manifest(files)}).json()
    # the process died after publishing x.txt and before recording the node
    a.personal_disk().mkdir(parents=True, exist_ok=True)
    (a.personal_disk() / "x.txt").write_bytes(b"data")
    with env.w.ctx.sessions() as db:
        row = db.scalars(
            select(ImportEntry).where(
                ImportEntry.batch_id == uuid.UUID(batch["id"]), ImportEntry.kind == "file"
            )
        ).one()
        root_node = db.scalars(
            select(StorageRoot).where(StorageRoot.owner_user_id == a.uid())
        ).one()
        row.status, row.dst_name = "writing", "x.txt"
        from app.db.models import Node

        row.dir_node_id = db.scalars(
            select(Node.id).where(Node.root_id == root_node.id, Node.parent_id.is_(None))
        ).one()
        db.commit()
    assert imports.recover_imports(env.w.ctx.sessions, env.w.settings)["adopted"] == 1
    assert state(a, root)["state"] == "closed"


# ------------------------------------------------------------------ 4. the destination moved away


def inbox_world(env: Env) -> tuple[str, str]:
    a, c = env.alice, env.carol
    root = a.personal_root()
    a.post(f"/nodes/{root}/folders", json={"name": "Inbox"})
    a.post(f"/nodes/{root}/folders", json={"name": "Private"})
    kids = children(a, root)
    inbox, private = kids["Inbox"]["id"], kids["Private"]["id"]
    assert (
        a.req("PUT", f"/nodes/{inbox}/grants/{c.uid()}", json={"level": "editor"}).status_code
        == 200
    )
    return inbox, private


def test_entries_are_not_written_after_the_folder_moved_out_of_the_uploaders_reach(
    env: Env,
) -> None:
    a, c = env.alice, env.carol
    inbox, private = inbox_world(env)
    files = {"Batch/one.txt": b"1", "Batch/sub/two.txt": b"2", "Batch/three.txt": b"3"}
    batch = c.post(f"/nodes/{inbox}/folder-uploads", json={"entries": manifest(files)}).json()
    idx = {e["path"]: e["index"] for e in entries(c, batch["id"])}
    assert put(c, batch["id"], idx["Batch/one.txt"], b"1").status_code == 200

    moved = children(a, inbox)["Batch"]
    r = a.post(
        f"/nodes/{moved['id']}/move",
        json={"target_parent_id": private, "expected_revision": moved["revision"]},
    )
    assert r.status_code in (200, 201), r.text
    assert c.get(f"/nodes/{private}/children").status_code == 403  # carol has nothing on Private

    for path in ("Batch/three.txt", "Batch/sub/two.txt"):  # one existing folder, one new subfolder
        w = put(c, batch["id"], idx[path], files[path])
        assert w.status_code == 409 and code(w) == "PARENT_CHANGED", w.text
        for secret in ("Private", str(private), moved["id"]):
            assert secret not in w.text
    landed = a.personal_disk() / "Private" / "Batch"
    assert sorted(p.name for p in landed.iterdir()) == ["one.txt"]  # nothing new, no subfolder
    failed = {e["path"]: e["reason"] for e in entries(c, batch["id"]) if e["status"] == "failed"}
    assert failed == {"Batch/three.txt": "PARENT_CHANGED", "Batch/sub/two.txt": "PARENT_CHANGED"}


def test_a_folder_that_stays_under_the_target_is_still_written_into(env: Env) -> None:
    a, c = env.alice, env.carol
    inbox, _ = inbox_world(env)
    a.post(f"/nodes/{inbox}/folders", json={"name": "Deeper"})
    deeper = children(a, inbox)["Deeper"]["id"]
    files = {"Batch/one.txt": b"1", "Batch/two.txt": b"2"}
    batch = c.post(f"/nodes/{inbox}/folder-uploads", json={"entries": manifest(files)}).json()
    idx = {e["path"]: e["index"] for e in entries(c, batch["id"])}
    assert put(c, batch["id"], idx["Batch/one.txt"], b"1").status_code == 200
    moved = children(a, inbox)["Batch"]
    a.post(
        f"/nodes/{moved['id']}/move",
        json={"target_parent_id": deeper, "expected_revision": moved["revision"]},
    )
    assert put(c, batch["id"], idx["Batch/two.txt"], b"2").status_code == 200  # editor via Inbox


def test_a_folder_the_uploader_can_no_longer_write_to_is_refused_even_under_the_target(
    env: Env,
) -> None:
    a, c = env.alice, env.carol
    inbox, _ = inbox_world(env)
    files = {"Batch/one.txt": b"1", "Batch/two.txt": b"2"}
    batch = c.post(f"/nodes/{inbox}/folder-uploads", json={"entries": manifest(files)}).json()
    idx = {e["path"]: e["index"] for e in entries(c, batch["id"])}
    assert put(c, batch["id"], idx["Batch/one.txt"], b"1").status_code == 200
    # the grant is narrowed to read-only on the batch's own folder (a viewer grant replaces it)
    assert (
        a.req("PUT", f"/nodes/{inbox}/grants/{c.uid()}", json={"level": "viewer"}).status_code
        == 200
    )
    w = put(c, batch["id"], idx["Batch/two.txt"], b"2")
    assert w.status_code in (403, 409)
    assert not (a.personal_disk() / "Inbox" / "Batch" / "two.txt").exists()


def test_the_final_check_before_publishing_also_notices_a_move(env: Env) -> None:
    """A folder moved between `begin()` and `finish()` of one entry."""
    a, c = env.alice, env.carol
    inbox, private = inbox_world(env)
    files = {"Batch/one.txt": b"1", "Batch/two.txt": b"2"}
    batch = c.post(f"/nodes/{inbox}/folder-uploads", json={"entries": manifest(files)}).json()
    idx = {e["path"]: e["index"] for e in entries(c, batch["id"])}
    assert put(c, batch["id"], idx["Batch/one.txt"], b"1").status_code == 200
    ctx = imports.OpCtx(env.w.ctx.sessions(), env.w.settings, c.uid())
    upload = imports.EntryUpload(ctx, uuid.UUID(batch["id"]), idx["Batch/two.txt"])
    upload.begin()
    upload.write(b"2")
    moved = children(a, inbox)["Batch"]
    a.post(
        f"/nodes/{moved['id']}/move",
        json={"target_parent_id": private, "expected_revision": moved["revision"]},
    )
    with pytest.raises(OpError) as err:
        upload.finish()
    assert err.value.code == "PARENT_CHANGED"
    upload.abort(err.value.code)
    assert sorted(p.name for p in (a.personal_disk() / "Private" / "Batch").iterdir()) == [
        "one.txt"
    ]


# ---------------------------------------------------------------- 5. the pause is bounded


def start_batch(env: Env, p: Person, root: str, files: dict[str, bytes]) -> str:
    batch = plan_folder(p, root, files).json()
    p.post(f"/imports/{batch['id']}/start")
    return str(batch["id"])


def test_resuming_or_touching_a_batch_does_not_hold_the_reconciler_off(env: Env) -> None:
    a, c = env.alice, env.carol
    root = a.personal_root()
    a.post(f"/nodes/{root}/folders", json={"name": "Drop"})
    drop = children(a, root)["Drop"]["id"]
    a.req("PUT", f"/nodes/{drop}/grants/{c.uid()}", json={"level": "editor"})
    files = {"never.txt": b"zz"}
    batch = c.post(f"/nodes/{drop}/folder-uploads", json={"entries": manifest(files)}).json()
    c.post(f"/imports/{batch['id']}/resume", json={"entries": manifest(files)})
    (a.personal_disk() / "outside.txt").write_bytes(b"from host")
    result = Reconciler(env.w.ctx.sessions, env.w.settings).run_root(_root_id(env, root), "manual")
    assert result.outcome == "ok"
    assert "outside.txt" in children(a, root)


def test_the_pause_is_bounded_and_the_root_then_reports_a_delayed_scan(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    bid = start_batch(env, a, root, {"a.txt": b"a", "b.txt": b"b"})
    a.personal_disk().mkdir(parents=True, exist_ok=True)
    (a.personal_disk() / "from-host.txt").write_bytes(b"outside")
    set_entry_status(env, bid, 0, "writing")  # a file that is "in flight" for ever
    recon = Reconciler(env.w.ctx.sessions, env.w.settings)
    rid = _root_id(env, root)
    ceiling = env.w.settings.scan_pause_ceiling_seconds
    assert recon.run_root(rid, "periodic").outcome == "skipped"
    assert a.get(f"/nodes/{root}").json()["scan_delayed"] is True
    env.w.advance(seconds=ceiling - 60)
    assert recon.run_root(rid, "periodic").outcome == "skipped"  # still inside the ceiling
    env.w.advance(seconds=120)
    a = Person(env.w, "alice@example.com")  # the earlier session is long gone
    assert recon.run_root(rid, "periodic").outcome == "ok"  # the ceiling passed: the scan proceeds
    assert state(a, root)["state"] == "closed"  # it saw the outside file
    assert a.get(f"/nodes/{root}").json()["scan_delayed"] is True  # the root says it was delayed
    set_entry_status(env, bid, 0, "pending")
    assert recon.run_root(rid, "periodic").outcome == "ok"
    assert a.get(f"/nodes/{root}").json()["scan_delayed"] is False  # cleared once nothing writes
    assert 30 * 60 == ceiling  # the default is thirty minutes


def test_a_mount_over_the_root_is_noticed_even_while_an_import_is_writing(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = env.alice
    root = a.personal_root()
    bid = start_batch(env, a, root, {"a.txt": b"a"})
    set_entry_status(env, bid, 0, "writing")
    monkeypatch.setattr(Reconciler, "_left_library_device", lambda self, r: True)
    result = Reconciler(env.w.ctx.sessions, env.w.settings).run_root(_root_id(env, root), "manual")
    assert result.outcome == "skipped"
    with env.w.ctx.sessions() as db:
        row = db.scalars(select(StorageRoot).where(StorageRoot.owner_user_id == a.uid())).one()
        assert row.status == "unsupported_mount"


def test_a_running_batch_with_no_file_in_flight_does_not_pause_scanning(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    start_batch(env, a, root, {"a.txt": b"a"})
    a.personal_disk().mkdir(parents=True, exist_ok=True)
    recon = Reconciler(env.w.ctx.sessions, env.w.settings)
    assert recon.run_root(_root_id(env, root), "manual").outcome == "ok"
    assert a.get(f"/nodes/{root}").json()["scan_delayed"] is False


# ------------------------------------------------------------------ 6. lower-confidence items


def test_one_open_setup_batch_per_root_is_a_database_guarantee(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = env.alice
    root = a.personal_root()
    assert plan_folder(a, root, {"a.txt": b"a"}).status_code == 201
    # two requests that both passed the pre-check: the unique index decides the second
    monkeypatch.setattr(imports, "_no_open_setup_batch", lambda ctx, r: None)
    second = plan_folder(a, root, {"b.txt": b"b"})
    assert second.status_code == 409 and code(second) == "IMPORT_IN_PROGRESS"
    with env.w.ctx.sessions() as db:
        rows = list(db.scalars(select(ImportBatch).where(ImportBatch.kind == "setup_folder")))
        assert len(rows) == 1
        twin = ImportBatch(
            id=uuid.uuid4(),
            root_id=rows[0].root_id,
            target_node_id=rows[0].target_node_id,
            actor_user_id=rows[0].actor_user_id,
            kind="setup_zip",
            status="running",
            plan={},
            created_at=clock.utcnow(),
            updated_at=clock.utcnow(),
        )
        db.add(twin)
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


def test_a_finished_batch_is_never_reopened_by_resume(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    a.post(f"/nodes/{root}/folders", json={"name": "F"})
    folder = children(a, root)["F"]["id"]
    files = {"x.txt": b"x"}
    batch = upload_folder(a, folder, files)
    assert a.get(f"/imports/{batch['id']}").json()["status"] == "done"
    r = a.post(f"/imports/{batch['id']}/resume", json={"entries": manifest(files)})
    assert r.status_code == 409 and code(r) == "NOT_RESUMABLE"
    assert a.get(f"/imports/{batch['id']}").json()["status"] == "done"


def test_resume_and_expiry_are_audited(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    a.post(f"/nodes/{root}/folders", json={"name": "F"})
    folder = children(a, root)["F"]["id"]
    files = {"x.txt": b"x", "y.txt": b"y"}
    batch = a.post(f"/nodes/{folder}/folder-uploads", json={"entries": manifest(files)}).json()
    assert (
        a.post(f"/imports/{batch['id']}/resume", json={"entries": manifest(files)}).status_code
        == 200
    )
    assert "import.resumed" in env.audit_actions()
    env.w.advance(seconds=env.w.settings.import_expiry_seconds + 5)
    a = Person(env.w, "alice@example.com")
    assert a.get("/imports").json() == []  # expired on the way
    row = next(r for r in env.w.db_rows(AuthAudit) if r.action == "import.expired")
    assert row.details["batch_id"] == batch["id"]


def test_a_zip_is_refused_when_app_data_cannot_hold_it(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = env.alice
    root = a.personal_root()
    app_data = env.w.settings.app_data_path
    real = imports.shutil.disk_usage

    def tiny(path: Any) -> Any:
        usage = real(path)
        if Path(path) == app_data:
            return type(usage)(1000, 900, 100)
        return usage

    monkeypatch.setattr(imports.shutil, "disk_usage", tiny)
    r = plan_zip(a, root, make_zip({"a.txt": b"a" * 5000}))
    assert r.status_code == 507 and code(r) == "INSUFFICIENT_SPACE"
    staging = app_data / "staging" / "imports"
    assert not staging.exists() or list(staging.iterdir()) == []


def test_only_one_zip_upload_is_in_flight_per_root_and_per_person(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    other = a.shared_root()
    root_id, shared_id = _root_id(env, root), _root_id(env, other)
    imports.zip_slots.acquire(root_id, a.uid())
    try:
        busy = plan_zip(a, root, make_zip({"a.txt": b"a"}))
        assert busy.status_code == 409 and code(busy) == "IMPORT_IN_PROGRESS"
        with pytest.raises(OpError) as same_person:
            imports.zip_slots.acquire(shared_id, a.uid())
        assert same_person.value.status == 429 and same_person.value.code == "UPLOAD_IN_PROGRESS"
        imports.zip_slots.acquire(shared_id, env.bob.uid())  # someone else is not limited
        imports.zip_slots.release(shared_id, env.bob.uid())
    finally:
        imports.zip_slots.release(root_id, a.uid())
    assert plan_zip(a, root, make_zip({"a.txt": b"a"})).status_code == 201  # the slot was freed


def test_the_number_of_open_folder_uploads_per_person_is_limited(make_world: Any) -> None:
    env = Env(make_world(max_open_folder_uploads=2))
    a = env.alice
    root = a.personal_root()
    a.post(f"/nodes/{root}/folders", json={"name": "F"})
    folder = children(a, root)["F"]["id"]
    ids = []
    for i in range(2):
        r = a.post(
            f"/nodes/{folder}/folder-uploads", json={"entries": manifest({f"f{i}.txt": b"x"})}
        )
        assert r.status_code == 201
        ids.append(r.json()["id"])
    third = a.post(f"/nodes/{folder}/folder-uploads", json={"entries": manifest({"f3.txt": b"x"})})
    assert third.status_code == 429 and code(third) == "TOO_MANY_OPEN_UPLOADS"
    assert (
        env.bob.post(
            f"/nodes/{env.bob.personal_root()}/folder-uploads", json={"entries": []}
        ).status_code
        == 201
    )
    a.post(f"/imports/{ids[0]}/cancel")  # discarding one frees a slot
    again = a.post(f"/nodes/{folder}/folder-uploads", json={"entries": manifest({"f3.txt": b"x"})})
    assert again.status_code == 201


def test_the_open_upload_count_and_the_insert_are_serialized_per_person(
    make_world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second review: the limit was counted before a (possibly long) plan and checked again by
    nobody, so concurrent manifests could all pass it. Planning now holds a per-person lock from
    the count to the insert."""
    env = Env(make_world())
    a = env.alice
    root = a.personal_root()
    held: list[bool] = []
    real = imports.build_plan

    def watching(*args: Any, **kw: Any) -> Any:
        held.append(imports._user_lock(a.uid()).locked())
        return real(*args, **kw)

    monkeypatch.setattr(imports, "build_plan", watching)
    r = a.post(f"/nodes/{root}/folder-uploads", json={"entries": manifest({"f.txt": b"x"})})
    assert r.status_code == 201 and held == [True]


def test_an_entry_claimed_by_another_request_meanwhile_is_not_claimed_twice(
    make_world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second review: two requests sending the same entry could both pass the `writing` check and
    both claim it (two files, and the first one's crash-recovery record overwritten). The claim is
    now a conditional update."""
    env = Env(make_world())
    a = env.alice
    root = a.personal_root()
    r = a.post(f"/nodes/{root}/folder-uploads", json={"entries": manifest({"f.txt": b"x"})})
    batch = r.json()["id"]
    (entry,) = [e for e in entries(a, batch) if e["kind"] == "file"]
    real = imports._touch

    def raced(db: Any, batch_id: uuid.UUID) -> None:
        real(db, batch_id)
        set_entry_status(env, batch, entry["index"], "writing")  # the other request claims it

    monkeypatch.setattr(imports, "_touch", raced)
    busy = put(a, batch, entry["index"], b"x")
    assert busy.status_code == 409 and code(busy) == "ENTRY_BUSY"
    assert [e["status"] for e in entries(a, batch) if e["kind"] == "file"] == ["writing"]
    assert "f.txt" not in children(a, root)


# ------------------------------------------------------------------ C. retention and recovery


def finished_batch(env: Env, p: Person, folder: str, name: str, files: dict[str, bytes]) -> str:
    return str(upload_folder(p, folder, {f"{name}/{k}": v for k, v in files.items()})["id"])


def test_import_entries_are_pruned_ninety_days_after_completion_and_batches_are_kept(
    env: Env,
) -> None:
    a = env.alice
    root = a.personal_root()
    a.post(f"/nodes/{root}/folders", json={"name": "F"})
    folder = children(a, root)["F"]["id"]
    old = finished_batch(env, a, folder, "old", {"1.txt": b"1", "2.txt": b"2"})
    failing = a.post(
        f"/nodes/{folder}/folder-uploads", json={"entries": manifest({"bad.txt": b"abc"})}
    ).json()
    assert put(a, failing["id"], 0, b"x").status_code == 409  # SIZE_MISMATCH: an unresolved failure
    stopped = a.post(
        f"/nodes/{folder}/folder-uploads", json={"entries": manifest({"s.txt": b"s"})}
    ).json()
    with env.w.ctx.sessions() as db:
        for bid in (failing["id"], stopped["id"]):
            row = db.get(ImportBatch, uuid.UUID(bid))
            assert row is not None
            row.status, row.finished_at = (
                "stopped" if bid == stopped["id"] else "done",
                clock.utcnow(),
            )
        db.commit()
    env.w.advance(days=60)
    recent = finished_batch(
        env, Person(env.w, "alice@example.com"), folder, "recent", {"3.txt": b"3"}
    )
    env.w.advance(days=31)  # `old` is 91 days past completion, `recent` 31
    assert env.w.settings.import_entry_retention_days == 90

    with env.w.ctx.sessions() as db:
        assert imports.prune_import_reports(db, env.w.settings) == 1
        assert imports.prune_import_reports(db, env.w.settings) == 0  # idempotent
        counts = {
            bid: len(
                list(db.scalars(select(ImportEntry).where(ImportEntry.batch_id == uuid.UUID(bid))))
            )
            for bid in (old, recent, failing["id"], stopped["id"])
        }
        assert counts[old] == 0 and counts[recent] > 0
        assert counts[failing["id"]] > 0 and counts[stopped["id"]] > 0  # never pruned
        rows = {r.id: r for r in db.scalars(select(ImportBatch))}
        assert uuid.UUID(old) in rows  # the summary row stays
        assert rows[uuid.UUID(old)].entries_pruned_at is not None
        assert rows[uuid.UUID(recent)].entries_pruned_at is None
    assert any(r.action == "import.reports_pruned" for r in env.w.db_rows(AuthAudit))

    a = Person(env.w, "alice@example.com")
    got = a.get(f"/imports/{old}").json()
    assert got["entries_pruned"] is True and got["status"] == "done" and got["plan"]["files"] == 2
    assert entries(a, old) == []


def test_pruning_runs_with_the_periodic_work_and_the_period_is_a_setting(make_world: Any) -> None:
    env = Env(make_world(import_entry_retention_days=1))
    a = env.alice
    root = a.personal_root()
    a.post(f"/nodes/{root}/folders", json={"name": "F"})
    folder = children(a, root)["F"]["id"]
    bid = finished_batch(env, a, folder, "b", {"1.txt": b"1"})
    env.w.advance(days=2)
    service = ReconcilerService(env.w.ctx.sessions, env.w.settings)
    service._prune_reports()
    with env.w.ctx.sessions() as db:
        assert not list(
            db.scalars(select(ImportEntry).where(ImportEntry.batch_id == uuid.UUID(bid)))
        )
        assert db.get(ImportBatch, uuid.UUID(bid)) is not None


def test_unfinished_batches_are_listed_for_their_own_actor_only(env: Env) -> None:
    a, b = env.alice, env.bob
    root = a.personal_root()
    a.post(f"/nodes/{root}/folders", json={"name": "F"})
    folder = children(a, root)["F"]["id"]
    open_upload = a.post(
        f"/nodes/{folder}/folder-uploads", json={"entries": manifest({"x.txt": b"x"})}
    ).json()
    done = upload_folder(a, folder, {"y.txt": b"y"})
    setup_root = b.personal_root()
    planned = plan_folder(b, setup_root, {"z.txt": b"z"}).json()

    mine = a.get("/imports")
    assert mine.status_code == 200
    assert [x["id"] for x in mine.json()] == [open_upload["id"]]
    assert done["id"] not in [x["id"] for x in mine.json()]
    assert [x["id"] for x in b.get("/imports").json()] == [planned["id"]]
    assert env.carol.get("/imports").json() == []
    anon = env.w.browser().get("/api/v1/imports")
    assert anon.status_code == 401
    # the listed batch can be resumed with the folder selected again; the server skips what is done
    resumed = a.post(
        f"/imports/{open_upload['id']}/resume", json={"entries": manifest({"x.txt": b"x"})}
    )
    assert [p["path"] for p in resumed.json()["pending"]] == ["x.txt"]
