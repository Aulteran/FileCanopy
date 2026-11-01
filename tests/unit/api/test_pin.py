"""M12b: the Confidential PIN, its attempt limits and the lock screen (fake clock)."""

import json
import re
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from test_acl import Fx, _api_routes
from test_auth import PASSWORD, World, signed_in
from test_files import Api, code
from test_oidc_mail import SMTP, FakeSMTP, token_of

from app.auth import pin as pin_module
from app.db.models import AuthAudit, AuthSession, ConfidentialUnlock, UserPin
from app.notifications.mail import Mailer

PIN = "482915"
OTHER_PIN = "739104"


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fx:
    return Fx(World(tmp_path, monkeypatch))


def set_pin(api: Api, pin: str = PIN, **proof: Any) -> Any:
    body = {"pin": pin, **(proof or {"password": PASSWORD})}
    return api.client.put("/api/v1/auth/pin", json=body, headers=api.headers)


def conf(fx: Fx, api: Api) -> uuid.UUID:
    r = api.client.put(
        f"/api/v1/nodes/{fx.y2026}/privacy",
        json={"label": "confidential"},
        headers=api.headers,
    )
    assert r.status_code == 200, r.text
    return fx.y2026


def unlock(api: Api, node: uuid.UUID, **secret: Any) -> Any:
    return api.post(f"/nodes/{node}/unlock", json=secret)


def row(fx: Fx, email: str = "alice@example.com") -> UserPin:
    with fx.w.ctx.sessions() as db:
        r = db.get(UserPin, fx.uid(email))
        assert r is not None
        db.expunge(r)
        return r


def relogin(fx: Fx, email: str = "alice") -> Api:
    return Api(fx, email)


# ------------------------------------------------------------------ format and storage


def test_pin_format_and_easy_pins(fx: Fx) -> None:
    alice = Api(fx, "alice")
    for bad in ("12345", "1234567", "abcdef", "12 456", ""):
        r = set_pin(alice, bad)
        assert r.status_code == 422, bad
    for easy in ("000000", "111111", "123456", "654321", "121212", "123123", "345678"):
        r = set_pin(alice, easy)
        assert r.status_code == 422 and code(r) == "PIN_TOO_EASY", easy
    assert set_pin(alice).status_code == 200


def test_pin_is_hashed_with_a_pepper_that_is_not_in_the_database(fx: Fx) -> None:
    alice, bob = Api(fx, "alice"), Api(fx, "bob")
    assert set_pin(alice).status_code == 200 and set_pin(bob).status_code == 200
    a, b = row(fx), row(fx, "bob@example.com")
    assert a.pin_hash.startswith("$argon2id$") and PIN not in a.pin_hash
    assert a.pin_hash != b.pin_hash
    pins = fx.w.ctx.pins
    assert pins._digest(fx.uid("alice@example.com"), PIN) != pins._digest(
        fx.uid("bob@example.com"), PIN
    )
    pepper = fx.w.settings.app_data_path / "pin-pepper"
    assert pepper.stat().st_mode & 0o777 == 0o600 and len(pepper.read_text().strip()) == 64
    # the pepper is not stored in the database
    with fx.w.ctx.sessions() as db:
        dump = json.dumps([str(r) for r in db.scalars(select(UserPin))])
    assert pepper.read_text().strip() not in dump


def test_configured_pepper_must_be_long(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    w = World(tmp_path / "a", monkeypatch, pin_pepper="short")
    with pytest.raises(pin_module.PinError):
        w.ctx.pins._digest(uuid.uuid4(), PIN)
    w2 = World(tmp_path / "b", monkeypatch, pin_pepper="p" * 40)
    assert w2.ctx.pins._digest(uuid.uuid4(), PIN)


# ------------------------------------------------------------------ proofs


def test_setting_a_pin_needs_a_proof_not_just_a_cookie(fx: Fx) -> None:
    alice = Api(fx, "alice")
    r = alice.client.put("/api/v1/auth/pin", json={"pin": PIN}, headers=alice.headers)
    assert r.status_code == 422 and code(r) == "PROOF_REQUIRED"
    r = set_pin(alice, password="wrong password here")
    assert r.status_code == 403 and code(r) == "INVALID_CREDENTIALS"
    r = set_pin(alice, current_pin=PIN)  # no PIN yet: a PIN is not a proof
    assert r.status_code == 422
    r = alice.client.put(
        "/api/v1/auth/pin",
        json={"pin": PIN, "password": PASSWORD, "reauth": True},
        headers=alice.headers,
    )
    assert r.status_code == 422  # exactly one proof
    assert set_pin(alice).status_code == 200


def test_changing_the_pin_with_the_old_pin_or_password(fx: Fx) -> None:
    alice = Api(fx, "alice")
    set_pin(alice)
    assert set_pin(alice, OTHER_PIN, current_pin="000000").status_code == 403
    assert set_pin(alice, OTHER_PIN, current_pin=PIN).status_code == 200
    node = conf(fx, alice)
    assert unlock(alice, node, pin=PIN).status_code == 403
    assert unlock(alice, node, pin=OTHER_PIN).status_code == 200
    # the password also resets (the owner forgot the PIN)
    assert set_pin(alice, PIN, password=PASSWORD).status_code == 200


def test_changing_the_pin_locks_every_folder_again(fx: Fx) -> None:
    alice = Api(fx, "alice")
    set_pin(alice)
    node = conf(fx, alice)
    assert unlock(alice, node, pin=PIN).status_code == 200
    assert alice.get(f"/nodes/{node}/children").status_code == 200
    assert set_pin(alice, OTHER_PIN, current_pin=PIN).status_code == 200
    assert alice.get(f"/nodes/{node}/children").status_code == 403


def test_removing_the_pin_needs_a_proof_and_brings_back_the_password(fx: Fx) -> None:
    alice = Api(fx, "alice")
    set_pin(alice)
    node = conf(fx, alice)
    r = alice.post("/auth/pin/remove", json={})
    assert r.status_code == 422
    assert alice.post("/auth/pin/remove", json={"password": PASSWORD}).status_code == 200
    assert unlock(alice, node, password=PASSWORD).status_code == 200


# ------------------------------------------------------------------ unlocking with it


def test_a_pin_replaces_the_password_for_confidential(fx: Fx) -> None:
    alice = Api(fx, "alice")
    node = conf(fx, alice)
    assert unlock(alice, node, pin=PIN).status_code == 409  # no PIN yet
    set_pin(alice)
    r = unlock(alice, node, password=PASSWORD)  # a stolen password is no longer enough
    assert r.status_code == 403 and code(r) == "PIN_REQUIRED"
    assert unlock(alice, node, pin="000001").status_code == 403
    assert unlock(alice, node, pin=PIN).status_code == 200
    assert alice.get(f"/nodes/{node}/children").status_code == 200


def test_lowering_confidential_takes_the_pin(fx: Fx) -> None:
    alice = Api(fx, "alice")
    set_pin(alice)
    node = conf(fx, alice)
    unlock(alice, node, pin=PIN)
    url = f"/api/v1/nodes/{node}/privacy"
    r = alice.client.put(url, json={"label": "normal", "password": PASSWORD}, headers=alice.headers)
    assert r.status_code == 403 and code(r) == "PIN_REQUIRED"
    r = alice.client.put(url, json={"label": "normal", "pin": PIN}, headers=alice.headers)
    assert r.status_code == 200


def test_an_account_without_a_password_needs_a_pin_first(fx: Fx) -> None:
    alice = Api(fx, "alice")
    node = conf(fx, alice)
    with fx.w.ctx.sessions() as db:
        u = db.get(__import__("app.db.models", fromlist=["User"]).User, alice.uid)
        assert u is not None
        u.password_hash = None
        db.commit()
    r = unlock(alice, node, password=PASSWORD)
    assert r.status_code == 403 and code(r) == "PIN_REQUIRED"


# ------------------------------------------------------------------ guessing


def wrong(api: Api, node: uuid.UUID, pin: str = "000001") -> Any:
    return unlock(api, node, pin=pin)


def test_waiting_starts_at_the_third_failure_and_blocked_attempts_do_not_compare(
    fx: Fx, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = Api(fx, "alice")
    set_pin(alice)
    node = conf(fx, alice)
    assert wrong(alice, node).status_code == 403
    assert wrong(alice, node).status_code == 403
    assert wrong(alice, node).status_code == 403  # third: the next one waits
    compared: list[str] = []
    real = fx.w.ctx.passwords.verify
    monkeypatch.setattr(fx.w.ctx.passwords, "verify", lambda s, p: compared.append(p) or real(s, p))
    r = unlock(alice, node, pin=PIN)  # even the right PIN is refused while waiting
    assert r.status_code == 429 and code(r) == "PIN_BLOCKED"
    assert 1 <= int(r.headers["Retry-After"]) <= 31
    assert compared == []
    fx.w.advance(seconds=31)
    assert wrong(alice, node).status_code == 403  # fourth
    r = unlock(alice, node, pin=PIN)
    assert r.status_code == 429 and 30 < int(r.headers["Retry-After"]) <= 61
    fx.w.advance(seconds=61)
    assert unlock(alice, node, pin=PIN).status_code == 200
    assert row(fx).failures == 0 and row(fx).blocked_until is None


def test_the_wait_schedule() -> None:
    assert [pin_module.wait_after(n) for n in range(1, 12)] == [
        0, 0, 30, 60, 120, 240, 480, 900, 900, 900, 900,
    ]  # fmt: skip


def test_an_attempt_is_counted_before_the_pin_is_compared(
    fx: Fx, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = Api(fx, "alice")
    set_pin(alice)
    node = conf(fx, alice)
    seen: list[int] = []
    real = fx.w.ctx.passwords.verify

    def spy(stored: str | None, candidate: str) -> bool:
        seen.append(row(fx).failures)  # as committed by then
        return real(stored, candidate)

    monkeypatch.setattr(fx.w.ctx.passwords, "verify", spy)
    unlock(alice, node, pin=PIN)
    assert seen == [1]
    assert row(fx).failures == 0  # and forgiven on success


def test_five_failures_end_the_session_and_the_counter_survives_new_sessions(
    fx: Fx,
) -> None:
    alice = Api(fx, "alice")
    set_pin(alice)
    node = conf(fx, alice)
    for _ in range(4):
        assert wrong(alice, node).status_code == 403
        fx.w.advance(seconds=1000)
    r = wrong(alice, node)
    assert r.status_code == 401 and code(r) == "SESSION_ENDED_TOO_MANY_UNLOCK_FAILURES"
    assert alice.get("/auth/session").status_code == 401
    # a new sign-in does not start from zero
    again = relogin(fx)
    assert row(fx).failures == 5
    fx.w.advance(seconds=1000)
    assert unlock(again, node, pin=PIN).status_code == 200  # the right PIN still works at 5
    assert row(fx).failures == 0


def test_ten_failures_disable_the_pin_until_a_stronger_proof(fx: Fx) -> None:
    alice = Api(fx, "alice")
    set_pin(alice)
    node = conf(fx, alice)
    for i in range(10):
        fx.w.advance(seconds=1000)
        if i >= 4:
            alice = relogin(fx)
        wrong(alice, node)
    assert row(fx).disabled and row(fx).failures == 10
    alice = relogin(fx)
    fx.w.advance(seconds=5000)
    r = unlock(alice, node, pin=PIN)
    assert r.status_code == 403 and code(r) == "PIN_DISABLED"
    st = alice.get("/auth/pin").json()
    assert st["set"] and st["disabled"]
    # the password resets it, the counter starts again
    assert set_pin(alice, OTHER_PIN, password=PASSWORD).status_code == 200
    assert row(fx).failures == 0 and not row(fx).disabled
    assert unlock(alice, node, pin=OTHER_PIN).status_code == 200


def test_current_pin_proof_counts_as_a_guess_too(fx: Fx) -> None:
    alice = Api(fx, "alice")
    set_pin(alice)
    assert set_pin(alice, OTHER_PIN, current_pin="000001").status_code == 403
    assert row(fx).failures == 1


def test_failures_are_audited_without_the_pin(fx: Fx) -> None:
    alice = Api(fx, "alice")
    set_pin(alice)
    node = conf(fx, alice)
    wrong(alice, node, "135792")
    unlock(alice, node, pin=PIN)
    with fx.w.ctx.sessions() as db:
        rows = [(a.action, a.details) for a in db.scalars(select(AuthAudit))]
    text = json.dumps(rows)
    assert "pin.failed" in text and "pin.set" in text
    assert "135792" not in text and PIN not in text and PASSWORD not in text


# ------------------------------------------------------------------ lock screen


def test_locking_needs_a_pin(fx: Fx) -> None:
    alice = Api(fx, "alice")
    r = alice.post("/auth/lock-screen")
    assert r.status_code == 409 and code(r) == "PIN_NOT_SET"


def test_lock_screen_keeps_the_session_but_shuts_everything_else(fx: Fx) -> None:
    alice = Api(fx, "alice")
    set_pin(alice)
    node = conf(fx, alice)
    unlock(alice, node, pin=PIN)
    assert alice.post("/auth/lock-screen").status_code == 200
    with fx.w.ctx.sessions() as db:
        assert db.scalars(select(ConfidentialUnlock)).all() == []  # folders locked too
    s = alice.get("/auth/session")
    assert s.status_code == 200 and s.json()["locked"] is True
    r = alice.get(f"/nodes/{node}/children")
    assert r.status_code == 423 and code(r) == "SESSION_LOCKED"
    assert alice.get("/roots").status_code == 423
    # wrong PIN on the screen counts
    r = alice.post("/auth/lock-screen/unlock", json={"pin": "000001"})
    assert r.status_code == 403 and code(r) == "INVALID_PIN"
    assert alice.post("/auth/lock-screen/unlock", json={"pin": PIN}).status_code == 200
    assert alice.get("/auth/session").json()["locked"] is False
    assert alice.get("/roots").status_code == 200
    # coming back does not reopen Confidential folders
    assert alice.get(f"/nodes/{node}/children").status_code == 403


def test_a_locked_session_can_only_unlock_or_sign_out(fx: Fx) -> None:
    alice = Api(fx, "alice")
    set_pin(alice)
    assert alice.post("/auth/lock-screen").status_code == 200
    open_routes = {
        ("GET", "/api/v1/auth/session"),
        ("GET", "/api/v1/auth/pin"),
        ("POST", "/api/v1/auth/lock-screen/unlock"),
        ("POST", "/api/v1/auth/logout"),
    }
    public = (
        "/api/v1/setup/",
        "/api/v1/auth/login",
        "/api/v1/auth/config",
        "/api/v1/auth/password-reset/",
        "/api/v1/auth/invitations/",
        "/api/v1/auth/oidc/login/start",
        "/api/v1/auth/oidc/callback",
        "/api/v1/auth/oidc/invitation/start",
        "/api/v1/health",
        "/api/v1/ready",
    )
    checked = 0
    for r in _api_routes(fx.w.app.routes):
        if not r.path.startswith("/api/v1"):
            continue
        if r.path.startswith(public):
            continue
        path = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), r.path)
        for method in sorted(r.methods - {"HEAD", "OPTIONS"}):
            if (method, path) in open_routes:
                continue
            resp = alice.client.request(method, path, headers=alice.headers, json={})
            assert resp.status_code in (423, 401), (method, r.path, resp.status_code)
            checked += 1
    assert checked > 40
    assert alice.post("/auth/logout").status_code == 200


def test_unlocking_the_screen_is_throttled_like_any_pin_check(fx: Fx) -> None:
    alice = Api(fx, "alice")
    set_pin(alice)
    alice.post("/auth/lock-screen")
    for _ in range(3):
        assert alice.post("/auth/lock-screen/unlock", json={"pin": "000001"}).status_code == 403
    r = alice.post("/auth/lock-screen/unlock", json={"pin": PIN})
    assert r.status_code == 429


def test_lock_survives_a_reload_and_other_sessions_are_unaffected(fx: Fx) -> None:
    alice = Api(fx, "alice")
    set_pin(alice)
    other = Api(fx, "alice")
    alice.post("/auth/lock-screen")
    assert other.get("/roots").status_code == 200
    with fx.w.ctx.sessions() as db:
        assert sum(1 for s in db.scalars(select(AuthSession)) if s.locked_at) == 1


# ------------------------------------------------------------------ emailed link


def mail_fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fx:
    w = World(tmp_path, monkeypatch, **SMTP)
    FakeSMTP.sent = []
    w.ctx.mailer = Mailer(w.settings, FakeSMTP)  # type: ignore[arg-type]
    w.ctx.mailer.send_in_background = lambda *m: w.ctx.mailer.send(*m)  # type: ignore[method-assign]
    return Fx(w)


def test_emailed_link_resets_the_pin_once_and_only_in_the_same_signed_in_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = mail_fx(tmp_path, monkeypatch)
    alice, bob = Api(fx, "alice"), Api(fx, "bob")
    set_pin(alice)
    FakeSMTP.sent = []
    assert alice.post("/auth/pin/reset-email").status_code == 202
    assert len(FakeSMTP.sent) == 1
    msg = FakeSMTP.sent[0]
    assert msg["To"] == "alice@example.com" and "/pin-reset#token=" in msg.get_content()
    token = token_of(msg)
    # a minute has not passed: no second mail, and no new token
    alice.post("/auth/pin/reset-email")
    assert len(FakeSMTP.sent) == 1
    # someone else's session cannot redeem it
    assert set_pin(bob, OTHER_PIN, email_token=token).status_code == 400
    # a request without any session cannot either
    anon = fx.w.browser()
    assert anon.put("/api/v1/auth/pin", json={"pin": PIN, "email_token": token}).status_code == 401
    assert set_pin(alice, OTHER_PIN, email_token=token).status_code == 200
    assert set_pin(alice, PIN, email_token=token).status_code == 400  # one time
    node = conf(fx, alice)
    assert unlock(alice, node, pin=OTHER_PIN).status_code == 200


def test_email_reset_needs_mail(fx: Fx) -> None:
    alice = Api(fx, "alice")
    set_pin(alice)
    r = alice.post("/auth/pin/reset-email")
    assert r.status_code == 409 and code(r) == "EMAIL_NOT_CONFIGURED"
    assert alice.get("/auth/pin").json()["email_reset_available"] is False


def test_an_expired_link_does_not_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = mail_fx(tmp_path, monkeypatch)
    alice = Api(fx, "alice")
    set_pin(alice)
    FakeSMTP.sent = []
    alice.post("/auth/pin/reset-email")
    token = token_of(FakeSMTP.sent[0])
    fx.w.advance(seconds=fx.w.settings.reset_ttl_seconds + 1)
    alice = relogin(fx)
    assert set_pin(alice, OTHER_PIN, email_token=token).status_code == 400


def test_status_for_a_new_account(fx: Fx) -> None:
    alice = Api(fx, "alice")
    st = alice.get("/auth/pin").json()
    assert st == {
        "set": False,
        "disabled": False,
        "blocked_for": None,
        "has_password": True,
        "reauth_available": False,
        "email_reset_available": False,
        "locked": False,
    }
    set_pin(alice)
    assert alice.get("/auth/session").json()["pin_set"] is True
    _ = timedelta, signed_in
