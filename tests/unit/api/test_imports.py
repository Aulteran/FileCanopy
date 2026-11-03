"""M06b setup import and folder upload (ADR-011): the setup window and its closure, the plan and
its pre-checks, folder and zip imports, zip-slip and zip-bomb fixtures, interrupted-import resume,
keep-both and merge, and authorization (administrator for the shared tree, owner for a personal
folder, no administrator bypass into a personal folder).

In-memory SQLite plus a per-test temp library on a Linux container, so files, inodes and
`O_NOFOLLOW` are real. All content is generated; no real documents.
"""

import io
import os
import struct
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from test_auth import World, onboard, signed_in

from app.db.models import (
    AuthAudit,
    FileEvent,
    ImportBatch,
    ImportEntry,
    Node,
    StorageRoot,
    User,
)
from app.domain import imports
from app.storage.reconcile import Reconciler
from app.storage.ziparchive import ZipRefused, declared_directory, inspect_zip


@pytest.fixture
def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., World]:
    def factory(**overrides: Any) -> World:
        values = {"import_free_space_reserve_bytes": 0, **overrides}
        return World(tmp_path, monkeypatch, **values)

    return factory


class Person:
    """One signed-in persona with a few request helpers."""

    def __init__(self, w: World, email: str) -> None:
        self.w, self.email = w, email
        self.client, self.headers = signed_in(w, email)

    def req(self, method: str, path: str, **kw: Any) -> Any:
        return self.client.request(method, f"/api/v1{path}", headers=self.headers, **kw)

    def get(self, path: str, **kw: Any) -> Any:
        return self.req("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self.req("POST", path, **kw)

    def uid(self) -> uuid.UUID:
        with self.w.ctx.sessions() as db:
            return db.scalars(select(User.id).where(User.email == self.email)).one()

    def personal_root(self) -> str:
        roots = self.get("/roots").json()
        return str(next(r["node_id"] for r in roots if r["kind"] == "personal"))

    def shared_root(self) -> str:
        roots = self.get("/roots").json()
        return str(next(r["node_id"] for r in roots if r["kind"] == "shared"))

    def personal_disk(self) -> Path:
        return self.w.settings.library_path / "personal" / str(self.uid())


class Env:
    def __init__(self, w: World) -> None:
        self.w = w
        w.create_admin()
        admin = signed_in(w)
        for email in ("alice@example.com", "bob@example.com", "carol@example.com"):
            onboard(w, admin, email)
        onboard(w, admin, "admin2@example.com", "admin")
        self.admin = Person(w, "admin@example.com")
        self.admin2 = Person(w, "admin2@example.com")
        self.alice = Person(w, "alice@example.com")
        self.bob = Person(w, "bob@example.com")
        self.carol = Person(w, "carol@example.com")
        self.shared_disk = w.settings.library_path / "shared"

    def audit_actions(self) -> list[str]:
        return [r.action for r in self.w.db_rows(AuthAudit)]


@pytest.fixture
def env(make_world: Callable[..., World]) -> Env:
    return Env(make_world())


def listing(directory: Path) -> list[str]:
    """Names in a directory; a personal folder only exists on disk once something is written."""
    return sorted(p.name for p in directory.iterdir()) if directory.exists() else []


def code(r: Any) -> str:
    return str(r.json()["detail"]["code"])


def manifest(
    files: dict[str, bytes], dirs: tuple[str, ...] = (), mtime: int | None = 1_700_000_000_000
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"path": d, "kind": "dir"} for d in dirs]
    out += [
        {"path": p, "kind": "file", "size": len(b), **({"mtime": mtime} if mtime else {})}
        for p, b in files.items()
    ]
    return out


def plan_folder(p: Person, root: str, files: dict[str, bytes], **kw: Any) -> Any:
    body = {"entries": manifest(files, kw.pop("dirs", ())), **kw}
    return p.post(f"/nodes/{root}/setup/import", json=body)


def entries(p: Person, batch: str, **params: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    after = -1
    while True:
        r = p.get(f"/imports/{batch}/entries", params={"after": after, "limit": 500, **params})
        assert r.status_code == 200, r.text
        out += r.json()["items"]
        if r.json()["next_after"] is None:
            return out
        after = r.json()["next_after"]


def put(p: Person, batch: str, index: int, data: bytes) -> Any:
    return p.req("PUT", f"/imports/{batch}/entries/{index}", content=data)


def send(p: Person, batch: str, contents: dict[str, bytes], only: set[str] | None = None) -> None:
    for e in entries(p, batch, status="pending"):
        if e["kind"] == "file" and (only is None or e["path"] in only):
            r = put(p, batch, e["index"], contents[e["path"]])
            assert r.status_code == 200, r.text


def state(p: Person, root: str) -> dict[str, Any]:
    r = p.get(f"/nodes/{root}/setup")
    assert r.status_code == 200, r.text
    return dict(r.json())


def children(p: Person, node: str) -> dict[str, dict[str, Any]]:
    r = p.get(f"/nodes/{node}/children")
    assert r.status_code == 200, r.text
    return {c["name"]: c for c in r.json()["items"]}


def wait_zip(env: Env, p: Person, batch: str, final: tuple[str, ...] = ("done",)) -> dict[str, Any]:
    runner: imports.ImportRunner = env.w.app.state.imports
    assert runner.wait(uuid.UUID(batch), 60)
    got = p.get(f"/imports/{batch}").json()
    assert got["status"] in final, got
    return dict(got)


def make_zip(items: dict[str, bytes | None], method: int = zipfile.ZIP_STORED) -> bytes:
    """None marks a directory entry."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as zf:
        for name, data in items.items():
            zf.writestr(name, data if data is not None else b"")
    return buf.getvalue()


def plan_zip(p: Person, root: str, data: bytes, **params: Any) -> Any:
    return p.post(f"/nodes/{root}/setup/import-zip", content=data, params=params)


# ------------------------------------------------------------------ the setup window


def test_setup_window_is_open_for_new_roots_and_visible_only_to_who_may_use_it(env: Env) -> None:
    alice_root, shared = env.alice.personal_root(), env.alice.shared_root()
    assert state(env.alice, alice_root)["state"] == "open"
    roots = {r["kind"]: r for r in env.alice.get("/roots").json()}
    assert roots["personal"]["setup_open"] is True
    assert roots["shared"]["setup_open"] is False  # a member cannot set up the shared tree
    admin_roots = {r["kind"]: r for r in env.admin.get("/roots").json()}
    assert admin_roots["shared"]["setup_open"] is True
    assert admin_roots["personal"]["setup_open"] is True  # the administrator's own folder

    info = env.alice.get(f"/nodes/{alice_root}").json()
    assert info["can_manage"] is True and info["setup_state"] == "open"
    member_view = env.alice.get(f"/nodes/{shared}").json()
    assert member_view["can_manage"] is False and member_view["setup_state"] is None
    assert member_view["scan_state"] is None


def test_folder_import_copies_files_and_nothing_is_written_before_start(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    files = {
        "Docs/report.txt": os.urandom(5000),
        "Docs/deep/er/notes.txt": b"notes",
        "photo.bin": os.urandom(70_000),
    }
    r = plan_folder(a, root, files, dirs=("Docs", "Empty/Inside"))
    assert r.status_code == 201, r.text
    batch = r.json()
    assert batch["status"] == "planned" and batch["kind"] == "setup_folder"
    plan = batch["plan"]
    assert plan["files"] == 3 and plan["bytes"] == sum(map(len, files.values()))
    assert plan["folders"] == 5  # Docs, Docs/deep, Docs/deep/er, Empty, Empty/Inside
    assert plan["fits"] is True and plan["skipped"] == {}
    assert listing(a.personal_disk()) == []  # nothing is written before the plan is started

    first_file = next(e for e in entries(a, batch["id"]) if e["kind"] == "file")["index"]
    assert put(a, batch["id"], first_file, b"early").status_code == 409  # not running yet
    started = a.post(f"/imports/{batch['id']}/start").json()
    assert started["status"] == "running"
    send(a, batch["id"], files)

    done = a.get(f"/imports/{batch['id']}").json()
    assert done["status"] == "done"
    assert done["counts"]["done"] == 3 and done["counts"]["failed"] == 0
    assert done["counts"]["bytes_done"] == plan["bytes"]
    disk = a.personal_disk()
    for path, data in files.items():
        assert (disk / path).read_bytes() == data
        assert abs((disk / path).stat().st_mtime - 1_700_000_000) < 2  # the mtime is kept
    assert (disk / "Empty" / "Inside").is_dir()  # empty folders are created at the end

    tops = children(a, root)
    assert set(tops) == {"Docs", "Empty", "photo.bin"}
    with env.w.ctx.sessions() as db:
        photo = db.get(Node, uuid.UUID(tops["photo.bin"]["id"]))
        assert photo is not None and photo.content_hash is not None and photo.fs_ino is not None
        actions = {(e.action, e.source, e.actor_kind) for e in db.scalars(select(FileEvent))}
        assert ("imported", "import", "user") in actions
    # the import's own content did not close the window
    assert state(a, root)["state"] == "open"
    assert "import.finished" in env.audit_actions()


def test_finish_and_skip_close_the_window_for_good(env: Env) -> None:
    a, b = env.alice, env.bob
    a_root, b_root = a.personal_root(), b.personal_root()
    assert a.post(f"/nodes/{a_root}/setup/finish", json={"action": "finish"}).status_code == 200
    assert state(a, a_root)["state"] == "closed"
    assert plan_folder(a, a_root, {"x.txt": b"x"}).status_code == 409
    assert code(plan_folder(a, a_root, {"x.txt": b"x"})) == "SETUP_CLOSED"
    # closed is final and idempotent
    assert a.post(f"/nodes/{a_root}/setup/finish", json={}).status_code == 200
    assert state(a, a_root)["state"] == "closed"
    roots = {r["kind"]: r for r in a.get("/roots").json()}
    assert roots["personal"]["setup_open"] is False

    assert b.post(f"/nodes/{b_root}/setup/finish", json={"action": "skip"}).status_code == 200
    rows = [r for r in env.w.db_rows(AuthAudit) if r.action == "storage.setup_closed"]
    assert {r.details["reason"] for r in rows} == {"finish", "skip"}
    # emptying the root later does not reopen it
    assert state(b, b_root)["state"] == "closed"


@pytest.mark.parametrize("how", ["upload", "folder", "scan"])
def test_content_created_any_other_way_closes_the_window(env: Env, how: str) -> None:
    a = env.alice
    root = a.personal_root()
    if how == "upload":
        r = a.post(f"/nodes/{root}/files", params={"name": "a.txt"}, content=b"hi")
        assert r.status_code == 201
    elif how == "folder":
        assert a.post(f"/nodes/{root}/folders", json={"name": "F"}).status_code == 201
    else:
        a.personal_disk().mkdir(parents=True, exist_ok=True)
        (a.personal_disk() / "from-host.txt").write_bytes(b"outside")
        result = Reconciler(env.w.ctx.sessions, env.w.settings).run_root(
            _root_id(env, root), "manual"
        )
        assert result.outcome == "ok"
    assert state(a, root)["state"] == "closed"
    reasons = [
        r.details["reason"] for r in env.w.db_rows(AuthAudit) if r.action == "storage.setup_closed"
    ]
    assert reasons == ["content"]


def _root_id(env: Env, node_id: str) -> uuid.UUID:
    with env.w.ctx.sessions() as db:
        node = db.get(Node, uuid.UUID(node_id))
        assert node is not None
        return node.root_id


def test_copy_into_an_open_root_closes_it(env: Env) -> None:
    admin = env.admin
    shared = admin.shared_root()
    mine = admin.personal_root()
    admin.post(f"/nodes/{mine}/folders", json={"name": "Src"})  # closes the personal window
    src = children(admin, mine)["Src"]["id"]
    assert state(admin, shared)["state"] == "open"
    r = admin.post(f"/nodes/{src}/copy", json={"target_parent_id": shared})
    assert r.status_code == 201, r.text
    assert state(admin, shared)["state"] == "closed"


def test_window_closing_mid_import_stops_the_batch_and_nothing_else_is_written(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    files = {f"f{i}.txt": f"data {i}".encode() for i in range(4)}
    batch = plan_folder(a, root, files).json()
    a.post(f"/imports/{batch['id']}/start")
    send(a, batch["id"], files, only={"f0.txt", "f1.txt"})
    # the owner uses the app in another tab: the window closes
    assert a.post(f"/nodes/{root}/folders", json={"name": "Other"}).status_code == 201
    first_pending = entries(a, batch["id"], status="pending")[0]["index"]
    r = put(a, batch["id"], first_pending, b"data 2")
    assert r.status_code == 409 and code(r) == "SETUP_CLOSED"
    got = a.get(f"/imports/{batch['id']}").json()
    assert got["status"] == "stopped" and got["error_code"] == "SETUP_CLOSED"
    assert got["counts"]["done"] == 2 and got["counts"]["failed"] == 2
    assert not (a.personal_disk() / "f2.txt").exists()


# ------------------------------------------------------------------ the plan


def test_plan_lists_skipped_unsupported_and_colliding_entries(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    deep = "/".join(f"d{i}" for i in range(40)) + "/file.txt"
    files = {
        "ok/one.txt": b"1",
        "../escape.txt": b"x",
        "a/../../escape2.txt": b"x",
        "/absolute.txt": b"x",
        "C:/drive.txt": b"x",
        "back\\slash.txt": b"x",
        "ctrl\x01char.txt": b"x",
        ".filecanopy-tmp-abc": b"x",
        ".DS_Store": b"x",
        "__MACOSX/ok/._one.txt": b"x",
        "sub/Thumbs.db": b"x",
        deep: b"x",
        "Case/a.txt": b"1",
        "case/A.TXT": b"2",
        "dup.txt": b"1",
    }
    body = {"entries": manifest(files) + [{"path": "dup.txt", "kind": "file", "size": 1}]}
    r = a.post(f"/nodes/{root}/setup/import", json=body)
    assert r.status_code == 201, r.text
    plan = r.json()["plan"]
    assert plan["skipped"]["INVALID_PATH"] == 4  # ../, a/../.., /abs, C:/
    assert plan["skipped"]["INVALID_NAME"] == 3  # backslash, control character, staging prefix
    assert plan["skipped"]["SYSTEM_FILE"] == 3
    assert plan["skipped"]["TOO_DEEP"] == 1
    assert {e["reason"] for e in plan["skipped_examples"]} >= {"INVALID_PATH", "TOO_DEEP"}
    assert plan["collisions"]["count"] == 2  # a.txt and A.TXT, dup.txt twice
    assert plan["merged_folders"] == 0  # "Case" and "case" are implicit parents, merged silently
    assert plan["files"] == 5  # ok/one, Case/a, case/A, dup.txt twice
    assert plan["fits"] is True
    assert listing(a.personal_disk()) == []

    # the system-file toggle includes them
    a.post(f"/imports/{r.json()['id']}/cancel")
    r2 = a.post(
        f"/nodes/{root}/setup/import",
        json={"entries": manifest({".DS_Store": b"x"}), "include_system": True},
    )
    assert r2.json()["plan"]["files"] == 1 and r2.json()["plan"]["skipped"] == {}


def test_plan_counts_in_import_collisions_and_keep_both_on_write(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    files = {"Docs/a.txt": b"first", "docs/A.txt": b"second", "docs/b.txt": b"third"}
    batch = plan_folder(a, root, files).json()
    a.post(f"/imports/{batch['id']}/start")
    send(a, batch["id"], files)
    disk = a.personal_disk()
    assert (disk / "Docs" / "a.txt").read_bytes() == b"first"
    assert (disk / "Docs" / "A (2).txt").read_bytes() == b"second"  # kept both, nothing replaced
    assert (disk / "Docs" / "b.txt").read_bytes() == b"third"
    assert not (disk / "docs").exists()  # the two folders merged into the first
    done = entries(a, batch["id"], status="renamed")
    assert [e["result_name"] for e in done] == ["A (2).txt"]


def test_free_space_check_blocks_start_and_folder_upload(
    make_world: Callable[..., World],
) -> None:
    env = Env(make_world(import_free_space_reserve_bytes=1 << 60))
    a = env.alice
    root = a.personal_root()
    batch = plan_folder(a, root, {"x.txt": b"x"}).json()
    assert batch["plan"]["fits"] is False
    r = a.post(f"/imports/{batch['id']}/start")
    assert r.status_code == 507 and code(r) == "INSUFFICIENT_SPACE"
    assert listing(a.personal_disk()) == []

    a.post(f"/imports/{batch['id']}/cancel")
    a.post(f"/nodes/{root}/setup/finish", json={})
    r = a.post(
        f"/nodes/{root}/folder-uploads",
        json={"entries": manifest({"x.txt": b"x"})},
    )
    assert r.status_code == 507 and code(r) == "INSUFFICIENT_SPACE"
    assert not (a.personal_disk() / "x.txt").exists()


def test_entry_limit_is_enforced_for_manifests(make_world: Callable[..., World]) -> None:
    env = Env(make_world(max_import_entries=3))
    a = env.alice
    r = plan_folder(a, a.personal_root(), {f"{i}.txt": b"x" for i in range(4)})
    assert r.status_code == 413 and code(r) == "TOO_MANY_ENTRIES"


def test_size_mismatch_fails_the_entry_and_resume_retries_it(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    files = {"a.txt": b"abcdef"}
    batch = plan_folder(a, root, files).json()
    a.post(f"/imports/{batch['id']}/start")
    r = put(a, batch["id"], 0, b"abc")  # the file changed after the manifest was made
    assert r.status_code == 409 and code(r) == "SIZE_MISMATCH"
    assert not (a.personal_disk() / "a.txt").exists()
    assert listing(a.personal_disk()) == []  # the temporary file is gone
    assert entries(a, batch["id"], status="failed")[0]["reason"] == "SIZE_MISMATCH"
    chunks = (b"abcd", b"efghij")  # a body that grows past the manifest while it is read
    assert put(a, batch["id"], 0, chunks[0] + chunks[1]).status_code == 409
    resume = a.post(f"/imports/{batch['id']}/resume", json={"entries": manifest(files)}).json()
    assert [p["index"] for p in resume["pending"]] == [0]
    assert put(a, batch["id"], 0, b"abcdef").status_code == 200
    assert a.get(f"/imports/{batch['id']}").json()["status"] == "done"


# ------------------------------------------------------------------ folder upload


def test_folder_upload_merges_folders_and_keeps_both_on_file_conflicts(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    a.post(f"/nodes/{root}/folders", json={"name": "Docs"})  # also closes the setup window
    docs = children(a, root)["Docs"]["id"]
    a.post(f"/nodes/{docs}/files", params={"name": "a.txt"}, content=b"original")
    disk = a.personal_disk()

    files = {"Docs/a.txt": b"uploaded", "Docs/new/b.txt": b"b", "Docs/DOCS-sub/c.txt": b"c"}
    r = a.post(f"/nodes/{root}/folder-uploads", json={"entries": manifest(files)})
    assert r.status_code == 201, r.text
    batch = r.json()
    assert batch["status"] == "running" and batch["kind"] == "folder_upload"
    send(a, batch["id"], files)
    assert a.get(f"/imports/{batch['id']}").json()["status"] == "done"
    assert (disk / "Docs" / "a.txt").read_bytes() == b"original"  # never overwritten
    assert (disk / "Docs" / "a (2).txt").read_bytes() == b"uploaded"
    assert (disk / "Docs" / "new" / "b.txt").read_bytes() == b"b"
    assert sorted(p.name for p in (disk / "Docs").iterdir()) == [
        "DOCS-sub",
        "a (2).txt",
        "a.txt",
        "new",
    ]
    # uploading the same folder again merges the folders and keeps both once more
    again = a.post(f"/nodes/{root}/folder-uploads", json={"entries": manifest(files)}).json()
    send(a, again["id"], files)
    assert (disk / "Docs" / "a (3).txt").read_bytes() == b"uploaded"
    assert (disk / "Docs" / "new" / "b (2).txt").read_bytes() == b"b"
    assert sorted(p.name for p in disk.iterdir()) == ["Docs"]  # no duplicate folders


def test_folder_upload_of_empty_folders_and_idempotent_resend(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    a.post(f"/nodes/{root}/folders", json={"name": "x"})
    body = {"entries": [{"path": "Only/Empty", "kind": "dir"}]}
    batch = a.post(f"/nodes/{root}/folder-uploads", json=body).json()
    assert batch["status"] == "done"  # no files to wait for
    assert (a.personal_disk() / "Only" / "Empty").is_dir()

    files = {"one.txt": b"1"}
    batch = a.post(f"/nodes/{root}/folder-uploads", json={"entries": manifest(files)}).json()
    first = put(a, batch["id"], 0, b"1")
    again = put(a, batch["id"], 0, b"1")  # a retry after a lost response
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    assert sorted(p.name for p in a.personal_disk().iterdir()) == ["Only", "one.txt", "x"]


def test_folder_upload_never_enters_unsupported_or_missing_names(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    a.post(f"/nodes/{root}/folders", json={"name": "x"})
    outside = env.w.settings.library_path / "outside-secret"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"secret")
    (a.personal_disk() / "link").symlink_to(outside)  # a symlink the host admin placed
    Reconciler(env.w.ctx.sessions, env.w.settings).run_root(_root_id(env, root), "manual")
    link = children(a, root)["link"]
    assert link["unsupported_reason"] == "symlink"
    files = {"link/pwned.txt": b"nope"}
    batch = a.post(f"/nodes/{root}/folder-uploads", json={"entries": manifest(files)}).json()
    # the name "link" is taken by an unsupported node, so the folder gets a keep-both name
    send(a, batch["id"], files)
    assert not (outside / "pwned.txt").exists()
    assert (a.personal_disk() / "link (2)" / "pwned.txt").read_bytes() == b"nope"


def test_folder_upload_needs_write_on_the_target(env: Env) -> None:
    a, b, c = env.alice, env.bob, env.carol
    root = a.personal_root()
    a.post(f"/nodes/{root}/folders", json={"name": "Shared"})
    folder = children(a, root)["Shared"]["id"]
    body = {"entries": manifest({"f.txt": b"x"})}
    assert b.post(f"/nodes/{folder}/folder-uploads", json=body).status_code == 403
    uid = lambda p: str(p.uid())  # noqa: E731
    assert (
        a.req("PUT", f"/nodes/{folder}/grants/{uid(b)}", json={"level": "viewer"}).status_code
        == 200
    )
    assert b.post(f"/nodes/{folder}/folder-uploads", json=body).status_code == 403  # viewer
    a.req("PUT", f"/nodes/{folder}/grants/{uid(c)}", json={"level": "editor"})
    r = c.post(f"/nodes/{folder}/folder-uploads", json=body)
    assert r.status_code == 201, r.text
    send(c, r.json()["id"], {"f.txt": b"x"})
    assert (a.personal_disk() / "Shared" / "f.txt").read_bytes() == b"x"

    # revoking the grant mid-batch: the next entry is refused, the batch stops, nothing more lands
    files = {"g1.txt": b"1", "g2.txt": b"2"}
    batch = c.post(f"/nodes/{folder}/folder-uploads", json={"entries": manifest(files)}).json()
    assert put(c, batch["id"], 0, b"1").status_code == 200
    a.req("DELETE", f"/nodes/{folder}/grants/{uid(c)}")
    denied = put(c, batch["id"], 1, b"2")
    assert denied.status_code == 403 and code(denied) == "FORBIDDEN"
    got = entries(c, batch["id"])
    assert [e["status"] for e in got if e["kind"] == "file"] == ["done", "failed"]
    assert (a.personal_disk() / "Shared" / "g1.txt").exists()
    assert not (a.personal_disk() / "Shared" / "g2.txt").exists()


# ------------------------------------------------------------------ authorization


def test_only_the_owner_may_set_up_a_personal_folder_and_an_admin_has_no_bypass(env: Env) -> None:
    a_root = env.alice.personal_root()
    body = {"entries": manifest({"x.txt": b"x"})}
    for who in (env.bob, env.admin, env.admin2):
        assert who.post(f"/nodes/{a_root}/setup/import", json=body).status_code == 403
        assert who.get(f"/nodes/{a_root}/setup").status_code == 403
        assert who.post(f"/nodes/{a_root}/setup/finish", json={}).status_code == 403
        assert plan_zip(who, a_root, make_zip({"x.txt": b"x"})).status_code == 403
    assert not (env.alice.personal_disk() / "x.txt").exists()
    assert state(env.alice, a_root)["state"] == "open"  # the denials changed nothing
    denied = [r for r in env.w.db_rows(AuthAudit) if r.action == "access.denied"]
    assert len(denied) >= 4


def test_shared_tree_setup_needs_the_administrator_role(env: Env) -> None:
    shared = env.alice.shared_root()
    body = {"entries": manifest({"x.txt": b"x"})}
    assert env.alice.post(f"/nodes/{shared}/setup/import", json=body).status_code == 403
    assert env.alice.get(f"/nodes/{shared}/setup").status_code == 403
    assert plan_zip(env.alice, shared, make_zip({"x.txt": b"x"})).status_code == 403
    r = env.admin.post(f"/nodes/{shared}/setup/import", json=body)
    assert r.status_code == 201, r.text
    env.admin.post(f"/imports/{r.json()['id']}/start")
    send(env.admin, r.json()["id"], {"x.txt": b"x"})
    assert (env.shared_disk / "x.txt").read_bytes() == b"x"
    # a member with the default editor grant can read it; the administrator needs the same grant
    assert "x.txt" in children(env.alice, shared)


def test_demoting_the_administrator_stops_a_shared_import_at_the_next_entry(env: Env) -> None:
    shared = env.admin2.shared_root()
    files = {"s1.txt": b"1", "s2.txt": b"2"}
    batch = plan_folder(env.admin2, shared, files).json()
    env.admin2.post(f"/imports/{batch['id']}/start")
    assert put(env.admin2, batch["id"], 0, b"1").status_code == 200
    r = env.admin.req("PATCH", f"/admin/users/{env.admin2.uid()}", json={"role": "member"})
    assert r.status_code == 200, r.text
    denied = put(env.admin2, batch["id"], 1, b"2")
    assert denied.status_code == 403
    assert not (env.shared_disk / "s2.txt").exists()
    assert env.admin2.get(f"/imports/{batch['id']}").json()["status"] == "stopped"


def test_batches_and_reports_are_visible_to_their_actor_only(env: Env) -> None:
    a_root = env.alice.personal_root()
    batch = plan_folder(env.alice, a_root, {"x.txt": b"x"}).json()
    bid = batch["id"]
    for who in (env.bob, env.admin):
        assert who.get(f"/imports/{bid}").status_code == 403
        assert who.get(f"/imports/{bid}/entries").status_code == 403
        assert who.post(f"/imports/{bid}/start").status_code == 403
        assert who.post(f"/imports/{bid}/cancel").status_code == 403
        assert who.post(f"/imports/{bid}/resume", json={"entries": []}).status_code == 403
        assert put(who, bid, 0, b"x").status_code == 403
    assert env.alice.get(f"/imports/{bid}").status_code == 200
    unknown = uuid.uuid4()
    assert env.alice.get(f"/imports/{unknown}").status_code == 403  # same as someone else's
    assert env.alice.post(f"/imports/{bid}/start").json()["status"] == "running"
    assert env.alice.post(f"/imports/{bid}/start").status_code == 409  # not startable twice


def test_only_one_unfinished_setup_import_per_root(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    first = plan_folder(a, root, {"x.txt": b"x"}).json()
    again = plan_folder(a, root, {"y.txt": b"y"})
    assert again.status_code == 409 and code(again) == "IMPORT_IN_PROGRESS"
    shown = state(a, root)["batch"]
    assert shown["id"] == first["id"] and shown["status"] == "planned"
    assert a.post(f"/imports/{first['id']}/cancel").json()["status"] == "cancelled"
    assert state(a, root)["batch"] is None
    assert plan_folder(a, root, {"y.txt": b"y"}).status_code == 201
    # finishing setup cancels a plan that never started, and refuses while one is running
    assert a.post(f"/nodes/{root}/setup/finish", json={}).status_code == 200
    batch = env.bob.post(
        f"/nodes/{env.bob.personal_root()}/setup/import", json={"entries": manifest({"z": b"z"})}
    ).json()
    env.bob.post(f"/imports/{batch['id']}/start")
    refused = env.bob.post(f"/nodes/{env.bob.personal_root()}/setup/finish", json={})
    assert refused.status_code == 409 and code(refused) == "IMPORT_IN_PROGRESS"


# ------------------------------------------------------------------ zip: slip, bombs, limits


def test_zip_import_extracts_in_the_background_and_lists_before_writing(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    files: dict[str, bytes | None] = {
        "Photos/": None,
        "Photos/a.jpg": os.urandom(3000),
        "Photos/2026/b.jpg": os.urandom(2000),
        "readme.txt": b"hello",
        "Empty/": None,
        "__MACOSX/._readme.txt": b"junk",
    }
    r = plan_zip(a, root, make_zip(files, zipfile.ZIP_DEFLATED))
    assert r.status_code == 201, r.text
    batch = r.json()
    assert batch["kind"] == "setup_zip" and batch["status"] == "planned"
    assert batch["plan"]["files"] == 3 and batch["plan"]["skipped"] == {"SYSTEM_FILE": 1}
    assert listing(a.personal_disk()) == []  # only listed so far
    staged = list((env.w.settings.app_data_path / "staging" / "imports").iterdir())
    assert len(staged) == 1  # kept in the app's own area, never in the library
    assert not any(p.suffix == ".zip" for p in env.w.settings.library_path.rglob("*"))

    a.post(f"/imports/{batch['id']}/start")
    done = wait_zip(env, a, batch["id"])
    assert done["counts"]["done"] == 3
    disk = a.personal_disk()
    assert (disk / "Photos" / "a.jpg").read_bytes() == files["Photos/a.jpg"]
    assert (disk / "Photos" / "2026" / "b.jpg").read_bytes() == files["Photos/2026/b.jpg"]
    assert (disk / "Empty").is_dir() and not (disk / "__MACOSX").exists()
    assert not any(staged_path.exists() for staged_path in staged)  # deleted when the batch ends
    assert state(a, root)["state"] == "open"


def test_zip_slip_entries_are_refused_and_nothing_lands_outside(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    outside = env.w.settings.library_path
    items: dict[str, bytes | None] = {
        "../evil1.txt": b"evil",
        "a/../../evil2.txt": b"evil",
        "..\\evil3.txt": b"evil",
        "/abs/evil4.txt": b"evil",
        "\\abs\\evil5.txt": b"evil",
        "C:\\evil6.txt": b"evil",
        "c:/evil7.txt": b"evil",
        "ok/../../../evil8.txt": b"evil",
        "dir\\nested.txt": b"backslash is a separator in a zip",
        "fine.txt": b"fine",
    }
    batch = plan_zip(a, root, make_zip(items)).json()
    assert batch["plan"]["skipped"] == {"INVALID_PATH": 8}
    assert batch["plan"]["files"] == 2
    a.post(f"/imports/{batch['id']}/start")
    wait_zip(env, a, batch["id"])
    for name in ("evil1", "evil2", "evil3", "evil4", "evil5", "evil6", "evil7", "evil8"):
        assert not list(outside.rglob(f"*{name}*")), name
        assert not list(env.w.settings.app_data_path.rglob(f"*{name}*")), name
    disk = a.personal_disk()
    assert (disk / "fine.txt").read_bytes() == b"fine"
    assert (disk / "dir" / "nested.txt").exists()
    written = sorted(p.name for p in env.w.settings.library_path.rglob("*") if p.is_file())
    assert written == ["fine.txt", "nested.txt"]


def test_zip_symlink_encrypted_and_special_entries_are_skipped(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        link = zipfile.ZipInfo("link-to-etc")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        zf.writestr(link, "/etc/passwd")
        fifo = zipfile.ZipInfo("a-fifo")
        fifo.create_system = 3
        fifo.external_attr = 0o010644 << 16
        zf.writestr(fifo, b"")
        zf.writestr("secret.txt", b"encrypted body")
        zf.writestr("plain.txt", b"plain")
    data = bytearray(buf.getvalue())
    # mark "secret.txt" as encrypted in the central directory (general purpose bit 0)
    at = data.index(b"secret.txt", data.index(b"PK\x01\x02"))
    header = data.rfind(b"PK\x01\x02", 0, at)
    flags = struct.unpack_from("<H", data, header + 8)[0]
    struct.pack_into("<H", data, header + 8, flags | 1)
    batch = plan_zip(a, root, bytes(data)).json()
    assert batch["plan"]["skipped"] == {"SYMLINK": 1, "UNSUPPORTED_TYPE": 1, "ENCRYPTED": 1}
    a.post(f"/imports/{batch['id']}/start")
    wait_zip(env, a, batch["id"])
    assert sorted(p.name for p in a.personal_disk().iterdir()) == ["plain.txt"]


def test_zip_bomb_entry_is_refused_by_ratio_and_the_rest_continues(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("before.txt", b"before")
        zf.writestr(zipfile.ZipInfo("bomb.bin"), b"\0" * (24 * 1024 * 1024), zipfile.ZIP_BZIP2)
        zf.writestr("after.txt", b"after")
    r = plan_zip(a, root, buf.getvalue())
    assert r.status_code == 201, r.text
    assert r.json()["plan"]["bytes"] == 24 * 1024 * 1024 + 11  # the header size is not trusted
    a.post(f"/imports/{r.json()['id']}/start")
    done = wait_zip(env, a, r.json()["id"])
    disk = a.personal_disk()
    assert sorted(p.name for p in disk.iterdir()) == ["after.txt", "before.txt"]
    failed = entries(a, r.json()["id"], status="failed")
    assert [(e["path"], e["reason"]) for e in failed] == [("bomb.bin", "DECOMPRESSION_RATIO")]
    assert done["counts"]["failed"] == 1 and done["counts"]["done"] == 2
    assert not list(disk.glob(".filecanopy-tmp-*"))  # the partial file was removed


def test_zip_total_decompressed_limit_stops_the_batch_and_keeps_what_was_committed(
    env: Env,
) -> None:
    a = env.alice
    root = a.personal_root()
    one_mib = 1024 * 1024
    items: dict[str, bytes | None] = {f"f{i}.bin": os.urandom(one_mib) for i in range(3)}
    batch = plan_zip(a, root, make_zip(items)).json()
    # the limit is counted while streaming, so lowering it after the plan still holds
    env.w.settings.max_zip_uncompressed_bytes = int(2.5 * one_mib)
    a.post(f"/imports/{batch['id']}/start")
    got = wait_zip(env, a, batch["id"], final=("failed",))
    assert got["error_code"] == "ZIP_TOO_LARGE"
    assert sorted(p.name for p in a.personal_disk().iterdir()) == ["f0.bin", "f1.bin"]
    assert got["counts"]["done"] == 2 and got["counts"]["failed"] == 1


def test_zip_plan_limits_and_invalid_archives(make_world: Callable[..., World]) -> None:
    env = Env(make_world(max_import_entries=4, max_zip_uncompressed_bytes=1000, max_zip_bytes=5000))
    a = env.alice
    root = a.personal_root()
    staging = env.w.settings.app_data_path / "staging" / "imports"

    too_many = make_zip({f"f{i}.txt": b"x" for i in range(5)})
    r = plan_zip(a, root, too_many)
    assert r.status_code == 413 and code(r) == "TOO_MANY_ENTRIES"

    declared = make_zip({"big.bin": b"\0" * 2000}, zipfile.ZIP_DEFLATED)
    r = plan_zip(a, root, declared)
    assert r.status_code == 413 and code(r) == "ZIP_TOO_LARGE"

    r = plan_zip(a, root, os.urandom(4000))
    assert r.status_code == 422 and code(r) == "INVALID_ZIP"

    r = plan_zip(a, root, os.urandom(6000))  # larger than the archive limit
    assert r.status_code == 413 and code(r) == "ZIP_TOO_LARGE"
    assert not staging.exists() or list(staging.iterdir()) == []  # failed uploads leave nothing
    assert a.get(f"/nodes/{root}/setup").json()["batch"] is None


def test_zip_central_directory_declarations_are_checked_without_loading_it(tmp_path: Path) -> None:
    good = tmp_path / "good.zip"
    good.write_bytes(make_zip({"a": b"1", "b": b"2"}))
    assert declared_directory(good)[0] == 2
    assert [i.filename for i in inspect_zip(good, max_entries=10)] == ["a", "b"]
    with pytest.raises(ZipRefused) as many:
        inspect_zip(good, max_entries=1)
    assert many.value.code == "TOO_MANY_ENTRIES"
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip" * 100)
    with pytest.raises(ZipRefused) as bad:
        declared_directory(junk)
    assert bad.value.code == "INVALID_ZIP"


@pytest.mark.filterwarnings("ignore:Duplicate name")
def test_zip_duplicate_names_keep_both_and_nothing_is_overwritten(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("same.txt", b"one")
        zf.writestr("Docs/x.txt", b"in docs")
        zf.writestr("same.txt", b"two")  # duplicate path
        zf.writestr("docs/X.txt", b"case twin")
    batch = plan_zip(a, root, buf.getvalue()).json()
    assert batch["plan"]["collisions"]["count"] == 2
    a.post(f"/imports/{batch['id']}/start")
    wait_zip(env, a, batch["id"])
    disk = a.personal_disk()
    assert (disk / "same.txt").read_bytes() == b"one"
    assert (disk / "same (2).txt").read_bytes() == b"two"
    assert (disk / "Docs" / "x.txt").read_bytes() == b"in docs"
    assert (disk / "Docs" / "X (2).txt").read_bytes() == b"case twin"


def test_cancelling_a_planned_zip_removes_the_staged_archive(env: Env) -> None:
    a = env.alice
    batch = plan_zip(a, a.personal_root(), make_zip({"a.txt": b"a"})).json()
    staging = env.w.settings.app_data_path / "staging" / "imports"
    assert len(list(staging.iterdir())) == 1
    a.post(f"/imports/{batch['id']}/cancel")
    assert list(staging.iterdir()) == []
    assert listing(a.personal_disk()) == []


def test_zip_cannot_be_started_by_someone_whose_authority_ended(env: Env) -> None:
    shared = env.admin2.shared_root()
    batch = plan_zip(env.admin2, shared, make_zip({"s.txt": b"s"})).json()
    env.admin.req("PATCH", f"/admin/users/{env.admin2.uid()}", json={"role": "member"})
    assert env.admin2.post(f"/imports/{batch['id']}/start").status_code == 403
    assert not (env.shared_disk / "s.txt").exists()


# ------------------------------------------------------------------ interrupted imports


def test_interrupted_folder_import_resumes_without_duplicates(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    files = {f"Photos/p{i}.bin": os.urandom(500 + i) for i in range(5)}
    files["top.txt"] = b"top"
    batch = plan_folder(a, root, files).json()
    a.post(f"/imports/{batch['id']}/start")
    send(a, batch["id"], files, only={"Photos/p0.bin", "Photos/p1.bin"})
    disk = a.personal_disk()

    # the server died while p2 was being written: a temporary file and a `writing` entry remain
    target = next(e for e in entries(a, batch["id"]) if e["path"] == "Photos/p2.bin")
    tmp = disk / "Photos" / ".filecanopy-tmp-deadbeef"
    tmp.write_bytes(b"partial")
    with env.w.ctx.sessions() as db:
        folder = db.scalars(select(Node).where(Node.name == "Photos")).one()
        row = db.scalars(
            select(ImportEntry).where(
                ImportEntry.batch_id == uuid.UUID(batch["id"]), ImportEntry.idx == target["index"]
            )
        ).one()
        row.status, row.dir_node_id, row.tmp_name = "writing", folder.id, tmp.name
        db.commit()
    assert put(a, batch["id"], target["index"], files["Photos/p2.bin"]).status_code == 409  # busy

    counts = imports.recover_imports(env.w.ctx.sessions, env.w.settings)
    assert counts["requeued"] == 1 and not tmp.exists()
    # the browser was reloaded: the person re-selects the folder (matched by path, size, mtime)
    got = state(a, root)["batch"]
    assert got["id"] == batch["id"] and got["status"] == "running"
    changed = dict(files, **{"top.txt": b"top changed!"})
    resume = a.post(f"/imports/{batch['id']}/resume", json={"entries": manifest(changed)}).json()
    assert sorted(p["path"] for p in resume["pending"]) == [
        "Photos/p2.bin",
        "Photos/p3.bin",
        "Photos/p4.bin",
    ]
    assert resume["unmatched"] == 1  # top.txt changed since the first selection: not sent
    resume = a.post(f"/imports/{batch['id']}/resume", json={"entries": manifest(files)}).json()
    for item in resume["pending"]:
        assert put(a, batch["id"], item["index"], files[item["path"]]).status_code == 200
    assert a.get(f"/imports/{batch['id']}").json()["status"] == "done"
    for path, data in files.items():
        assert (disk / path).read_bytes() == data
    assert sorted(p.name for p in (disk / "Photos").iterdir()) == [f"p{i}.bin" for i in range(5)]


def test_a_file_published_before_the_crash_is_adopted_not_duplicated(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    files = {"kept.bin": os.urandom(900), "later.bin": b"later"}
    batch = plan_folder(a, root, files).json()
    a.post(f"/imports/{batch['id']}/start")
    disk = a.personal_disk()
    # crash after the link() but before the database commit
    disk.mkdir(parents=True, exist_ok=True)
    (disk / "kept.bin").write_bytes(files["kept.bin"])
    with env.w.ctx.sessions() as db:
        row = db.scalars(select(ImportEntry).where(ImportEntry.path == "kept.bin")).one()
        folder = db.scalars(
            select(Node).where(Node.root_id == row_root(db, batch["id"]), Node.parent_id.is_(None))
        ).one()
        row.status, row.dir_node_id, row.dst_name = "writing", folder.id, "kept.bin"
        db.commit()
    counts = imports.recover_imports(env.w.ctx.sessions, env.w.settings)
    assert counts["adopted"] == 1
    assert children(a, root)["kept.bin"]["size"] == 900
    resume = a.post(f"/imports/{batch['id']}/resume", json={"entries": manifest(files)}).json()
    assert [p["path"] for p in resume["pending"]] == ["later.bin"]
    put(a, batch["id"], resume["pending"][0]["index"], b"later")
    assert sorted(p.name for p in disk.iterdir()) == ["kept.bin", "later.bin"]


def row_root(db: Any, batch_id: str) -> uuid.UUID:
    batch = db.get(ImportBatch, uuid.UUID(batch_id))
    assert batch is not None
    return batch.root_id


def test_interrupted_zip_import_stops_on_restart_and_resumes_from_the_staged_archive(
    env: Env,
) -> None:
    a = env.alice
    root = a.personal_root()
    items: dict[str, bytes | None] = {f"f{i}.bin": os.urandom(400) for i in range(6)}
    batch = plan_zip(a, root, make_zip(items)).json()
    runner: imports.ImportRunner = env.w.app.state.imports
    original = runner._one
    seen: list[int] = []

    def stop_after_three(*args: Any, **kw: Any) -> int:
        total: int = original(*args, **kw)
        seen.append(1)
        if len(seen) == 3:
            runner.stop.set()  # the process is going away
        return total

    runner._one = stop_after_three  # type: ignore[method-assign]
    a.post(f"/imports/{batch['id']}/start")
    assert runner.wait(uuid.UUID(batch["id"]), 30)
    mid = a.get(f"/imports/{batch['id']}").json()
    assert mid["status"] == "running" and mid["counts"]["done"] == 3

    # restart: recovery marks the extraction interrupted and keeps the staged archive
    runner.stop.clear()
    runner._one = original  # type: ignore[method-assign]
    imports.recover_imports(env.w.ctx.sessions, env.w.settings)
    stopped = a.get(f"/imports/{batch['id']}").json()
    assert stopped["status"] == "stopped" and stopped["error_code"] == "INTERRUPTED"
    staging = env.w.settings.app_data_path / "staging" / "imports"
    assert len(list(staging.iterdir())) == 1
    assert state(a, root)["batch"]["status"] == "stopped"

    a.post(f"/imports/{batch['id']}/start")
    done = wait_zip(env, a, batch["id"])
    assert done["counts"]["done"] == 6
    assert sorted(p.name for p in a.personal_disk().iterdir()) == [f"f{i}.bin" for i in range(6)]
    assert list(staging.iterdir()) == []


def test_stale_batches_expire_and_lose_their_staged_archive(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    batch = plan_zip(a, root, make_zip({"a.txt": b"a"})).json()
    staging = env.w.settings.app_data_path / "staging" / "imports"
    env.w.advance(seconds=env.w.settings.import_expiry_seconds + 5)
    a = Person(env.w, "alice@example.com")  # the old session expired too
    assert state(a, root)["batch"] is None
    with env.w.ctx.sessions() as db:
        row = db.get(ImportBatch, uuid.UUID(batch["id"]))
        assert row is not None and row.status == "expired"
    assert list(staging.iterdir()) == []
    assert plan_folder(a, root, {"x.txt": b"x"}).status_code == 201  # a new plan is possible


# ------------------------------------------------------------------ the reconciler and imports


def test_the_reconciler_leaves_a_root_alone_while_a_file_is_being_written(env: Env) -> None:
    a = env.alice
    root = a.personal_root()
    files = {"a.txt": b"a", "b.txt": b"b"}
    batch = plan_folder(a, root, files).json()
    a.post(f"/imports/{batch['id']}/start")
    a.personal_disk().mkdir(parents=True, exist_ok=True)
    (a.personal_disk() / "late-arrival.txt").write_bytes(b"x")
    set_entry_status(env, batch["id"], 0, "writing")  # a file is in flight right now
    recon = Reconciler(env.w.ctx.sessions, env.w.settings)
    assert recon.run_root(_root_id(env, root), "manual").outcome == "skipped"
    assert state(a, root)["state"] == "open"  # nothing was classified as outside content
    set_entry_status(env, batch["id"], 0, "pending")  # the upload ended: the scanner is let in
    assert recon.run_root(_root_id(env, root), "manual").outcome == "ok"
    assert state(a, root)["state"] == "closed"


def set_entry_status(env: Env, batch_id: str, idx: int, status: str) -> None:
    with env.w.ctx.sessions() as db:
        row = db.scalars(
            select(ImportEntry).where(
                ImportEntry.batch_id == uuid.UUID(batch_id), ImportEntry.idx == idx
            )
        ).one()
        row.status = status
        db.commit()


def test_the_import_actor_is_checked_per_entry_against_the_root_not_the_request(env: Env) -> None:
    """The route guard passes, then the root is disabled: the entry commit re-checks and refuses."""
    a = env.alice
    root = a.personal_root()
    batch = plan_folder(a, root, {"a.txt": b"a"}).json()
    a.post(f"/imports/{batch['id']}/start")
    with env.w.ctx.sessions() as db:
        sr = db.scalars(select(StorageRoot).where(StorageRoot.owner_user_id == a.uid())).one()
        sr.status = "disabled"
        db.commit()
    r = put(a, batch["id"], 0, b"a")
    assert r.status_code == 403
    assert not (a.personal_disk() / "a.txt").exists()


def test_unit_level_plan_is_pure_and_deterministic() -> None:
    raws = [
        imports.RawEntry("A/b.txt", "file", 3, None),
        imports.RawEntry("a/", "dir", None, 5),
        imports.RawEntry("a/B.txt", "file", 3, None),
        imports.RawEntry("x\x00y", "file", 1, None),
    ]
    args = {"max_depth": 8, "max_file_bytes": 100, "include_system": False, "zip_style": True}
    first = imports.build_plan(raws, **args)
    second = imports.build_plan(raws, **args)
    assert [vars(e) for e in first.entries] == [vars(e) for e in second.entries]
    assert first.summary["skipped"] == {"INVALID_NAME": 1}
    dirs = [e for e in first.entries if e.kind == "dir"]
    assert len(dirs) == 1 and dirs[0].mtime_ms == 5  # "A" and "a" are one folder
    assert first.summary["collisions"]["count"] >= 2
    assert imports.split_path("dir\\file.txt", zip_style=True) == ["dir", "file.txt"]
    with pytest.raises(imports.PathReject):
        imports.split_path("dir\\file.txt", zip_style=False)
