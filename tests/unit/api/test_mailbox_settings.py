"""Incoming mail set up in the web UI, environment precedence and the "source changed" notice."""

import base64
import json
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import select
from test_auth import World, admin_session, make_world

__all__ = ["make_world"]

from app.db.models import AppSetting, Notification
from app.mail import source_watch
from app.mail.imap import MailboxError

KEY = base64.b64encode(bytes(range(32))).decode()
BODY: dict[str, Any] = {
    "host": "imap.example.net",
    "port": 993,
    "security": "ssl",
    "username": "box",
    "password": "imap-secret-pass",
    "folder": "INBOX",
    "personal_address": "Me@Example.net",
    "ingest_address": "shared@example.net",
    "authserv_id": "",
}
ENV: dict[str, Any] = {
    "imap_host": "imap.env.example",
    "imap_username": "envbox",
    "imap_password": "env-secret-pass",
    "imap_personal_address": "me@env.example",
    "imap_ingest_address": "shared@env.example",
}
URL = "/api/v1/admin/mailbox/settings"


@pytest.fixture(autouse=True)
def no_poller(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.mail.poller import MailboxPoller

    monkeypatch.setattr(MailboxPoller, "restart", lambda self: None)


def test_saved_mailbox_applies_and_never_leaks_the_password(
    make_world: Callable[..., World],
) -> None:
    w = make_world(secrets_key=KEY)
    c, h = admin_session(w)
    assert c.get(URL).json()["configured"] is False
    r = c.put(URL, json=BODY, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["source"] == "saved" and out["personal_address"] == "me@example.net"
    assert "imap-secret" not in json.dumps(out) and "imap-secret" not in c.get(URL).text
    assert w.settings.imap_configured
    assert c.get("/api/v1/admin/mailbox").json()["configured"] is True
    with w.ctx.sessions() as db:
        stored = db.get(AppSetting, "imap")
        assert stored is not None and "imap-secret" not in json.dumps(stored.value)
    # Editing without a password keeps the saved one.
    again = {k: v for k, v in BODY.items() if k != "password"} | {"folder": "Files"}
    assert c.put(URL, json=again, headers=h).status_code == 200
    assert w.settings.imap_password.get_secret_value() == "imap-secret-pass"
    out = c.delete(URL, headers=h).json()
    assert out["configured"] is False and not w.settings.imap_configured


def test_missing_secrets_key_is_refused(make_world: Callable[..., World]) -> None:
    w = make_world()  # no secrets key
    c, h = admin_session(w)
    assert c.put(URL, json=BODY, headers=h).json()["detail"]["code"] == "SECRETS_KEY_MISSING"


def test_invalid_input_is_refused(make_world: Callable[..., World]) -> None:
    w = make_world(secrets_key=KEY)
    c, h = admin_session(w)

    def code(**kw: Any) -> str:
        r = c.put(URL, json={**BODY, **kw}, headers=h)
        return str(r.json()["detail"]["code"])

    assert code(security="none") == "IMAP_PLAINTEXT_REFUSED"
    assert code(personal_address="x@example.net", ingest_address="X@example.net") == (
        "IMAP_ADDRESSES_INVALID"
    )
    assert code(ingest_address="not an address") == "IMAP_ADDRESSES_INVALID"
    assert code(folder='a"b') == "IMAP_FOLDER_INVALID"
    assert code(password="") == "IMAP_LOGIN_INVALID"


def test_environment_mailbox_wins_and_is_read_only(make_world: Callable[..., World]) -> None:
    w = make_world(secrets_key=KEY, **ENV)
    c, h = admin_session(w)
    out = c.get(URL).json()
    assert out["source"] == "environment" and out["host"] == "imap.env.example"
    assert "env-secret" not in c.get(URL).text
    r = c.put(URL, json=BODY, headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "MAIL_SET_IN_ENVIRONMENT"
    assert c.delete(URL, headers=h).status_code == 409
    assert w.settings.imap_host == "imap.env.example"


def test_connection_test_reports_codes_only(
    make_world: Callable[..., World], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.mailbox as routes

    seen: list[str] = []

    class Fake:
        def __init__(self, s: Any) -> None:
            seen.append(s.imap_host)

        def connect(self) -> "Fake":
            if seen[-1] == "imap.example.net":
                raise MailboxError("LOGIN_FAILED")
            return self

        def select(self, folder: str) -> int:
            return 1

        def close(self) -> None: ...

    monkeypatch.setattr(routes, "ImapClient", Fake)
    w = make_world(secrets_key=KEY)
    c, h = admin_session(w)
    r = c.post(URL + "/test", json=BODY, headers=h)
    assert r.status_code == 502 and r.json()["detail"]["code"] == "LOGIN_FAILED"
    assert "imap-secret" not in r.text
    assert (
        c.post(URL + "/test", json={**BODY, "host": "ok.example.net"}, headers=h).status_code == 200
    )
    assert c.post(URL + "/test", headers=h).status_code == 409  # nothing saved yet
    assert c.get(URL).json()["configured"] is False  # testing never saves


def notices(w: World) -> list[tuple[str, str | None]]:
    with w.ctx.sessions() as db:
        rows = db.scalars(select(Notification).where(Notification.kind == "mail_source_changed"))
        return [(n.kind, n.detail) for n in rows]


def check(w: World, *, smtp: bool = False, mailbox: bool = False) -> list[str]:
    with w.ctx.sessions() as db:
        sent = source_watch.check(db, env_smtp=smtp, env_mailbox=mailbox)
        db.commit()
    return sent


def test_notice_only_when_the_environment_takes_over(make_world: Callable[..., World]) -> None:
    w = make_world(secrets_key=KEY)
    c, h = admin_session(w)
    assert check(w) == []  # first start: nothing to compare
    c.put(URL, json=BODY, headers=h)  # saved in the UI: recorded, no notice
    assert check(w) == [] and notices(w) == []
    # Later the variables appear and replace what was saved: one notice, no secret in it.
    assert check(w, mailbox=True) == ["mailbox:environment"]
    assert notices(w) == [("mail_source_changed", "mailbox:environment")]
    with w.ctx.sessions() as db:
        text = json.dumps([[n.kind, n.detail, n.count] for n in db.scalars(select(Notification))])
    assert "secret" not in text and "imap" not in text.replace("mailbox", "")
    # Another start with the same source is silent.
    assert check(w, mailbox=True) == [] and len(notices(w)) == 1
    # Removing the variables falls back to the saved settings, which is also told once.
    assert check(w) == ["mailbox:saved"]
    assert check(w) == []


def test_smtp_takeover_and_no_notice_without_a_saved_row(
    make_world: Callable[..., World],
) -> None:
    w = make_world(secrets_key=KEY)
    admin_session(w)
    with w.ctx.sessions() as db:
        db.add(AppSetting(key="smtp", value={"host": "relay.example.net"}))
        db.commit()
    assert check(w, smtp=True) == ["smtp:environment"]  # saved row, no record yet: inferred


def test_variables_from_the_start_replace_nothing(make_world: Callable[..., World]) -> None:
    w = make_world(secrets_key=KEY)
    admin_session(w)
    assert check(w, smtp=True) == []
