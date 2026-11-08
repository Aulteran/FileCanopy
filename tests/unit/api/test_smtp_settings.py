"""Outgoing mail set up and tested from the web UI instead of environment variables."""

import base64
import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from test_auth import PASSWORD, World, admin_session, invite, make_world, signed_in
from test_oidc_mail import FakeSMTP, mail_world

__all__ = ["make_world"]

from app.db.models import AppSetting, AuthAudit
from app.notifications.mail import Mailer, SmtpConfig

KEY = base64.b64encode(bytes(range(32))).decode()
BODY: dict[str, Any] = {
    "host": "relay.example.net",
    "port": 465,
    "security": "ssl",
    "username": "mailer",
    "password": "hunter2-relay-secret",
    "sender": "files@example.net",
}


def blank_world(make_world: Callable[..., World], **kw: Any) -> World:
    """No mail variables in the environment: the administrator may save settings in the UI."""
    w = make_world(public_url="https://files.example.org", **kw)
    FakeSMTP.sent = []
    w.ctx.mailer = Mailer(w.settings, FakeSMTP)  # type: ignore[arg-type]
    return w


def test_saved_settings_never_leak_the_password(
    make_world: Callable[..., World],
) -> None:
    w = blank_world(make_world, secrets_key=KEY)
    c, h = admin_session(w)
    before = c.get("/api/v1/admin/smtp").json()
    assert before["source"] is None and before["configured"] is False
    r = c.put("/api/v1/admin/smtp", json=BODY, headers=h)
    assert r.status_code == 200
    out = r.json()
    assert out["source"] == "saved" and out["host"] == "relay.example.net"
    assert out["has_password"] is True and out["can_store_password"] is True
    assert "hunter2" not in json.dumps(out) and "hunter2" not in c.get("/api/v1/admin/smtp").text
    assert w.ctx.mailer.config.sender == "files@example.net"
    with w.ctx.sessions() as db:
        stored = db.get(AppSetting, "smtp")
        assert stored is not None and "hunter2" not in json.dumps(stored.value)
        audit = db.scalars(select(AuthAudit).where(AuthAudit.action == "smtp.save")).all()
        assert audit and "hunter2" not in json.dumps([a.details for a in audit])
    cfg = c.get("/api/v1/admin/auth-config").json()
    assert cfg["smtp"]["host"] == "relay.example.net"
    # An edit without a password keeps the one already saved.
    again = {k: v for k, v in BODY.items() if k != "password"} | {"port": 587}
    assert c.put("/api/v1/admin/smtp", json=again, headers=h).json()["has_password"] is True
    assert w.ctx.mailer.config.password == "hunter2-relay-secret"


def test_test_uses_the_posted_settings_without_saving(make_world: Callable[..., World]) -> None:
    w = blank_world(make_world, secrets_key=KEY)
    c, h = admin_session(w)
    assert c.post("/api/v1/admin/smtp/test", json=BODY, headers=h).status_code == 200
    assert FakeSMTP.sent[-1]["To"] == "admin@example.com"
    assert FakeSMTP.sent[-1]["From"] == "files@example.net"
    assert c.get("/api/v1/admin/smtp").json()["configured"] is False  # not saved


def test_failures_have_a_reason_and_no_relay_text(make_world: Callable[..., World]) -> None:
    w = mail_world(make_world, down=True, secrets_key=KEY)
    c, h = admin_session(w)
    r = c.post("/api/v1/admin/smtp/test", json=BODY, headers=h)
    assert r.status_code == 502 and r.json()["detail"]["code"] == "SMTP_CONNECT_FAILED"
    assert "hunter2" not in r.text


def test_invalid_settings_and_missing_key_are_refused(make_world: Callable[..., World]) -> None:
    w = blank_world(make_world)  # no secrets key
    c, h = admin_session(w)

    def put(body: dict[str, Any]) -> Any:
        return c.put("/api/v1/admin/smtp", json=body, headers=h)

    assert put(BODY).status_code == 409  # password cannot be sealed
    assert put({**BODY, "password": None, "username": ""}).status_code == 200
    bad_from = put({**BODY, "sender": "nobody", "password": None})
    assert bad_from.json()["detail"]["code"] == "SMTP_FROM_INVALID"
    plain = put({**BODY, "security": "none", "password": None})
    assert plain.json()["detail"]["code"] == "SMTP_PLAINTEXT_REFUSED"
    assert put({**BODY, "port": 0, "password": None}).status_code == 422


def test_environment_settings_win_and_cannot_be_replaced_from_the_ui(
    make_world: Callable[..., World],
) -> None:
    w = mail_world(make_world, secrets_key=KEY)
    c, h = admin_session(w)
    out = c.get("/api/v1/admin/smtp").json()
    assert out["source"] == "environment" and out["host"] == "smtp.example.org"
    r = c.put("/api/v1/admin/smtp", json=BODY, headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "MAIL_SET_IN_ENVIRONMENT"
    assert c.delete("/api/v1/admin/smtp", headers=h).status_code == 409
    # Even a row saved earlier loses to the variables.
    w.ctx.mailer.use_saved(SmtpConfig("old.example.net", 25, "none", "", "", "o@example.net", 10))
    assert c.get("/api/v1/admin/smtp").json()["host"] == "smtp.example.org"


def test_clearing_switches_off_and_members_are_refused(
    make_world: Callable[..., World],
) -> None:
    w = blank_world(make_world, secrets_key=KEY)
    c, h = admin_session(w)
    c.put("/api/v1/admin/smtp", json=BODY, headers=h)
    out = c.delete("/api/v1/admin/smtp", headers=h).json()
    assert out["source"] is None and out["configured"] is False
    member = invite(w, (c, h), "m@example.com")
    w.browser().post(
        "/api/v1/auth/invitations/accept", json={"token": member, "password": PASSWORD}
    )
    mc, mh = signed_in(w, "m@example.com")
    assert mc.get("/api/v1/admin/smtp").status_code == 403
    assert mc.put("/api/v1/admin/smtp", json=BODY, headers=mh).status_code == 403
    assert mc.delete("/api/v1/admin/smtp", headers=mh).status_code == 403
