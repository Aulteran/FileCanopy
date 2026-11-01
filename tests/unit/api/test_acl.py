"""M03 centralized ACL: matrix, non-disclosure, admin separation, revocation, bootstrap, guards.

In-memory SQLite plus a per-test temp library. Mirrors ADR-004; the compose suite repeats the key
flows on PostgreSQL behind nginx.
"""

import os
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_auth import PASSWORD, World, invite, signed_in

from app.db.models import AuthAudit, Node, NodeGrant, StorageRoot, User
from app.domain import nodes
from app.policy import engine
from app.policy.model import ALL_PRIVILEGES, EDITOR, VIEWER, Actor, Privilege
from app.storage.resolver import StorageEscape, open_dir
from app.storage.roots import ensure_roots

FORBIDDEN = {"detail": {"code": "FORBIDDEN"}}
NONE: frozenset[Privilege] = frozenset()


@pytest.fixture
def world(make_world: Callable[..., World]) -> World:
    return make_world()


# The factory fixture lives in test_auth; re-declare it here so this module is self-contained.
@pytest.fixture
def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., World]:
    def factory(**overrides: Any) -> World:
        return World(tmp_path, monkeypatch, **overrides)

    return factory


class Fx:
    """Alice's tree, a shared tree, and the personas the matrix needs."""

    def __init__(self, w: World) -> None:
        self.w = w
        w.create_admin()
        self.admin = signed_in(w)
        for email, role in [
            ("alice@example.com", "member"),
            ("bob@example.com", "member"),
            ("carol@example.com", "member"),
            ("dave@example.com", "member"),
            ("eve@example.com", "member"),
            ("admin2@example.com", "admin"),
        ]:
            self.onboard(email, role)
        self.alice = signed_in(w, "alice@example.com")
        self.alice_root = self.root_of("alice@example.com")
        self.taxes = self.folder(self.alice_root, "Taxes")
        self.y2026 = self.folder(self.taxes, "2026")
        self.other = self.folder(self.taxes, "Other")
        self.private = self.folder(self.alice_root, "Private")
        self.shared_root = self.root_of(None)
        self.shared_sub = self.folder(self.shared_root, "Family")

    # -- builders
    def onboard(self, email: str, role: str) -> None:
        token = invite(self.w, self.admin, email, role)
        r = self.w.browser().post(
            "/api/v1/auth/invitations/accept", json={"token": token, "password": PASSWORD}
        )
        assert r.status_code == 200, r.text

    def uid(self, email: str) -> uuid.UUID:
        with self.w.ctx.sessions() as db:
            return db.scalars(select(User.id).where(User.email == email)).one()

    def root_of(self, email: str | None) -> uuid.UUID:
        with self.w.ctx.sessions() as db:
            q = select(Node.id).join(StorageRoot, StorageRoot.id == Node.root_id)
            q = q.where(Node.parent_id.is_(None))
            if email is None:
                q = q.where(StorageRoot.kind == "shared")
            else:
                q = q.where(StorageRoot.owner_user_id == self.uid(email))
            return db.scalars(q).one()

    def folder(self, parent_id: uuid.UUID, name: str) -> uuid.UUID:
        with self.w.ctx.sessions() as db:
            parent = db.get(Node, parent_id)
            assert parent is not None
            child = nodes.create_child_dir(db, parent, name)
            root = db.get(StorageRoot, parent.root_id)
            assert root is not None
            comps = nodes.path_components(db, child)
            db.commit()
            with open_dir(self.w.settings.library_path, root.relative_dir, comps, create=True):
                pass
            return child.id

    def grant(
        self, owner: tuple[TestClient, dict[str, str]], node: uuid.UUID, email: str, level: str
    ) -> None:
        c, h = owner
        r = c.put(
            f"/api/v1/nodes/{node}/grants/{self.uid(email)}", json={"level": level}, headers=h
        )
        assert r.status_code == 200, r.text

    def privs(self, email: str, node: uuid.UUID) -> frozenset[Privilege]:
        with self.w.ctx.sessions() as db:
            return engine.effective(db, Actor(self.uid(email)), db.get(Node, node))


@pytest.fixture
def fx(world: World) -> Fx:
    return Fx(world)


# ---------------------------------------------------------------- ACL matrix


def matrix_world(fx: Fx) -> dict[str, tuple[TestClient, dict[str, str]]]:
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "viewer")
    fx.grant(fx.alice, fx.taxes, "carol@example.com", "editor")
    fx.grant(fx.alice, fx.taxes, "admin2@example.com", "viewer")
    fx.grant(fx.alice, fx.taxes, "eve@example.com", "editor")
    sessions = {
        e: signed_in(fx.w, f"{e}@example.com")
        for e in ("alice", "bob", "carol", "dave", "eve", "admin", "admin2")
    }
    # Disable eve last: the grant row stays, the account must not.
    c, h = fx.admin
    r = c.patch(
        f"/api/v1/admin/users/{fx.uid('eve@example.com')}", json={"status": "disabled"}, headers=h
    )
    assert r.status_code == 200
    return sessions


def expected(persona: str, resource: str) -> frozenset[Privilege]:
    if resource in ("shared_root", "shared_sub"):  # default 'editor' for every active account
        return NONE if persona == "eve" else EDITOR
    if persona == "alice":
        return ALL_PRIVILEGES
    if persona in ("bob", "admin2"):
        return VIEWER if resource in ("taxes", "y2026", "other") else NONE
    if persona == "carol":
        return EDITOR if resource in ("taxes", "y2026", "other") else NONE
    return NONE  # dave, eve (disabled), admin without a grant


PERSONAS = ["alice", "bob", "carol", "dave", "eve", "admin", "admin2"]
RESOURCES = ["alice_root", "taxes", "y2026", "other", "private", "shared_root", "shared_sub"]


def test_acl_matrix(fx: Fx) -> None:
    sessions = matrix_world(fx)
    ids = {
        "alice_root": fx.alice_root,
        "taxes": fx.taxes,
        "y2026": fx.y2026,
        "other": fx.other,
        "private": fx.private,
        "shared_root": fx.shared_root,
        "shared_sub": fx.shared_sub,
    }
    for persona in PERSONAS:
        email = f"{persona}@example.com"
        for resource in RESOURCES:
            want = expected(persona, resource)
            node_id = ids[resource]
            got = fx.privs(email, node_id)
            assert got == want, (persona, resource, got)
            if persona == "eve":
                continue  # a disabled account has no session; the engine row above is the test
            c, _ = sessions[persona]
            r = c.get(f"/api/v1/nodes/{node_id}")
            if Privilege.TRAVERSE in want:
                assert r.status_code == 200, (persona, resource, r.text)
                assert r.json()["my_privileges"] == sorted(p.value for p in want)
            else:
                assert (r.status_code, r.json()) == (403, FORBIDDEN), (persona, resource)
            # Grant administration: personal tree = owner only; shared tree = any administrator.
            g = c.get(f"/api/v1/nodes/{node_id}/grants")
            can_admin = (persona == "alice" and resource not in ("shared_root", "shared_sub")) or (
                persona in ("admin", "admin2") and resource in ("shared_root", "shared_sub")
            )
            assert g.status_code == (200 if can_admin else 403), (persona, resource, g.text)
            if not can_admin:
                assert g.json() == FORBIDDEN


def test_privileges_never_propagate_upward_or_sideways(fx: Fx) -> None:
    fx.grant(fx.alice, fx.y2026, "bob@example.com", "viewer")
    assert fx.privs("bob@example.com", fx.y2026) == VIEWER
    for node in (fx.taxes, fx.other, fx.private, fx.alice_root):
        assert fx.privs("bob@example.com", node) == NONE


# ---------------------------------------------------------------- non-disclosure


def test_private_sibling_non_disclosure(fx: Fx) -> None:
    fx.grant(fx.alice, fx.y2026, "bob@example.com", "viewer")
    bob, _ = signed_in(fx.w, "bob@example.com")
    unknown = bob.get(f"/api/v1/nodes/{uuid.uuid4()}")
    assert (unknown.status_code, unknown.json()) == (403, FORBIDDEN)
    for hidden in (fx.taxes, fx.other, fx.private, fx.alice_root):
        r = bob.get(f"/api/v1/nodes/{hidden}")
        assert r.status_code == unknown.status_code
        assert r.content == unknown.content  # byte-identical to a random id
        g = bob.get(f"/api/v1/nodes/{hidden}/grants")
        assert (g.status_code, g.json()) == (403, FORBIDDEN)
    visible = bob.get(f"/api/v1/nodes/{fx.y2026}").json()
    assert visible["parent_id"] is None  # Taxes is not revealed
    assert visible["name"] == "2026"
    blob = bob.get("/api/v1/roots").text + str(visible)
    for secret in (str(fx.taxes), str(fx.alice_root), "Taxes", "Private", "Other"):
        assert secret not in blob
    entries = {e["node_id"]: e for e in bob.get("/api/v1/roots").json()}
    assert entries[str(fx.y2026)]["kind"] == "shared_with_me"
    assert entries[str(fx.y2026)]["owner_name"] == "New"  # alice's display name from invite()
    # The grantee's own listing of grants on the folder is the owner's business only.
    assert bob.get(f"/api/v1/nodes/{fx.y2026}/grants").status_code == 403


def test_roots_lists_only_top_most_grants_and_own_root(fx: Fx) -> None:
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "viewer")
    fx.grant(fx.alice, fx.y2026, "bob@example.com", "editor")  # nested under an existing grant
    bob, _ = signed_in(fx.w, "bob@example.com")
    kinds = {e["node_id"]: e["kind"] for e in bob.get("/api/v1/roots").json()}
    assert str(fx.taxes) in kinds and str(fx.y2026) not in kinds
    assert sorted(kinds.values()) == ["personal", "shared", "shared_with_me"]
    # The union of overlapping grants still applies to the nested folder.
    assert fx.privs("bob@example.com", fx.y2026) == EDITOR


# ---------------------------------------------------------------- admin separation


def test_admin_personal_403_and_no_leak_in_admin_storage(fx: Fx) -> None:
    c, h = fx.admin
    for node in (fx.alice_root, fx.taxes, fx.y2026):
        r = c.get(f"/api/v1/nodes/{node}")
        assert (r.status_code, r.json()) == (403, FORBIDDEN)
        assert c.get(f"/api/v1/nodes/{node}/grants").status_code == 403
        put = c.put(
            f"/api/v1/nodes/{node}/grants/{fx.uid('admin@example.com')}",
            json={"level": "editor"},
            headers=h,
        )
        assert (put.status_code, put.json()) == (403, FORBIDDEN)
    assert fx.privs("admin@example.com", fx.alice_root) == NONE
    overview = c.get("/api/v1/admin/storage")
    assert overview.status_code == 200
    body = overview.json()
    assert set(body) == {
        "shared_root_node_id",
        "shared_root_status",
        "network_fs",
        "fs_type",
        "network_acknowledged",
        "shared_default_access",
        "shared_trash_items",
        "shared_trash_bytes",
        "trash_retention_days",
        "personal_roots",
    }
    # Trash totals only (ADR-017 §3): counts and sizes, never names.
    assert all(
        set(p) == {"user_id", "status", "trash_items", "trash_bytes"}
        for p in body["personal_roots"]
    )
    text = overview.text.lower()
    assert "alice" not in text and "new" not in text and str(fx.taxes) not in text
    assert body["shared_root_node_id"] == str(fx.shared_root)
    assert len(body["personal_roots"]) == 7


def test_admin_reads_personal_only_through_owner_grant_and_revocation_is_immediate(
    fx: Fx,
) -> None:
    c, _ = fx.admin
    assert c.get(f"/api/v1/nodes/{fx.taxes}").status_code == 403
    fx.grant(fx.alice, fx.taxes, "admin@example.com", "viewer")
    r = c.get(f"/api/v1/nodes/{fx.taxes}")
    assert r.status_code == 200
    assert r.json()["my_privileges"] == ["list", "read", "traverse"]  # read-only, no write/share
    assert c.get(f"/api/v1/nodes/{fx.alice_root}").status_code == 403  # nothing above it
    assert c.get(f"/api/v1/nodes/{fx.taxes}/grants").status_code == 403  # viewer cannot re-share
    ac, ah = fx.alice
    d = ac.delete(f"/api/v1/nodes/{fx.taxes}/grants/{fx.uid('admin@example.com')}", headers=ah)
    assert d.status_code == 200
    assert c.get(f"/api/v1/nodes/{fx.taxes}").status_code == 403  # same session, next request


def test_admin_administers_shared_grants_but_reads_only_with_own_grant(fx: Fx) -> None:
    c, h = fx.admin
    # Default grant: the first admin can use the shared tree from day one...
    assert fx.privs("admin@example.com", fx.shared_sub) == EDITOR
    # ...administering is separate from holding: remove the admin's own grant, content closes.
    uid = fx.uid("admin@example.com")
    assert c.delete(f"/api/v1/nodes/{fx.shared_root}/grants/{uid}", headers=h).status_code == 200
    assert fx.privs("admin@example.com", fx.shared_sub) == NONE
    assert c.get(f"/api/v1/nodes/{fx.shared_sub}").status_code == 403
    assert c.get(f"/api/v1/nodes/{fx.shared_sub}/grants").status_code == 200  # still administers
    # Members do not administer shared grants, even with editor access.
    carol, ch = signed_in(fx.w, "carol@example.com")
    assert carol.get(f"/api/v1/nodes/{fx.shared_root}").status_code == 200
    r = carol.put(
        f"/api/v1/nodes/{fx.shared_root}/grants/{uid}", json={"level": "editor"}, headers=ch
    )
    assert r.status_code == 403


def test_shared_tree_subfolder_grant_is_scoped(fx: Fx) -> None:
    c, h = fx.admin
    dave = fx.uid("dave@example.com")
    assert c.delete(f"/api/v1/nodes/{fx.shared_root}/grants/{dave}", headers=h).status_code == 200
    assert fx.privs("dave@example.com", fx.shared_root) == NONE
    assert fx.privs("dave@example.com", fx.shared_sub) == NONE
    fx.grant(fx.admin, fx.shared_sub, "dave@example.com", "viewer")
    assert fx.privs("dave@example.com", fx.shared_sub) == VIEWER
    assert fx.privs("dave@example.com", fx.shared_root) == NONE
    dc, _ = signed_in(fx.w, "dave@example.com")
    shared_entries = [e for e in dc.get("/api/v1/roots").json() if e["kind"] == "shared"]
    assert [e["node_id"] for e in shared_entries] == [str(fx.shared_sub)]


# ---------------------------------------------------------------- revocation and disable


def test_revocation_applies_to_the_existing_session(fx: Fx) -> None:
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "editor")
    bob, _ = signed_in(fx.w, "bob@example.com")
    assert bob.get(f"/api/v1/nodes/{fx.other}").status_code == 200
    ac, ah = fx.alice
    assert (
        ac.delete(
            f"/api/v1/nodes/{fx.taxes}/grants/{fx.uid('bob@example.com')}", headers=ah
        ).status_code
        == 200
    )
    assert bob.get(f"/api/v1/nodes/{fx.other}").status_code == 403
    # A job-style actor (originating user id, no HTTP session) is re-evaluated the same way.
    assert fx.privs("bob@example.com", fx.other) == NONE
    again = ac.delete(f"/api/v1/nodes/{fx.taxes}/grants/{fx.uid('bob@example.com')}", headers=ah)
    assert again.status_code == 404


def test_downgrade_takes_effect_immediately(fx: Fx) -> None:
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "editor")
    assert Privilege.WRITE in fx.privs("bob@example.com", fx.other)
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "viewer")
    assert fx.privs("bob@example.com", fx.other) == VIEWER
    with fx.w.ctx.sessions() as db:
        count = db.scalar(
            select(func.count()).select_from(NodeGrant).where(NodeGrant.node_id == fx.taxes)
        )
    assert count == 1  # upsert, not a second row


def test_disabled_grantee_loses_access_and_disabled_owner_keeps_grants(fx: Fx) -> None:
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "viewer")
    bob, _ = signed_in(fx.w, "bob@example.com")
    c, h = fx.admin
    assert (
        c.patch(
            f"/api/v1/admin/users/{fx.uid('bob@example.com')}",
            json={"status": "disabled"},
            headers=h,
        ).status_code
        == 200
    )
    assert bob.get(f"/api/v1/nodes/{fx.taxes}").status_code == 401
    assert fx.privs("bob@example.com", fx.taxes) == NONE
    # Re-enable bob, then disable the owner: shared-out folders keep working (owner decision 4).
    assert (
        c.patch(
            f"/api/v1/admin/users/{fx.uid('bob@example.com')}", json={"status": "active"}, headers=h
        ).status_code
        == 200
    )
    assert (
        c.patch(
            f"/api/v1/admin/users/{fx.uid('alice@example.com')}",
            json={"status": "disabled"},
            headers=h,
        ).status_code
        == 200
    )
    assert fx.privs("bob@example.com", fx.taxes) == VIEWER
    assert fx.privs("alice@example.com", fx.taxes) == NONE


# ---------------------------------------------------------------- grant API validation


def test_grant_validation_and_authorization(fx: Fx) -> None:
    ac, ah = fx.alice
    base = f"/api/v1/nodes/{fx.taxes}/grants"
    me, bob = fx.uid("alice@example.com"), fx.uid("bob@example.com")
    assert (
        ac.put(f"{base}/{me}", json={"level": "viewer"}, headers=ah).json()["detail"]["code"]
        == "CANNOT_GRANT_SELF"
    )
    assert (
        ac.put(f"{base}/{uuid.uuid4()}", json={"level": "viewer"}, headers=ah).json()["detail"][
            "code"
        ]
        == "UNKNOWN_GRANTEE"
    )
    assert ac.put(f"{base}/{bob}", json={"level": "owner"}, headers=ah).status_code == 422
    assert (
        ac.put(f"{base}/{bob}", json={"level": "viewer", "extra": 1}, headers=ah).status_code == 422
    )
    assert ac.put(f"{base}/{bob}", json={"level": "viewer"}).status_code == 403  # no CSRF header
    assert ac.put(f"{base}/not-a-uuid", json={"level": "viewer"}, headers=ah).status_code == 422
    # Grants on files are not offered in the MVP.
    with fx.w.ctx.sessions() as db:
        taxes = db.get(Node, fx.taxes)
        assert taxes is not None
        file_id = uuid.uuid4()
        db.add(
            Node(
                id=file_id,
                root_id=taxes.root_id,
                parent_id=taxes.id,
                name="a.pdf",
                kind="file",
                lineage=f"{taxes.lineage}{file_id}/",
                status="active",
                created_at=taxes.created_at,
                updated_at=taxes.created_at,
            )
        )
        db.commit()
    r = ac.put(f"/api/v1/nodes/{file_id}/grants/{bob}", json={"level": "viewer"}, headers=ah)
    assert r.json()["detail"]["code"] == "NOT_A_DIRECTORY"
    # An editor grantee cannot re-share; a stranger cannot grant on someone else's folder.
    fx.grant(fx.alice, fx.taxes, "carol@example.com", "editor")
    cc, ch = signed_in(fx.w, "carol@example.com")
    assert cc.put(f"{base}/{bob}", json={"level": "viewer"}, headers=ch).status_code == 403
    dc, dh = signed_in(fx.w, "dave@example.com")
    assert dc.put(f"{base}/{bob}", json={"level": "viewer"}, headers=dh).status_code == 403
    # Audit holds ids only.
    with fx.w.ctx.sessions() as db:
        rows = db.scalars(
            select(AuthAudit).where(AuthAudit.action.in_(["grant.set", "access.denied"]))
        ).all()
    assert rows and all("name" not in str(r.details) and "@" not in str(r.details) for r in rows)


def test_path_like_ids_are_rejected(fx: Fx) -> None:
    c, _ = fx.admin
    for bad in ("..", "%2e%2e", "%2e%2e%2f%2e%2e", "..%5c..", "etc%2fpasswd", "a%00b"):
        r = c.get(f"/api/v1/nodes/{bad}")
        assert r.status_code in (404, 422), (bad, r.status_code)
        assert r.status_code != 200


# ---------------------------------------------------------------- bootstrap and roots


def test_every_account_has_exactly_one_personal_root_and_default_grant(fx: Fx) -> None:
    with fx.w.ctx.sessions() as db:
        users = db.scalars(select(User)).all()
        assert len(users) == 7
        for u in users:
            roots = db.scalars(select(StorageRoot).where(StorageRoot.owner_user_id == u.id)).all()
            assert len(roots) == 1
            assert roots[0].relative_dir == f"personal/{u.id}"
            assert roots[0].kind == "personal"
            level = db.scalars(
                select(NodeGrant.level).where(
                    NodeGrant.node_id == fx.shared_root, NodeGrant.grantee_user_id == u.id
                )
            ).all()
            assert level == ["editor"]
        assert (
            db.scalar(
                select(func.count()).select_from(StorageRoot).where(StorageRoot.kind == "shared")
            )
            == 1
        )


def test_default_shared_access_is_configurable_for_future_accounts(fx: Fx) -> None:
    c, h = fx.admin
    for value, expect in (("viewer", ["viewer"]), ("none", [])):
        r = c.put("/api/v1/admin/storage/shared-default-access", json={"value": value}, headers=h)
        assert r.status_code == 200
        fx.onboard(f"user-{value}@example.com", "member")
        with fx.w.ctx.sessions() as db:
            got = db.scalars(
                select(NodeGrant.level).where(
                    NodeGrant.node_id == fx.shared_root,
                    NodeGrant.grantee_user_id == fx.uid(f"user-{value}@example.com"),
                )
            ).all()
        assert got == expect
    assert c.get("/api/v1/admin/storage").json()["shared_default_access"] == "none"
    # Existing grants are untouched, and bad values / non-admins are refused.
    assert fx.privs("bob@example.com", fx.shared_root) == EDITOR
    assert (
        c.put(
            "/api/v1/admin/storage/shared-default-access", json={"value": "owner"}, headers=h
        ).status_code
        == 422
    )
    bc, bh = signed_in(fx.w, "bob@example.com")
    assert bc.get("/api/v1/admin/storage").status_code == 403
    assert (
        bc.put(
            "/api/v1/admin/storage/shared-default-access", json={"value": "editor"}, headers=bh
        ).status_code
        == 403
    )


def test_ensure_roots_creates_directories_and_marks_unusable_roots(fx: Fx, tmp_path: Path) -> None:
    lib = fx.w.settings.library_path
    counts = ensure_roots(fx.w.ctx.sessions, fx.w.settings)
    assert counts == {"active": 8, "missing": 0, "unsupported_mount": 0}  # 7 personal + shared
    assert (lib / "shared").is_dir()
    alice_dir = lib / "personal" / str(fx.uid("alice@example.com"))
    assert alice_dir.is_dir() and alice_dir.stat().st_mode & 0o007 == 0
    # A symlinked root is refused, not followed, and reported as missing.
    bob_dir = lib / "personal" / str(fx.uid("bob@example.com"))
    bob_dir.rmdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    bob_dir.symlink_to(elsewhere, target_is_directory=True)
    assert ensure_roots(fx.w.ctx.sessions, fx.w.settings) == {
        "active": 7,
        "missing": 1,
        "unsupported_mount": 0,
    }
    assert list(elsewhere.iterdir()) == []
    with fx.w.ctx.sessions() as db:
        bob_root = db.scalars(
            select(StorageRoot).where(StorageRoot.owner_user_id == fx.uid("bob@example.com"))
        ).one()
        assert bob_root.status == "missing"
    # Once the link is removed the root recovers on the next start.
    bob_dir.unlink()
    assert ensure_roots(fx.w.ctx.sessions, fx.w.settings) == {
        "active": 8,
        "missing": 0,
        "unsupported_mount": 0,
    }


def test_ensure_roots_with_missing_library_does_not_crash(fx: Fx) -> None:
    shutil.rmtree(fx.w.settings.library_path)
    assert ensure_roots(fx.w.ctx.sessions, fx.w.settings) == {
        "active": 0,
        "missing": 8,
        "unsupported_mount": 0,
    }


def test_node_path_resolves_from_lineage_and_refuses_corrupted_names(fx: Fx) -> None:
    lib = fx.w.settings.library_path
    with fx.w.ctx.sessions() as db:
        node = db.get(Node, fx.y2026)
        root = db.get(StorageRoot, node.root_id) if node else None
        assert node is not None and root is not None
        comps = nodes.path_components(db, node)
        assert comps == ["Taxes", "2026"]
        with open_dir(lib, root.relative_dir, comps) as fd:
            assert os.fstat(fd).st_ino == (lib / root.relative_dir / "Taxes" / "2026").stat().st_ino
        taxes = db.get(Node, fx.taxes)
        assert taxes is not None
        taxes.name = ".."
        db.flush()
        with (
            pytest.raises(StorageEscape),
            open_dir(lib, root.relative_dir, nodes.path_components(db, node)),
        ):
            pass
        db.rollback()


def test_policy_denies_unknown_actor_and_missing_node(fx: Fx) -> None:
    with fx.w.ctx.sessions() as db:
        node = db.get(Node, fx.taxes)
        assert engine.effective(db, Actor(uuid.uuid4()), node) == NONE
        assert engine.effective(db, Actor(fx.uid("alice@example.com")), None) == NONE
        with pytest.raises(engine.Forbidden):
            engine.require(db, Actor(fx.uid("bob@example.com")), fx.taxes, Privilege.TRAVERSE)
        with pytest.raises(engine.Forbidden):
            engine.require(db, Actor(fx.uid("alice@example.com")), uuid.uuid4(), Privilege.TRAVERSE)


def test_denials_are_audited_with_ids_only(fx: Fx) -> None:
    dave, _ = signed_in(fx.w, "dave@example.com")
    assert dave.get(f"/api/v1/nodes/{fx.private}").status_code == 403
    assert dave.get(f"/api/v1/nodes/{uuid.uuid4()}").status_code == 403
    with fx.w.ctx.sessions() as db:
        rows = db.scalars(select(AuthAudit).where(AuthAudit.action == "access.denied")).all()
    assert len(rows) == 2
    assert all(set(r.details) == {"node_id", "required"} for r in rows)
    assert all(r.actor_user_id == fx.uid("dave@example.com") for r in rows)


# ---------------------------------------------------------------- route inventory


PUBLIC_OR_INSTANCE = {
    "/api/v1/health",
    "/api/v1/health/live",
    "/api/v1/setup/status",
    "/api/v1/setup/admin",
    "/api/v1/auth/login",
    "/api/v1/auth/session",
    "/api/v1/auth/logout",
    "/api/v1/auth/password",
    "/api/v1/auth/invitations/accept",
    "/api/v1/auth/password-reset/complete",
    "/api/v1/roots",  # actor-scoped entry points; not addressed by node id
    "/api/v1/admin/users",
    "/api/v1/admin/users/{user_id}",
    "/api/v1/admin/users/{user_id}/invitation",
    "/api/v1/admin/users/{user_id}/password-reset",
    "/api/v1/admin/users/{user_id}/revoke-sessions",
    "/api/v1/admin/storage",
    "/api/v1/admin/storage/shared-default-access",
    "/api/v1/admin/storage/network-ack",
    "/api/v1/admin/backups",
    "/api/v1/admin/backups/settings",
    "/api/v1/admin/backups/run",
    "/api/v1/admin/backups/key/rotate",
    "/api/v1/auth/lock",  # own session only; no node addressed
    "/api/v1/preferences/local-processing",  # own account only; no node addressed
}


def _guards(dependant: Any) -> bool:
    for dep in dependant.dependencies:
        if getattr(dep.call, "policy_guard", False) or _guards(dep):
            return True
    return False


def _api_routes(routes: Any) -> list[APIRoute]:
    found: list[APIRoute] = []
    for r in routes:
        if isinstance(r, APIRoute):
            found.append(r)
        elif hasattr(r, "original_router"):  # routers added with include_router
            found.extend(_api_routes(r.original_router.routes))
    return found


def test_every_node_route_depends_on_a_policy_guard(world: World) -> None:
    routes = [r for r in _api_routes(world.app.routes) if r.path.startswith("/api/v1")]
    assert len(routes) > 20
    unguarded = [
        (sorted(r.methods), r.path)
        for r in routes
        if r.path not in PUBLIC_OR_INSTANCE and not _guards(r.dependant)
    ]
    assert unguarded == []
    # Anything addressed by node id must be guarded, even if someone adds it to the allowlist.
    assert all(_guards(r.dependant) for r in routes if "{node_id}" in r.path)
    assert any(r.path == "/api/v1/nodes/{node_id}" for r in routes)
