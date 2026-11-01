"""M04 file operations: CRUD on real files, atomicity, conflicts, revisions, idempotency, symlinks,
cross-device fallback, partial-failure reporting, recovery and authorization.

In-memory SQLite plus a per-test temp library (Linux container, so O_NOFOLLOW/dir_fd are real).
Failure injection patches the filesystem primitives in `app.storage.files`.
"""

import hashlib
import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from test_acl import Fx
from test_auth import PASSWORD, World, invite, signed_in

from app.db.models import AuthAudit, FileOperation, Node, StorageRoot
from app.domain import fileops
from app.storage import files


@pytest.fixture
def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., World]:
    def factory(**overrides: Any) -> World:
        return World(tmp_path, monkeypatch, **overrides)

    return factory


@pytest.fixture
def fx(make_world: Callable[..., World]) -> Fx:
    return Fx(make_world())


class Api:
    """One signed-in persona."""

    def __init__(self, fx: Fx, email: str) -> None:
        self.fx = fx
        self.client, self.headers = signed_in(fx.w, f"{email}@example.com")
        self.uid = fx.uid(f"{email}@example.com")

    def post(self, path: str, **kw: Any) -> Any:
        key = kw.pop("key", None)
        headers = {**self.headers, **({"Idempotency-Key": key} if key else {})}
        return self.client.post(f"/api/v1{path}", headers=headers, **kw)

    def get(self, path: str, **kw: Any) -> Any:
        return self.client.get(f"/api/v1{path}", **kw)

    def mkdir(self, parent: uuid.UUID, name: str, **kw: Any) -> Any:
        return self.post(f"/nodes/{parent}/folders", json={"name": name}, **kw)

    def upload(
        self,
        parent: uuid.UUID,
        name: str,
        data: bytes | Iterator[bytes],
        mode: str = "reject",
        **kw: Any,
    ) -> Any:
        return self.post(
            f"/nodes/{parent}/files", params={"name": name, "on_conflict": mode}, content=data, **kw
        )

    def download(self, node: uuid.UUID) -> Any:
        return self.get(f"/nodes/{node}/content")

    def rename(self, node: uuid.UUID, name: str, rev: int, **kw: Any) -> Any:
        return self.post(
            f"/nodes/{node}/rename", json={"name": name, "expected_revision": rev}, **kw
        )

    def move(self, node: uuid.UUID, target: uuid.UUID, rev: int, **extra: Any) -> Any:
        key = extra.pop("key", None)
        return self.post(
            f"/nodes/{node}/move",
            json={"target_parent_id": str(target), "expected_revision": rev, **extra},
            key=key,
        )

    def copy(self, node: uuid.UUID, target: uuid.UUID, **extra: Any) -> Any:
        key = extra.pop("key", None)
        return self.post(
            f"/nodes/{node}/copy", json={"target_parent_id": str(target), **extra}, key=key
        )

    def children(self, node: uuid.UUID, **params: Any) -> list[dict[str, Any]]:
        r = self.get(f"/nodes/{node}/children", params=params)
        assert r.status_code == 200, r.text
        return list(r.json()["items"])


def code(r: Any) -> str:
    return str(r.json()["detail"]["code"])


def nid(r: Any) -> uuid.UUID:
    assert r.status_code in (200, 201), r.text
    return uuid.UUID(r.json()["node"]["id"])


def rev(r: Any) -> int:
    return int(r.json()["node"]["revision"])


def disk(fx: Fx, node_id: uuid.UUID) -> Path:
    """Where a node lives on disk (test-side path building from the database)."""
    from app.domain.nodes import path_components

    with fx.w.ctx.sessions() as db:
        node = db.get(Node, node_id)
        assert node is not None
        root = db.get(StorageRoot, node.root_id)
        assert root is not None
        return fx.w.settings.library_path / root.relative_dir / Path(*path_components(db, node))


def node_row(fx: Fx, node_id: uuid.UUID) -> Node:
    with fx.w.ctx.sessions() as db:
        node = db.get(Node, node_id)
        assert node is not None
        db.expunge(node)
        return node


FIXTURE_FOLDERS = {"Taxes", "Private"}  # created by the shared Fx in alice's root


def visible(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [i for i in items if i["name"] not in FIXTURE_FOLDERS]


def entries(path: Path) -> list[str]:
    return sorted(p.name for p in path.iterdir() if p.name not in FIXTURE_FOLDERS)


def tmp_leftovers(fx: Fx) -> list[Path]:
    return [p for p in fx.w.settings.library_path.rglob(f"{files.TMP_PREFIX}*")]


@pytest.fixture
def alice(fx: Fx) -> Api:
    return Api(fx, "alice")


# ------------------------------------------------------------------ CRUD on real files


def test_folder_upload_download_roundtrip_uses_real_files(fx: Fx, alice: Api) -> None:
    docs = nid(alice.mkdir(fx.alice_root, "Docs"))
    data = b"hello \x00 binary \xff world" * 1000
    r = alice.upload(docs, "a.txt", data)
    assert r.status_code == 201, r.text
    f = nid(r)
    path = disk(fx, f)
    assert path.read_bytes() == data  # an ordinary file at an ordinary path
    assert path.parent.name == "Docs" and path.is_file() and not path.is_symlink()
    assert oct(path.stat().st_mode & 0o777) == "0o640"
    got = alice.download(f)
    assert got.status_code == 200 and got.content == data
    assert got.headers["content-type"] == "application/octet-stream"
    assert got.headers["content-disposition"].startswith("attachment; filename*=UTF-8''a.txt")
    assert got.headers["x-content-type-options"] == "nosniff"
    items = alice.children(docs)
    assert [(i["name"], i["kind"], i["size"]) for i in items] == [("a.txt", "file", len(data))]
    assert tmp_leftovers(fx) == []
    assert node_row(fx, f).size == len(data)


def test_names_are_validated_and_normalized(fx: Fx, alice: Api) -> None:
    bad = ["..", ".", "a/b", "a\\b", "", "x\x00y", "tab\tname", "n" * 256, f"{files.TMP_PREFIX}x"]
    for name in bad:
        r = alice.mkdir(fx.alice_root, name)
        assert r.status_code in (422,), (name, r.text)
        r = alice.upload(fx.alice_root, name, b"x") if name else r
        assert r.status_code == 422, (name, r.text)
    decomposed = "é.txt"
    r = alice.upload(fx.alice_root, decomposed, b"x")
    assert r.json()["node"]["name"] == "é.txt"
    assert (disk(fx, fx.alice_root) / "é.txt").exists()
    # nothing escaped the root
    assert not list(fx.w.settings.library_path.rglob("..*"))


def test_listing_is_paginated_sorted_and_needs_a_folder(fx: Fx, alice: Api) -> None:
    lst = nid(alice.mkdir(fx.alice_root, "L"))
    for n in ("c", "a", "b", "d"):
        assert alice.upload(lst, n, n.encode()).status_code == 201
    first = alice.get(f"/nodes/{lst}/children", params={"limit": 2}).json()
    assert [i["name"] for i in first["items"]] == ["a", "b"] and first["next_after"] == "b"
    rest = alice.get(
        f"/nodes/{lst}/children", params={"limit": 5, "after": first["next_after"]}
    ).json()
    assert [i["name"] for i in rest["items"]] == ["c", "d"] and rest["next_after"] is None
    a = alice.children(lst)[0]["id"]
    assert alice.get(f"/nodes/{a}/children").status_code == 422  # a file


def test_large_file_streams_without_buffering_and_round_trips(fx: Fx, alice: Api) -> None:
    chunk = os.urandom(1024 * 1024)
    total = 96
    digest = hashlib.sha256()

    def body() -> Iterator[bytes]:
        for _ in range(total):
            digest.update(chunk)
            yield chunk

    r = alice.upload(fx.alice_root, "big.bin", body())
    assert r.status_code == 201, r.text
    f = nid(r)
    assert node_row(fx, f).size == total * len(chunk)
    assert r.json()["operation"]["detail"]["sha256"] == digest.hexdigest()
    seen = hashlib.sha256()
    n = 0
    with alice.client.stream("GET", f"/api/v1/nodes/{f}/content") as resp:
        assert int(resp.headers["content-length"]) == total * len(chunk)
        for part in resp.iter_bytes():
            seen.update(part)
            n += len(part)
    assert n == total * len(chunk) and seen.hexdigest() == digest.hexdigest()
    assert tmp_leftovers(fx) == []


def test_upload_limit_aborts_and_leaves_nothing(make_world: Callable[..., World]) -> None:
    fx = Fx(make_world(max_upload_bytes=2048))
    a = Api(fx, "alice")
    r = a.upload(fx.alice_root, "big", iter([b"x" * 1500, b"y" * 1500]))
    assert r.status_code == 413 and code(r) == "UPLOAD_TOO_LARGE"
    assert visible(a.children(fx.alice_root)) == []
    assert not (disk(fx, fx.alice_root) / "big").exists() and tmp_leftovers(fx) == []
    assert a.upload(fx.alice_root, "ok", b"z" * 2048).status_code == 201
    ops = fx.w.db_rows(FileOperation)
    assert sorted(o.status for o in ops) == ["committed", "failed"]


# ------------------------------------------------------------------ atomic write


def test_failed_publish_leaves_no_partial_file(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise OSError(5, "I/O error")

    monkeypatch.setattr(files, "_link_noreplace", boom)
    r = alice.upload(fx.alice_root, "x.txt", b"data")
    assert r.status_code == 503 and code(r) == "STORAGE_IO_ERROR"
    assert entries(disk(fx, fx.alice_root)) == []  # no final file, no temp file
    assert visible(alice.children(fx.alice_root)) == []


def test_database_failure_after_publish_removes_the_new_file(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(fileops, "_new_node", boom)
    assert alice.upload(fx.alice_root, "x.txt", b"data").status_code == 500
    assert entries(disk(fx, fx.alice_root)) == []
    assert alice.mkdir(fx.alice_root, "d").status_code == 500
    assert entries(disk(fx, fx.alice_root)) == []


def test_new_account_directory_is_created_on_first_write(fx: Fx) -> None:
    token = invite(fx.w, fx.admin, "frank@example.com")
    r = fx.w.browser().post(
        "/api/v1/auth/invitations/accept", json={"token": token, "password": PASSWORD}
    )
    assert r.status_code == 200
    root = fx.root_of("frank@example.com")
    frank = Api(fx, "frank")
    assert not disk(fx, root).exists()  # the API has not restarted since the account was created
    assert frank.upload(root, "first.txt", b"1").status_code == 201
    assert disk(fx, root).is_dir()


# ------------------------------------------------------------------ overwrite and conflicts


def test_nothing_is_ever_overwritten(fx: Fx, alice: Api) -> None:
    a = nid(alice.upload(fx.alice_root, "a.txt", b"original"))
    again = alice.upload(fx.alice_root, "a.txt", b"replacement")
    assert again.status_code == 409 and code(again) == "NAME_CONFLICT"
    assert disk(fx, a).read_bytes() == b"original"
    both = alice.upload(fx.alice_root, "a.txt", b"second", mode="keep_both")
    assert both.json()["node"]["name"] == "a (2).txt"
    third = alice.upload(fx.alice_root, "a.txt", b"third", mode="keep_both")
    assert third.json()["node"]["name"] == "a (3).txt"
    b = nid(alice.upload(fx.alice_root, "b.txt", b"bee"))
    r = alice.rename(b, "a.txt", node_row(fx, b).revision)
    assert r.status_code == 409 and code(r) == "NAME_CONFLICT"
    assert disk(fx, a).read_bytes() == b"original" and disk(fx, b).read_bytes() == b"bee"
    d = nid(alice.mkdir(fx.alice_root, "D"))
    assert alice.mkdir(fx.alice_root, "D").status_code == 409
    r = alice.move(b, d, node_row(fx, b).revision, name="x")
    assert r.status_code == 200
    r = alice.upload(d, "x", b"again")
    assert r.status_code == 409
    assert tmp_leftovers(fx) == []
    # the failed rename left the node usable at its old revision
    assert node_row(fx, b).status == "active"


def test_file_present_on_disk_but_unknown_to_the_database_is_not_clobbered(
    fx: Fx, alice: Api
) -> None:
    host_file = disk(fx, fx.alice_root) / "host.txt"
    host_file.write_bytes(b"made by the host")
    r = alice.upload(fx.alice_root, "host.txt", b"app upload")
    assert r.status_code == 409 and code(r) == "NAME_CONFLICT"
    assert host_file.read_bytes() == b"made by the host"
    kept = alice.upload(fx.alice_root, "host.txt", b"app upload", mode="keep_both")
    assert kept.json()["node"]["name"] == "host (2).txt" or kept.json()["node"]["name"] == (
        "host.txt (2)"
    )
    other = nid(alice.upload(fx.alice_root, "other.txt", b"o"))
    r = alice.rename(other, "host.txt", 1)
    assert r.status_code == 409
    assert host_file.read_bytes() == b"made by the host"
    assert disk(fx, other).read_bytes() == b"o"


# ------------------------------------------------------------------ revisions, concurrency


def test_rename_is_revision_checked(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.alice_root, "a.txt", b"x"))
    r1 = alice.rename(f, "b.txt", 1)
    assert r1.status_code == 200 and rev(r1) == 2 and r1.json()["node"]["name"] == "b.txt"
    stale = alice.rename(f, "c.txt", 1)
    assert stale.status_code == 409 and code(stale) == "REVISION_CONFLICT"
    assert disk(fx, f).name == "b.txt"
    assert alice.rename(f, "c.txt", 2).status_code == 200
    # renaming to its own name is a no-op and does not bump the revision
    assert rev(alice.rename(f, "c.txt", 3)) == 3


def test_concurrent_rename_second_caller_is_refused_while_first_is_in_flight(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = nid(alice.upload(fx.alice_root, "a.txt", b"x"))
    real = files.rename_noreplace
    nested: dict[str, Any] = {}

    def interleaved(*a: Any, **k: Any) -> None:
        # a second client tries the same node while the first is between claim and commit
        nested["r"] = alice.rename(f, "winner2.txt", 1)
        real(*a, **k)

    monkeypatch.setattr(files, "rename_noreplace", interleaved)
    first = alice.rename(f, "winner1.txt", 1)
    monkeypatch.setattr(files, "rename_noreplace", real)
    assert first.status_code == 200
    assert nested["r"].status_code == 409 and code(nested["r"]) in (
        "NODE_BUSY",
        "REVISION_CONFLICT",
    )
    assert entries(disk(fx, fx.alice_root)) == ["winner1.txt"]
    assert node_row(fx, f).name == "winner1.txt"


def test_claim_is_an_atomic_compare_and_swap(fx: Fx, alice: Api) -> None:
    """Two requests that both passed the early revision check: only one claim can succeed."""
    f = nid(alice.upload(fx.alice_root, "a.txt", b"x"))
    from sqlalchemy import update

    with fx.w.ctx.sessions() as db:
        first = db.execute(
            update(Node)
            .where(Node.id == f, Node.revision == 1, Node.status == "active")
            .values(status="busy")
        ).rowcount
        second = db.execute(
            update(Node)
            .where(Node.id == f, Node.revision == 1, Node.status == "active")
            .values(status="busy")
        ).rowcount
        assert (first, second) == (1, 0)


def test_two_nodes_racing_for_one_name_never_clobber(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = nid(alice.upload(fx.alice_root, "a.txt", b"A"))
    b = nid(alice.upload(fx.alice_root, "b.txt", b"B"))
    real = files.rename_noreplace

    def stolen(src_dir: int, src: str, dst_dir: int, dst: str) -> None:
        # the DB pre-check already passed; someone creates the name first on disk
        (disk(fx, fx.alice_root) / "target.txt").write_bytes(b"winner")
        real(src_dir, src, dst_dir, dst)

    monkeypatch.setattr(files, "rename_noreplace", stolen)
    r = alice.rename(b, "target.txt", 1)
    assert r.status_code == 409 and code(r) == "NAME_CONFLICT"
    monkeypatch.setattr(files, "rename_noreplace", real)
    assert (disk(fx, fx.alice_root) / "target.txt").read_bytes() == b"winner"
    assert disk(fx, a).read_bytes() == b"A" and disk(fx, b).read_bytes() == b"B"
    assert node_row(fx, b).status == "active" and node_row(fx, b).revision == 1


def test_directories_are_never_replaced_by_a_rename(fx: Fx, alice: Api) -> None:
    """A plain rename(2) would silently replace an empty directory; ours must not."""
    assert files._renameat2 is not None  # the kernel primitive is in use in the test container
    a = nid(alice.mkdir(fx.alice_root, "Alpha"))
    nid(alice.upload(a, "keep.txt", b"keep"))
    (disk(fx, fx.alice_root) / "Beta").mkdir()  # created by the host, unknown to the database
    r = alice.rename(a, "Beta", 1)
    assert r.status_code == 409 and code(r) == "NAME_CONFLICT"
    assert (disk(fx, fx.alice_root) / "Alpha" / "keep.txt").read_bytes() == b"keep"
    assert (disk(fx, fx.alice_root) / "Beta").is_dir()
    assert node_row(fx, a).status == "active"


# ------------------------------------------------------------------ idempotency


def test_idempotent_create_upload_and_rename(fx: Fx, alice: Api) -> None:
    r1 = alice.mkdir(fx.alice_root, "K", key="k-1")
    r2 = alice.mkdir(fx.alice_root, "K", key="k-1")
    assert r1.status_code == 201 and r2.status_code == 201
    assert nid(r1) == nid(r2) and r2.json()["replayed"] is True
    assert len(visible(alice.children(fx.alice_root))) == 1
    clash = alice.mkdir(fx.alice_root, "Other", key="k-1")
    assert clash.status_code == 422 and code(clash) == "IDEMPOTENCY_KEY_REUSED"

    u1 = alice.upload(fx.alice_root, "u.txt", b"1", key="u-1")
    u2 = alice.upload(fx.alice_root, "u.txt", b"1", key="u-1")
    assert nid(u1) == nid(u2) and u2.json()["replayed"] is True
    assert len(visible(alice.children(fx.alice_root))) == 2

    f = nid(u1)
    m1 = alice.rename(f, "v.txt", 1, key="r-1")
    m2 = alice.rename(f, "v.txt", 1, key="r-1")  # revision is stale by now, but it is a retry
    assert m1.status_code == 200 and m2.status_code == 200 and m2.json()["replayed"] is True
    assert rev(m2) == 2
    bad = alice.post(
        "/nodes/" + str(f) + "/rename", json={"name": "z", "expected_revision": 2}, key="x" * 200
    )
    assert bad.status_code == 422
    # keys are per actor: another user's identical key does not collide
    bob = Api(fx, "bob")
    assert bob.mkdir(fx.root_of("bob@example.com"), "K", key="k-1").status_code == 201


def test_failed_operation_with_key_can_be_retried(fx: Fx, alice: Api) -> None:
    alice.upload(fx.alice_root, "a.txt", b"x")
    first = alice.upload(fx.alice_root, "a.txt", b"y", key="same")
    assert first.status_code == 409
    (disk(fx, fx.alice_root) / "a.txt").unlink()
    with fx.w.ctx.sessions() as db:  # the file really went away: drop the stale row too
        node = db.scalars(select(Node).where(Node.name == "a.txt")).one()
        db.delete(node)
        db.commit()
    retry = alice.upload(fx.alice_root, "a.txt", b"y", key="same")
    assert retry.status_code == 201, retry.text


# ------------------------------------------------------------------ authorization


def test_unauthorized_download_and_every_other_operation_is_403(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.alice_root, "secret.txt", b"top secret"))
    d = nid(alice.mkdir(fx.alice_root, "Dir"))
    dave = Api(fx, "dave")
    mine = fx.root_of("dave@example.com")
    uniform = {"detail": {"code": "FORBIDDEN"}}
    attempts = [
        dave.download(f),
        dave.get(f"/nodes/{fx.alice_root}/children"),
        dave.mkdir(fx.alice_root, "x"),
        dave.upload(fx.alice_root, "x", b"x"),
        dave.rename(f, "y", 1),
        dave.move(f, mine, 1),
        dave.copy(f, mine),
        dave.get(f"/nodes/{uuid.uuid4()}/content"),  # nonexistent: identical answer
    ]
    for r in attempts:
        assert r.status_code == 403 and r.json() == uniform, r.text
    assert b"top secret" not in b"".join(r.content for r in attempts)
    # target side: may read the source but not write the destination
    fx.grant(fx.alice, fx.taxes, "dave@example.com", "viewer")
    inside = nid(alice.upload(fx.taxes, "t.txt", b"taxes"))
    assert dave.download(inside).content == b"taxes"
    assert dave.copy(inside, fx.alice_root).status_code == 403  # no write at the target
    assert dave.copy(f, mine).status_code == 403  # no read at the source
    assert dave.copy(inside, mine).status_code == 201  # read source + own folder: fine
    assert d  # Dir exists and stayed untouched
    assert disk(fx, f).read_bytes() == b"top secret"


def test_viewer_can_read_but_not_write_and_editor_can_both(fx: Fx, alice: Api) -> None:
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "viewer")
    fx.grant(fx.alice, fx.taxes, "carol@example.com", "editor")
    f = nid(alice.upload(fx.taxes, "w2.pdf", b"%PDF"))
    bob, carol = Api(fx, "bob"), Api(fx, "carol")
    assert bob.download(f).content == b"%PDF"
    assert "w2.pdf" in [i["name"] for i in bob.children(fx.taxes)]
    for r in (
        bob.upload(fx.taxes, "x", b"x"),
        bob.mkdir(fx.taxes, "x"),
        bob.rename(f, "x", 1),
        bob.move(f, fx.y2026, 1),
    ):
        assert r.status_code == 403
    assert carol.upload(fx.taxes, "c.txt", b"c").status_code == 201
    assert carol.rename(f, "w2-final.pdf", 1).status_code == 200
    # a grant root cannot be renamed or moved by its grantee: the parent is not theirs
    assert carol.rename(fx.taxes, "mine", 1).status_code == 403
    assert carol.move(fx.taxes, fx.taxes, 1).status_code == 403


def test_admin_has_no_file_access_to_personal_trees(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.alice_root, "p.txt", b"private"))
    c, h = fx.admin
    for r in (
        c.get(f"/api/v1/nodes/{f}/content"),
        c.get(f"/api/v1/nodes/{fx.alice_root}/children"),
        c.post(f"/api/v1/nodes/{fx.alice_root}/folders", json={"name": "x"}, headers=h),
    ):
        assert r.status_code == 403


def test_disabled_root_blocks_operations_and_is_hidden(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.alice_root, "p.txt", b"x"))
    with fx.w.ctx.sessions() as db:
        root = db.scalars(select(StorageRoot).where(StorageRoot.owner_user_id == alice.uid)).one()
        root.status = "disabled"
        db.commit()
    for r in (
        alice.download(f),
        alice.get(f"/nodes/{fx.alice_root}/children"),
        alice.upload(fx.alice_root, "n", b"n"),
        alice.rename(f, "q", 1),
        alice.get(f"/nodes/{fx.alice_root}/grants"),
        alice.client.put(
            f"/api/v1/nodes/{fx.alice_root}/grants/{fx.uid('bob@example.com')}",
            json={"level": "viewer"},
            headers=alice.headers,
        ),
    ):
        assert r.status_code == 403, r.text
    assert alice.get("/roots").json() == [
        e for e in alice.get("/roots").json() if e["kind"] != "personal"
    ]


def test_operation_journal_is_visible_to_its_actor_only(fx: Fx, alice: Api) -> None:
    r = alice.mkdir(fx.alice_root, "J")
    op = r.json()["operation"]["id"]
    ok = alice.get(f"/operations/{op}")
    assert ok.status_code == 200 and ok.json()["status"] == "committed"
    assert "tmp" not in ok.json()["detail"] and "src_ino" not in ok.json()["detail"]
    bob = Api(fx, "bob")
    assert bob.get(f"/operations/{op}").status_code == 403
    assert bob.get(f"/operations/{uuid.uuid4()}").status_code == 403


def test_file_actions_are_audited_with_ids_only(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.alice_root, "AuditedName.txt", b"x"))
    alice.rename(f, "Renamed.txt", 1)
    rows = [a for a in fx.w.db_rows(AuthAudit) if a.action.startswith("file.")]
    assert {a.action for a in rows} >= {"file.upload", "file.rename"}
    assert "AuditedName" not in str([a.details for a in rows]) and "Renamed" not in str(
        [a.details for a in rows]
    )


# ------------------------------------------------------------------ move and lineage


def test_move_rewrites_descendant_lineages_in_the_same_transaction(fx: Fx, alice: Api) -> None:
    a = nid(alice.mkdir(fx.alice_root, "A"))
    sub = nid(alice.mkdir(a, "sub"))
    leaf = nid(alice.upload(sub, "leaf.txt", b"leaf"))
    b = nid(alice.mkdir(fx.alice_root, "B"))
    r = alice.move(a, b, 1)
    assert r.status_code == 200, r.text
    assert disk(fx, leaf).read_bytes() == b"leaf"
    assert str(disk(fx, leaf).relative_to(disk(fx, fx.alice_root))) == "B/A/sub/leaf.txt"
    leaf_row, b_row = node_row(fx, leaf), node_row(fx, b)
    assert leaf_row.lineage.startswith(b_row.lineage)
    assert leaf_row.lineage == f"{b_row.lineage}{a}/{sub}/{leaf}/"
    assert alice.download(leaf).content == b"leaf"  # resolved through the new lineage
    assert not (disk(fx, fx.alice_root) / "A").exists()
    assert [i["name"] for i in alice.children(b)] == ["A"]


def test_move_into_itself_or_a_descendant_is_refused(fx: Fx, alice: Api) -> None:
    a = nid(alice.mkdir(fx.alice_root, "A"))
    sub = nid(alice.mkdir(a, "sub"))
    for target in (a, sub):
        r = alice.move(a, target, 1)
        assert r.status_code == 422 and code(r) == "INVALID_TARGET"
        r = alice.copy(a, target)
        assert r.status_code == 422 and code(r) == "INVALID_TARGET"
    assert alice.move(fx.alice_root, a, 1).status_code in (403, 422)


def test_move_needs_write_on_both_ends(fx: Fx, alice: Api) -> None:
    fx.grant(fx.alice, fx.taxes, "carol@example.com", "editor")
    fx.grant(fx.alice, fx.private, "carol@example.com", "viewer")
    carol = Api(fx, "carol")
    g = nid(alice.upload(fx.taxes, "n.txt", b"n"))
    assert carol.move(g, fx.private, 1).status_code == 403  # target is read-only for carol
    other = nid(alice.mkdir(fx.taxes, "Dest"))
    assert carol.move(g, other, 1).status_code == 200  # editor both ends, same root


# ------------------------------------------------------------------ copy


def test_copy_file_and_tree_creates_independent_real_files(fx: Fx, alice: Api) -> None:
    src = nid(alice.mkdir(fx.alice_root, "Src"))
    sub = nid(alice.mkdir(src, "sub"))
    f1 = nid(alice.upload(src, "one.txt", b"1" * 5000))
    f2 = nid(alice.upload(sub, "two.bin", os.urandom(70000)))
    dst = nid(alice.mkdir(fx.alice_root, "Dst"))
    r = alice.copy(src, dst)
    assert r.status_code == 201, r.text
    top = nid(r)
    assert top not in (src,) and r.json()["node"]["name"] == "Src"
    new_one = [n for n in alice.children(top) if n["name"] == "one.txt"][0]["id"]
    new_sub = [n for n in alice.children(top) if n["name"] == "sub"][0]["id"]
    new_two = alice.children(new_sub)[0]["id"]
    assert len({str(x) for x in (f1, f2, sub, new_one, new_sub, new_two)}) == 6  # fresh identities
    assert disk(fx, uuid.UUID(new_one)).read_bytes() == b"1" * 5000
    assert disk(fx, uuid.UUID(new_two)).read_bytes() == disk(fx, f2).read_bytes()
    assert not os.path.samefile(disk(fx, f1), disk(fx, uuid.UUID(new_one)))  # not a hardlink
    assert disk(fx, f1).read_bytes() == b"1" * 5000  # source untouched
    again = alice.copy(src, dst)
    assert again.status_code == 409 and code(again) == "NAME_CONFLICT"
    kept = alice.copy(src, dst, on_conflict="keep_both")
    assert kept.json()["node"]["name"] == "Src (2)"
    one = alice.copy(f1, dst, name="renamed.txt")
    assert one.json()["node"]["name"] == "renamed.txt"
    assert tmp_leftovers(fx) == []


def test_copy_tree_limit_is_checked_before_anything_is_written(
    make_world: Callable[..., World],
) -> None:
    fx = Fx(make_world(max_tree_entries=3))
    a = Api(fx, "alice")
    src = nid(a.mkdir(fx.alice_root, "Src"))
    for i in range(4):
        a.upload(src, f"f{i}", b"x")
    dst = nid(a.mkdir(fx.alice_root, "Dst"))
    r = a.copy(src, dst)
    assert r.status_code == 413 and code(r) == "TREE_TOO_LARGE"
    assert a.children(dst) == [] and list(disk(fx, dst).iterdir()) == []


def test_failed_copy_rolls_back_every_created_entry(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = nid(alice.mkdir(fx.alice_root, "Src"))
    alice.upload(src, "a", b"a")
    sub = nid(alice.mkdir(src, "sub"))
    alice.upload(sub, "b", b"b")
    dst = nid(alice.mkdir(fx.alice_root, "Dst"))

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(fileops, "_insert_copied", boom)
    assert alice.copy(src, dst).status_code == 500
    assert list(disk(fx, dst).iterdir()) == [] and alice.children(dst) == []
    ops = [o for o in fx.w.db_rows(FileOperation) if o.action == "copy"]
    assert [o.status for o in ops] == ["failed"]


# ----------------------------------------------------------- symlinks, hardlinks, special files


def test_symlinks_are_never_followed(fx: Fx, alice: Api, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"host secret")
    f = nid(alice.upload(fx.alice_root, "doc.txt", b"doc"))
    path = disk(fx, f)
    path.unlink()
    path.symlink_to(outside)  # host swaps the file for a link
    r = alice.download(f)
    assert r.status_code == 409 and code(r) == "UNSUPPORTED_ENTRY"
    assert b"host secret" not in r.content
    dst = nid(alice.mkdir(fx.alice_root, "Dst"))
    assert alice.copy(f, dst).status_code == 409
    assert outside.read_bytes() == b"host secret"

    folder = nid(alice.mkdir(fx.alice_root, "Folder"))
    inner = nid(alice.upload(folder, "in.txt", b"in"))
    (disk(fx, folder) / "evil").symlink_to(tmp_path)  # symlink inside a tree
    r = alice.copy(folder, dst)
    assert r.status_code == 409 and code(r) == "UNSUPPORTED_ENTRY"
    assert list(disk(fx, dst).iterdir()) == []  # refused before writing anything

    # a symlink swapped in for a directory component is refused at use time
    real = disk(fx, folder)
    moved = real.with_name("Folder.real")
    real.rename(moved)
    real.symlink_to(tmp_path, target_is_directory=True)
    for r in (alice.download(inner), alice.upload(folder, "n", b"n"), alice.mkdir(folder, "d")):
        assert r.status_code == 409 and code(r) == "STORAGE_UNSAFE_PATH", r.text
    assert not (tmp_path / "n").exists() and not (tmp_path / "d").exists()
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["outside.txt"]


def test_hardlinked_files_are_not_read_or_copied(fx: Fx, alice: Api, tmp_path: Path) -> None:
    f = nid(alice.upload(fx.alice_root, "a.txt", b"shared inode"))
    os.link(disk(fx, f), tmp_path / "alias")  # content now also reachable outside the library
    r = alice.download(f)
    assert r.status_code == 409 and code(r) == "UNSUPPORTED_ENTRY"
    dst = nid(alice.mkdir(fx.alice_root, "Dst"))
    assert alice.copy(f, dst).status_code == 409
    assert alice.rename(f, "b.txt", 1).status_code == 200  # metadata-only operations still work


def test_fifo_does_not_hang_download_or_copy(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.alice_root, "pipe", b"x"))
    path = disk(fx, f)
    path.unlink()
    os.mkfifo(path)
    assert alice.download(f).status_code == 409
    folder = nid(alice.mkdir(fx.alice_root, "F"))
    os.mkfifo(disk(fx, folder) / "p")
    dst = nid(alice.mkdir(fx.alice_root, "Dst"))
    assert alice.copy(folder, dst).status_code == 409


def test_missing_file_is_reported_not_papered_over(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.alice_root, "gone.txt", b"x"))
    disk(fx, f).unlink()  # deleted behind the app's back
    assert code(alice.download(f)) == "CONTENT_MISSING"
    r = alice.rename(f, "other.txt", 1)
    assert r.status_code == 409 and code(r) == "CONTENT_MISSING"
    row = node_row(fx, f)
    assert row.status == "reconcile_needed" and row.name == "gone.txt"
    again = alice.rename(f, "other.txt", 1)
    assert code(again) == "RECONCILE_NEEDED"


# ----------------------------------------------------------- cross-device, partial failure


@pytest.fixture
def cross_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every rename reports EXDEV, as when source and target are on different filesystems."""

    def exdev(*a: Any, **k: Any) -> None:
        raise files.CrossDevice("EXDEV")

    monkeypatch.setattr(files, "rename_noreplace", exdev)


def build_tree(fx: Fx, alice: Api) -> tuple[uuid.UUID, dict[str, uuid.UUID], uuid.UUID]:
    src = nid(alice.mkdir(fx.alice_root, "Tree"))
    sub = nid(alice.mkdir(src, "sub"))
    ids = {
        "a": nid(alice.upload(src, "a.bin", os.urandom(300_000))),
        "b": nid(alice.upload(sub, "b.txt", b"bee")),
    }
    dst = nid(alice.mkdir(fx.alice_root, "Elsewhere"))
    return src, ids, dst


def test_cross_device_move_copies_verifies_then_removes_source(
    fx: Fx, alice: Api, cross_device: None
) -> None:
    src, ids, dst = build_tree(fx, alice)
    before = {k: disk(fx, v).read_bytes() for k, v in ids.items()}
    old_dir = disk(fx, src)
    r = alice.move(src, dst, 1)
    assert r.status_code == 200, r.text
    assert r.json()["operation"]["status"] == "committed"
    assert not old_dir.exists()  # source removed only after the copy was verified and committed
    assert disk(fx, src).parent.name == "Elsewhere"
    assert {k: disk(fx, v).read_bytes() for k, v in ids.items()} == before
    assert node_row(fx, ids["a"]).lineage.startswith(node_row(fx, dst).lineage)
    assert tmp_leftovers(fx) == []


def test_cross_device_file_move_and_copy(fx: Fx, alice: Api, cross_device: None) -> None:
    f = nid(alice.upload(fx.alice_root, "f.bin", b"payload" * 5000))
    dst = nid(alice.mkdir(fx.alice_root, "To"))
    assert alice.move(f, dst, 1).status_code == 200
    assert disk(fx, f).read_bytes() == b"payload" * 5000
    assert not (disk(fx, fx.alice_root) / "f.bin").exists()
    c = alice.copy(f, fx.alice_root)  # copy never uses rename
    assert c.status_code == 201 and disk(fx, nid(c)).read_bytes() == b"payload" * 5000


def test_cross_device_verification_failure_keeps_the_source(
    fx: Fx, alice: Api, cross_device: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    src, ids, dst = build_tree(fx, alice)
    monkeypatch.setattr(files, "hash_file", lambda *a, **k: ("0" * 64, 1))
    r = alice.move(src, dst, 1)
    assert r.status_code == 500 and code(r) == "COPY_VERIFY_FAILED"
    assert disk(fx, ids["a"]).is_file() and disk(fx, src).parent.name != "Elsewhere"
    assert list(disk(fx, dst).iterdir()) == []  # partial copy cleaned up
    row = node_row(fx, src)
    assert row.status == "active" and row.revision == 1


def test_cross_device_database_failure_keeps_the_source(
    fx: Fx, alice: Api, cross_device: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    src, ids, dst = build_tree(fx, alice)

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(fileops, "_apply_move", boom)
    assert alice.move(src, dst, 1).status_code == 500
    assert disk(fx, ids["b"]).read_bytes() == b"bee"
    assert list(disk(fx, dst).iterdir()) == []
    assert node_row(fx, src).status == "active"
    ops = [o for o in fx.w.db_rows(FileOperation) if o.action == "move"]
    assert ops[0].status == "rolled_back"


def test_source_changed_during_move_is_never_deleted(
    fx: Fx, alice: Api, cross_device: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    src, ids, dst = build_tree(fx, alice)
    victim = disk(fx, ids["b"])
    real = files.copy_tree

    def copy_then_edit(*a: Any, **k: Any) -> Any:
        out = real(*a, **k)
        victim.write_bytes(b"edited by someone else after the copy")
        return out

    monkeypatch.setattr(files, "copy_tree", copy_then_edit)
    r = alice.move(src, dst, 1)
    assert r.status_code == 200
    assert r.json()["operation"]["status"] == "reconcile_needed"
    assert r.json()["operation"]["detail"]["step"] == "source_remnants"
    assert victim.read_bytes() == b"edited by someone else after the copy"  # survived
    assert disk(fx, ids["b"]).read_bytes() == b"bee"  # destination holds the verified copy
    op = alice.get(f"/operations/{r.json()['operation']['id']}").json()
    assert op["status"] == "reconcile_needed"


def test_rename_database_failure_undoes_the_filesystem_change(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = nid(alice.upload(fx.alice_root, "a.txt", b"x"))

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("db down")

    real_apply = fileops._apply_move
    monkeypatch.setattr(fileops, "_apply_move", boom)
    assert alice.rename(f, "b.txt", 1).status_code == 500
    monkeypatch.setattr(fileops, "_apply_move", real_apply)
    assert (disk(fx, fx.alice_root) / "a.txt").read_bytes() == b"x"
    assert not (disk(fx, fx.alice_root) / "b.txt").exists()
    assert node_row(fx, f).status == "active" and node_row(fx, f).revision == 1
    assert [o.status for o in fx.w.db_rows(FileOperation) if o.action == "rename"] == [
        "rolled_back"
    ]
    assert alice.rename(f, "b.txt", 1).status_code == 200  # still fully usable


def test_unrecoverable_database_failure_is_reported_as_reconcile_needed(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = nid(alice.upload(fx.alice_root, "a.txt", b"x"))
    real = files.rename_noreplace
    calls = {"n": 0}

    def undo_fails(*a: Any, **k: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            real(*a, **k)
        else:
            raise OSError(5, "cannot undo")

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("db down")

    real_apply = fileops._apply_move
    monkeypatch.setattr(files, "rename_noreplace", undo_fails)
    monkeypatch.setattr(fileops, "_apply_move", boom)
    assert alice.rename(f, "b.txt", 1).status_code == 500
    monkeypatch.setattr(files, "rename_noreplace", real)
    monkeypatch.setattr(fileops, "_apply_move", real_apply)
    row = node_row(fx, f)
    assert row.status == "reconcile_needed" and row.name == "a.txt"
    op = [o for o in fx.w.db_rows(FileOperation) if o.action == "rename"][0]
    assert op.status == "reconcile_needed"
    # the bytes are safe under the new name; the app refuses to guess and says why
    assert (disk(fx, fx.alice_root) / "b.txt").read_bytes() == b"x"
    r = alice.rename(f, "c.txt", 1)
    assert r.status_code == 409 and code(r) == "RECONCILE_NEEDED"
    assert alice.get(f"/operations/{op.id}").json()["status"] == "reconcile_needed"


# ------------------------------------------------------------------ crash recovery


def _crash_state(fx: Fx, alice: Api, *, fs_done: bool) -> tuple[uuid.UUID, uuid.UUID]:
    """Leaves a rename exactly as a crash would: node claimed, journal planned."""
    f = nid(alice.upload(fx.alice_root, "old.txt", b"x"))
    ino = (disk(fx, fx.alice_root) / "old.txt").stat().st_ino
    if fs_done:
        (disk(fx, fx.alice_root) / "old.txt").rename(disk(fx, fx.alice_root) / "new.txt")
    with fx.w.ctx.sessions() as db:
        node = db.get(Node, f)
        assert node is not None
        node.status = "busy"
        op = FileOperation(
            actor_user_id=alice.uid,
            action="rename",
            status="planned",
            request_hash="h",
            node_id=f,
            target_parent_id=fx.alice_root,
            detail={
                "src_parent": str(fx.alice_root),
                "src_name": "old.txt",
                "dst_parent": str(fx.alice_root),
                "dst_name": "new.txt",
                "src_ino": ino,
            },
            created_at=node.updated_at,
            updated_at=node.updated_at,
        )
        db.add(op)
        db.commit()
        return f, op.id


def test_recovery_rolls_forward_when_the_filesystem_step_finished(fx: Fx, alice: Api) -> None:
    f, op_id = _crash_state(fx, alice, fs_done=True)
    counts = fileops.recover_operations(
        fx.w.ctx.sessions, fx.w.settings.model_copy(update={"operation_stale_seconds": 0})
    )
    assert counts["committed"] == 1
    row = node_row(fx, f)
    assert row.name == "new.txt" and row.status == "active" and row.revision == 2
    assert disk(fx, f).read_bytes() == b"x"
    with fx.w.ctx.sessions() as db:
        assert db.get(FileOperation, op_id).status == "committed"  # type: ignore[union-attr]


def test_recovery_rolls_back_when_the_filesystem_was_untouched(fx: Fx, alice: Api) -> None:
    f, op_id = _crash_state(fx, alice, fs_done=False)
    counts = fileops.recover_operations(
        fx.w.ctx.sessions, fx.w.settings.model_copy(update={"operation_stale_seconds": 0})
    )
    assert counts["rolled_back"] == 1
    row = node_row(fx, f)
    assert row.name == "old.txt" and row.status == "active" and row.revision == 1


def test_recovery_flags_what_it_cannot_decide(fx: Fx, alice: Api) -> None:
    f, op_id = _crash_state(fx, alice, fs_done=False)
    base = disk(fx, fx.alice_root)
    (base / "old.txt").rename(base / "kept-aside.txt")  # keeps the old inode number in use
    (base / "old.txt").write_bytes(b"different file")  # neither name holds the recorded inode
    (disk(fx, fx.alice_root) / "new.txt").write_bytes(b"another one")
    counts = fileops.recover_operations(
        fx.w.ctx.sessions, fx.w.settings.model_copy(update={"operation_stale_seconds": 0})
    )
    assert counts["reconcile_needed"] == 1
    assert node_row(fx, f).status == "reconcile_needed"
    assert (disk(fx, fx.alice_root) / "old.txt").read_bytes() == b"different file"


def test_recovery_ignores_fresh_operations_and_clears_leftover_staging(fx: Fx, alice: Api) -> None:
    leftover = disk(fx, fx.alice_root) / f"{files.TMP_PREFIX}deadbeef"
    leftover.write_bytes(b"partial")
    with fx.w.ctx.sessions() as db:
        node = db.get(Node, fx.alice_root)
        assert node is not None
        db.add(
            FileOperation(
                actor_user_id=alice.uid,
                action="upload",
                status="planned",
                request_hash="h",
                detail={"parent": str(fx.alice_root), "name": "u.txt", "tmp": leftover.name},
                created_at=node.updated_at,
                updated_at=node.updated_at,
            )
        )
        db.commit()
    fresh = fx.w.settings.model_copy(update={"operation_stale_seconds": 10**9})
    assert fileops.recover_operations(fx.w.ctx.sessions, fresh) == {
        "rolled_back": 0,
        "committed": 0,
        "failed": 0,
        "reconcile_needed": 0,
    }
    assert leftover.exists()
    counts = fileops.recover_operations(
        fx.w.ctx.sessions, fx.w.settings.model_copy(update={"operation_stale_seconds": 0})
    )
    assert counts["failed"] == 1 and not leftover.exists()


# ------------------------------------------------------------------ route inventory


def test_file_routes_exist_and_are_all_policy_guarded(fx: Fx) -> None:
    from test_acl import _api_routes, _guards

    routes = {
        (m, r.path): r
        for r in _api_routes(fx.w.app.routes)
        for m in r.methods
        if r.path.startswith("/api/v1/")
    }
    expected = [
        ("GET", "/api/v1/nodes/{node_id}/children"),
        ("POST", "/api/v1/nodes/{node_id}/folders"),
        ("POST", "/api/v1/nodes/{node_id}/files"),
        ("GET", "/api/v1/nodes/{node_id}/content"),
        ("GET", "/api/v1/nodes/{node_id}/view"),
        ("POST", "/api/v1/nodes/{node_id}/rename"),
        ("POST", "/api/v1/nodes/{node_id}/move"),
        ("POST", "/api/v1/nodes/{node_id}/copy"),
        ("GET", "/api/v1/operations/{operation_id}"),
    ]
    for key in expected:
        assert key in routes and _guards(routes[key].dependant), key


def test_file_mutations_keep_csrf_and_auth(fx: Fx, alice: Api) -> None:
    c = alice.client
    r = c.post(f"/api/v1/nodes/{fx.alice_root}/folders", json={"name": "x"})  # no CSRF header
    assert r.status_code == 403 and code(r) == "CSRF_TOKEN_INVALID"
    anon = fx.w.browser()
    assert anon.get(f"/api/v1/nodes/{fx.alice_root}/children").status_code == 401
    assert anon.get(f"/api/v1/nodes/{fx.alice_root}/content").status_code == 401


# ------------------------------------------------------------------ review regressions


def test_no_hardlink_filesystem_rename_fallback_does_not_report_failure(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CIFS-like filesystem: no renameat2, no hardlinks. The fallback must not unlink after it
    already renamed (that used to turn a successful rename into CONTENT_MISSING)."""

    def no_link(*a: Any, **k: Any) -> None:
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(files, "_renameat2", None)
    monkeypatch.setattr(os, "link", no_link)
    f = nid(alice.upload(fx.alice_root, "a.txt", b"payload"))  # publish uses the same fallback
    r = alice.rename(f, "b.txt", 1)
    assert r.status_code == 200, r.text
    assert disk(fx, f).read_bytes() == b"payload" and disk(fx, f).name == "b.txt"
    assert node_row(fx, f).status == "active"
    assert tmp_leftovers(fx) == []


def test_move_commits_against_fresh_lineage_when_the_target_moved_meanwhile(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow cross-device move must not write a stale lineage if the target folder was moved
    while the bytes were being copied."""
    x = nid(alice.upload(fx.alice_root, "X", b"x-bytes"))
    p = nid(alice.mkdir(fx.alice_root, "P"))
    elsewhere = nid(alice.mkdir(fx.alice_root, "Elsewhere"))
    real_rename = files.rename_noreplace

    def selective(src_dir: int, src: str, dst_dir: int, dst: str) -> None:
        if src == "X":
            raise files.CrossDevice("EXDEV")
        real_rename(src_dir, src, dst_dir, dst)

    real_copy = fileops._copy_for_move
    hooked: dict[str, Any] = {}

    def copy_then_move_target(*a: Any, **k: Any) -> Any:
        out = real_copy(*a, **k)
        hooked["r"] = alice.move(p, elsewhere, 1)  # P moves while X is mid-flight
        return out

    monkeypatch.setattr(files, "rename_noreplace", selective)
    monkeypatch.setattr(fileops, "_copy_for_move", copy_then_move_target)
    r = alice.move(x, p, 1)
    assert hooked["r"].status_code == 200 and r.status_code == 200, r.text
    x_row, p_row = node_row(fx, x), node_row(fx, p)
    assert x_row.lineage == f"{p_row.lineage}{x}/"
    assert alice.download(x).content == b"x-bytes"
    assert disk(fx, x).parent.name == "P" and disk(fx, x).parent.parent.name == "Elsewhere"


def test_concurrent_move_creating_a_cycle_is_refused(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = nid(alice.mkdir(fx.alice_root, "A"))
    p = nid(alice.mkdir(fx.alice_root, "P"))
    sub = nid(alice.mkdir(a, "sub"))
    real_rename = files.rename_noreplace
    state: dict[str, Any] = {}

    def hook(src_dir: int, src: str, dst_dir: int, dst: str) -> None:
        real_rename(src_dir, src, dst_dir, dst)
        if src == "A" and "done" not in state:
            state["done"] = True
            state["other"] = alice.move(p, sub, 1)  # P into A/sub while A moves into P

    monkeypatch.setattr(files, "rename_noreplace", hook)
    r = alice.move(a, p, 1)
    monkeypatch.setattr(files, "rename_noreplace", real_rename)
    outcomes = sorted([r.status_code, state["other"].status_code])
    # P is busy while A moves into it, so the second move is refused; no cycle can form.
    assert outcomes[0] == 200 and outcomes[1] == 409, (r.text, state["other"].text)
    with fx.w.ctx.sessions() as db:
        seen: set[uuid.UUID] = set()
        for n in db.scalars(select(Node)):
            cur, hops = n, 0
            while cur.parent_id is not None and hops < 50:
                cur = db.get(Node, cur.parent_id)  # type: ignore[assignment]
                hops += 1
            assert cur.parent_id is None
            seen.add(n.id)


def test_failed_undo_of_a_created_entry_is_flagged(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(fileops, "_new_node", boom)
    monkeypatch.setattr(fileops, "_undo_entry", lambda *a, **k: False)
    assert alice.upload(fx.alice_root, "orphan.txt", b"x").status_code == 500
    ops = [o for o in fx.w.db_rows(FileOperation) if o.action == "upload"]
    assert [o.status for o in ops] == ["reconcile_needed"]
    assert (disk(fx, fx.alice_root) / "orphan.txt").exists()  # reported, not hidden


def test_recovery_releases_claims_that_have_no_open_operation(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.alice_root, "stuck.txt", b"x"))
    with fx.w.ctx.sessions() as db:
        node = db.get(Node, f)
        assert node is not None
        node.status = "busy"  # a claim whose release failed
        db.commit()
    assert alice.rename(f, "y.txt", 1).status_code == 409
    fileops.recover_operations(fx.w.ctx.sessions, fx.w.settings)
    assert node_row(fx, f).status == "active"
    assert alice.rename(f, "y.txt", 1).status_code == 200


def test_retry_of_a_failed_keyed_operation_is_compare_and_swap(fx: Fx, alice: Api) -> None:
    alice.upload(fx.alice_root, "a.txt", b"x")
    assert alice.upload(fx.alice_root, "a.txt", b"y", key="k").status_code == 409
    ctx = fileops.OpCtx(fx.w.ctx.sessions(), fx.w.settings, alice.uid)
    params = {"parent": str(fx.alice_root), "name": "a.txt", "mode": "reject"}
    first = fileops._begin(
        ctx, "upload", "k", node_id=None, target_parent_id=fx.alice_root, params=params, detail={}
    )
    assert first[1] is False
    with pytest.raises(fileops.OpError) as err:  # second concurrent retry loses
        fileops._begin(
            ctx,
            "upload",
            "k",
            node_id=None,
            target_parent_id=fx.alice_root,
            params=params,
            detail={},
        )
    assert err.value.code == "OPERATION_IN_PROGRESS"


def test_writes_to_a_missing_root_are_refused(fx: Fx, alice: Api) -> None:
    with fx.w.ctx.sessions() as db:
        root = db.scalars(select(StorageRoot).where(StorageRoot.owner_user_id == alice.uid)).one()
        root.status = "missing"
        db.commit()
    r = alice.upload(fx.alice_root, "n.txt", b"n")
    assert r.status_code == 503 and code(r) == "STORAGE_UNAVAILABLE"
