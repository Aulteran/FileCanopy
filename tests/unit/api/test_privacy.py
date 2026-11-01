"""M09: privacy labels, the Confidential lock (fake clock), subtree rules, AI routing."""

import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from test_acl import Fx
from test_auth import PASSWORD, World
from test_files import Api, code

from app.db.models import ConfidentialUnlock, Node
from app.policy import engine, privacy
from app.policy.model import Actor

INTERACTIVE = {"X-FC-Interactive": "1"}


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fx:
    return Fx(World(tmp_path, monkeypatch))


def label(api: Api, node: uuid.UUID, value: str, **extra: Any) -> Any:
    return api.client.put(
        f"/api/v1/nodes/{node}/privacy", json={"label": value, **extra}, headers=api.headers
    )


def unlock(api: Api, node: uuid.UUID, password: str = PASSWORD) -> Any:
    return api.post(f"/nodes/{node}/unlock", json={"password": password})


def kids(api: Api, node: uuid.UUID) -> Any:
    return api.get(f"/nodes/{node}/children")


def grant(api: Api, node: uuid.UUID, who: uuid.UUID) -> Any:
    return api.client.put(
        f"/api/v1/nodes/{node}/grants/{who}", json={"level": "viewer"}, headers=api.headers
    )


def upload_id(api: Api, parent: uuid.UUID, name: str) -> uuid.UUID:
    r = api.upload(parent, name, b"classified")
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["node"]["id"])


def test_label_locks_content_but_not_the_folder_itself(fx: Fx) -> None:
    alice = Api(fx, "alice")
    fid = upload_id(alice, fx.y2026, "secret.txt")
    assert label(alice, fx.y2026, "confidential").status_code == 200
    meta = alice.get(f"/nodes/{fx.y2026}").json()
    assert meta["locked"] and meta["privacy_effective"] == "confidential"
    assert meta["my_privileges"] == ["traverse"]
    r = alice.download(fid)
    assert r.status_code == 403 and code(r) == "LOCKED"
    assert kids(alice, fx.y2026).status_code == 403
    assert unlock(alice, fx.y2026, "wrong-password").status_code == 403
    assert unlock(alice, fx.y2026).status_code == 200
    assert alice.download(fid).content == b"classified"
    assert kids(alice, fx.y2026).status_code == 200


def test_private_label_inherits_and_needs_no_unlock(fx: Fx) -> None:
    alice = Api(fx, "alice")
    assert label(alice, fx.taxes, "private").status_code == 200
    upload_id(alice, fx.y2026, "a.txt")
    assert alice.get(f"/nodes/{fx.y2026}").json()["privacy_effective"] == "private"
    assert code(unlock(alice, fx.y2026)) == "NOT_CONFIDENTIAL"


def test_idle_lock_only_interactive_requests_renew(fx: Fx) -> None:
    alice = Api(fx, "alice")
    fid = upload_id(alice, fx.y2026, "s.txt")
    label(alice, fx.y2026, "confidential")
    unlock(alice, fx.y2026)
    fx.w.offset += timedelta(seconds=299)
    assert alice.download(fid).status_code == 200  # no header: does not renew
    fx.w.offset += timedelta(seconds=2)  # 301 s since the unlock
    assert alice.download(fid).status_code == 403
    unlock(alice, fx.y2026)
    fx.w.offset += timedelta(seconds=200)
    assert alice.get(f"/nodes/{fid}/content", headers=INTERACTIVE).status_code == 200
    fx.w.offset += timedelta(seconds=200)
    assert alice.download(fid).status_code == 200  # 200 s since the renewal
    fx.w.offset += timedelta(seconds=301)
    # expired: an interactive request at 5:01 is denied and does not revive the unlock
    assert alice.get(f"/nodes/{fid}/content", headers=INTERACTIVE).status_code == 403
    assert alice.download(fid).status_code == 403


def test_other_session_and_jobs_are_locked(fx: Fx) -> None:
    alice = Api(fx, "alice")
    fid = upload_id(alice, fx.y2026, "s.txt")
    label(alice, fx.y2026, "confidential")
    unlock(alice, fx.y2026)
    second = Api(fx, "alice")  # another browser, same account
    assert second.download(fid).status_code == 403
    assert alice.download(fid).status_code == 200  # a second tab shares the session cookie
    with fx.w.ctx.sessions() as db:
        node = db.get(Node, fid)
        assert node is not None
        assert not engine.effective(db, Actor(alice.uid), node)  # no session: never unlocked


def test_sharing_or_moving_a_parent_needs_the_unlock(fx: Fx) -> None:
    alice, bob = Api(fx, "alice"), Api(fx, "bob")
    label(alice, fx.y2026, "confidential")
    r = grant(alice, fx.taxes, bob.uid)
    assert r.status_code == 403 and code(r) == "LOCKED"
    other = uuid.UUID(alice.mkdir(fx.alice_root, "Elsewhere").json()["node"]["id"])
    mv = alice.move(fx.taxes, other, 1)
    assert mv.status_code == 403 and code(mv) == "LOCKED"
    assert alice.copy(fx.taxes, other).status_code == 403
    unlock(alice, fx.y2026)
    assert grant(alice, fx.taxes, bob.uid).status_code == 200


def test_copy_keeps_labels_and_activity_hides_locked_names(fx: Fx) -> None:
    alice = Api(fx, "alice")
    upload_id(alice, fx.y2026, "secret-name.txt")
    label(alice, fx.y2026, "confidential")
    log = alice.get(f"/nodes/{fx.taxes}/activity").json()["items"]
    assert "secret-name" not in str(log)
    unlock(alice, fx.y2026)
    assert "secret-name" in str(alice.get(f"/nodes/{fx.taxes}/activity").json()["items"])
    dest = uuid.UUID(alice.mkdir(fx.alice_root, "Copy").json()["node"]["id"])
    r = alice.copy(fx.taxes, dest)
    assert r.status_code in (200, 201), r.text
    with fx.w.ctx.sessions() as db:
        copies = db.scalars(select(Node).where(Node.name == "2026", Node.id != fx.y2026)).all()
        assert [c.privacy_label for c in copies] == ["confidential"]


def test_moving_out_carries_the_label_and_files_are_refused(fx: Fx) -> None:
    alice = Api(fx, "alice")
    label(alice, fx.taxes, "private")
    sub = uuid.UUID(alice.mkdir(fx.y2026, "Sub").json()["node"]["id"])
    f = upload_id(alice, fx.y2026, "a.txt")
    out = uuid.UUID(alice.mkdir(fx.alice_root, "Plain").json()["node"]["id"])
    assert alice.move(f, out, 1).status_code == 409  # a file cannot keep its protection
    assert alice.move(sub, out, 1).status_code in (200, 201)
    with fx.w.ctx.sessions() as db:
        moved = db.get(Node, sub)
        assert moved is not None and moved.privacy_label == "private"


def test_lowering_needs_confirmation_and_password(fx: Fx) -> None:
    alice = Api(fx, "alice")
    label(alice, fx.taxes, "private")
    assert code(label(alice, fx.taxes, "normal")) == "CONFIRMATION_REQUIRED"
    assert label(alice, fx.taxes, "normal", confirm_lowering=True).status_code == 200
    label(alice, fx.taxes, "confidential")
    unlock(alice, fx.taxes)
    assert code(label(alice, fx.taxes, "normal")) == "PASSWORD_REQUIRED"
    assert label(alice, fx.taxes, "normal", password="nope").status_code == 403
    assert label(alice, fx.taxes, "normal", password=PASSWORD).status_code == 200


def test_five_wrong_unlocks_end_the_session(fx: Fx) -> None:
    alice = Api(fx, "alice")
    label(alice, fx.y2026, "confidential")
    for _ in range(4):
        assert unlock(alice, fx.y2026, "bad").status_code == 403
    r = unlock(alice, fx.y2026, "bad")
    assert r.status_code == 401 and code(r) == "SESSION_ENDED_TOO_MANY_UNLOCK_FAILURES"
    assert alice.get("/roots").status_code == 401


def test_switching_the_feature_off_never_relabels(fx: Fx) -> None:
    alice = Api(fx, "alice")
    label(alice, fx.y2026, "confidential")
    admin, hdr = fx.admin
    off = {"labels_enabled": False}
    assert admin.put("/api/v1/admin/privacy", json=off, headers=hdr).status_code == 409
    r = admin.put("/api/v1/admin/privacy", json={**off, "confirm_labels_stay": True}, headers=hdr)
    assert r.json() == {"labels_enabled": False, "private_folders": 0, "confidential_folders": 1}
    assert alice.get(f"/nodes/{fx.y2026}").json()["locked"]  # still enforced
    assert code(label(alice, fx.taxes, "private")) == "PRIVACY_LABELS_DISABLED"


def test_admin_cannot_open_a_personal_confidential_folder(fx: Fx) -> None:
    alice = Api(fx, "alice")
    label(alice, fx.y2026, "confidential")
    admin, _ = fx.admin
    assert admin.get(f"/api/v1/nodes/{fx.y2026}").status_code == 403


def test_logout_drops_unlocks(fx: Fx) -> None:
    alice = Api(fx, "alice")
    label(alice, fx.y2026, "confidential")
    unlock(alice, fx.y2026)
    alice.post("/auth/logout")
    with fx.w.ctx.sessions() as db:
        assert db.scalars(select(ConfidentialUnlock)).all() == []


def test_ai_routing_matrix() -> None:
    allowed = privacy.ai_route_allowed
    assert allowed("normal", "cloud").allowed
    for lab in ("private", "confidential"):
        assert not allowed(lab, "cloud", local_opt_in=True, unlocked_by_person=True).allowed
        assert not allowed(lab, "local").allowed
    assert allowed("private", "local", local_opt_in=True).allowed
    assert not allowed("confidential", "local", local_opt_in=True).allowed
    assert allowed("confidential", "local", local_opt_in=True, unlocked_by_person=True).allowed
    assert not allowed("weird", "local", local_opt_in=True).allowed


def test_restore_gets_the_strictest_label(fx: Fx) -> None:
    from app.backup import restore as restore_mod
    from app.db.models import StorageRoot

    alice = Api(fx, "alice")
    label(alice, fx.y2026, "private")
    with fx.w.ctx.sessions() as db:
        root = db.scalars(select(StorageRoot).where(StorageRoot.owner_user_id == alice.uid)).one()
        assert restore_mod.required_label(db, root) == "private"  # pre-M09 backup: root decides
        assert restore_mod.required_label(db, root, "confidential") == "confidential"


def trash_confidential(fx: Fx) -> tuple[Api, str]:
    """Alice trashes the Confidential `2026` while it is unlocked; returns the trash item id."""
    alice = Api(fx, "alice")
    label(alice, fx.y2026, "confidential")
    unlock(alice, fx.y2026)
    node = alice.get(f"/nodes/{fx.y2026}").json()
    r = alice.client.delete(
        f"/api/v1/nodes/{fx.y2026}",
        params={"expected_revision": 1},
        headers=alice.headers,
    )
    assert r.status_code in (200, 202), r.text
    assert node["privacy_effective"] == "confidential"
    items = alice.get("/trash").json()["items"]
    assert len(items) == 1
    return alice, str(items[0]["id"])


def test_a_trashed_confidential_folder_can_be_unlocked_and_restored(fx: Fx) -> None:
    alice, item = trash_confidential(fx)
    fx.w.offset += timedelta(seconds=400)  # the unlock has lapsed
    # the folders above it are not held hostage by it
    bob = Api(fx, "bob")
    assert grant(alice, fx.taxes, bob.uid).status_code == 200
    # restoring needs the unlock, and the trashed folder can be unlocked
    r = alice.post(f"/trash/{item}/restore", json={})
    assert r.status_code == 403 and code(r) == "LOCKED"
    assert unlock(alice, fx.y2026).status_code == 200
    assert alice.post(f"/trash/{item}/restore", json={}).status_code in (200, 201)


def test_a_purged_confidential_folder_blocks_nothing(fx: Fx) -> None:
    alice, item = trash_confidential(fx)
    fx.w.offset += timedelta(seconds=400)
    token = alice.get("/trash").json()["items"][0]["confirm_token"]
    assert (
        alice.client.delete(
            f"/api/v1/trash/{item}", params={"confirm": token}, headers=alice.headers
        ).status_code
        == 403
    )  # purging Confidential data needs the unlock
    unlock(alice, fx.y2026)
    assert alice.client.delete(
        f"/api/v1/trash/{item}", params={"confirm": token}, headers=alice.headers
    ).status_code in (200, 204)
    fx.w.offset += timedelta(seconds=400)
    bob = Api(fx, "bob")
    assert grant(alice, fx.alice_root, bob.uid).status_code in (200, 422)  # not LOCKED
    assert grant(alice, fx.taxes, bob.uid).status_code == 200


def test_password_change_drops_unlocks(fx: Fx) -> None:
    alice = Api(fx, "alice")
    label(alice, fx.y2026, "confidential")
    unlock(alice, fx.y2026)
    r = alice.client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": PASSWORD + "-new-1"},
        headers=alice.headers,
    )
    assert r.status_code == 200, r.text
    with fx.w.ctx.sessions() as db:
        assert db.scalars(select(ConfidentialUnlock)).all() == []


def test_a_grantee_inside_the_scope_unlocks_without_seeing_the_parent(fx: Fx) -> None:
    alice, bob = Api(fx, "alice"), Api(fx, "bob")
    inner = uuid.UUID(alice.mkdir(fx.y2026, "Inner").json()["node"]["id"])
    assert grant(alice, inner, bob.uid).status_code == 200
    label(alice, fx.y2026, "confidential")
    r = bob.get(f"/nodes/{inner}")
    assert r.status_code == 403 and code(r) == "LOCKED"
    assert "unlock_node_id" not in str(r.json()) and str(fx.y2026) not in r.text
    assert unlock(bob, inner).status_code == 200
    assert bob.get(f"/nodes/{inner}").status_code == 200
    assert bob.get(f"/nodes/{fx.y2026}").status_code == 403  # the parent stays out of sight


def test_ai_agent_actors_are_never_unlocked(fx: Fx) -> None:
    alice = Api(fx, "alice")
    fid = upload_id(alice, fx.y2026, "s.txt")
    label(alice, fx.y2026, "confidential")
    unlock(alice, fx.y2026)
    with fx.w.ctx.sessions() as db:
        node = db.get(Node, fid)
        session_id = db.scalars(select(ConfidentialUnlock.session_id)).one()
        assert node is not None
        assert engine.effective(db, Actor(alice.uid, session_id=session_id), node)
        ai = Actor(alice.uid, "ai_agent", session_id=session_id)
        assert not engine.effective(db, ai, node)


def test_lowering_in_the_shared_tree_needs_an_unlock(fx: Fx) -> None:
    admin, hdr = fx.admin
    r = admin.put(
        f"/api/v1/nodes/{fx.shared_sub}/privacy", json={"label": "confidential"}, headers=hdr
    )
    assert r.status_code == 200, r.text  # an administrator may raise a label there
    r = admin.put(
        f"/api/v1/nodes/{fx.shared_sub}/privacy",
        json={"label": "normal", "password": PASSWORD},
        headers=hdr,
    )
    assert r.status_code == 403 and code(r) == "LOCKED"


def test_restore_that_fails_leaves_no_unregistered_folder(
    fx: Fx, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.backup import restore as restore_mod
    from app.db.models import StorageRoot

    alice = Api(fx, "alice")
    label(alice, fx.y2026, "private")

    def boom(*a: Any, **k: Any) -> uuid.UUID:
        raise OSError("disk")

    monkeypatch.setattr(restore_mod, "_register_folder", boom)
    with fx.w.ctx.sessions() as db:
        root = db.scalars(select(StorageRoot).where(StorageRoot.owner_user_id == alice.uid)).one()
        with pytest.raises(OSError):
            restore_mod._publish(
                db, fx.w.settings, root, "nonexistent-work", set(), "2026-11-01 0000", "private"
            )
    lib = fx.w.settings.library_path / root.relative_dir
    assert not any(p.name.startswith("Restored from backup") for p in lib.iterdir())
