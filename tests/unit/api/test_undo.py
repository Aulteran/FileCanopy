"""M07a: undo with recorded preconditions, the cross-root move (ADR-013 §7) and import undo.
Every refused undo must change nothing on disk."""

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from test_acl import Fx
from test_auth import World
from test_files import code, disk, nid, node_row
from test_imports import manifest
from test_trash import P, trash_it

from app.db.models import FileEvent


@pytest.fixture
def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., World]:
    def factory(**overrides: Any) -> World:
        return World(tmp_path, monkeypatch, **overrides)

    return factory


@pytest.fixture
def fx(make_world: Callable[..., World]) -> Fx:
    # long sessions: some tests move the clock by months
    return Fx(make_world(session_idle_seconds=10**9, session_absolute_seconds=10**9))


@pytest.fixture
def me(fx: Fx) -> P:
    return P(fx, "alice")


def library_state(fx: Fx) -> dict[str, bytes | None]:
    """Every path under the library (trash included) with file contents: undo must not touch it
    when it refuses."""
    base = fx.w.settings.library_path
    return {
        str(p.relative_to(base)): (p.read_bytes() if p.is_file() else None)
        for p in sorted(base.rglob("*"))
    }


def op_of(r: Any) -> str:
    return str(r.json()["operation"]["id"])


def refused(fx: Fx, p: P, op: str, reason: str) -> None:
    before = library_state(fx)
    r = p.undo(op)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {"code": "UNDO_CONFLICT", "reason": reason}, r.text
    assert library_state(fx) == before


# ------------------------------------------------------------------ rename and move


def test_undo_a_rename_once_and_the_same_request_again(fx: Fx, me: P) -> None:
    f = nid(me.upload(fx.alice_root, "a.txt", b"x"))
    op = op_of(me.rename(f, "b.txt", 1))
    recent = {r["id"]: r for r in me.get("/activity/mine").json()}
    assert recent[op]["reversible"] is True and recent[op]["undone"] is False
    done = me.undo(op, key="u1")
    assert done.status_code == 200, done.text
    assert done.json()["node"]["name"] == "a.txt" and disk(fx, f).read_bytes() == b"x"
    assert not disk(fx, f).with_name("b.txt").exists()
    assert [e.action for e in fx.w.db_rows(FileEvent) if e.node_id == f][-1] == "undone"
    again = me.undo(op, key="u1")
    assert again.status_code == 200 and again.json()["replayed"] is True
    other = me.undo(op, key="u2")
    assert other.status_code == 409 and code(other) == "NOT_REVERSIBLE"
    assert {r["id"]: r for r in me.get("/activity/mine").json()}[op]["undone"] is True
    stranger = P(fx, "bob").undo(op)
    assert stranger.status_code == 403  # only the operation's actor


def test_undo_a_rename_is_refused_when_things_changed(fx: Fx, me: P) -> None:
    from test_reconcile import scan

    # edited outside the app
    f = nid(me.upload(fx.alice_root, "a.txt", b"x"))
    op = op_of(me.rename(f, "b.txt", 1))
    disk(fx, f).write_bytes(b"edited on the host, longer")
    refused(fx, me, op, "changed")
    # renamed outside the app and seen by a scan
    g = nid(me.upload(fx.alice_root, "g.txt", b"g"))
    op2 = op_of(me.rename(g, "h.txt", 1))
    disk(fx, g).rename(disk(fx, g).with_name("renamed-on-host.txt"))
    scan(fx)
    refused(fx, me, op2, "moved")
    # the old name was taken in the meantime
    k = nid(me.upload(fx.alice_root, "k.txt", b"k"))
    op3 = op_of(me.rename(k, "l.txt", 1))
    me.upload(fx.alice_root, "k.txt", b"another")
    refused(fx, me, op3, "name_taken")
    # the node is in the trash
    m = nid(me.upload(fx.alice_root, "m.txt", b"m"))
    op4 = op_of(me.rename(m, "n.txt", 1))
    trash_it(me, m, 2)
    refused(fx, me, op4, "missing")
    # the person lost access
    fx.grant(fx.alice, fx.taxes, "carol@example.com", "editor")
    carol = P(fx, "carol")
    c = nid(carol.upload(fx.taxes, "c.txt", b"c"))
    op5 = op_of(carol.rename(c, "d.txt", 1))
    fx.grant(fx.alice, fx.taxes, "carol@example.com", "viewer")
    refused(fx, carol, op5, "no_access")


def test_undo_a_move_and_refuse_after_the_target_folder_is_gone(fx: Fx, me: P) -> None:
    src = nid(me.mkdir(fx.alice_root, "Src"))
    dst = nid(me.mkdir(fx.alice_root, "Dst"))
    f = nid(me.upload(src, "a.txt", b"x"))
    op = op_of(me.move(f, dst, 1))
    assert disk(fx, f).parent.name == "Dst"
    assert me.undo(op).status_code == 200 and disk(fx, f).parent.name == "Src"
    op2 = op_of(me.move(f, dst, 3))
    trash_it(me, src)  # where it came from is in the trash now
    refused(fx, me, op2, "missing")


def test_undo_a_trash_restores_and_refuses_when_unsafe(fx: Fx, me: P) -> None:
    f = nid(me.upload(fx.alice_root, "a.txt", b"x"))
    op = op_of(me.delete(f, 1))
    assert me.undo(op).status_code == 200 and disk(fx, f).read_bytes() == b"x"
    assert node_row(fx, f).status == "active"
    g = nid(me.upload(fx.alice_root, "g.txt", b"g"))
    taken = op_of(me.delete(g, 1))
    me.upload(fx.alice_root, "g.txt", b"new owner of the name")
    refused(fx, me, taken, "name_taken")
    h = nid(me.upload(fx.alice_root, "h.txt", b"h"))
    out = trash_it(me, h)
    purged = out["operation"]["id"]
    me.purge(out["item"]["id"], out["item"]["confirm_token"])
    refused(fx, me, purged, "missing")  # (a purged item cannot come back through undo)


# ------------------------------------------------------------------ copy


def test_undo_a_copy_trashes_it_and_leaves_changed_entries_in_place(fx: Fx, me: P) -> None:
    folder = nid(me.mkdir(fx.alice_root, "Folder"))
    nid(me.upload(folder, "a.txt", b"a"))
    nid(me.upload(folder, "b.txt", b"b"))
    clean = me.copy(folder, fx.taxes)
    top = nid(clean)
    undone = me.undo(op_of(clean))
    assert undone.status_code == 200 and undone.json()["left_in_place"] == []
    assert undone.json()["trashed"] == 3
    assert node_row(fx, top).status == "trashed" and disk(fx, folder).is_dir()  # source untouched
    only = me.items()
    assert len(only) == 1 and only[0]["reason"] == "copy_undone"
    # a copy someone has worked in since: the new file and the edited one stay, the rest goes
    second = me.copy(folder, fx.taxes)
    top2 = nid(second)
    me.upload(top2, "mine.txt", b"added later")
    kids = {c["name"]: c["id"] for c in me.children(top2)}
    (disk(fx, top2) / "a.txt").write_bytes(b"edited on the host, longer")
    from test_reconcile import scan

    scan(fx)
    res = me.undo(op_of(second))
    assert res.status_code == 200, res.text
    left = {x["name"]: x["reason"] for x in res.json()["left_in_place"]}
    assert left["a.txt"] == "changed" and left["mine.txt"] == "added_later"
    assert left["Folder"] == "contains_changes"
    assert (disk(fx, top2) / "a.txt").read_bytes() == b"edited on the host, longer"
    assert (disk(fx, top2) / "mine.txt").exists() and not (disk(fx, top2) / "b.txt").exists()
    assert node_row(fx, uuid.UUID(kids["b.txt"])).status == "trashed"
    nothing = me.copy(folder, fx.taxes, on_conflict="keep_both")
    gone = nid(nothing)
    trash_it(me, gone)
    refused(fx, me, op_of(nothing), "missing")


# ------------------------------------------------------------------ cross-root move


def test_cross_root_move_copies_then_trashes_the_source_in_the_owners_trash(fx: Fx, me: P) -> None:
    sub = nid(me.mkdir(fx.alice_root, "Share"))
    leaf = nid(me.upload(sub, "x.txt", b"payload"))
    r = me.move(sub, fx.shared_sub, 1)
    assert r.status_code == 200, r.text
    new = uuid.UUID(r.json()["node"]["id"])
    assert new != sub  # a new identity in the other root
    assert (disk(fx, new) / "x.txt").read_bytes() == b"payload"
    assert not any(p.name == "Share" for p in (fx.w.settings.library_path / "personal").rglob("*"))
    item = me.items()[0]
    assert item["reason"] == "moved_out" and item["name"] == "Share"
    assert node_row(fx, sub).status == "trashed" and node_row(fx, leaf).status == "trashed"
    out = [e for e in fx.w.db_rows(FileEvent) if e.node_id == sub and e.action == "moved_out"]
    inn = [e for e in fx.w.db_rows(FileEvent) if e.node_id == new and e.action == "moved_in"]
    assert len(out) == len(inn) == 1
    assert out[0].details["to_node_id"] == str(new) and inn[0].details["from_node_id"] == str(sub)
    assert out[0].correlation_id == inn[0].correlation_id
    # both histories show the step, to people who may see it
    assert "moved_out" in str(me.activity(fx.alice_root).json()["items"])
    assert "moved_in" in str(P(fx, "bob").activity(fx.shared_sub).json()["items"])
    # the owner can restore the original: the content is not lost to the mover
    assert me.restore(item["id"]).status_code == 200 and disk(fx, sub).is_dir()


def test_an_editor_moving_out_of_someone_elses_folder_lands_in_the_owners_trash(
    fx: Fx, me: P
) -> None:
    fx.grant(fx.alice, fx.taxes, "carol@example.com", "editor")
    carol = P(fx, "carol")
    f = nid(me.upload(fx.taxes, "m.txt", b"m"))
    mine = fx.root_of("carol@example.com")
    r = carol.move(f, mine, 1)
    assert r.status_code == 200, r.text
    assert (disk(fx, uuid.UUID(r.json()["node"]["id"]))).read_bytes() == b"m"
    owner_view = me.items()
    assert [(i["name"], i["reason"]) for i in owner_view] == [("m.txt", "moved_out")]
    assert owner_view[0]["trashed_by_name"] is not None
    assert carol.items()[0]["id"] == owner_view[0]["id"]  # Carol still holds write there


def test_undo_a_cross_root_move_and_refuse_when_the_copy_changed(fx: Fx, me: P) -> None:
    from test_reconcile import scan

    a = nid(me.upload(fx.alice_root, "a.txt", b"a"))
    moved = me.move(a, fx.shared_sub, 1)
    new = uuid.UUID(moved.json()["node"]["id"])
    undone = me.undo(op_of(moved))
    assert undone.status_code == 200, undone.text
    assert disk(fx, a).read_bytes() == b"a" and node_row(fx, a).status == "active"
    assert node_row(fx, new).status == "trashed"
    b = nid(me.upload(fx.alice_root, "b.txt", b"b"))
    moved2 = me.move(b, fx.shared_sub, 1)
    new2 = uuid.UUID(moved2.json()["node"]["id"])
    disk(fx, new2).write_bytes(b"someone edited the shared copy")
    scan(fx, fx.shared_sub)
    refused(fx, me, op_of(moved2), "changed")
    c = nid(me.upload(fx.alice_root, "c.txt", b"c"))
    moved3 = me.move(c, fx.shared_sub, 1)
    me.upload(fx.alice_root, "c.txt", b"took the name")
    refused(fx, me, op_of(moved3), "name_taken")


def test_cross_root_move_needs_write_at_both_ends_and_reports_conflicts(fx: Fx, me: P) -> None:
    f = nid(me.upload(fx.alice_root, "f.txt", b"f"))
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "viewer")
    nid(me.upload(fx.taxes, "x.txt", b"x"))
    bob = P(fx, "bob")
    assert bob.move(f, fx.shared_sub, 1).status_code == 403
    nid(P(fx, "bob").upload(fx.shared_sub, "f.txt", b"already here"))
    r = me.move(f, fx.shared_sub, 1)
    assert r.status_code == 409 and code(r) == "NAME_CONFLICT"
    assert disk(fx, f).read_bytes() == b"f" and node_row(fx, f).status == "active"
    both = me.move(f, fx.shared_sub, 1, on_conflict="keep_both")
    assert both.status_code == 200 and both.json()["node"]["name"] == "f (2).txt"


# ------------------------------------------------------------------ import undo


def test_import_undo_trashes_what_it_created_and_leaves_changed_entries(fx: Fx, me: P) -> None:
    from test_imports import entries, put

    class Shim:
        """test_imports helpers expect a Person with `.req`."""

        def req(self, method: str, path: str, **kw: Any) -> Any:
            return me.client.request(method, f"/api/v1{path}", headers=me.headers, **kw)

        def get(self, path: str, **kw: Any) -> Any:
            return self.req("GET", path, **kw)

    shim: Any = Shim()
    me.mkdir(fx.alice_root, "Keep")  # closes the setup window, like any content
    contents = {"Imp/a.txt": b"a", "Imp/sub/b.txt": b"b", "Imp/c.txt": b"c"}
    r = me.post(f"/nodes/{fx.alice_root}/folder-uploads", json={"entries": manifest(contents)})
    assert r.status_code == 201, r.text
    batch = r.json()["id"]
    for e in entries(shim, batch, status="pending"):
        if e["kind"] == "file":
            assert put(shim, batch, e["index"], contents[e["path"]]).status_code == 200
    kids = {c["name"]: c for c in me.children(fx.alice_root)}
    imp = uuid.UUID(kids["Imp"]["id"])
    (disk(fx, imp) / "c.txt").write_bytes(b"edited on the host, longer")
    from test_reconcile import scan

    scan(fx)
    res = me.post(f"/imports/{batch}/undo")
    assert res.status_code == 200, res.text
    left = {x["name"]: x["reason"] for x in res.json()["left_in_place"]}
    assert left == {"Imp": "contains_changes", "c.txt": "changed"}
    assert (disk(fx, imp) / "c.txt").read_bytes() == b"edited on the host, longer"
    assert not (disk(fx, imp) / "a.txt").exists() and not (disk(fx, imp) / "sub").exists()
    assert {i["reason"] for i in me.items()} == {"import_undone"}
    assert [x for x in me.get("/activity/mine").json() if x["kind"] == "import"][0]["undone"]
    assert P(fx, "bob").post(f"/imports/{batch}/undo").status_code == 403  # the batch's actor only
    # a clean import goes as one item per top-level entry, and restore brings it back
    clean = {"Solo/only.txt": b"o"}
    b2 = me.post(f"/nodes/{fx.alice_root}/folder-uploads", json={"entries": manifest(clean)})
    for e in entries(shim, b2.json()["id"], status="pending"):
        if e["kind"] == "file":
            put(shim, b2.json()["id"], e["index"], clean["Solo/only.txt"])
    res2 = me.post(f"/imports/{b2.json()['id']}/undo")
    assert res2.status_code == 200 and res2.json()["left_in_place"] == []
    assert res2.json()["trashed"] == 2


def test_operations_are_listed_with_what_can_be_undone(fx: Fx, me: P) -> None:
    f = nid(me.upload(fx.alice_root, "a.txt", b"x"))
    fx.w.advance(seconds=1)
    me.rename(f, "b.txt", 1)
    fx.w.advance(seconds=1)
    me.delete(f, 2)
    mine = me.get("/activity/mine").json()
    assert [(r["action"], r["reversible"]) for r in mine][:2] == [("trash", True), ("rename", True)]
    assert mine[0]["node_id"] is None  # a trashed node is not offered by id
    plain = next(r for r in mine if r["action"] == "upload")
    assert plain["reversible"] is False  # an upload is not on the list of undoable actions
    assert P(fx, "bob").get("/activity/mine").json() == []
