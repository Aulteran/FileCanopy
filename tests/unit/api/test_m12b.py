"""M12b: OIDC-only mode and its safety nets, Google specifics, re-authentication, break-glass."""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_auth import PASSWORD, World, admin_session, invite, login, signed_in
from test_oidc_mail import (
    PUBLIC,
    Idp,
    audit_text,
    callback,
    link,
    oidc_world,
    outcome,
    sessions_ok,
    sso_login,
    start,
)

from app.auth import cli
from app.config import Settings
from app.db.models import AppSetting, AuthAudit, User, UserIdentity


@pytest.fixture
def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., World]:
    def factory(**overrides: Any) -> World:
        return World(tmp_path, monkeypatch, **overrides)

    return factory


@pytest.fixture
def idp() -> Idp:
    return Idp()


GOOGLE = "https://accounts.google.com"
CSRF = "X-CSRF-Token"


def reauth(
    w: World,
    idp: Idp,
    c: TestClient,
    h: dict[str, str],
    sub: str = "sub-ada",
    **kw: Any,
) -> str:
    r = c.post("/api/v1/auth/oidc/reauth/start", headers=h)
    assert r.status_code == 200, r.text
    return outcome(callback(c, idp, idp.authorize(r.json()["authorization_url"], sub, **kw)))


def only_world(
    make_world: Callable[..., World], idp: Idp, **kw: Any
) -> tuple[World, TestClient, dict[str, str]]:
    w = oidc_world(make_world, idp, **kw)
    c, h = admin_session(w)
    assert link(w, idp, c, h) == "linked"
    return w, c, h


def put_mode(c: TestClient, h: dict[str, str], enabled: bool = True, **body: Any) -> Any:
    return c.put("/api/v1/admin/oidc-only", json={"enabled": enabled, **body}, headers=h)


def turn_on(w: World, idp: Idp, c: TestClient, h: dict[str, str], **body: Any) -> Any:
    assert reauth(w, idp, c, h) == "reauthed"
    r = put_mode(c, h, **body)
    assert r.status_code == 200, r.text
    return r


def code(r: Any) -> str:
    return str(r.json()["detail"]["code"])


def onboard_member(w: World, c: TestClient, h: dict[str, str], email: str) -> None:
    token = invite(w, (c, h), email)
    r = w.browser().post(
        "/api/v1/auth/invitations/accept", json={"token": token, "password": PASSWORD}
    )
    assert r.status_code == 200


# ---------------------------------------------------------------- turning it on: guards


def test_off_by_default_and_reported(make_world: Callable[..., World], idp: Idp) -> None:
    w, c, h = only_world(make_world, idp)
    st = c.get("/api/v1/admin/oidc-only").json()
    assert st["enabled"] is False and st["effective"] is False
    assert st["oidc_configured"] and st["admin_linked"] and st["linked_admins"] == 1
    assert c.get("/api/v1/auth/config").json()["password_login"] is True


def test_cannot_turn_on_without_oidc(make_world: Callable[..., World]) -> None:
    w = make_world()
    c, h = admin_session(w)
    r = put_mode(c, h)
    assert r.status_code == 409 and code(r) == "OIDC_NOT_CONFIGURED"


def test_cannot_turn_on_unless_the_admin_has_a_link(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w = oidc_world(make_world, idp)
    c, h = admin_session(w)
    r = put_mode(c, h)
    assert r.status_code == 409 and code(r) == "ADMIN_NOT_LINKED"


def test_cannot_turn_on_without_a_fresh_provider_check(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    r = put_mode(c, h)
    assert r.status_code == 403 and code(r) == "REAUTH_REQUIRED"
    assert "auth.oidc_only_blocked" in audit_text(w)
    # an old check does not count either (ten minutes)
    assert reauth(w, idp, c, h) == "reauthed"
    w.advance(minutes=11)
    assert put_mode(c, h).status_code == 403
    assert c.get("/api/v1/admin/oidc-only").json()["enabled"] is False


def test_the_provider_check_is_single_use(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    turn_on(w, idp, c, h)
    assert put_mode(c, h, False).status_code == 200  # turning off needs nothing
    assert put_mode(c, h).status_code == 403  # the earlier check is spent


def test_the_check_must_be_for_the_same_person(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    onboard_member(w, c, h, "bob@example.com")
    bc, bh = signed_in(w, "bob@example.com")
    assert link(w, idp, bc, bh, "sub-bob") == "linked"
    # Bob's provider account answering Ada's re-authentication proves nothing about Ada
    assert reauth(w, idp, c, h, "sub-bob") == "OIDC_REAUTH_MISMATCH"
    assert put_mode(c, h).status_code == 403


def test_a_stale_provider_login_is_not_a_reauthentication(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    old = int(time.time()) - 3600
    assert reauth(w, idp, c, h, auth_time=old) == "OIDC_REAUTH_STALE"
    assert reauth(w, idp, c, h, auth_time=int(time.time()) - 5) == "reauthed"


def test_reauth_asks_the_provider_to_check_again(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    url = c.post("/api/v1/auth/oidc/reauth/start", headers=h).json()["authorization_url"]
    assert "max_age=0" in url and "prompt=login" in url
    plain = start(w.browser(), "login")
    assert "max_age" not in plain and "prompt" not in plain


def test_reauth_needs_a_link_and_a_session(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w = oidc_world(make_world, idp)
    c, h = admin_session(w)
    r = c.post("/api/v1/auth/oidc/reauth/start", headers=h)
    assert r.status_code == 409 and code(r) == "OIDC_NOT_LINKED"
    assert w.browser().post("/api/v1/auth/oidc/reauth/start").status_code == 401


def test_people_without_a_link_must_be_acknowledged(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    onboard_member(w, c, h, "bob@example.com")
    assert reauth(w, idp, c, h) == "reauthed"
    r = put_mode(c, h)
    assert r.status_code == 409 and r.json()["detail"] == {
        "code": "UNLINKED_USERS",
        "count": 1,
    }
    assert put_mode(c, h, confirm_unlinked_users=2).status_code == 409
    # the refusals did not spend the provider check
    assert put_mode(c, h, confirm_unlinked_users=1).status_code == 200
    assert c.get("/api/v1/admin/oidc-only").json()["unlinked_users"] == 1


# ---------------------------------------------------------------- what it does


def test_password_routes_are_closed_and_the_provider_still_works(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    turn_on(w, idp, c, h)
    st = c.get("/api/v1/admin/oidc-only").json()
    assert st["enabled"] and st["effective"]
    cfg = w.browser().get("/api/v1/auth/config").json()
    assert cfg["password_login"] is False and cfg["password_reset_by_email"] is False
    anon = w.browser()
    r = login(anon, "admin@example.com")
    assert r.status_code == 403 and code(r) == "PASSWORD_LOGIN_DISABLED"
    r = login(anon, "nobody@example.com", "x" * 14)  # the same answer for everyone
    assert code(r) == "PASSWORD_LOGIN_DISABLED"
    for path, body in [
        ("/api/v1/auth/password-reset/request", {"email": "admin@example.com"}),
        ("/api/v1/auth/password-reset/complete", {"token": "t", "password": PASSWORD}),
        ("/api/v1/auth/invitations/accept", {"token": "t", "password": PASSWORD}),
    ]:
        r = anon.post(path, json=body)
        assert r.status_code == 403 and code(r) == "PASSWORD_LOGIN_DISABLED", path
    sso, result = sso_login(w, idp, "sub-ada", email=None)
    assert result == "signed_in" and sessions_ok(sso)
    assert sessions_ok(c)  # signed-in sessions keep working


def test_turning_it_off_brings_passwords_back_and_is_audited(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    turn_on(w, idp, c, h)
    assert put_mode(c, h, False).status_code == 200
    assert login(w.browser(), "admin@example.com").status_code == 200
    actions = [a.action for a in w.db_rows(AuthAudit)]
    assert "auth.oidc_only_on" in actions and "auth.oidc_only_off" in actions


def test_removing_the_provider_configuration_brings_passwords_back(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    turn_on(w, idp, c, h)
    # same database, provider variables gone: the switch is still "on" but inert
    values = {k: v for k, v in w.settings.model_dump().items() if not k.startswith("oidc_")}
    w.ctx.settings = Settings(**{**values, "database_url": "postgresql+psycopg://u:p@h/db"})
    w.app.state.auth.settings = w.ctx.settings
    assert login(w.browser(), "admin@example.com").status_code == 200
    st = c.get("/api/v1/admin/oidc-only").json()
    assert st["enabled"] is True and st["effective"] is False and not st["oidc_configured"]


def test_force_local_login_overrides_the_switch(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    turn_on(w, idp, c, h)
    w.ctx.settings.force_local_login = True
    assert login(w.browser(), "admin@example.com").status_code == 200
    st = c.get("/api/v1/admin/oidc-only").json()
    assert st["enabled"] and not st["effective"] and st["force_local_login"]
    assert reauth(w, idp, c, h) == "reauthed"
    r = put_mode(c, h)
    assert r.status_code == 409 and code(r) == "FORCE_LOCAL_LOGIN_SET"


def test_command_line_recovery(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    turn_on(w, idp, c, h)
    lines: list[str] = []
    assert cli.run(["status"], w.settings, w.ctx.sessions, lines.append) == 0
    assert "oidc-only in force: True" in lines
    assert login(w.browser(), "admin@example.com").status_code == 403
    assert cli.run(["oidc-only", "off"], w.settings, w.ctx.sessions, lines.append) == 0
    assert login(w.browser(), "admin@example.com").status_code == 200
    with w.ctx.sessions() as db:
        row = db.get(AppSetting, "auth.oidc_only")
        assert row is not None and row.value is False
        last = [a for a in db.scalars(select(AuthAudit)) if a.action == "auth.oidc_only_off"][-1]
        assert last.details["via"] == "cli" and last.actor_user_id is None
    cli.run(["oidc-only", "off"], w.settings, w.ctx.sessions, lines.append)
    assert lines[-1] == "already off"
    with pytest.raises(SystemExit):
        cli.run(["oidc-only", "on"], w.settings, w.ctx.sessions, lines.append)


def test_nobody_can_unlink_themselves_while_it_is_on(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    turn_on(w, idp, c, h)
    r = c.post("/api/v1/auth/oidc/unlink", json={"password": PASSWORD}, headers=h)
    assert r.status_code == 409 and code(r) == "OIDC_ONLY_ACTIVE"
    assert len(w.db_rows(UserIdentity)) == 1


def test_the_last_administrator_who_can_sign_in_cannot_be_removed(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    token = invite(w, (c, h), "admin2@example.com", "admin")
    w.browser().post("/api/v1/auth/invitations/accept", json={"token": token, "password": PASSWORD})
    c2, h2 = signed_in(w, "admin2@example.com")  # a session that predates the switch
    turn_on(w, idp, c, h, confirm_unlinked_users=1)
    ada = next(u for u in w.db_rows(User) if u.email == "admin@example.com")
    r = c2.delete(f"/api/v1/admin/users/{ada.id}/identity", headers=h2)
    assert r.status_code == 409 and code(r) == "LAST_LINKED_ADMIN"
    r = c2.patch(f"/api/v1/admin/users/{ada.id}", json={"status": "disabled"}, headers=h2)
    assert r.status_code == 409 and code(r) == "LAST_LINKED_ADMIN"
    r = c2.patch(f"/api/v1/admin/users/{ada.id}", json={"role": "member"}, headers=h2)
    assert r.status_code == 409 and code(r) == "LAST_LINKED_ADMIN"
    assert sessions_ok(c)


# ---------------------------------------------------------------- people without a password


def test_invitation_through_the_provider_needs_no_password_in_this_mode(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    token = invite(w, (c, h), "nina@example.com")
    turn_on(w, idp, c, h, confirm_unlinked_users=1)
    anon = w.browser()
    r = anon.post(
        "/api/v1/auth/oidc/invitation/start",
        json={"token": token, "password": PASSWORD},
    )
    assert r.status_code == 422 and code(r) == "PASSWORD_NOT_ACCEPTED"
    url = start(anon, "invitation", token=token, name="Nina")
    result = outcome(callback(anon, idp, idp.authorize(url, "sub-nina", email="nina@example.com")))
    assert result == "welcome" and sessions_ok(anon)
    nina = next(u for u in w.db_rows(User) if u.email == "nina@example.com")
    assert nina.status == "active" and nina.password_hash is None


def test_password_is_still_required_when_the_mode_is_off(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    token = invite(w, (c, h), "nina@example.com")
    r = w.browser().post("/api/v1/auth/oidc/invitation/start", json={"token": token})
    assert r.status_code == 422 and code(r) == "PASSWORD_REQUIRED"


def test_a_person_without_a_password_sets_a_pin_through_the_provider(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    token = invite(w, (c, h), "nina@example.com")
    turn_on(w, idp, c, h, confirm_unlinked_users=1)
    nc = w.browser()
    url = start(nc, "invitation", token=token)
    outcome(callback(nc, idp, idp.authorize(url, "sub-nina", email="nina@example.com")))
    nh = {CSRF: nc.get("/api/v1/auth/session").json()["csrf_token"]}
    st = nc.get("/api/v1/auth/pin").json()
    assert st["has_password"] is False and st["reauth_available"] is True
    body = {"pin": "482915"}
    assert nc.put("/api/v1/auth/pin", json=body, headers=nh).status_code == 422
    r = nc.put("/api/v1/auth/pin", json={**body, "password": PASSWORD}, headers=nh)
    assert r.status_code == 403  # there is no password to prove
    # a provider answer without auth_time (Google) proves nothing about who is at the keyboard
    weak = reauth(w, idp, nc, nh, "sub-nina", email="nina@example.com")
    assert weak == "reauthed"
    r = nc.put("/api/v1/auth/pin", json={**body, "reauth": True}, headers=nh)
    assert r.status_code == 403 and code(r) == "REAUTH_REQUIRED"
    fresh = int(time.time()) - 3
    assert reauth(w, idp, nc, nh, "sub-nina", email="nina@example.com", auth_time=fresh) == (
        "reauthed"
    )
    r = nc.put("/api/v1/auth/pin", json={**body, "reauth": True}, headers=nh)
    assert r.status_code == 200, r.text
    # one check, one use
    r = nc.put("/api/v1/auth/pin", json={"pin": "739104", "reauth": True}, headers=nh)
    assert r.status_code == 403 and code(r) == "REAUTH_REQUIRED"
    assert nc.get("/api/v1/auth/session").json()["pin_set"] is True


def test_re_enabling_a_linked_person_without_a_password_keeps_them_active(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    token = invite(w, (c, h), "nina@example.com")
    turn_on(w, idp, c, h, confirm_unlinked_users=1)
    nc = w.browser()
    outcome(
        callback(
            nc,
            idp,
            idp.authorize(
                start(nc, "invitation", token=token),
                "sub-nina",
                email="nina@example.com",
            ),
        )
    )
    nina = next(u for u in w.db_rows(User) if u.email == "nina@example.com")
    assert c.patch(f"/api/v1/admin/users/{nina.id}", json={"status": "disabled"}, headers=h)
    r = c.patch(f"/api/v1/admin/users/{nina.id}", json={"status": "active"}, headers=h)
    assert r.json()["status"] == "active"


# ---------------------------------------------------------------- Google


def google_world(
    make_world: Callable[..., World],
    idp: Idp,
    **kw: Any,
) -> World:
    idp.issuer_in_discovery = GOOGLE
    return oidc_world(make_world, idp, oidc_issuer=GOOGLE, **kw)


def test_google_issuer_spellings_are_accepted_only_for_google(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w = google_world(make_world, idp)
    c, h = admin_session(w)
    assert link(w, idp, c, h, iss=GOOGLE) == "linked"
    _, r = sso_login(w, idp, "sub-ada", iss=GOOGLE)
    assert r == "signed_in"
    _, r = sso_login(w, idp, "sub-ada", iss="accounts.google.com")  # Google's other spelling
    assert r == "signed_in"
    _, r = sso_login(w, idp, "sub-ada", iss="https://evil.example.com")
    assert r == "ID_TOKEN_INVALID"
    _, r = sso_login(w, idp, "sub-ada", iss="https://accounts.google.com.evil.example")
    assert r == "ID_TOKEN_INVALID"
    # the stored link names the configured issuer, whichever spelling the token used
    assert {i.issuer for i in w.db_rows(UserIdentity)} == {GOOGLE}


def test_the_bare_google_spelling_means_nothing_for_other_providers(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w = oidc_world(make_world, idp)
    c, h = admin_session(w)
    assert link(w, idp, c, h) == "linked"
    _, r = sso_login(w, idp, "sub-ada", iss="accounts.google.com")
    assert r == "ID_TOKEN_INVALID"


def test_google_never_links_by_email(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w = google_world(make_world, idp)
    admin_session(w)
    c, r = sso_login(w, idp, "1234567890", iss=GOOGLE, email="admin@example.com")
    assert r == "OIDC_NOT_LINKED" and not sessions_ok(c)
    assert not w.db_rows(UserIdentity)


@pytest.mark.parametrize(
    ("kw", "email"),
    [
        ({"verified": False}, "nina@example.com"),
        (
            {"email_verified": "true"},
            "nina@example.com",
        ),  # not the boolean Google sends
        ({"email_verified": None}, "nina@example.com"),
        (
            {},
            "nina.l@gmail.com",
        ),  # Gmail ignores dots; the invited address is a different one
        ({}, "nina+x@example.com"),
    ],
)
def test_google_invitation_needs_a_verified_exact_address(
    make_world: Callable[..., World],
    idp: Idp,
    kw: dict[str, Any],
    email: str,
) -> None:
    w = google_world(make_world, idp)
    adm = admin_session(w)
    token = invite(w, adm, "nina@example.com")
    idp.userinfo = {"sub": "sub-new"}
    c = w.browser()
    url = start(c, "invitation", token=token, password=PASSWORD)
    code_ = idp.authorize(url, "sub-new", iss=GOOGLE, email=email, **kw)
    assert outcome(callback(c, idp, code_)) == "OIDC_EMAIL_MISMATCH"
    assert not w.db_rows(UserIdentity)


def test_google_invitation_succeeds_with_a_verified_address(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w = google_world(make_world, idp)
    adm = admin_session(w)
    token = invite(w, adm, "nina@example.com")
    c = w.browser()
    url = start(c, "invitation", token=token, password=PASSWORD)
    code_ = idp.authorize(url, "108234", iss=GOOGLE, email="Nina@Example.com")
    assert outcome(callback(c, idp, code_)) == "welcome" and sessions_ok(c)


def test_google_hosted_domain_is_checked_on_the_token(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w = google_world(make_world, idp, oidc_hosted_domain="example.com")
    c, h = admin_session(w)
    url = start(c, "link", h, password=PASSWORD)
    assert "hd=example.com" in url  # a hint for the account chooser, not the control
    # the hint can be stripped by the person: the token decides
    assert (
        outcome(callback(c, idp, idp.authorize(url, "s", iss=GOOGLE))) == "OIDC_DOMAIN_NOT_ALLOWED"
    )
    url = start(c, "link", h, password=PASSWORD)
    r = outcome(callback(c, idp, idp.authorize(url, "s", iss=GOOGLE, hd="other.example")))
    assert r == "OIDC_DOMAIN_NOT_ALLOWED"
    url = start(c, "link", h, password=PASSWORD)
    r = outcome(callback(c, idp, idp.authorize(url, "s", iss=GOOGLE, hd="Example.com")))
    assert r == "linked"


def test_hosted_domain_is_a_google_only_setting() -> None:
    base: dict[str, Any] = {
        "database_url": "postgresql+psycopg://u:p@h/db",
        "oidc_client_id": "id",
        "public_url": PUBLIC,
        "oidc_hosted_domain": "example.com",
    }
    with pytest.raises(ValueError):
        Settings(**base, oidc_issuer="https://idp.example.org")
    assert Settings(**base, oidc_issuer=GOOGLE).oidc_is_google


def test_google_reauth_uses_the_account_chooser(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w = google_world(make_world, idp)
    c, h = admin_session(w)
    assert link(w, idp, c, h, iss=GOOGLE) == "linked"
    url = c.post("/api/v1/auth/oidc/reauth/start", headers=h).json()["authorization_url"]
    assert "prompt=select_account" in url and "max_age=0" in url and "prompt=login" not in url


def test_admin_config_view_names_google(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w = google_world(make_world, idp, oidc_hosted_domain="example.com")
    c, _ = admin_session(w)
    cfg = c.get("/api/v1/admin/auth-config").json()["oidc"]
    assert cfg["google"] is True and cfg["hosted_domain"] == "example.com"
    assert "client_secret" not in str(cfg)


_ = (httpx, jwt)


def test_a_link_invitation_brings_an_unlinked_person_back(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    onboard_member(w, c, h, "bob@example.com")
    turn_on(w, idp, c, h, confirm_unlinked_users=1)
    bob = next(u for u in w.db_rows(User) if u.email == "bob@example.com")
    assert login(w.browser(), "bob@example.com").status_code == 403
    r = c.post(f"/api/v1/admin/users/{bob.id}/invitation", headers=h)
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    anon = w.browser()
    r = anon.post("/api/v1/auth/oidc/invitation/start", json={"token": token, "password": PASSWORD})
    assert r.status_code == 422 and code(r) == "PASSWORD_NOT_ACCEPTED"
    # the password route cannot redeem it, and a wrong provider address is refused
    bad = anon.post("/api/v1/auth/invitations/accept", json={"token": token, "password": PASSWORD})
    assert bad.status_code == 403
    url = start(anon, "invitation", token=token)
    mismatch = idp.authorize(url, "sub-bob", email="someone.else@example.com")
    assert outcome(callback(anon, idp, mismatch)) == "OIDC_EMAIL_MISMATCH"
    r = c.post(f"/api/v1/admin/users/{bob.id}/invitation", headers=h)
    token = r.json()["token"]
    url = start(anon, "invitation", token=token)
    code_ = idp.authorize(url, "sub-bob", email="bob@example.com")
    assert outcome(callback(anon, idp, code_)) == "welcome" and sessions_ok(anon)
    again = w.browser()
    assert sso_login(w, idp, "sub-bob", email=None)[1] == "signed_in"
    assert again is not None
    # linked people are not offered one, and the password is untouched
    r = c.post(f"/api/v1/admin/users/{bob.id}/invitation", headers=h)
    assert r.status_code == 409 and code(r) == "NOT_INVITED"


def test_link_invitations_exist_only_in_provider_only_mode(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    onboard_member(w, c, h, "bob@example.com")
    bob = next(u for u in w.db_rows(User) if u.email == "bob@example.com")
    r = c.post(f"/api/v1/admin/users/{bob.id}/invitation", headers=h)
    assert r.status_code == 409 and code(r) == "NOT_INVITED"


def test_admin_reset_tokens_are_refused_in_provider_only_mode(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    onboard_member(w, c, h, "bob@example.com")
    turn_on(w, idp, c, h, confirm_unlinked_users=1)
    bob = next(u for u in w.db_rows(User) if u.email == "bob@example.com")
    r = c.post(f"/api/v1/admin/users/{bob.id}/password-reset", headers=h)
    assert r.status_code == 403 and code(r) == "PASSWORD_LOGIN_DISABLED"


def test_a_password_holder_cannot_replace_the_pin_through_a_weak_provider_check(
    make_world: Callable[..., World],
    idp: Idp,
) -> None:
    w, c, h = only_world(make_world, idp)
    assert reauth(w, idp, c, h) == "reauthed"  # no auth_time, as Google sends
    body = {"pin": "482915", "reauth": True}
    r = c.put("/api/v1/auth/pin", json=body, headers=h)
    assert r.status_code == 403 and code(r) == "REAUTH_REQUIRED"
    assert reauth(w, idp, c, h, auth_time=int(time.time())) == "reauthed"
    assert c.put("/api/v1/auth/pin", json=body, headers=h).status_code == 200


def test_wrong_passwords_on_the_pin_routes_end_the_session_at_five(
    make_world: Callable[..., World],
) -> None:
    w = make_world()
    c, h = admin_session(w)
    for i in range(4):
        r = c.put(
            "/api/v1/auth/pin", json={"pin": "482915", "password": "wrong password!!"}, headers=h
        )
        assert r.status_code == 403, i
        w.advance(seconds=1)
    r = c.put("/api/v1/auth/pin", json={"pin": "482915", "password": "wrong password!!"}, headers=h)
    assert r.status_code == 401 and code(r) == "SESSION_ENDED_TOO_MANY_UNLOCK_FAILURES"
    assert not sessions_ok(c)
