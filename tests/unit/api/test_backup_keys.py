# ruff: noqa: F811
"""M25 per-root backup keys (ADR-016): key isolation between roots, owner-only creation with a
fresh secret, the save-it-first confirmation, default-off administrator escrow with consent,
audit and offline opening, rotation per root, and legacy instance-key chains. Synthetic data."""

import base64
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from test_acl import Fx
from test_auth import PASSWORD
from test_backup import (
    PLAIN,
    archives,
    audit_text,
    enable,
    fx,  # noqa: F401  (fixture)
    make_owner_key,
    make_world,  # noqa: F401  (fixture)
    members,
    restore,
    run,
    run_ok,
    runs,
)
from test_files import Api, code

from app.backup import cli, crypto
from app.db.models import AuthAudit, BackupRootKey, BackupRun


def alice_api(fx: Fx) -> Api:
    return Api(fx, "alice")


def key_url(node: uuid.UUID, tail: str = "") -> str:
    return f"/nodes/{node}/backup-key{tail}"


def encrypted_on(fx: Fx, **over: Any) -> dict[str, Any]:
    return enable(fx, encrypt=True, acknowledge_unencrypted=False, **over)


def roots_of(fx: Fx) -> list[dict[str, Any]]:
    c, h = fx.admin
    out: list[dict[str, Any]] = c.get("/api/v1/admin/backups", headers=h).json()["roots"]
    return out


def alice_out(fx: Fx) -> dict[str, Any]:
    uid = str(fx.uid("alice@example.com"))
    return next(r for r in roots_of(fx) if r["user_id"] == uid)


def put_escrow(api: Api, node: uuid.UUID, **body: Any) -> Any:
    return api.client.put(f"/api/v1{key_url(node, '/escrow')}", json=body, headers=api.headers)


def test_each_root_has_its_own_key_and_no_key_opens_another_root(fx: Fx) -> None:
    alice, bob = alice_api(fx), Api(fx, "bob")
    alice.upload(fx.taxes, "a.txt", b"alice-data")
    bob_root = fx.root_of("bob@example.com")
    bob.upload(bob_root, "b.txt", b"bob-data")
    admin_key = encrypted_on(fx, full_every=5)["recovery_key"]
    ka = make_owner_key(fx, alice, fx.alice_root)
    kb = make_owner_key(fx, bob, bob_root)
    assert len({admin_key, ka, kb}) == 3
    run_ok(fx)
    run_ok(fx, bob_root)
    run_ok(fx, fx.shared_root)
    a, b, s = (archives(fx, n)[0] for n in (fx.alice_root, bob_root, fx.shared_root))
    assert members(a, ka)["files/Taxes/a.txt"] == b"alice-data"
    assert members(b, kb)["files/b.txt"] == b"bob-data"
    assert members(s, admin_key) is not None
    for archive, wrong in ((a, kb), (a, admin_key), (b, ka), (b, admin_key), (s, ka), (s, kb)):
        with pytest.raises(crypto.DecryptionError):
            members(archive, wrong)


def test_personal_root_waits_for_its_owner_key_and_never_uses_the_instance_key(fx: Fx) -> None:
    alice = alice_api(fx)
    alice.upload(fx.taxes, "a.txt", b"x")
    encrypted_on(fx)
    result = run(fx)
    assert (result.status, result.code) == ("refused", "BACKUP_OWNER_KEY_PENDING")
    assert not archives(fx)
    status = fx.admin[0].get("/api/v1/admin/backups", headers=fx.admin[1]).json()
    assert "owner_keys_pending" in status["warnings"]
    made = alice.post(key_url(fx.alice_root), json={"password": PASSWORD}).json()
    assert made["state"] == "pending" and made["recovery_key"]
    fx.w.advance(minutes=61)
    assert run(fx).status == "refused"  # created but not proven saved: still not used
    wrong = crypto.generate_keypair()[0]
    r = alice.post(
        key_url(fx.alice_root, "/confirm"), json={"password": PASSWORD, "recovery_key": wrong}
    )
    assert (r.status_code, code(r)) == (409, "NO_PENDING_KEY")
    r = alice.post(
        key_url(fx.alice_root, "/confirm"), json={"password": PASSWORD, "recovery_key": "junk"}
    )
    assert (r.status_code, code(r)) == (422, "BACKUP_KEY_INVALID")
    done = alice.post(
        key_url(fx.alice_root, "/confirm"),
        json={"password": PASSWORD, "recovery_key": made["recovery_key"]},
    ).json()
    assert done["state"] == "active" and done["recovery_key"] is None and done["escrow"] is False
    fx.w.advance(minutes=61)
    assert run(fx).status == "ok"
    assert alice.get(key_url(fx.alice_root)).json()["recovery_key"] is None  # never shown again


def test_only_the_owner_can_manage_a_personal_key_and_needs_a_fresh_secret(fx: Fx) -> None:
    alice = alice_api(fx)
    encrypted_on(fx)
    c, h = fx.admin
    admin_try = c.post(f"/api/v1{key_url(fx.alice_root)}", json={"password": PASSWORD}, headers=h)
    assert admin_try.status_code == 403
    bob_try = Api(fx, "bob").post(key_url(fx.alice_root), json={"password": PASSWORD})
    assert bob_try.status_code == 403
    assert c.get(f"/api/v1{key_url(fx.alice_root)}", headers=h).status_code == 403
    assert alice.post(key_url(fx.alice_root), json={}).status_code == 403  # no secret
    bad = alice.post(key_url(fx.alice_root), json={"password": "wrong-password"})
    assert bad.status_code == 403
    assert not fx.w.db_rows(BackupRootKey)
    shared = alice.post(key_url(fx.shared_root), json={"password": PASSWORD})
    assert shared.status_code == 403  # alice does not administer the shared tree


def test_creation_requires_encrypted_backups_and_is_compare_and_swap(fx: Fx) -> None:
    alice = alice_api(fx)
    off = alice.post(key_url(fx.alice_root), json={"password": PASSWORD})
    assert (off.status_code, code(off)) == (409, "BACKUP_NOT_ENCRYPTED")
    enable(fx)  # plain jobs need no key
    assert alice.post(key_url(fx.alice_root), json={"password": PASSWORD}).status_code == 409
    encrypted_on(fx)
    first = make_owner_key(fx, alice)
    stale = alice.post(
        key_url(fx.alice_root),
        json={"password": PASSWORD, "expected_fingerprint": "0123456789abcdef"},
    )
    assert (stale.status_code, code(stale)) == (409, "KEY_CHANGED")
    blind = alice.post(key_url(fx.alice_root), json={"password": PASSWORD})
    assert (blind.status_code, code(blind)) == (409, "KEY_CHANGED")  # would silently rotate
    second = make_owner_key(fx, alice, expected=crypto.recovery_key_fingerprint(first))
    assert second != first
    assert sorted(k.state for k in fx.w.db_rows(BackupRootKey)) == ["active", "retired"]


def test_rotation_touches_only_its_root_and_old_chains_keep_their_key(fx: Fx) -> None:
    alice, bob = alice_api(fx), Api(fx, "bob")
    alice.upload(fx.taxes, "a.txt", b"a")
    bob_root = fx.root_of("bob@example.com")
    bob.upload(bob_root, "b.txt", b"b")
    encrypted_on(fx, full_every=5)
    old = make_owner_key(fx, alice)
    kb = make_owner_key(fx, bob, bob_root)
    run_ok(fx)
    run_ok(fx, bob_root)
    new = make_owner_key(fx, alice, expected=crypto.recovery_key_fingerprint(old))
    bob_before = {r.key_fingerprint for r in runs(fx, bob_root)}
    run_ok(fx)
    run_ok(fx, bob_root)
    first, second = archives(fx)
    assert members(first, old) and members(second, new)
    with pytest.raises(crypto.DecryptionError):
        members(second, old)
    assert [r.kind for r in runs(fx)] == ["full", "full"]  # a new chain for the new key
    assert bob_before <= {r.key_fingerprint for r in runs(fx, bob_root)}
    assert len({r.key_fingerprint for r in runs(fx, bob_root)}) == 1
    assert all(members(a, kb) for a in archives(fx, bob_root))
    points = alice.get(f"/nodes/{fx.alice_root}/backups").json()
    assert {p["key_scope"] for p in points} == {"owner"}
    stale = restore(alice, fx.alice_root, recovery_key=old)
    assert stale.status_code == 422  # the newest point needs the new key
    assert restore(alice, fx.alice_root, recovery_key=new).status_code == 200


def test_escrow_is_off_by_default_needs_consent_and_is_audited(fx: Fx, tmp_path: Path) -> None:
    alice = alice_api(fx)
    alice.upload(fx.taxes, "a.txt", b"secret-content")
    admin_key = encrypted_on(fx)["recovery_key"]
    key = make_owner_key(fx, alice)  # no consent given
    assert alice.get(key_url(fx.alice_root)).json()["escrow"] is False
    c, h = fx.admin
    root_id = alice_out(fx)["root_id"]
    export_url = f"/api/v1/admin/backups/roots/{root_id}/escrow"
    assert c.post(export_url, headers=h).status_code == 404
    other = crypto.generate_keypair()[0]
    r = put_escrow(alice, fx.alice_root, enabled=True, password=PASSWORD, recovery_key=other)
    assert (r.status_code, code(r)) == (422, "BACKUP_KEY_MISMATCH")
    denied = c.put(
        f"/api/v1{key_url(fx.alice_root, '/escrow')}",
        json={"enabled": True, "password": PASSWORD, "recovery_key": key},
        headers=h,
    )
    assert denied.status_code == 403
    on = put_escrow(alice, fx.alice_root, enabled=True, password=PASSWORD, recovery_key=key).json()
    assert on["escrow"] is True and on["escrow_current"] is True
    assert next(r for r in roots_of(fx) if r["root_id"] == root_id)["escrow"] is True
    export = c.post(export_url, headers=h)
    assert export.status_code == 200
    blob = base64.b64decode(export.json()["sealed"])
    assert key.encode() not in blob and key.removeprefix("fcbk1-").encode() not in blob
    # offline: the administrator opens it with the administrator key, then decrypts the archive
    run_ok(fx)
    exp, adm, out = tmp_path / "e.json", tmp_path / "adm.txt", tmp_path / "owner.txt"
    exp.write_text(json.dumps(export.json()))
    adm.write_text(admin_key)
    assert cli.main(["escrow-open", str(exp), str(out), "--key-file", str(adm)]) == 0
    assert out.read_text() == key
    assert members(archives(fx)[0], out.read_text())
    assert cli.main(["escrow-open", str(exp), str(out), "--key-file", str(adm)]) == 2
    wrong = tmp_path / "wrong.txt"
    wrong.write_text(crypto.generate_keypair()[0])
    assert cli.main(["escrow-open", str(exp), str(tmp_path / "o2"), "--key-file", str(wrong)]) == 1
    # the administrator still cannot restore the personal root in the app
    assert restore((c, h), fx.alice_root, recovery_key=key).status_code == 403
    off = put_escrow(alice, fx.alice_root, enabled=False, password=PASSWORD).json()
    assert off["escrow"] is False
    assert c.post(export_url, headers=h).status_code == 404
    actions = {a.action for a in fx.w.db_rows(AuthAudit)}
    assert {
        "backup.owner_key_created",
        "backup.owner_key_confirmed",
        "backup.escrow_enabled",
        "backup.escrow_exported",
        "backup.escrow_disabled",
    } <= actions
    text = audit_text(fx)
    assert key.removeprefix("fcbk1-") not in text and admin_key.removeprefix("fcbk1-") not in text


def test_consent_at_confirmation_seals_the_key_and_a_rotated_admin_key_is_flagged(fx: Fx) -> None:
    alice = alice_api(fx)
    encrypted_on(fx)
    make_owner_key(fx, alice, escrow=True)
    st = alice.get(key_url(fx.alice_root)).json()
    assert st["escrow"] is True and st["escrow_current"] is True
    fx.admin[0].post("/api/v1/admin/backups/key/rotate", headers=fx.admin[1])
    assert alice.get(key_url(fx.alice_root)).json()["escrow_current"] is False


def test_legacy_instance_key_chains_are_labelled_and_stay_readable(fx: Fx) -> None:
    alice = alice_api(fx)
    alice.upload(fx.taxes, "a.txt", b"old")
    admin_key = encrypted_on(fx, full_every=5)["recovery_key"]
    make_owner_key(fx, alice)
    run_ok(fx)
    # a chain written before M25 was encrypted to the instance key, not to alice's own key
    with fx.w.ctx.sessions() as db:
        row = db.query(BackupRun).filter(BackupRun.status == "ok").one()
        row.key_fingerprint = crypto.recovery_key_fingerprint(admin_key)
        db.commit()
    points = alice.get(f"/nodes/{fx.alice_root}/backups").json()
    assert [p["key_scope"] for p in points] == ["legacy_instance"]
    assert alice.get(key_url(fx.alice_root)).json()["legacy_chains"] == 1
    mine = alice_out(fx)
    assert mine["legacy_chains"] == 1 and mine["key_state"] == "active"


def test_unencrypted_jobs_need_no_owner_key(fx: Fx) -> None:
    alice_api(fx).upload(fx.taxes, "a.txt", b"x")
    enable(fx, **{k: v for k, v in PLAIN.items() if k in ("encrypt", "acknowledge_unencrypted")})
    assert run_ok(fx).encrypted is False
    status = fx.admin[0].get("/api/v1/admin/backups", headers=fx.admin[1]).json()
    assert "owner_keys_pending" not in status["warnings"] and "unencrypted" in status["warnings"]
