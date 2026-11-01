"""M07a: delete to trash, restore, permanent delete, retention and the hidden library trash folder
(ADR-017). In-memory SQLite plus a per-test temp library on Linux, so files, inodes and
`O_NOFOLLOW` are real. All content is generated.
"""

import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from test_acl import Fx
from test_auth import World
from test_files import Api, code, disk, nid, node_row

from app.db.models import FileEvent, FileOperation, Node, TrashItem
from app.domain import trash

TRASH = ".filecanopy-trash"


@pytest.fixture
def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., World]:
    def factory(**overrides: Any) -> World:
        return World(tmp_path, monkeypatch, **overrides)

    return factory


@pytest.fixture
def fx(make_world: Callable[..., World]) -> Fx:
    # long sessions: the retention tests move the clock by months
    return Fx(make_world(session_idle_seconds=10**9, session_absolute_seconds=10**9))


@pytest.fixture
def alice(fx: Fx) -> Api:
    return Api(fx, "alice")


class P(Api):
    """Persona with the trash routes."""

    def delete(self, node: uuid.UUID, revision: int, key: str | None = None) -> Any:
        headers = {**self.headers, **({"Idempotency-Key": key} if key else {})}
        return self.client.delete(
            f"/api/v1/nodes/{node}", params={"expected_revision": revision}, headers=headers
        )

    def trash_list(self, **params: Any) -> dict[str, Any]:
        r = self.get("/trash", params=params)
        assert r.status_code == 200, r.text
        return dict(r.json())

    def items(self, **params: Any) -> list[dict[str, Any]]:
        return list(self.trash_list(**params)["items"])

    def restore(self, item: str, **body: Any) -> Any:
        return self.post(f"/trash/{item}/restore", json=body)

    def purge(self, item: str, confirm: str) -> Any:
        return self.client.delete(
            f"/api/v1/trash/{item}", params={"confirm": confirm}, headers=self.headers
        )

    def undo(self, op: str, key: str | None = None) -> Any:
        return self.post(f"/operations/{op}/undo", key=key)

    def activity(self, node: uuid.UUID, **params: Any) -> Any:
        return self.get(f"/nodes/{node}/activity", params=params)


@pytest.fixture
def me(fx: Fx) -> P:
    return P(fx, "alice")


def trash_dir(fx: Fx) -> Path:
    return fx.w.settings.library_path / TRASH


def item_path(fx: Fx, item: str, name: str) -> Path:
    with fx.w.ctx.sessions() as db:
        row = db.get(TrashItem, uuid.UUID(item))
        assert row is not None
        return trash_dir(fx) / str(row.root_id) / item / name


def trash_it(p: P, node: uuid.UUID, revision: int = 1) -> dict[str, Any]:
    r = p.delete(node, revision)
    assert r.status_code == 200, r.text
    return dict(r.json())


# ------------------------------------------------------------------ trash and restore


def test_delete_moves_to_the_hidden_library_trash_and_hides_the_node(fx: Fx, me: P) -> None:
    f = nid(me.upload(fx.alice_root, "a.txt", b"data"))
    original = disk(fx, f)
    out = trash_it(me, f)
    item = out["item"]
    assert item["name"] == "a.txt" and item["reason"] == "deleted" and item["status"] == "in_trash"
    assert item["purge_after"] is not None and item["days_left"] == 30 and not item["expiring"]
    assert not original.exists()  # gone from the root...
    assert item_path(fx, item["id"], "a.txt").read_bytes() == b"data"  # ...into the library trash
    assert trash_dir(fx).stat().st_mode & 0o777 == 0o700
    # outside every root: the roots do not contain it and no API path or listing names it
    assert "a.txt" not in [c["name"] for c in me.children(fx.alice_root)]
    assert me.get(f"/nodes/{f}").status_code == 403  # the same answer as for no access
    assert me.download(f).status_code == 403
    assert str(trash_dir(fx)) not in str(out)
    assert out["operation"]["action"] == "trash"
    row = node_row(fx, f)
    assert row.status == "trashed" and row.revision == 2
    assert [i["id"] for i in me.items()] == [item["id"]]


def test_trashed_name_is_released_and_restore_keeps_both_or_fails(fx: Fx, me: P) -> None:
    f = nid(me.upload(fx.alice_root, "a.txt", b"old"))
    item = trash_it(me, f)["item"]["id"]
    g = nid(me.upload(fx.alice_root, "a.txt", b"new"))  # the name is free again
    assert disk(fx, g).read_bytes() == b"new"
    r = me.restore(item, on_conflict="fail")
    assert r.status_code == 409 and code(r) == "NAME_CONFLICT"
    assert [i["status"] for i in me.items()] == ["in_trash"]  # nothing changed
    assert item_path(fx, item, "a.txt").read_bytes() == b"old"
    r = me.restore(item, on_conflict="keep_both")
    assert r.status_code == 200, r.text
    assert r.json()["node"]["name"] == "a (2).txt" and r.json()["node"]["id"] == str(f)
    assert disk(fx, f).read_bytes() == b"old" and disk(fx, g).read_bytes() == b"new"
    assert me.items() == []  # restored items leave the list
    assert not (trash_dir(fx) / str(node_row(fx, f).root_id) / item).exists()  # empty dir tidied
    assert node_row(fx, f).status == "active"


def test_restore_to_the_original_folder_a_chosen_folder_and_never_across_roots(
    fx: Fx, me: P
) -> None:
    a = nid(me.mkdir(fx.alice_root, "A"))
    b = nid(me.mkdir(fx.alice_root, "B"))
    f = nid(me.upload(a, "f.txt", b"f"))
    item = trash_it(me, f)["item"]["id"]
    r = me.restore(item, target_parent_id=str(fx.shared_sub))
    assert r.status_code == 422 and code(r) == "RESTORE_TARGET_OTHER_ROOT"
    assert me.restore(item, target_parent_id=str(b)).status_code == 200
    assert disk(fx, f).parent.name == "B" and node_row(fx, f).parent_id == b
    item2 = trash_it(me, f, 3)["item"]
    assert item2["original_parent_id"] == str(b) and item2["original_parent_name"] == "B"
    assert me.restore(item2["id"]).status_code == 200 and disk(fx, f).parent.name == "B"


def test_nested_folder_roundtrip_and_an_earlier_item_stays_in_the_trash(fx: Fx, me: P) -> None:
    top = nid(me.mkdir(fx.alice_root, "Top"))
    sub = nid(me.mkdir(top, "Sub"))
    deep = nid(me.upload(sub, "deep.txt", b"deep"))
    keep = nid(me.upload(top, "keep.txt", b"keep"))
    early = trash_it(me, deep)["item"]["id"]  # trashed on its own first
    assert node_row(fx, top).revision == 1
    folder = trash_it(me, top)["item"]
    assert folder["entry_count"] == 3 and folder["size_bytes"] == 4  # Top, Sub, keep.txt
    assert {node_row(fx, n).status for n in (top, sub, keep, deep)} == {"trashed"}
    assert node_row(fx, deep).trash_item_id == uuid.UUID(early)  # still its own item
    assert node_row(fx, keep).trash_item_id == uuid.UUID(folder["id"])
    assert me.restore(folder["id"]).status_code == 200
    assert disk(fx, keep).read_bytes() == b"keep" and disk(fx, sub).is_dir()
    assert [node_row(fx, n).status for n in (top, sub, keep)] == ["active"] * 3
    assert node_row(fx, deep).status == "trashed"  # not revived: it is not inside Sub on disk
    assert not (disk(fx, sub) / "deep.txt").exists()
    assert me.restore(early).status_code == 200  # and it restores into the restored folder
    assert (disk(fx, sub) / "deep.txt").read_bytes() == b"deep"
    assert node_row(fx, deep).lineage.startswith(node_row(fx, top).lineage)


def test_a_trashed_parent_means_choosing_another_folder(fx: Fx, me: P) -> None:
    a = nid(me.mkdir(fx.alice_root, "A"))
    f = nid(me.upload(a, "f.txt", b"f"))
    item = trash_it(me, f)["item"]["id"]
    trash_it(me, a)
    r = me.restore(item)
    assert r.status_code == 409 and code(r) == "PARENT_UNAVAILABLE"
    listed = me.items()
    assert {i["name"] for i in listed} == {"A", "f.txt"}
    assert next(i for i in listed if i["name"] == "f.txt")["original_parent_id"] is None
    assert me.restore(item, target_parent_id=str(fx.alice_root)).status_code == 200
    assert disk(fx, f).parent == disk(fx, fx.alice_root)


def test_no_symlinked_or_foreign_device_trash_and_never_a_hard_delete(fx: Fx, me: P) -> None:
    f = nid(me.upload(fx.alice_root, "a.txt", b"keep me"))
    outside = fx.w.settings.library_path.parent / "elsewhere"
    outside.mkdir()
    trash_dir(fx).rmdir() if trash_dir(fx).exists() else None
    os.symlink(outside, trash_dir(fx))
    r = me.delete(f, 1)
    assert r.status_code == 503 and code(r) == "TRASH_UNAVAILABLE"
    assert disk(fx, f).read_bytes() == b"keep me" and list(outside.iterdir()) == []
    assert node_row(fx, f).status == "active"
    ops = [o for o in fx.w.db_rows(FileOperation) if o.action == "trash"]
    assert ops == []  # refused before anything was journaled
    trash_dir(fx).unlink()
    assert me.delete(f, 1).status_code == 200  # healthy again


def test_delete_needs_the_current_revision_and_is_idempotent(fx: Fx, me: P) -> None:
    f = nid(me.upload(fx.alice_root, "a.txt", b"x"))
    r = me.delete(f, 5)
    assert r.status_code == 409 and code(r) == "REVISION_CONFLICT"
    first = me.delete(f, 1, key="k1")
    again = me.delete(f, 1, key="k1")
    assert first.status_code == again.status_code == 200
    assert (
        again.json()["replayed"] is True
        and again.json()["item"]["id"] == first.json()["item"]["id"]
    )
    assert len(me.items()) == 1
    other = me.delete(f, 2)  # no key, already in the trash
    assert other.status_code == 409 and code(other) == "ALREADY_TRASHED"
    root = me.delete(fx.alice_root, 1)
    assert root.status_code == 422 and code(root) == "CANNOT_TRASH_ROOT"


def test_a_grant_root_cannot_be_trashed_by_its_grantee(fx: Fx, me: P) -> None:
    fx.grant(fx.alice, fx.taxes, "carol@example.com", "editor")
    carol = P(fx, "carol")
    inner = nid(carol.upload(fx.taxes, "c.txt", b"c"))
    assert carol.delete(fx.taxes, 1).status_code == 403  # write on the parent is missing
    assert carol.delete(inner, 1).status_code == 200


def test_delete_forever_needs_the_confirmation_and_audits(fx: Fx, me: P) -> None:
    f = nid(me.upload(fx.alice_root, "a.txt", b"12345"))
    item = trash_it(me, f)["item"]
    r = me.purge(item["id"], "wrong")
    assert r.status_code == 409 and code(r) == "CONFIRMATION_REQUIRED"
    assert item_path(fx, item["id"], "a.txt").exists()
    done = me.purge(item["id"], item["confirm_token"])
    assert done.status_code == 200 and done.json()["status"] == "purged"
    assert not (trash_dir(fx) / str(node_row(fx, f).root_id) / item["id"]).exists()
    assert node_row(fx, f).status == "purged" and me.items() == []
    kinds = [e.action for e in fx.w.db_rows(FileEvent) if e.node_id == f]
    assert kinds == ["uploaded", "trashed", "purged"]
    again = me.restore(item["id"])
    assert again.status_code == 409 and code(again) == "TRASH_ITEM_UNAVAILABLE"


# ------------------------------------------------------------------ activity and provenance


def events_of(fx: Fx, node: uuid.UUID) -> list[FileEvent]:
    return sorted((e for e in fx.w.db_rows(FileEvent) if e.node_id == node), key=lambda e: e.id)


def test_every_action_writes_one_event_with_actor_source_and_correlation(fx: Fx, me: P) -> None:
    d = nid(me.mkdir(fx.alice_root, "D"))
    f = nid(me.upload(d, "a.txt", b"abc"))
    me.rename(f, "b.txt", 1)
    other = nid(me.mkdir(fx.alice_root, "E"))
    me.move(f, other, 2)
    c = nid(me.copy(f, fx.alice_root))
    trash_it(me, f, 3)
    item = me.items()[0]["id"]
    me.restore(item)
    ops = {o.action: o for o in fx.w.db_rows(FileOperation)}
    assert [e.action for e in events_of(fx, d)] == ["folder_created"]
    assert [e.action for e in events_of(fx, f)] == [
        "uploaded",
        "renamed",
        "moved",
        "trashed",
        "restored",
    ]
    for e in events_of(fx, f):
        assert (e.actor_kind, e.actor_user_id, e.source) == ("user", me.uid, "ui")
        assert e.on_behalf_of_user_id is None and e.correlation_id is not None
    by_action = {e.action: e for e in events_of(fx, f)}
    assert by_action["renamed"].correlation_id == ops["rename"].id
    assert by_action["moved"].correlation_id == ops["move"].id
    assert by_action["trashed"].correlation_id == ops["trash"].id
    assert by_action["restored"].correlation_id == ops["restore"].id
    assert by_action["renamed"].details["old_name"] == "a.txt"
    assert by_action["renamed"].details["name"] == "b.txt"
    copied = events_of(fx, c)
    assert [e.action for e in copied] == ["copied"]
    assert copied[0].details["copied_from"] == str(f) and copied[0].correlation_id == ops["copy"].id
    # no host path, content or email anywhere in what was stored
    blob = repr([e.details for e in fx.w.db_rows(FileEvent)])
    assert str(fx.w.settings.library_path) not in blob and "@example.com" not in blob


def test_downloads_are_logged_only_when_they_succeed(fx: Fx, me: P) -> None:
    f = nid(me.upload(fx.alice_root, "a.txt", b"hello"))
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "viewer")
    t = nid(me.upload(fx.taxes, "t.txt", b"tax"))
    bob = P(fx, "bob")
    assert bob.download(f).status_code == 403  # refused
    disk(fx, f).rename(disk(fx, f).with_name("moved-away.txt"))
    assert me.download(f).status_code == 409  # failed: the content is missing
    disk(fx, f).with_name("moved-away.txt").rename(disk(fx, f))
    assert [e.action for e in events_of(fx, f)] == ["uploaded"]
    ok = me.download(f)
    assert ok.status_code == 200 and ok.content == b"hello"
    assert bob.download(t).content == b"tax"
    got = events_of(fx, f)[-1]
    assert (got.action, got.actor_user_id, got.details["name"], got.details["size"]) == (
        "downloaded",
        me.uid,
        "a.txt",
        5,
    )
    seen = bob.activity(t).json()["items"]
    assert [(e["action"], e["actor_name"]) for e in seen][0][0] == "downloaded"
    assert seen[0]["actor_name"] == "New"  # the display name, never the email
    assert "@" not in str(seen)


def test_external_changes_name_no_user(fx: Fx, me: P) -> None:
    from test_reconcile import scan

    f = nid(me.upload(fx.alice_root, "a.txt", b"one"))
    disk(fx, f).write_bytes(b"changed outside")
    scan(fx)
    last = events_of(fx, f)[-1]
    assert (last.action, last.actor_kind, last.actor_user_id) == (
        "external_modified",
        "external_filesystem",
        None,
    )
    shown = me.activity(f).json()["items"][0]
    assert shown["actor_name"] is None and shown["actor_kind"] == "external_filesystem"


def test_ai_agent_events_name_the_agent_and_the_user_and_obey_the_policy(fx: Fx) -> None:
    from app.domain import fileops
    from app.domain.fileops import OpCtx, OpError

    with fx.w.ctx.sessions() as db:
        ctx = OpCtx(
            db,
            fx.w.settings,
            fx.uid("alice@example.com"),
            actor_kind="ai_agent",
            proposal_id="prop-1",
        )
        out = fileops.create_folder(ctx, fx.alice_root, "FromAgent", None)
        node_id = out.node.id
    ev = events_of(fx, node_id)[0]
    assert (ev.action, ev.actor_kind, ev.actor_user_id) == ("folder_created", "ai_agent", None)
    assert ev.on_behalf_of_user_id == fx.uid("alice@example.com")
    assert ev.details["proposal_id"] == "prop-1" and ev.source == "ai"
    shown = P(fx, "alice").activity(node_id).json()["items"][0]
    assert shown["actor_kind"] == "ai_agent" and shown["actor_name"] == "New"
    assert shown["details"]["proposal_id"] == "prop-1"
    # the agent has exactly the user's rights: Bob has none in Alice's folder
    with fx.w.ctx.sessions() as db:
        ctx = OpCtx(
            db, fx.w.settings, fx.uid("bob@example.com"), actor_kind="ai_agent", proposal_id="p"
        )
        with pytest.raises(OpError) as err:
            fileops.create_folder(ctx, fx.alice_root, "Nope", None)
    assert err.value.status == 403
    assert not [e for e in fx.w.db_rows(FileEvent) if e.details.get("name") == "Nope"]


def test_folder_activity_lists_descendants_and_needs_read(fx: Fx, me: P) -> None:
    sub = nid(me.mkdir(fx.taxes, "Sub"))
    f = nid(me.upload(sub, "a.txt", b"a"))
    secret = nid(me.upload(fx.private, "p.txt", b"p"))
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "viewer")
    bob, dave = P(fx, "bob"), P(fx, "dave")
    folder = bob.activity(fx.taxes).json()["items"]
    assert {e["node_id"] for e in folder} == {str(sub), str(f)}
    assert str(secret) not in str(folder)  # a sibling tree: no counts, names or ids
    assert bob.activity(secret).status_code == 403 and dave.activity(f).status_code == 403
    assert bob.activity(fx.private).status_code == 403
    page = me.activity(fx.taxes, limit=1).json()
    assert len(page["items"]) == 1 and page["next_before"] is not None
    more = me.activity(fx.taxes, before=page["next_before"]).json()["items"]
    assert page["items"][0]["id"] > more[0]["id"]  # newest first, no overlap
    # trashing keeps the history readable to someone with read on the folder
    trash_it(me, f)
    assert "trashed" in [e["action"] for e in bob.activity(fx.taxes).json()["items"]]


def test_history_follows_a_node_through_rename_and_move(fx: Fx, me: P) -> None:
    f = nid(me.upload(fx.alice_root, "a.txt", b"a"))
    dest = nid(me.mkdir(fx.alice_root, "Dest"))
    me.rename(f, "b.txt", 1)
    me.move(f, dest, 2)
    items = me.activity(f).json()["items"]
    assert [e["action"] for e in items] == ["moved", "renamed", "uploaded"]
    assert items[0]["details"]["to_parent_id"] == str(dest)


# ------------------------------------------------------------------ authorization on trash


def test_trash_visibility_per_adr_017(fx: Fx, me: P) -> None:
    fx.grant(fx.alice, fx.taxes, "carol@example.com", "editor")
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "viewer")
    fx.grant(fx.alice, fx.taxes, "admin2@example.com", "editor")
    carol, bob, dave, _admin2 = P(fx, "carol"), P(fx, "bob"), P(fx, "dave"), P(fx, "admin2")
    mine = nid(me.upload(fx.alice_root, "mine.txt", b"m"))
    shared_by_carol = nid(carol.upload(fx.taxes, "c.txt", b"c"))
    by_alice = nid(me.upload(fx.taxes, "t.txt", b"t"))
    mine_item = trash_it(me, mine)["item"]["id"]
    carol_item = trash_it(carol, shared_by_carol)["item"]["id"]
    alice_item = trash_it(me, by_alice)["item"]["id"]
    # the owner sees everything in the root; an editor only what they trashed themselves
    assert {i["id"] for i in me.items()} == {mine_item, carol_item, alice_item}
    assert [i["id"] for i in carol.items()] == [carol_item]
    for stranger in (bob, dave):  # a viewer has no write; an unrelated user has nothing
        assert stranger.items() == []
        for item in (mine_item, carol_item, alice_item):
            assert stranger.restore(item).status_code == 403
            assert stranger.purge(item, "x").status_code == 403
    admin = P(fx, "admin")  # an administrator is not special in a personal root
    assert admin.items() == [] and admin.restore(mine_item).status_code == 403
    # who deleted it is shown by name; the restored file goes back where it was
    got = next(i for i in me.items() if i["id"] == carol_item)
    assert got["trashed_by_name"] == "New" and "@" not in str(got)
    assert carol.restore(mine_item).status_code == 403  # Alice's own item is not Carol's to restore
    assert carol.restore(carol_item).status_code == 200
    # losing write on the original folder takes the item out of sight
    fx.grant(fx.alice, fx.taxes, "admin2@example.com", "viewer")
    other = nid(carol.upload(fx.taxes, "again.txt", b"x"))
    trash_it(carol, other)
    fx.grant(fx.alice, fx.taxes, "carol@example.com", "viewer")
    assert carol.items() == []
    # the shared tree: anyone who can still write the original folder sees the item
    sf = nid(P(fx, "alice").upload(fx.shared_sub, "s.txt", b"s"))
    s_item = trash_it(P(fx, "alice"), sf)["item"]["id"]
    assert [i["id"] for i in P(fx, "bob").items(root_id=str(fx.shared_root))] == [s_item]
    assert [i["id"] for i in dave.items(root_id=str(fx.shared_root))] == [s_item]


def test_empty_trash_and_the_admin_storage_page(fx: Fx, me: P) -> None:
    a = nid(me.upload(fx.alice_root, "a.txt", b"12345"))
    b = nid(me.upload(fx.alice_root, "b.txt", b"123"))
    trash_it(me, a)
    trash_it(me, b)
    listing = me.trash_list()
    mine = next(r for r in listing["roots"] if r["root_node_id"] == str(fx.alice_root))
    assert (mine["item_count"], mine["total_size"]) == (2, 8)
    # only the manager of that root can empty it, and only with the token that states it
    bob = P(fx, "bob")
    assert bob.post(f"/roots/{fx.alice_root}/trash/empty", json={"confirm": "x"}).status_code == 403
    admin = P(fx, "admin")
    assert (
        admin.post(f"/roots/{fx.alice_root}/trash/empty", json={"confirm": "x"}).status_code == 403
    )
    r = me.post(f"/roots/{fx.alice_root}/trash/empty", json={"confirm": "stale"})
    assert r.status_code == 409 and code(r) == "CONFIRMATION_REQUIRED"
    # the administrator sees counts and sizes per root and nothing else
    overview = admin.get("/admin/storage").json()
    row = next(p for p in overview["personal_roots"] if p["user_id"] == str(me.uid))
    assert (row["trash_items"], row["trash_bytes"]) == (2, 8)
    assert "a.txt" not in str(overview)
    done = me.post(f"/roots/{fx.alice_root}/trash/empty", json={"confirm": mine["empty_token"]})
    assert done.status_code == 200 and done.json() == {"purged": 2, "failed": 0, "bytes": 8}
    assert me.items() == [] and {node_row(fx, n).status for n in (a, b)} == {"purged"}
    assert not any((trash_dir(fx) / str(node_row(fx, a).root_id)).iterdir())


# ------------------------------------------------------------------ retention


def advance_days(fx: Fx, days: float) -> None:
    fx.w.advance(days=days)


def purge_run(fx: Fx) -> dict[str, int]:
    return trash.purge_due(fx.w.ctx.sessions, fx.w.settings)


def test_purge_at_the_deadline_and_not_before(fx: Fx, me: P) -> None:
    f = nid(me.upload(fx.alice_root, "a.txt", b"x"))
    item = trash_it(me, f)["item"]["id"]
    advance_days(fx, 29)
    assert purge_run(fx)["purged"] == 0
    assert me.items()[0]["days_left"] == 0 or me.items()[0]["expiring"]
    advance_days(fx, 1.01)
    counts = purge_run(fx)
    assert counts["purged"] == 1 and counts["failed"] == 0 and counts["bytes"] == 1
    assert node_row(fx, f).status == "purged"
    assert not item_path(fx, item, "a.txt").exists()
    events = [e for e in events_of(fx, f) if e.action == "purged"]
    assert (
        len(events) == 1
        and events[0].actor_kind == "system"
        and events[0].details["how"] == "retention"
    )
    assert events[0].actor_user_id is None
    from app.db.models import AuthAudit

    assert [a for a in fx.w.db_rows(AuthAudit) if a.action == "trash.purge_run"][0].details[
        "purged"
    ] == 1  # an aggregate line: counts, no names
    assert "a.txt" not in str([a.details for a in fx.w.db_rows(AuthAudit)])


def test_retention_changes_never_shorten_and_zero_means_never(fx: Fx, me: P) -> None:
    admin = P(fx, "admin")
    f = nid(me.upload(fx.alice_root, "a.txt", b"x"))
    trash_it(me, f)
    first = me.items()[0]["purge_after"]
    assert (
        admin.client.put(
            "/api/v1/admin/storage/trash-retention", json={"days": 7}, headers=admin.headers
        ).status_code
        == 200
    )
    assert me.items()[0]["purge_after"] == first  # lowering does not shorten existing items
    advance_days(fx, 8)
    assert purge_run(fx)["purged"] == 0
    g = nid(me.upload(fx.alice_root, "g.txt", b"y"))
    trash_it(me, g)
    advance_days(fx, 8)
    assert purge_run(fx)["purged"] == 1  # the new item follows the new setting; the old one waits
    put = lambda days: admin.client.put(  # noqa: E731
        "/api/v1/admin/storage/trash-retention", json={"days": days}, headers=admin.headers
    )
    assert put(3).status_code == 422 and put(4000).status_code == 422
    assert put(0).status_code == 200
    advance_days(fx, 400)
    assert purge_run(fx)["purged"] == 0  # 0 = never purge automatically
    assert me.items()[0]["purge_after"] is not None
    assert put(30).status_code == 200  # turning it back on gives a fresh date, not a surprise
    assert purge_run(fx)["purged"] == 0
    assert me.items()[0]["purge_after"] > "2026"
    new_item = me.trash_list()
    assert new_item["retention_days"] == 30


def test_a_setting_that_cannot_be_read_purges_nothing_and_an_unhealthy_trash_too(
    fx: Fx, me: P
) -> None:
    from app.db.models import AppSetting

    f = nid(me.upload(fx.alice_root, "a.txt", b"x"))
    trash_it(me, f)
    advance_days(fx, 90)
    with fx.w.ctx.sessions() as db:
        db.add(AppSetting(key=trash.RETENTION_KEY, value="soon"))
        db.commit()
    assert purge_run(fx)["purged"] == 0
    with fx.w.ctx.sessions() as db:
        row = db.get(AppSetting, trash.RETENTION_KEY)
        assert row is not None
        row.value = 30
        db.commit()
    trash_dir(fx).rename(trash_dir(fx).with_name("moved-aside"))
    os.symlink(trash_dir(fx).with_name("moved-aside"), trash_dir(fx))
    assert purge_run(fx)["purged"] == 0
    assert node_row(fx, f).status == "trashed"
    trash_dir(fx).unlink()
    trash_dir(fx).with_name("moved-aside").rename(trash_dir(fx))
    assert purge_run(fx)["purged"] == 1


def test_expiring_items_are_marked_for_the_last_seven_days(fx: Fx, me: P) -> None:
    f = nid(me.upload(fx.alice_root, "a.txt", b"x"))
    trash_it(me, f)
    advance_days(fx, 23)
    item = me.items()[0]
    assert item["expiring"] is True and item["days_left"] == 7
    advance_days(fx, 3)
    assert me.items()[0]["days_left"] == 4


# ------------------------------------------------------------------ route inventory


def test_trash_routes_exist_and_are_all_policy_guarded(fx: Fx) -> None:
    from test_acl import _api_routes, _guards

    routes = {
        (m, r.path): r
        for r in _api_routes(fx.w.app.routes)
        for m in r.methods
        if r.path.startswith("/api/v1/")
    }
    expected = [
        ("DELETE", "/api/v1/nodes/{node_id}"),
        ("GET", "/api/v1/trash"),
        ("POST", "/api/v1/trash/{item_id}/restore"),
        ("DELETE", "/api/v1/trash/{item_id}"),
        ("POST", "/api/v1/roots/{node_id}/trash/empty"),
        ("GET", "/api/v1/nodes/{node_id}/activity"),
        ("GET", "/api/v1/activity/mine"),
        ("POST", "/api/v1/operations/{operation_id}/undo"),
        ("POST", "/api/v1/imports/{import_id}/undo"),
        ("PUT", "/api/v1/admin/storage/trash-retention"),
    ]
    for key in expected:
        assert key in routes and _guards(routes[key].dependant), key


def test_trash_routes_keep_csrf_and_auth(fx: Fx, me: P) -> None:
    f = nid(me.upload(fx.alice_root, "a.txt", b"x"))
    no_csrf = me.client.delete(f"/api/v1/nodes/{f}", params={"expected_revision": 1})
    assert no_csrf.status_code == 403 and code(no_csrf) == "CSRF_TOKEN_INVALID"
    anon = fx.w.browser()
    assert anon.get("/api/v1/trash").status_code == 401
    assert anon.get(f"/api/v1/nodes/{f}/activity").status_code == 401
    assert anon.get("/api/v1/activity/mine").status_code == 401
    assert node_row(fx, f).status == "active"


def test_scans_and_backups_ignore_the_trash(fx: Fx, me: P) -> None:
    from test_reconcile import scan

    d = nid(me.mkdir(fx.alice_root, "D"))
    f = nid(me.upload(d, "a.txt", b"x"))
    trash_it(me, d)
    result = scan(fx)
    assert result.outcome == "ok" and result.counts.get("missing", 0) == 0
    assert node_row(fx, d).status == "trashed" and node_row(fx, f).status == "trashed"
    assert not [e for e in fx.w.db_rows(FileEvent) if e.actor_kind == "external_filesystem"]
    # something new appearing at the old place is an ordinary new entry, not the trashed one
    disk(fx, fx.alice_root).joinpath("D").mkdir()
    scan(fx)
    again = [n for n in fx.w.db_rows(Node) if n.name == "D" and n.status == "active"]
    assert len(again) == 1 and again[0].id != d
    item = me.items()[0]["id"]
    r = me.restore(item, on_conflict="keep_both")
    assert r.status_code == 200 and r.json()["node"]["name"] == "D (2)"


def test_a_download_whose_first_read_fails_is_not_logged(
    fx: Fx, me: P, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.storage import files as storage_files

    f = nid(me.upload(fx.alice_root, "a.txt", b"hello"))

    def broken(fileobj: Any, limit: int) -> Any:
        fileobj.close()
        raise OSError(5, "read error")
        yield b""  # pragma: no cover

    monkeypatch.setattr(storage_files, "read_chunks", broken)
    with pytest.raises(OSError):
        me.download(f)
    monkeypatch.undo()
    assert [e.action for e in events_of(fx, f)] == ["uploaded"]
    assert me.download(f).content == b"hello"
    assert [e.action for e in events_of(fx, f)] == ["uploaded", "downloaded"]
