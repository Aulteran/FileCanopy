"""M08 sharing: viewer is read-only through every write route, ACL combinatorics for grant
creation, share events in the activity log, notification leakage, people picker, leaving a share,
and reference filtering (the rule collections and search will use)."""

import itertools
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from test_acl import NONE, Fx
from test_auth import PASSWORD, World
from test_files import Api

from app.db.models import AuthAudit, FileEvent, Node
from app.policy import engine
from app.policy.model import Actor


@pytest.fixture
def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., World]:
    def factory(**overrides: Any) -> World:
        return World(tmp_path, monkeypatch, **overrides)

    return factory


@pytest.fixture
def fx(make_world: Callable[..., World]) -> Fx:
    return Fx(make_world())


def put(api: Api, node: uuid.UUID, who: uuid.UUID, level: str) -> Any:
    return api.client.put(
        f"/api/v1/nodes/{node}/grants/{who}", json={"level": level}, headers=api.headers
    )


def test_viewer_cannot_write_through_any_route(fx: Fx) -> None:
    alice, bob = Api(fx, "alice"), Api(fx, "bob")
    f = alice.upload(fx.y2026, "a.txt", b"hello").json()["node"]["id"]
    assert put(alice, fx.taxes, bob.uid, "viewer").status_code == 200
    assert bob.download(uuid.UUID(f)).status_code == 200  # reading works
    assert bob.mkdir(fx.taxes, "x").status_code == 403
    assert bob.upload(fx.taxes, "b.txt", b"no").status_code == 403
    assert bob.rename(uuid.UUID(f), "z.txt", 1).status_code == 403
    assert bob.move(uuid.UUID(f), fx.other, 1).status_code == 403
    assert bob.post(f"/nodes/{f}/copy", json={"target_parent_id": str(fx.other)}).status_code == 403
    assert bob.client.delete(f"/api/v1/nodes/{f}", headers=bob.headers).status_code == 403
    assert bob.client.get(f"/api/v1/nodes/{fx.taxes}/grants").status_code == 403
    assert put(bob, fx.taxes, fx.uid("carol@example.com"), "viewer").status_code == 403
    # An upgrade to editor makes the same calls succeed, a downgrade stops them at once.
    put(alice, fx.taxes, bob.uid, "editor")
    assert bob.mkdir(fx.taxes, "x").status_code == 201
    put(alice, fx.taxes, bob.uid, "viewer")
    assert bob.mkdir(fx.taxes, "y").status_code == 403


@pytest.mark.parametrize(
    ("caller", "target", "ok"),
    [
        ("alice", "taxes", True),
        ("alice", "y2026", True),
        ("alice", "alice_root", True),
        ("bob", "taxes", False),  # viewer
        ("carol", "taxes", False),  # editor cannot re-share
        ("dave", "taxes", False),  # no access at all
        ("admin", "taxes", False),  # administrator has no say over personal folders
        ("admin", "shared_sub", True),  # but does administer the shared tree
        ("bob", "shared_sub", False),
    ],
)
def test_who_may_share_combinatorics(fx: Fx, caller: str, target: str, ok: bool) -> None:
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "viewer")
    fx.grant(fx.alice, fx.taxes, "carol@example.com", "editor")
    nodes = {
        "taxes": fx.taxes,
        "y2026": fx.y2026,
        "alice_root": fx.alice_root,
        "shared_sub": fx.shared_sub,
    }
    api = Api(fx, caller)
    r = put(api, nodes[target], fx.uid("eve@example.com"), "viewer")
    assert (r.status_code == 200) is ok, r.text
    if not ok:
        assert r.json() == {"detail": {"code": "FORBIDDEN"}}


def test_sharing_a_nested_folder_reveals_no_siblings_or_parents(fx: Fx) -> None:
    alice, bob = Api(fx, "alice"), Api(fx, "bob")
    put(alice, fx.y2026, bob.uid, "viewer")
    roots = [e for e in bob.get("/roots").json() if e["kind"] == "shared_with_me"]
    assert [e["node_id"] for e in roots] == [str(fx.y2026)]
    assert roots[0]["owner_name"]
    assert bob.get(f"/nodes/{fx.y2026}").json()["parent_id"] is None
    for hidden in (fx.taxes, fx.other, fx.private, fx.alice_root):
        assert bob.get(f"/nodes/{hidden}").status_code == 403


def test_share_changes_are_in_the_folder_history_for_the_owner_only(fx: Fx) -> None:
    alice, bob, carol = Api(fx, "alice"), Api(fx, "bob"), Api(fx, "carol")
    put(alice, fx.taxes, bob.uid, "viewer")
    put(alice, fx.taxes, carol.uid, "viewer")
    put(alice, fx.taxes, bob.uid, "viewer")  # unchanged: no new event
    put(alice, fx.taxes, bob.uid, "editor")
    alice.client.delete(f"/api/v1/nodes/{fx.taxes}/grants/{bob.uid}", headers=alice.headers)
    mine = alice.get(f"/nodes/{fx.taxes}/activity").json()["items"]
    actions = [
        e["action"]
        for e in mine
        if e["action"].startswith("share") or e["action"] in ("shared", "unshared")
    ]
    assert actions == ["unshared", "share_changed", "shared", "shared"]
    assert {e["details"].get("grantee_name") for e in mine if "grantee_name" in e["details"]}
    with fx.w.ctx.sessions() as db:
        assert db.scalars(select(FileEvent.action).where(FileEvent.node_id == fx.taxes)).all()
        audits = db.scalars(select(AuthAudit.action)).all()
    assert "grant.set" in audits and "grant.revoked" in audits
    # A reader (carol, viewer) sees the folder's history but not who else has access.
    seen = carol.get(f"/nodes/{fx.taxes}/activity").json()["items"]
    assert not [e for e in seen if e["action"] in ("shared", "share_changed", "unshared")]
    assert "bob" not in str(seen).lower()


def test_notifications_name_the_sharer_and_never_leak_after_revocation(fx: Fx) -> None:
    alice, bob, admin = Api(fx, "alice"), Api(fx, "bob"), Api(fx, "admin")
    put(alice, fx.taxes, bob.uid, "editor")
    body = bob.get("/notifications").json()
    assert body["unread"] == 1
    n = body["items"][0]
    assert (n["kind"], n["node_name"], n["level"]) == ("shared", "Taxes", "editor")
    assert n["actor_name"] and "@" not in str(body)
    # Nobody else is told: not the administrator, not an unrelated member.
    assert admin.get("/notifications").json() == {"unread": 0, "items": []}
    assert Api(fx, "dave").get("/notifications").json()["items"] == []
    # Marking read is per person; another person cannot touch it.
    assert Api(fx, "dave").post(f"/notifications/{n['id']}/read").status_code == 404
    assert bob.post(f"/notifications/{n['id']}/read").status_code == 200
    assert bob.get("/notifications").json()["unread"] == 0
    # After revocation the old notice no longer names the folder; the new one names nothing.
    alice.client.delete(f"/api/v1/nodes/{fx.taxes}/grants/{bob.uid}", headers=alice.headers)
    items = bob.get("/notifications").json()["items"]
    assert {i["kind"] for i in items} == {"shared", "unshared"}
    assert all(i["node_name"] is None and i["node_id"] is None for i in items)
    assert "Taxes" not in str(items)


def test_admin_gets_no_notice_and_no_activity_for_personal_shares(fx: Fx) -> None:
    alice, admin = Api(fx, "alice"), Api(fx, "admin")
    put(alice, fx.taxes, Api(fx, "bob").uid, "viewer")
    assert admin.get("/notifications").json()["items"] == []
    assert admin.get(f"/nodes/{fx.taxes}/activity").status_code == 403
    assert admin.get("/people?q=alice").status_code == 200  # names only, no content


def test_people_picker_lists_names_only(fx: Fx) -> None:
    alice = Api(fx, "alice")
    everyone = alice.get("/people").json()
    assert all(set(p) == {"id", "name", "hint"} for p in everyone)
    assert str(alice.uid) not in str(everyone)  # not yourself
    assert not any("@" in str(p["name"]) for p in everyone)
    assert all(p["hint"] is None or "***@" in p["hint"] for p in everyone)
    assert "alice@example" not in str(everyone)
    assert alice.get("/people", params={"q": "zzz-nobody"}).json() == []
    # A disabled account cannot be found or shared with.
    c, h = fx.admin
    eve = fx.uid("eve@example.com")
    c.patch(f"/api/v1/admin/users/{eve}", json={"status": "disabled"}, headers=h)
    assert str(eve) not in str(alice.get("/people").json())
    assert put(alice, fx.taxes, eve, "viewer").status_code == 422


def test_leaving_a_share(fx: Fx) -> None:
    alice, bob = Api(fx, "alice"), Api(fx, "bob")
    put(alice, fx.taxes, bob.uid, "viewer")
    leave = bob.client.delete(f"/api/v1/nodes/{fx.taxes}/grants-mine", headers=bob.headers)
    assert leave.status_code == 200
    assert bob.get(f"/nodes/{fx.taxes}").status_code == 403
    kinds = [i["kind"] for i in alice.get("/notifications").json()["items"]]
    assert kinds == ["share_left"]
    acts = [e["action"] for e in alice.get(f"/nodes/{fx.taxes}/activity").json()["items"]]
    assert "share_left" in acts
    # Not a share you hold, the owner's own folder and the shared tree cannot be "left".
    assert (
        bob.client.delete(f"/api/v1/nodes/{fx.taxes}/grants-mine", headers=bob.headers).status_code
        == 403
    )
    r = alice.client.delete(f"/api/v1/nodes/{fx.taxes}/grants-mine", headers=alice.headers)
    assert r.status_code == 422
    r = bob.client.delete(f"/api/v1/nodes/{fx.shared_sub}/grants-mine", headers=bob.headers)
    assert r.status_code == 422


def test_shared_with_count_only_for_those_who_manage_sharing(fx: Fx) -> None:
    alice, bob = Api(fx, "alice"), Api(fx, "bob")
    put(alice, fx.taxes, bob.uid, "viewer")
    put(alice, fx.taxes, Api(fx, "carol").uid, "editor")
    assert alice.get(f"/nodes/{fx.taxes}").json()["shared_with"] == 2
    assert bob.get(f"/nodes/{fx.taxes}").json()["shared_with"] is None


def test_reference_filter_never_reveals_more_than_the_node_policy(fx: Fx) -> None:
    """Placeholder for collections (M-later): any list of node references is cut to what the
    reader can already see, so membership of a collection never widens access."""
    fx.grant(fx.alice, fx.y2026, "bob@example.com", "viewer")
    refs = [fx.taxes, fx.y2026, fx.private, fx.other, uuid.uuid4()]
    with fx.w.ctx.sessions() as db:
        bob = Actor(fx.uid("bob@example.com"))
        assert engine.filter_visible(db, bob, refs) == [fx.y2026]
        assert engine.filter_visible(db, Actor(fx.uid("alice@example.com")), refs) == refs[:4]
        assert engine.filter_visible(db, Actor(fx.uid("admin@example.com")), refs) == []


def test_grant_matrix_is_consistent_for_every_pair(fx: Fx) -> None:
    levels = ["viewer", "editor"]
    alice = Api(fx, "alice")
    for who, level in itertools.product(["bob", "carol"], levels):
        email = f"{who}@example.com"
        assert put(alice, fx.taxes, fx.uid(email), level).status_code == 200
        with fx.w.ctx.sessions() as db:
            privs = engine.effective(db, Actor(fx.uid(email)), db.get(Node, fx.y2026))
        assert ("write" in privs) is (level == "editor")
        assert privs != NONE


def test_people_with_the_same_name_get_a_masked_hint(fx: Fx) -> None:
    c, h = fx.admin
    for mail in ("sam.one@example.com", "sam.two@example.com"):
        r = c.post(
            "/api/v1/admin/users",
            json={"email": mail, "name": "Sam", "role": "member"},
            headers=h,
        )
        assert r.status_code == 201, r.text
        accept = {"token": r.json()["token"]["token"], "password": PASSWORD}
        done = fx.w.browser().post("/api/v1/auth/invitations/accept", json=accept)
        assert done.status_code == 200, done.text
    found = Api(fx, "alice").get("/people", params={"q": "sam"}).json()
    assert sorted(p["hint"] for p in found) == ["s***@example.com", "s***@example.com"]
    assert "sam.one" not in str(found)
