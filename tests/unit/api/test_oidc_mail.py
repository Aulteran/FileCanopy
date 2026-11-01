"""M12: invitations by email, optional SMTP, OIDC sign-in and account linking.

A fake identity provider (RSA key, discovery, JWKS, token and userinfo endpoints) is wired in
through an httpx MockTransport and a fake SMTP class replaces the relay: no network is used.
"""

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from test_auth import PASSWORD, World, admin_session, invite, login, signed_in

from app.auth.oidc import OidcProvider
from app.config import Settings
from app.db.models import AuthAudit, AuthToken, OidcLogin, User, UserIdentity
from app.notifications.mail import Mailer

ISSUER = "https://idp.example.org"
CLIENT_ID = "filecanopy"
SECRET = "client-secret-value"
PUBLIC = "https://files.example.org"
OIDC: dict[str, Any] = {
    "oidc_issuer": ISSUER,
    "oidc_client_id": CLIENT_ID,
    "oidc_client_secret": SECRET,
    "public_url": PUBLIC,
}
SMTP: dict[str, Any] = {
    "smtp_host": "smtp.example.org",
    "smtp_from": "files@example.org",
    "public_url": PUBLIC,
}


# ------------------------------------------------------------------ fakes


class FakeSMTP:
    sent: list[Any] = []

    def __init__(self) -> None:
        pass

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def starttls(self, **kw: Any) -> None:
        return None

    def login(self, *a: Any) -> None:
        return None

    def send_message(self, msg: Any) -> None:
        FakeSMTP.sent.append(msg)


def smtp_down() -> Any:
    raise ConnectionRefusedError("relay down")


def mail_world(make_world: Callable[..., World], down: bool = False, **kw: Any) -> World:
    w = make_world(**{**SMTP, **kw})
    FakeSMTP.sent = []
    w.ctx.mailer = Mailer(w.settings, smtp_down if down else FakeSMTP)  # type: ignore[arg-type]
    # "Forgot password" mails go out from a thread in production; run them inline here.
    w.ctx.mailer.send_in_background = lambda *m: w.ctx.mailer.send(*m)  # type: ignore[method-assign]
    return w


def link_of(msg: Any) -> str:
    m = re.search(r"https://\S+#token=\S+", msg.get_content())
    assert m, msg.get_content()
    return m.group(0)


def token_of(msg: Any) -> str:
    return link_of(msg).split("#token=", 1)[1]


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Idp:
    """A tiny OIDC provider. `authorize` plays the browser leg at the provider."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "key-1"
        self.codes: dict[str, dict[str, Any]] = {}
        self.userinfo: dict[str, Any] = {}
        self.down = False
        self.issuer_in_discovery = ISSUER
        self.token_calls = 0

    def jwk(self, key: Any = None, kid: str | None = None) -> dict[str, Any]:
        k = (key or self.key).public_key()
        d: dict[str, Any] = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(k))
        return {**d, "kid": kid or self.kid, "use": "sig", "alg": "RS256"}

    def handler(self, req: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down")
        path = req.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": self.issuer_in_discovery,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                    "userinfo_endpoint": f"{ISSUER}/userinfo",
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["client_secret_basic"],
                },
            )
        if path == "/jwks":
            return httpx.Response(200, json={"keys": [self.jwk()]})
        if path == "/userinfo":
            if req.headers.get("authorization") != "Bearer access-1":
                return httpx.Response(401, json={})
            return httpx.Response(200, json=self.userinfo)
        if path == "/token":
            self.token_calls += 1
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            basic = base64.b64encode(f"{CLIENT_ID}:{SECRET}".encode()).decode()
            if req.headers.get("authorization") != f"Basic {basic}":
                return httpx.Response(401, json={"error": "invalid_client"})
            entry = self.codes.pop(form.get("code", ""), None)  # a code works once
            if entry is None or form.get("redirect_uri") != f"{PUBLIC}/api/v1/auth/oidc/callback":
                return httpx.Response(400, json={"error": "invalid_grant"})
            verifier = form.get("code_verifier", "")
            challenge = b64(hashlib.sha256(verifier.encode()).digest())
            if challenge != entry["challenge"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(
                200,
                json={
                    "id_token": entry["id_token"],
                    "access_token": "access-1",
                    "token_type": "Bearer",
                },
            )
        return httpx.Response(404)

    def sign(self, claims: dict[str, Any], alg: str = "RS256", key: Any = None) -> str:
        pem = (key or self.key).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        return jwt.encode(claims, pem, algorithm=alg, headers={"kid": self.kid})

    def authorize(
        self,
        url: str,
        sub: str,
        email: str | None = "ada@example.com",
        verified: bool = True,
        raw_token: Callable[[dict[str, Any]], str] | None = None,
        **overrides: Any,
    ) -> str:
        """Returns the authorization code the provider would send back."""
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert q["response_type"] == "code" and q["code_challenge_method"] == "S256"
        assert q["client_id"] == CLIENT_ID and "openid" in q["scope"].split()
        assert q["redirect_uri"] == f"{PUBLIC}/api/v1/auth/oidc/callback"
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": sub,
            "iat": now,
            "exp": now + 300,
            "nonce": q["nonce"],
        }
        if email is not None:
            claims.update(email=email, email_verified=verified)
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        token = raw_token(claims) if raw_token else self.sign(claims)
        code = f"code-{len(self.codes)}-{sub}"
        self.codes[code] = {"challenge": q["code_challenge"], "id_token": token}
        self.last_state = q["state"]
        return code


@pytest.fixture
def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., World]:
    def factory(**overrides: Any) -> World:
        return World(tmp_path, monkeypatch, **overrides)

    return factory


@pytest.fixture
def idp() -> Idp:
    return Idp()


def oidc_world(make_world: Callable[..., World], idp: Idp, **kw: Any) -> World:
    w = make_world(**{**OIDC, **kw})
    w.ctx.oidc = OidcProvider(
        w.settings, lambda: httpx.Client(transport=httpx.MockTransport(idp.handler))
    )
    return w


def callback(c: TestClient, idp: Idp, code: str, state: str | None = None) -> httpx.Response:
    r: httpx.Response = c.get(
        "/api/v1/auth/oidc/callback",
        params={"code": code, "state": state or idp.last_state},
        follow_redirects=False,
    )
    return r


def outcome(r: httpx.Response) -> str:
    assert r.status_code == 303, r.text
    return r.headers["location"].removeprefix("/?sso=")


def start(c: TestClient, path: str, headers: dict[str, str] | None = None, **body: Any) -> str:
    r = c.post(f"/api/v1/auth/oidc/{path}/start", json=body, headers=headers or {})
    assert r.status_code == 200, r.text
    return str(r.json()["authorization_url"])


def sso_login(w: World, idp: Idp, sub: str, **kw: Any) -> tuple[TestClient, str]:
    c = w.browser()
    url = start(c, "login")
    code = idp.authorize(url, sub, **kw)
    return c, outcome(callback(c, idp, code))


def link(
    w: World, idp: Idp, c: TestClient, h: dict[str, str], sub: str = "sub-ada", **kw: Any
) -> str:
    url = start(c, "link", h, password=PASSWORD)
    return outcome(callback(c, idp, idp.authorize(url, sub, **kw)))


def sessions_ok(c: TestClient) -> bool:
    return c.get("/api/v1/auth/session").status_code == 200


def audit_text(w: World) -> str:
    return json.dumps(
        [
            [a.action, a.details, a.ip, str(a.actor_user_id), str(a.target_user_id)]
            for a in w.db_rows(AuthAudit)
        ]
    )


# ------------------------------------------------------------ configuration


def test_smtp_and_oidc_are_off_by_default_and_validated(make_world: Callable[..., World]) -> None:
    w = make_world()
    assert not w.settings.smtp_configured and not w.settings.oidc_enabled
    base: dict[str, Any] = {"database_url": "postgresql+psycopg://u:p@h/db"}
    with pytest.raises(ValueError):  # links need the public address
        Settings(**base, smtp_host="smtp.example.org", smtp_from="a@example.org")
    with pytest.raises(ValueError):  # half configured
        Settings(**base, smtp_host="smtp.example.org", public_url=PUBLIC)
    with pytest.raises(ValueError):  # plain SMTP to a remote host
        Settings(**base, **SMTP, smtp_security="none")
    Settings(**base, **{**SMTP, "smtp_host": "127.0.0.1"}, smtp_security="none")
    with pytest.raises(ValueError):  # redirect URI needs the public address
        Settings(**base, oidc_issuer=ISSUER, oidc_client_id=CLIENT_ID)
    with pytest.raises(ValueError):  # http issuer on the network
        Settings(**base, **{**OIDC, "oidc_issuer": "http://idp.lan"})
    Settings(**base, **{**OIDC, "oidc_issuer": "http://idp.lan", "oidc_allow_http": True})
    with pytest.raises(ValueError):
        Settings(**base, **OIDC, oidc_scopes="email")


def test_public_config_is_off_by_default(make_world: Callable[..., World]) -> None:
    assert make_world().browser().get("/api/v1/auth/config").json() == {
        "oidc": {"enabled": False, "label": "Single sign-on"},
        "password_reset_by_email": False,
        "password_login": True,
    }


def test_public_config_shows_only_what_the_sign_in_page_needs(
    make_world: Callable[..., World], idp: Idp
) -> None:
    w = oidc_world(make_world, idp, **SMTP, oidc_label="Authentik")
    body = w.browser().get("/api/v1/auth/config").json()
    assert body == {
        "oidc": {"enabled": True, "label": "Authentik"},
        "password_reset_by_email": True,
        "password_login": True,
    }
    assert SECRET not in json.dumps(body)


# --------------------------------------------------------------- invitations


def test_manual_invitation_link_still_works(make_world: Callable[..., World]) -> None:
    w = make_world()
    c, h = admin_session(w)
    r = c.post("/api/v1/admin/users", json={"email": "m@example.com", "name": "M"}, headers=h)
    assert r.status_code == 201
    assert r.json()["token"]["delivery"] == "manual" and r.json()["token"]["token"]


def test_email_requested_without_smtp_is_refused(make_world: Callable[..., World]) -> None:
    w = make_world()
    c, h = admin_session(w)
    r = c.post(
        "/api/v1/admin/users",
        json={"email": "m@example.com", "name": "M", "send_email": True},
        headers=h,
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "EMAIL_NOT_CONFIGURED"
    assert not [u for u in w.db_rows(User) if u.email == "m@example.com"]  # nothing was created


def test_invitation_by_email_hides_the_link_from_the_admin(
    make_world: Callable[..., World],
) -> None:
    w = mail_world(make_world)
    c, h = admin_session(w)
    r = c.post(
        "/api/v1/admin/users",
        json={"email": "m@example.com", "name": "Mia", "send_email": True},
        headers=h,
    )
    assert r.status_code == 201
    assert r.json()["token"]["delivery"] == "email_sent" and r.json()["token"]["token"] is None
    (msg,) = FakeSMTP.sent
    assert msg["To"] == "m@example.com" and msg["From"] == "files@example.org"
    assert link_of(msg).startswith(f"{PUBLIC}/invite#token=")
    ok = w.browser().post(
        "/api/v1/auth/invitations/accept", json={"token": token_of(msg), "password": PASSWORD}
    )
    assert ok.status_code == 200
    replay = w.browser().post(
        "/api/v1/auth/invitations/accept", json={"token": token_of(msg), "password": PASSWORD}
    )
    assert replay.status_code == 400
    assert token_of(msg) not in audit_text(w)


def test_smtp_down_never_blocks_an_invitation(make_world: Callable[..., World]) -> None:
    w = mail_world(make_world, down=True)
    c, h = admin_session(w)
    r = c.post(
        "/api/v1/admin/users",
        json={"email": "m@example.com", "name": "Mia", "send_email": True},
        headers=h,
    )
    assert r.status_code == 201
    body = r.json()["token"]
    assert body["delivery"] == "email_failed" and body["token"]  # manual fallback
    ok = w.browser().post(
        "/api/v1/auth/invitations/accept", json={"token": body["token"], "password": PASSWORD}
    )
    assert ok.status_code == 200
    again = c.post(
        f"/api/v1/admin/users/{r.json()['user']['id']}/invitation",
        json={"send_email": True},
        headers=h,
    )
    assert again.status_code == 409  # already active: nothing to re-send


def test_reset_by_email_hides_token_and_is_one_time(make_world: Callable[..., World]) -> None:
    w = mail_world(make_world)
    c, h = admin_session(w)
    uid = [u for u in w.db_rows(User)][0].id
    r = c.post(f"/api/v1/admin/users/{uid}/password-reset", json={"send_email": True}, headers=h)
    assert r.status_code == 200
    assert r.json()["delivery"] == "email_sent" and r.json()["token"] is None
    token = token_of(FakeSMTP.sent[-1])
    new = "a brand new passphrase 77"
    assert (
        w.browser()
        .post("/api/v1/auth/password-reset/complete", json={"token": token, "password": new})
        .status_code
        == 200
    )
    assert (
        w.browser()
        .post("/api/v1/auth/password-reset/complete", json={"token": token, "password": new})
        .status_code
        == 400
    )
    assert login(w.browser(), "admin@example.com", new).status_code == 200
    # the admin never sees any password or hash anywhere
    dump = audit_text(w) + json.dumps(c.get("/api/v1/admin/users").json())
    assert new not in dump and PASSWORD not in dump and "argon2" not in dump


def test_admin_reset_without_email_still_returns_the_manual_link(
    make_world: Callable[..., World],
) -> None:
    w = make_world()
    c, h = admin_session(w)
    uid = w.db_rows(User)[0].id
    r = c.post(f"/api/v1/admin/users/{uid}/password-reset", headers=h)
    assert r.status_code == 200 and r.json()["delivery"] == "manual" and r.json()["token"]


# ----------------------------------------------------------- forgot password


def forgot(c: TestClient, email: str) -> httpx.Response:
    return c.post("/api/v1/auth/password-reset/request", json={"email": email})


def test_forgot_password_needs_mail(make_world: Callable[..., World]) -> None:
    w = make_world()
    assert forgot(w.browser(), "a@example.com").status_code == 409


def test_forgot_password_answers_alike_for_everyone(make_world: Callable[..., World]) -> None:
    w = mail_world(make_world)
    c, h = admin_session(w)
    onboard_token = invite(w, (c, h), "pending@example.com")  # invited, never activated
    assert onboard_token
    mia = invite(w, (c, h), "mia@example.com")
    w.browser().post("/api/v1/auth/invitations/accept", json={"token": mia, "password": PASSWORD})
    mia_id = next(u.id for u in w.db_rows(User) if u.email == "mia@example.com")
    c.patch(f"/api/v1/admin/users/{mia_id}", json={"status": "disabled"}, headers=h)
    FakeSMTP.sent = []
    anon = w.browser()
    answers = {
        e: forgot(anon, e).json()
        for e in (
            "admin@example.com",
            "nobody@example.com",
            "mia@example.com",
            "pending@example.com",
        )
    }
    assert len({json.dumps(a) for a in answers.values()}) == 1
    assert [m["To"] for m in FakeSMTP.sent] == ["admin@example.com"]  # only the active account
    token = token_of(FakeSMTP.sent[0])
    r = anon.post(
        "/api/v1/auth/password-reset/complete",
        json={"token": token, "password": "yet another passphrase 9"},
    )
    assert r.status_code == 200


def test_forgot_password_is_limited_and_does_not_replace_a_fresh_link(
    make_world: Callable[..., World],
) -> None:
    w = mail_world(make_world)
    w.create_admin()
    anon = w.browser()
    assert forgot(anon, "admin@example.com").status_code == 202
    assert forgot(anon, "admin@example.com").status_code == 202  # inside the cooldown
    assert len(FakeSMTP.sent) == 1
    rows = [t for t in w.db_rows(AuthToken) if t.purpose == "reset"]
    assert len(rows) == 1
    assert forgot(anon, "admin@example.com").status_code == 202
    r = forgot(anon, "admin@example.com")
    assert r.status_code == 429 and "retry-after" in r.headers


def test_smtp_test_message_goes_to_the_admin_only(make_world: Callable[..., World]) -> None:
    w = mail_world(make_world)
    c, h = admin_session(w)
    assert c.post("/api/v1/admin/smtp/test", headers=h).status_code == 200
    assert FakeSMTP.sent[-1]["To"] == "admin@example.com"
    member = invite(w, (c, h), "m@example.com")
    w.browser().post(
        "/api/v1/auth/invitations/accept", json={"token": member, "password": PASSWORD}
    )
    mc, mh = signed_in(w, "m@example.com")
    assert mc.post("/api/v1/admin/smtp/test", headers=mh).status_code == 403
    assert mc.get("/api/v1/admin/auth-config").status_code == 403
    cfg = c.get("/api/v1/admin/auth-config").json()
    assert cfg["smtp"]["configured"] and "password" not in json.dumps(cfg)


def test_smtp_test_reports_a_dead_relay(make_world: Callable[..., World]) -> None:
    w = mail_world(make_world, down=True)
    c, h = admin_session(w)
    assert c.post("/api/v1/admin/smtp/test", headers=h).status_code == 502


# ------------------------------------------------------------------ OIDC off


def test_oidc_endpoints_are_absent_when_disabled_and_local_login_works(
    make_world: Callable[..., World],
) -> None:
    w = make_world()
    c, h = admin_session(w)
    r = c.post("/api/v1/auth/oidc/login/start", json={})
    assert r.status_code == 404
    r = c.post("/api/v1/auth/oidc/invitation/start", json={"token": "x" * 43, "password": PASSWORD})
    assert r.status_code == 404
    assert (
        c.post("/api/v1/auth/oidc/link/start", json={"password": PASSWORD}, headers=h).status_code
        == 404
    )
    assert (
        outcome(w.browser().get("/api/v1/auth/oidc/callback", follow_redirects=False))
        == "OIDC_DISABLED"
    )
    assert signed_in(w)


def test_provider_outage_does_not_affect_local_sign_in(
    make_world: Callable[..., World], idp: Idp
) -> None:
    w = oidc_world(make_world, idp)
    w.create_admin()
    idp.down = True
    r = w.browser().post("/api/v1/auth/oidc/login/start", json={})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "OIDC_UNAVAILABLE"
    assert not w.db_rows(OidcLogin)
    assert login(w.browser(), "admin@example.com").status_code == 200


def test_discovery_with_a_different_issuer_is_refused(
    make_world: Callable[..., World], idp: Idp
) -> None:
    idp.issuer_in_discovery = "https://evil.example.org"
    w = oidc_world(make_world, idp)
    assert w.browser().post("/api/v1/auth/oidc/login/start", json={}).status_code == 503


# --------------------------------------------------------- linking and login


def test_link_then_sign_in_with_the_provider(make_world: Callable[..., World], idp: Idp) -> None:
    w = oidc_world(make_world, idp)
    c, h = admin_session(w)
    bad = c.post("/api/v1/auth/oidc/link/start", json={"password": "wrong password!!"}, headers=h)
    assert bad.status_code == 403
    assert link(w, idp, c, h) == "linked"
    assert c.get("/api/v1/auth/oidc/identity").json() == {"linked": True, "label": "Single sign-on"}
    (ident,) = w.db_rows(UserIdentity)
    assert (ident.issuer, ident.subject) == (ISSUER, "sub-ada")
    row = w.db_rows(OidcLogin)[0]
    assert row.used_at is not None and row.code_verifier  # PKCE verifier was kept server-side
    other, result = sso_login(w, idp, "sub-ada", email=None)
    assert result == "signed_in" and sessions_ok(other)
    assert other.get("/api/v1/auth/session").json()["user"]["email"] == "admin@example.com"
    assert "login.success" in audit_text(w) and "sub-ada" not in audit_text(w)


def test_unlinked_identity_is_refused_and_creates_nothing(
    make_world: Callable[..., World], idp: Idp
) -> None:
    w = oidc_world(make_world, idp)
    w.create_admin()
    before = len(w.db_rows(User))
    c, result = sso_login(w, idp, "stranger", email="admin@example.com")  # same email!
    assert result == "OIDC_NOT_LINKED" and not sessions_ok(c)
    assert len(w.db_rows(User)) == before and not w.db_rows(UserIdentity)


def test_a_second_identity_with_the_same_email_cannot_take_over(
    make_world: Callable[..., World], idp: Idp
) -> None:
    w = oidc_world(make_world, idp)
    c, h = admin_session(w)
    assert link(w, idp, c, h, "sub-real", email="ada@example.com") == "linked"
    _, result = sso_login(w, idp, "sub-lookalike", email="ada@example.com")
    assert result == "OIDC_NOT_LINKED"
    # nor can the lookalike link itself onto another account
    m = invite(w, (c, h), "m@example.com")
    w.browser().post("/api/v1/auth/invitations/accept", json={"token": m, "password": PASSWORD})
    mc, mh = signed_in(w, "m@example.com")
    assert link(w, idp, mc, mh, "sub-real", email="ada@example.com") == "IDENTITY_IN_USE"
    assert len(w.db_rows(UserIdentity)) == 1


def test_one_identity_per_issuer_per_account(make_world: Callable[..., World], idp: Idp) -> None:
    w = oidc_world(make_world, idp)
    c, h = admin_session(w)
    assert link(w, idp, c, h, "one") == "linked"
    assert link(w, idp, c, h, "two") == "IDENTITY_IN_USE"
    assert [i.subject for i in w.db_rows(UserIdentity)] == ["one"]


def test_state_is_single_use_and_bound_to_the_browser(
    make_world: Callable[..., World], idp: Idp
) -> None:
    w = oidc_world(make_world, idp)
    c, h = admin_session(w)
    assert link(w, idp, c, h) == "linked"
    login_c = w.browser()
    url = start(login_c, "login")
    code = idp.authorize(url, "sub-ada")
    state = idp.last_state
    attacker = w.browser()  # a different browser: no binding cookie
    assert outcome(callback(attacker, idp, code, state)) == "OIDC_STATE_INVALID"
    assert not sessions_ok(attacker)
    # the state is spent even though the attempt failed
    assert outcome(callback(login_c, idp, code, state)) == "OIDC_STATE_INVALID"
    # unknown / garbage state
    assert outcome(callback(login_c, idp, "x", "nope")) == "OIDC_STATE_INVALID"
    # a good round trip, then a replay of the same callback
    url = start(login_c, "login")
    code = idp.authorize(url, "sub-ada")
    state = idp.last_state
    assert outcome(callback(login_c, idp, code, state)) == "signed_in"
    again = w.browser()
    again.cookies.update(login_c.cookies)
    assert outcome(callback(again, idp, code, state)) == "OIDC_STATE_INVALID"


def test_state_expires(make_world: Callable[..., World], idp: Idp) -> None:
    w = oidc_world(make_world, idp)
    c, h = admin_session(w)
    link(w, idp, c, h)
    lc = w.browser()
    code = idp.authorize(start(lc, "login"), "sub-ada")
    w.advance(minutes=11)
    assert outcome(callback(lc, idp, code)) == "OIDC_STATE_INVALID"


def test_authorization_error_from_the_provider(make_world: Callable[..., World], idp: Idp) -> None:
    w = oidc_world(make_world, idp)
    c = w.browser()
    start(c, "login")
    r = c.get(
        "/api/v1/auth/oidc/callback",
        params={
            "error": "access_denied",
            "state": idp.last_state if hasattr(idp, "last_state") else "x",
        },
        follow_redirects=False,
    )
    assert outcome(r) in ("OIDC_STATE_INVALID", "OIDC_DENIED")


def hs256_forgery(idp: Idp) -> Callable[[dict[str, Any]], str]:
    """The classic downgrade: HS256 signed with the provider's public key as the secret."""

    def forge(claims: dict[str, Any]) -> str:
        pem = idp.key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        head = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": idp.kid}).encode())
        body = b64(json.dumps(claims).encode())
        sig = hmac.new(pem, f"{head}.{body}".encode(), hashlib.sha256).digest()
        return f"{head}.{body}.{b64(sig)}"

    return forge


def unsigned(claims: dict[str, Any]) -> str:
    return jwt.encode(claims, None, algorithm="none", headers={"kid": "key-1"})  # type: ignore[arg-type]


def wrong_key(idp: Idp) -> Callable[[dict[str, Any]], str]:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return lambda claims: idp.sign(claims, key=other)


@pytest.mark.parametrize(
    "case",
    [
        "wrong_issuer",
        "wrong_audience",
        "expired",
        "wrong_nonce",
        "missing_nonce",
        "none_alg",
        "hs256",
        "bad_signature",
        "azp_mismatch",
        "no_subject",
        "other_azp_single_aud",
    ],
)
def test_invalid_id_tokens_never_sign_anyone_in(
    make_world: Callable[..., World], idp: Idp, case: str
) -> None:
    w = oidc_world(make_world, idp)
    c, h = admin_session(w)
    assert link(w, idp, c, h) == "linked"
    now = int(time.time())
    kw: dict[str, Any] = {}
    if case == "wrong_issuer":
        kw = {"iss": "https://evil.example.org"}
    elif case == "wrong_audience":
        kw = {"aud": "someone-else"}
    elif case == "expired":
        kw = {"iat": now - 4000, "exp": now - 3600}
    elif case == "wrong_nonce":
        kw = {"nonce": "attacker-chosen"}
    elif case == "missing_nonce":
        kw = {"nonce": None}
    elif case == "none_alg":
        kw = {"raw_token": unsigned}
    elif case == "hs256":
        kw = {"raw_token": hs256_forgery(idp)}
    elif case == "bad_signature":
        kw = {"raw_token": wrong_key(idp)}
    elif case == "azp_mismatch":
        kw = {"aud": [CLIENT_ID, "other"], "azp": "other"}
    elif case == "no_subject":
        kw = {"raw_token": lambda claims: idp.sign({k: v for k, v in claims.items() if k != "sub"})}
    elif case == "other_azp_single_aud":
        kw = {"azp": "other"}
    lc = w.browser()
    url = start(lc, "login")
    code = idp.authorize(url, "sub-ada", **kw)
    assert outcome(callback(lc, idp, code)) == "ID_TOKEN_INVALID"
    assert not sessions_ok(lc)


def test_code_exchange_failure_is_reported_without_detail(
    make_world: Callable[..., World], idp: Idp
) -> None:
    w = oidc_world(make_world, idp)
    c, h = admin_session(w)
    link(w, idp, c, h)
    lc = w.browser()
    state = parse_qs(urlparse(start(lc, "login")).query)["state"][0]
    assert outcome(callback(lc, idp, "never-issued", state)) == "OIDC_FAILED"
    assert not sessions_ok(lc)


def test_key_rotation_refreshes_the_key_set(
    make_world: Callable[..., World], idp: Idp, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = oidc_world(make_world, idp)
    c, h = admin_session(w)
    link(w, idp, c, h)
    monkeypatch.setattr("app.auth.oidc.JWKS_MIN_REFRESH", 0.0)
    sso_login(w, idp, "sub-ada")  # cache the key set
    idp.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    idp.kid = "key-2"
    _, result = sso_login(w, idp, "sub-ada")
    assert result == "signed_in"


def test_disabled_account_cannot_use_the_provider(
    make_world: Callable[..., World], idp: Idp
) -> None:
    w = oidc_world(make_world, idp)
    c, h = admin_session(w)
    m = invite(w, (c, h), "m@example.com")
    w.browser().post("/api/v1/auth/invitations/accept", json={"token": m, "password": PASSWORD})
    mc, mh = signed_in(w, "m@example.com")
    assert link(w, idp, mc, mh, "sub-m", email="m@example.com") == "linked"
    uid = next(u.id for u in w.db_rows(User) if u.email == "m@example.com")
    assert (
        c.patch(f"/api/v1/admin/users/{uid}", json={"status": "disabled"}, headers=h).status_code
        == 200
    )
    assert not sessions_ok(mc)
    _, result = sso_login(w, idp, "sub-m", email="m@example.com")
    assert result == "OIDC_NOT_LINKED"


def test_link_callback_dies_with_the_session(make_world: Callable[..., World], idp: Idp) -> None:
    w = oidc_world(make_world, idp)
    c, h = admin_session(w)
    url = start(c, "link", h, password=PASSWORD)
    code = idp.authorize(url, "sub-ada")
    assert c.post("/api/v1/auth/logout", headers=h).status_code == 200
    assert outcome(callback(c, idp, code)) == "OIDC_STATE_INVALID"
    assert not w.db_rows(UserIdentity)


def test_unlink_needs_the_password_and_keeps_local_login(
    make_world: Callable[..., World], idp: Idp
) -> None:
    w = oidc_world(make_world, idp)
    c, h = admin_session(w)
    link(w, idp, c, h)
    assert (
        c.post(
            "/api/v1/auth/oidc/unlink", json={"password": "nope nope nope"}, headers=h
        ).status_code
        == 403
    )
    assert w.db_rows(UserIdentity)
    assert (
        c.post("/api/v1/auth/oidc/unlink", json={"password": PASSWORD}, headers=h).status_code
        == 200
    )
    assert not w.db_rows(UserIdentity)
    _, result = sso_login(w, idp, "sub-ada")
    assert result == "OIDC_NOT_LINKED"
    assert login(w.browser(), "admin@example.com").status_code == 200


def test_admin_can_unlink_a_person_and_ends_their_sessions(
    make_world: Callable[..., World], idp: Idp
) -> None:
    w = oidc_world(make_world, idp)
    c, h = admin_session(w)
    m = invite(w, (c, h), "m@example.com")
    w.browser().post("/api/v1/auth/invitations/accept", json={"token": m, "password": PASSWORD})
    mc, mh = signed_in(w, "m@example.com")
    link(w, idp, mc, mh, "sub-m", email="m@example.com")
    users = {u["email"]: u for u in c.get("/api/v1/admin/users").json()}
    assert users["m@example.com"]["sso_linked"] and not users["admin@example.com"]["sso_linked"]
    uid = users["m@example.com"]["id"]
    assert c.delete(f"/api/v1/admin/users/{uid}/identity", headers=h).status_code == 200
    assert not sessions_ok(mc)
    assert c.delete(f"/api/v1/admin/users/{uid}/identity", headers=h).status_code == 404
    assert login(w.browser(), "m@example.com").status_code == 200


# ------------------------------------------------- invitation through provider


def sso_invite(
    w: World,
    idp: Idp,
    token: str,
    sub: str = "sub-new",
    password: str = PASSWORD,
    **kw: Any,
) -> tuple[TestClient, str]:
    c = w.browser()
    url = start(c, "invitation", token=token, password=password, name="Nina")
    return c, outcome(callback(c, idp, idp.authorize(url, sub, **kw)))


def test_accept_an_invitation_through_the_provider(
    make_world: Callable[..., World], idp: Idp
) -> None:
    w = oidc_world(make_world, idp)
    adm = admin_session(w)
    token = invite(w, adm, "nina@example.com")
    c, result = sso_invite(w, idp, token, email="nina@example.com")
    assert result == "welcome" and sessions_ok(c)
    user = next(u for u in w.db_rows(User) if u.email == "nina@example.com")
    assert user.status == "active" and user.name == "Nina"
    assert [i.subject for i in w.db_rows(UserIdentity)] == ["sub-new"]
    # the chosen local password is the fallback and the Confidential unlock secret
    assert login(w.browser(), "nina@example.com").status_code == 200
    assert (
        w.browser()
        .post("/api/v1/auth/invitations/accept", json={"token": token, "password": PASSWORD})
        .status_code
        == 400
    )
    assert PASSWORD not in audit_text(w) and token not in audit_text(w)
    stored = " ".join(r.password_hash or "" for r in w.db_rows(OidcLogin))
    assert PASSWORD not in stored


@pytest.mark.parametrize(
    "kw",
    [
        {"verified": False},  # not verified by the provider
        {"email": "someone.else@example.com"},  # verified, but not the invited address
        {"email": None},  # no email at all
    ],
)
def test_invitation_through_the_provider_needs_a_verified_matching_email(
    make_world: Callable[..., World], idp: Idp, kw: dict[str, Any]
) -> None:
    w = oidc_world(make_world, idp)
    adm = admin_session(w)
    token = invite(w, adm, "nina@example.com")
    idp.userinfo = {"sub": "sub-new"}
    c, result = sso_invite(w, idp, token, **{"email": "nina@example.com", **kw})
    assert result == "OIDC_EMAIL_MISMATCH" and not sessions_ok(c)
    assert not w.db_rows(UserIdentity)
    user = next(u for u in w.db_rows(User) if u.email == "nina@example.com")
    assert user.status == "invited" and user.password_hash is None
    # the token was not burned: the normal link still works
    ok = w.browser().post(
        "/api/v1/auth/invitations/accept", json={"token": token, "password": PASSWORD}
    )
    assert ok.status_code == 200


def test_email_can_come_from_the_userinfo_endpoint(
    make_world: Callable[..., World], idp: Idp
) -> None:
    w = oidc_world(make_world, idp)
    adm = admin_session(w)
    token = invite(w, adm, "nina@example.com")
    idp.userinfo = {"sub": "sub-new", "email": "Nina@Example.com", "email_verified": True}
    c, result = sso_invite(w, idp, token, email=None)
    assert result == "welcome" and sessions_ok(c)


def test_userinfo_for_a_different_subject_is_rejected(
    make_world: Callable[..., World], idp: Idp
) -> None:
    w = oidc_world(make_world, idp)
    adm = admin_session(w)
    token = invite(w, adm, "nina@example.com")
    idp.userinfo = {"sub": "someone-else", "email": "nina@example.com", "email_verified": True}
    _, result = sso_invite(w, idp, token, email=None)
    assert result == "ID_TOKEN_INVALID"
    assert not w.db_rows(UserIdentity)


def test_invitation_through_the_provider_cannot_take_an_identity_in_use(
    make_world: Callable[..., World], idp: Idp
) -> None:
    w = oidc_world(make_world, idp)
    adm = admin_session(w)
    c, h = adm
    assert link(w, idp, c, h, "shared-sub") == "linked"
    token = invite(w, adm, "nina@example.com")
    _, result = sso_invite(w, idp, token, sub="shared-sub", email="nina@example.com")
    assert result == "IDENTITY_IN_USE"
    user = next(u for u in w.db_rows(User) if u.email == "nina@example.com")
    assert user.status == "invited"
    assert len(w.db_rows(UserIdentity)) == 1


def test_invitation_start_checks_token_and_password_before_leaving(
    make_world: Callable[..., World], idp: Idp
) -> None:
    w = oidc_world(make_world, idp)
    adm = admin_session(w)
    token = invite(w, adm, "nina@example.com")
    c = w.browser()
    bad = c.post(
        "/api/v1/auth/oidc/invitation/start", json={"token": "x" * 43, "password": PASSWORD}
    )
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "INVALID_TOKEN"
    weak = c.post("/api/v1/auth/oidc/invitation/start", json={"token": token, "password": "short"})
    assert weak.status_code == 422
    assert w.db_rows(AuthToken)[0].used_at is None  # starting does not consume anything


def test_invitation_token_replay_after_provider_acceptance(
    make_world: Callable[..., World], idp: Idp
) -> None:
    w = oidc_world(make_world, idp)
    adm = admin_session(w)
    token = invite(w, adm, "nina@example.com")
    c = w.browser()
    url = start(c, "invitation", token=token, password=PASSWORD)
    code = idp.authorize(url, "sub-new", email="nina@example.com")
    # meanwhile the link is used the ordinary way
    w.browser().post("/api/v1/auth/invitations/accept", json={"token": token, "password": PASSWORD})
    assert outcome(callback(c, idp, code)) == "INVALID_TOKEN"
    assert not w.db_rows(UserIdentity)


def test_oidc_failures_are_audited_without_secrets(
    make_world: Callable[..., World], idp: Idp
) -> None:
    w = oidc_world(make_world, idp)
    w.create_admin()
    _, result = sso_login(w, idp, "sub-secret-subject", email="admin@example.com")
    assert result == "OIDC_NOT_LINKED"
    text = audit_text(w)
    assert "oidc.failed" in text
    for secret in ("sub-secret-subject", "admin@example.com", SECRET, idp.last_state):
        assert secret not in text


def test_starts_are_throttled_even_when_the_provider_is_down(
    make_world: Callable[..., World], idp: Idp
) -> None:
    w = oidc_world(make_world, idp)
    idp.down = True
    c = w.browser()
    codes = [c.post("/api/v1/auth/oidc/login/start", json={}).status_code for _ in range(42)]
    assert codes[:40] == [503] * 40
    assert codes[40] == 429 and codes[41] == 429


def test_a_dead_provider_is_not_contacted_on_every_request(
    make_world: Callable[..., World], idp: Idp
) -> None:
    w = oidc_world(make_world, idp)
    calls = {"n": 0}
    real = idp.handler

    def counting(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return real(req)

    w.ctx.oidc = OidcProvider(
        w.settings, lambda: httpx.Client(transport=httpx.MockTransport(counting))
    )
    idp.down = True
    for _ in range(5):
        assert w.browser().post("/api/v1/auth/oidc/login/start", json={}).status_code == 503
    assert calls["n"] == 1  # the failure is remembered for a short while
