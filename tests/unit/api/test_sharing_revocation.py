"""M08 review additions: access that comes from a parent share, move/copy impact, and downgrades
or revocation in the middle of work (upload batches, restore from trash, undo)."""

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from test_acl import Fx
from test_auth import World
from test_imports import manifest
from test_trash import P, nid

from app.db.models import Node


@pytest.fixture
def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., World]:
    def factory(**overrides: Any) -> World:
        return World(tmp_path, monkeypatch, **overrides)

    return factory


@pytest.fixture
def fx(make_world: Callable[..., World]) -> Fx:
    return Fx(make_world(session_idle_seconds=10**9, session_absolute_seconds=10**9))


def share(owner: P, node: uuid.UUID, who: P, level: str) -> None:
    r = owner.client.put(
        f"/api/v1/nodes/{node}/grants/{who.uid}", json={"level": level}, headers=owner.headers
    )
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ inherited access


def test_a_subfolder_lists_access_inherited_from_a_parent_share(fx: Fx) -> None:
    alice, bob, carol = P(fx, "alice"), P(fx, "bob"), P(fx, "carol")
    share(alice, fx.taxes, bob, "viewer")
    share(alice, fx.y2026, carol, "editor")
    rows = alice.get(f"/nodes/{fx.y2026}/grants").json()
    by_name = {r["grantee_name"]: r for r in rows}
    assert by_name[bob.client.get("/api/v1/auth/session").json()["user"]["name"]] is not None
    inherited = [r for r in rows if r["inherited_from_id"]]
    direct = [r for r in rows if not r["inherited_from_id"]]
    assert [r["grantee_user_id"] for r in inherited] == [str(bob.uid)]
    assert inherited[0]["inherited_from_name"] == "Taxes" and inherited[0]["level"] == "viewer"
    assert [r["grantee_user_id"] for r in direct] == [str(carol.uid)]
    assert alice.get(f"/nodes/{fx.y2026}").json()["shared_with"] == 2
    # The parent itself lists only its own grant; a viewer cannot see any of this.
    assert len(alice.get(f"/nodes/{fx.taxes}/grants").json()) == 1
    assert bob.get(f"/nodes/{fx.y2026}/grants").status_code == 403
    assert bob.get(f"/nodes/{fx.y2026}").json()["shared_with"] is None


# ------------------------------------------------------------------ who gains or loses access


def impact(p: P, node: uuid.UUID, target: uuid.UUID, **kw: Any) -> dict[str, Any]:
    r = p.get(f"/nodes/{node}/move-impact", params={"target": str(target), **kw})
    assert r.status_code == 200, r.text
    return dict(r.json())


def test_move_impact_names_who_gains_and_loses(fx: Fx) -> None:
    alice, bob = P(fx, "alice"), P(fx, "bob")
    share(alice, fx.taxes, bob, "viewer")
    secret = nid(alice.mkdir(fx.private, "Letters"))
    into = impact(alice, secret, fx.taxes)  # private folder into a shared one
    assert into["gain"]["count"] == 1 and into["gain"]["names"] and not into["lose"]["count"]
    out = impact(alice, fx.y2026, fx.private)  # shared subfolder out to a private one
    assert out["lose"]["count"] == 1 and not out["gain"]["count"]
    assert impact(alice, fx.y2026, fx.other)["gain"]["count"] == 0  # within the same share
    copy = impact(alice, secret, fx.taxes, copy=True)
    assert copy["gain"]["count"] == 1 and copy["lose"]["count"] == 0
    # To another area: the grants stay on the original that goes to the trash.
    gone = impact(alice, fx.taxes, fx.shared_sub)
    # Bob has access to the shared tree too, so he keeps seeing it; the others gain it. Alice does
    # not manage the shared tree, so she is told that people are affected, not who.
    assert gone["lose"]["count"] == 0 and gone["gain"]["count"] >= 1 and gone["gain"]["hidden"]


def test_move_impact_hides_names_from_people_who_do_not_manage_the_sharing(fx: Fx) -> None:
    alice, bob = P(fx, "alice"), P(fx, "bob")
    share(alice, fx.taxes, bob, "editor")
    share(alice, fx.taxes, P(fx, "carol"), "viewer")
    mine = nid(bob.mkdir(fx.root_of("bob@example.com"), "Bob stuff"))
    r = impact(bob, mine, fx.taxes)  # Bob may write here but does not manage the share
    assert r["gain"]["hidden"] is True and r["gain"]["names"] == []
    # A folder Bob cannot write to is refused outright, as is an unknown target.
    assert (
        bob.get(f"/nodes/{mine}/move-impact", params={"target": str(fx.private)}).status_code == 403
    )
    assert (
        bob.get(f"/nodes/{mine}/move-impact", params={"target": str(uuid.uuid4())}).status_code
        == 403
    )


# ------------------------------------------------------------------ viewer, on the shared folder


def test_viewer_cannot_copy_upload_or_trash_in_the_shared_folder_itself(fx: Fx) -> None:
    alice, bob = P(fx, "alice"), P(fx, "bob")
    f = nid(alice.upload(fx.taxes, "a.txt", b"x"))
    share(alice, fx.taxes, bob, "viewer")
    assert bob.post(f"/nodes/{f}/copy", json={"target_parent_id": str(fx.y2026)}).status_code == 403
    r = bob.post(f"/nodes/{fx.taxes}/folder-uploads", json={"entries": manifest({"d/x": b"1"})})
    assert r.status_code == 403
    assert bob.delete(fx.taxes, 1).status_code == 403  # the grant root itself
    assert bob.delete(f, 1).status_code == 403


# ------------------------------------------------------------------ downgrade in the middle of work


def test_downgrade_stops_a_started_upload_batch(fx: Fx) -> None:
    alice, bob = P(fx, "alice"), P(fx, "bob")
    share(alice, fx.taxes, bob, "editor")
    files = {"Batch/a.txt": b"a"}
    batch = bob.post(f"/nodes/{fx.taxes}/folder-uploads", json={"entries": manifest(files)})
    assert batch.status_code == 201, batch.text
    bid = batch.json()["id"]
    share(alice, fx.taxes, bob, "viewer")  # downgraded before anything was sent
    started = bob.post(f"/imports/{bid}/start")
    entries = bob.get(f"/imports/{bid}/entries").json()["items"]
    sent = [
        bob.client.put(
            f"/api/v1/imports/{bid}/entries/{e['index']}", content=b"a", headers=bob.headers
        ).status_code
        for e in entries
        if e["kind"] == "file"
    ]
    assert started.status_code != 200 or all(code != 200 for code in sent)
    assert all(code != 200 for code in sent)
    kids = {c["name"] for c in alice.children(fx.taxes)}
    assert "Batch" not in kids


def test_downgrade_blocks_restore_and_undo(fx: Fx) -> None:
    alice, bob = P(fx, "alice"), P(fx, "bob")
    share(alice, fx.taxes, bob, "editor")
    f = nid(bob.upload(fx.taxes, "mine.txt", b"x"))
    trashed = bob.delete(f, 1)
    assert trashed.status_code == 200, trashed.text
    op = trashed.json()["operation"]["id"]
    item = bob.items()[0]["id"]
    made = nid(bob.mkdir(fx.taxes, "NewFolder"))
    made_op = bob.get(f"/nodes/{made}/activity").json()["items"][0]
    assert made_op
    share(alice, fx.taxes, bob, "viewer")  # downgraded after making changes
    assert bob.restore(item).status_code == 403
    assert bob.undo(op).status_code in (403, 409)
    with fx.w.ctx.sessions() as db:
        assert db.get(Node, f).status == "trashed"  # type: ignore[union-attr]
    # Revoked altogether: nothing of the shared folder is reachable any more.
    bob_c = bob.client
    alice.client.delete(f"/api/v1/nodes/{fx.taxes}/grants/{bob.uid}", headers=alice.headers)
    assert bob_c.get(f"/api/v1/nodes/{fx.taxes}").status_code == 403
    assert bob.restore(item).status_code == 403
