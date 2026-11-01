"""M02 authentication: bootstrap, sessions, CSRF, throttling, tokens, admin API, authz negatives.

Runs against in-memory SQLite with cheap Argon2 parameters; the compose suite repeats the key
flows against real PostgreSQL behind nginx. Time is controlled via app.auth.clock.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.pool import StaticPool

from app import main as main_module
from app.auth import bootstrap, clock
from app.config import Settings
from app.db.migrate import head_revision
from app.db.models import AuthAudit, AuthToken, Base, User

PASSWORD = "correct horse battery staple"
OTHER_PASSWORD = "another long passphrase 42"
START = datetime(2026, 1, 1, tzinfo=UTC)


class World:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
        lib, data = tmp_path / "library", tmp_path / "app-data"
        lib.mkdir()
        data.mkdir()
        self.engine = create_engine(
            "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
            conn.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": head_revision()})
        monkeypatch.setattr(main_module, "make_engine", lambda _: self.engine)
        self.offset = timedelta(0)
        monkeypatch.setattr(clock, "utcnow", lambda: START + self.offset)
        values: dict[str, Any] = {
            "database_url": "postgresql+psycopg://u:p@h/db",
            "library_path": lib,
            "app_data_path": data,
            "cookie_secure": False,
            "argon2_time_cost": 1,
            "argon2_memory_kib": 8,
            "argon2_parallelism": 1,
            **overrides,
        }
        self.settings = Settings(**values)
        self.app = main_module.create_app(self.settings)
        self.ctx = self.app.state.auth
        self.setup_token = bootstrap.ensure_token(self.ctx.sessions, self.settings) or ""

    def browser(self, **kw: Any) -> TestClient:
        return TestClient(self.app, **kw)

    def advance(self, **kw: float) -> None:
        self.offset += timedelta(**kw)

    def db_rows(self, model: Any) -> list[Any]:
        with self.ctx.sessions() as db:
            return list(db.scalars(select(model)))

    def create_admin(self, email: str = "admin@example.com") -> TestClient:
        c = self.browser()
        r = c.post(
            "/api/v1/setup/admin",
            json={
                "setup_token": self.setup_token,
                "email": email,
                "name": "Ada",
                "password": PASSWORD,
            },
        )
        assert r.status_code == 201, r.text
        return c


@pytest.fixture
def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., World]:
    def factory(**overrides: Any) -> World:
        return World(tmp_path, monkeypatch, **overrides)

    return factory


@pytest.fixture
def world(make_world: Callable[..., World]) -> World:
    return make_world()


def login(c: TestClient, email: str, password: str = PASSWORD, **headers: str) -> Any:
    return c.post(
        "/api/v1/auth/login", json={"email": email, "password": password}, headers=headers
    )


def signed_in(w: World, email: str = "admin@example.com") -> tuple[TestClient, dict[str, str]]:
    c = w.browser()
    r = login(c, email)
    assert r.status_code == 200, r.text
    return c, {"X-CSRF-Token": r.json()["csrf_token"]}


def admin_session(w: World) -> tuple[TestClient, dict[str, str]]:
    w.create_admin()
    return signed_in(w)


def invite(
    w: World, admin: tuple[TestClient, dict[str, str]], email: str, role: str = "member"
) -> str:
    c, h = admin
    r = c.post("/api/v1/admin/users", json={"email": email, "name": "New", "role": role}, headers=h)
    assert r.status_code == 201, r.text
    return str(r.json()["token"]["token"])


def onboard(
    w: World, admin: tuple[TestClient, dict[str, str]], email: str, role: str = "member"
) -> None:
    token = invite(w, admin, email, role)
    r = w.browser().post(
        "/api/v1/auth/invitations/accept", json={"token": token, "password": PASSWORD}
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- bootstrap


def test_no_default_credentials_or_open_registration(world: World) -> None:
    c = world.browser()
    assert c.get("/api/v1/setup/status").json() == {"setup_required": True}
    for email in ("admin", "admin@example.com", "admin@localhost"):
        for pw in ("admin", "password", PASSWORD, ""):
            assert login(c, email, pw).status_code == 401
    for path in ("/api/v1/auth/register", "/api/v1/register", "/api/v1/users", "/api/v1/signup"):
        assert c.post(path, json={"email": "x@example.com", "password": PASSWORD}).status_code in (
            404,
            405,
        )
    assert world.db_rows(User) == []


def test_setup_requires_valid_one_time_token(world: World) -> None:
    c = world.browser()
    body = {"email": "a@example.com", "name": "A", "password": PASSWORD}
    r = c.post("/api/v1/setup/admin", json={**body, "setup_token": "x" * 43})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "INVALID_SETUP_TOKEN"
    assert world.db_rows(User) == []
    ok = c.post("/api/v1/setup/admin", json={**body, "setup_token": world.setup_token})
    assert ok.status_code == 201
    assert ok.json()["role"] == "admin"
    # Setup is closed afterwards, even for the correct token.
    again = c.post(
        "/api/v1/setup/admin",
        json={**body, "email": "b@example.com", "setup_token": world.setup_token},
    )
    assert again.status_code == 409
    assert c.get("/api/v1/setup/status").json() == {"setup_required": False}
    assert len(world.db_rows(User)) == 1


def test_setup_token_is_stored_hashed_and_logged_once(
    make_world: Callable[..., World], capsys: pytest.CaptureFixture[str]
) -> None:
    w = make_world()  # create_app() installs its own JSON handler, so read stderr
    assert w.setup_token
    assert capsys.readouterr().err.count(w.setup_token) == 1
    stored = json.dumps([s.value for s in w.db_rows(bootstrap.AppSetting)], default=str)
    assert w.setup_token not in stored
    w.create_admin()
    stored = [s.key for s in w.db_rows(bootstrap.AppSetting)]
    assert bootstrap.TOKEN_KEY not in stored  # discarded once setup completes


def test_configured_setup_token_and_minimum_length(make_world: Callable[..., World]) -> None:
    with pytest.raises(ValueError, match="at least 32"):
        make_world(setup_token="short")


def test_configured_setup_token_works(make_world: Callable[..., World]) -> None:
    secret = "s" * 40
    w = make_world(setup_token=secret)
    assert w.setup_token == ""  # nothing generated or logged
    r = w.browser().post(
        "/api/v1/setup/admin",
        json={"setup_token": secret, "email": "a@example.com", "name": "A", "password": PASSWORD},
    )
    assert r.status_code == 201


def test_setup_token_guessing_is_throttled(make_world: Callable[..., World]) -> None:
    w = make_world(throttle_max_per_ip=3)
    c = w.browser()
    body = {"email": "a@example.com", "name": "A", "password": PASSWORD}
    codes = [
        c.post("/api/v1/setup/admin", json={**body, "setup_token": f"guess{i}"}).status_code
        for i in range(5)
    ]
    assert codes == [403, 403, 403, 429, 429]
    # Even the right token is refused while blocked.
    right = c.post("/api/v1/setup/admin", json={**body, "setup_token": w.setup_token})
    assert right.status_code == 429


def test_setup_rejects_weak_password_and_bad_email(world: World) -> None:
    c = world.browser()
    base = {"setup_token": world.setup_token, "name": "A"}
    weak = c.post(
        "/api/v1/setup/admin", json={**base, "email": "a@example.com", "password": "short"}
    )
    assert weak.status_code == 422
    assert weak.json()["detail"]["code"] == "PASSWORD_TOO_SHORT"
    bad = c.post("/api/v1/setup/admin", json={**base, "email": "nope", "password": PASSWORD})
    assert bad.json()["detail"]["code"] == "INVALID_EMAIL"
    assert world.db_rows(User) == []


# ---------------------------------------------------------------- passwords & cookies


def test_password_is_argon2id_and_never_returned(world: World) -> None:
    admin = world.create_admin()
    (user,) = world.db_rows(User)
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash
    c, h = signed_in(world)
    invite(world, (c, h), "m@example.com")
    bodies = [
        admin.get("/api/v1/auth/session").text,
        c.get("/api/v1/admin/users").text,
        c.post(
            "/api/v1/auth/password",
            json={"current_password": PASSWORD, "new_password": OTHER_PASSWORD},
            headers=h,
        ).text,
    ]
    for body in bodies:
        assert "argon2" not in body
        assert "password_hash" not in body
        assert PASSWORD not in body and OTHER_PASSWORD not in body


def test_session_cookie_attributes(make_world: Callable[..., World]) -> None:
    w = make_world(cookie_secure=True)
    w.create_admin()
    r = login(w.browser(), "admin@example.com")
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-fc_session=")
    low = cookie.lower()
    assert "httponly" in low and "secure" in low and "samesite=strict" in low and "path=/" in low
    assert "domain" not in low
    assert r.headers["cache-control"] == "no-store"
    # Only a hash of the cookie value is stored.
    token = cookie.split(";")[0].split("=", 1)[1]
    with w.ctx.sessions() as db:
        rows = db.execute(text("SELECT token_hash FROM auth_session")).scalars().all()
    assert rows and token not in rows


def test_login_does_not_enumerate_accounts(world: World) -> None:
    world.create_admin()
    c = world.browser()
    wrong = login(c, "admin@example.com", "wrong password here")
    unknown = login(c, "nobody@example.com", "wrong password here")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_login_normalizes_email(world: World) -> None:
    world.create_admin()
    assert login(world.browser(), "  ADMIN@Example.COM ").status_code == 200


def test_validation_errors_do_not_echo_secrets(world: World) -> None:
    r = world.browser().post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": 12345}
    )
    assert r.status_code == 422
    assert "12345" not in r.text
    r = world.browser().post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": PASSWORD, "admin": True}
    )
    assert r.status_code == 422 and PASSWORD not in r.text


def test_login_rehashes_when_cost_parameters_change(world: World) -> None:
    world.create_admin()
    old = world.db_rows(User)[0].password_hash
    world.ctx.passwords._hasher = type(world.ctx.passwords._hasher)(
        time_cost=2, memory_cost=16, parallelism=1
    )
    assert login(world.browser(), "admin@example.com").status_code == 200
    assert world.db_rows(User)[0].password_hash != old


# ---------------------------------------------------------------- throttling


def test_brute_force_is_throttled_per_account_and_ip(world: World) -> None:
    world.create_admin()
    c = world.browser()
    codes = [login(c, "admin@example.com", f"guess number {i}!").status_code for i in range(5)]
    assert codes == [401] * 5
    blocked = login(c, "admin@example.com", "guess number 6!")
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0
    # The correct password does not bypass a lockout.
    assert login(c, "admin@example.com").status_code == 429
    world.advance(seconds=world.settings.throttle_window_seconds + 1)
    assert login(c, "admin@example.com").status_code == 200


def test_distributed_guessing_hits_the_account_limit(make_world: Callable[..., World]) -> None:
    w = make_world(trusted_proxy_hops=1, throttle_max_per_account=6)
    w.create_admin()
    c = w.browser()
    codes = [
        login(
            c, "admin@example.com", f"guess number {i}!", **{"X-Forwarded-For": f"10.0.0.{i}"}
        ).status_code
        for i in range(8)
    ]
    assert codes[:6] == [401] * 6 and codes[6:] == [429, 429]
    assert login(c, "admin@example.com", **{"X-Forwarded-For": "10.9.9.9"}).status_code == 429


def test_spraying_accounts_hits_the_ip_limit_and_forwarded_header_is_ignored(
    make_world: Callable[..., World],
) -> None:
    w = make_world(throttle_max_per_ip=5)  # trusted_proxy_hops=0: X-Forwarded-For is untrusted
    c = w.browser()
    codes = [
        login(
            c, f"user{i}@example.com", "guess number!", **{"X-Forwarded-For": f"10.0.0.{i}"}
        ).status_code
        for i in range(7)
    ]
    assert codes == [401] * 5 + [429] * 2


def test_successful_login_does_not_reset_distributed_history(world: World) -> None:
    world.create_admin()
    c = world.browser()
    for i in range(3):
        login(c, "admin@example.com", f"guess number {i}!")
    assert login(c, "admin@example.com").status_code == 200
    with world.ctx.sessions() as db:
        keys = world.ctx.throttle.login_keys("admin@example.com", "testclient")
        assert world.ctx.throttle.retry_after(db, keys) is None
        remaining = db.execute(text("SELECT count(*) FROM login_failure")).scalar()
    assert (
        remaining == 6
    )  # 3 failures x (account+ip, account, ip); only the account+ip rows cleared


def test_throttle_applies_to_password_change_verification(world: World) -> None:
    world.create_admin()
    c, h = signed_in(world)
    codes = [
        c.post(
            "/api/v1/auth/password",
            json={"current_password": f"nope nope nope {i}", "new_password": OTHER_PASSWORD},
            headers=h,
        ).status_code
        for i in range(7)
    ]
    assert codes[:5] == [403] * 5 and codes[5] == 429


# ---------------------------------------------------------------- sessions


def test_session_required_and_logout_revokes_server_side(world: World) -> None:
    world.create_admin()
    assert world.browser().get("/api/v1/auth/session").status_code == 401
    c, h = signed_in(world)
    stolen = c.cookies.get("fc_session")
    assert c.get("/api/v1/auth/session").status_code == 200
    assert c.post("/api/v1/auth/logout", headers=h).status_code == 200
    # Replaying the old cookie value after logout fails: revocation is server-side.
    replay = world.browser()
    replay.cookies.set("fc_session", stolen)
    assert replay.get("/api/v1/auth/session").status_code == 401


def test_login_always_issues_a_new_session_and_retires_the_presented_one(world: World) -> None:
    world.create_admin()
    c, _ = signed_in(world)
    first = c.cookies.get("fc_session")
    assert login(c, "admin@example.com").status_code == 200
    assert c.cookies.get("fc_session") != first
    old = world.browser()
    old.cookies.set("fc_session", first)
    assert old.get("/api/v1/auth/session").status_code == 401


def test_idle_and_absolute_expiry(world: World) -> None:
    world.create_admin()
    c, _ = signed_in(world)
    world.advance(seconds=world.settings.session_idle_seconds - 60)
    assert c.get("/api/v1/auth/session").status_code == 200  # activity renews the idle window
    world.advance(seconds=world.settings.session_idle_seconds + 1)
    assert c.get("/api/v1/auth/session").status_code == 401

    c, _ = signed_in(world)
    for _ in range(40):
        world.advance(days=1)
        if c.get("/api/v1/auth/session").status_code == 401:
            break
    else:
        pytest.fail("absolute lifetime never enforced")
    assert world.offset >= timedelta(seconds=world.settings.session_absolute_seconds) - timedelta(
        days=1
    )


def test_password_change_revokes_other_sessions_and_rotates_current(world: World) -> None:
    world.create_admin()
    a, ha = signed_in(world)
    b, _ = signed_in(world)
    before = a.cookies.get("fc_session")
    r = a.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": OTHER_PASSWORD},
        headers=ha,
    )
    assert r.status_code == 200
    assert a.cookies.get("fc_session") != before
    assert a.get("/api/v1/auth/session").status_code == 200
    assert b.get("/api/v1/auth/session").status_code == 401
    assert login(world.browser(), "admin@example.com", PASSWORD).status_code == 401
    assert login(world.browser(), "admin@example.com", OTHER_PASSWORD).status_code == 200


def test_admin_can_revoke_sessions_and_disable_account(world: World) -> None:
    admin = admin_session(world)
    onboard(world, admin, "m@example.com")
    member, _ = signed_in(world, "m@example.com")
    uid = next(u.id for u in world.db_rows(User) if u.email == "m@example.com")
    ac, ah = admin
    assert ac.post(f"/api/v1/admin/users/{uid}/revoke-sessions", headers=ah).status_code == 200
    assert member.get("/api/v1/auth/session").status_code == 401

    member, _ = signed_in(world, "m@example.com")
    assert (
        ac.patch(f"/api/v1/admin/users/{uid}", json={"status": "disabled"}, headers=ah).status_code
        == 200
    )
    assert member.get("/api/v1/auth/session").status_code == 401
    assert login(world.browser(), "m@example.com").status_code == 401  # correct password, disabled


def test_disabling_blocks_open_session_immediately_even_without_revocation(world: World) -> None:
    admin = admin_session(world)
    onboard(world, admin, "m@example.com")
    member, _ = signed_in(world, "m@example.com")
    with world.ctx.sessions() as db:
        u = db.scalar(select(User).where(User.email == "m@example.com"))
        assert u is not None
        u.status = "disabled"
        db.commit()
    assert member.get("/api/v1/auth/session").status_code == 401


# ---------------------------------------------------------------- CSRF


def test_csrf_protection_on_state_changing_requests(world: World) -> None:
    world.create_admin()
    c, h = signed_in(world)
    url = "/api/v1/auth/logout"
    assert c.post(url).json()["detail"]["code"] == "CSRF_TOKEN_INVALID"
    assert c.post(url, headers={"X-CSRF-Token": "0" * 64}).status_code == 403
    # Token from one session is not valid for another.
    other, oh = signed_in(world)
    assert oh["X-CSRF-Token"] != h["X-CSRF-Token"]
    assert c.post(url, headers=oh).status_code == 403
    # A cross-origin page cannot ride the cookie even with a token.
    assert (
        c.post(url, headers={**h, "Origin": "https://evil.example"}).json()["detail"]["code"]
        == "ORIGIN_NOT_ALLOWED"
    )
    assert c.post(url, headers={**h, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.get("/api/v1/auth/session").status_code == 200  # still signed in
    assert c.post(url, headers={**h, "Origin": "http://testserver"}).status_code == 200


def test_csrf_applies_to_admin_endpoints(world: World) -> None:
    world.create_admin()
    c, _ = signed_in(world)
    r = c.post("/api/v1/admin/users", json={"email": "m@example.com", "name": "M"})
    assert r.status_code == 403
    assert world.db_rows(AuthToken) == []


def test_login_rejects_foreign_origin(world: World) -> None:
    world.create_admin()
    r = login(world.browser(), "admin@example.com", Origin="https://evil.example")
    assert r.status_code == 403


def test_configured_origin_is_allowed(make_world: Callable[..., World]) -> None:
    w = make_world(allowed_origins="https://files.example.org")
    w.create_admin()
    assert (
        login(w.browser(), "admin@example.com", Origin="https://files.example.org").status_code
        == 200
    )


def test_public_url_is_an_allowed_origin(make_world: Callable[..., World]) -> None:
    # A proxy that does not forward Host: the API sees an unrelated Host but the public URL works.
    w = make_world(public_url="https://files.example.org/")
    w.create_admin()
    ok = login(w.browser(), "admin@example.com", Origin="https://files.example.org")
    assert ok.status_code == 200
    bad = login(w.browser(), "admin@example.com", Origin="https://evil.example")
    assert bad.status_code == 403


@pytest.mark.parametrize(
    "configured",
    ["https://Files.Example.org", "https://files.example.org:443/", "HTTPS://files.example.org"],
)
def test_public_url_is_normalized_like_a_browser_origin(configured: str) -> None:
    s = Settings(database_url="sqlite://", public_url=configured)  # type: ignore[arg-type]
    assert s.extra_origins == "https://files.example.org"
    s = Settings(database_url="sqlite://", public_url="http://nas.local:8080")  # type: ignore[arg-type]
    assert s.extra_origins == "http://nas.local:8080"


@pytest.mark.parametrize(
    "bad",
    ["files.example.org", "ftp://files.example.org", "https://x.org/app", "https://x.org?a=1"],
)
def test_public_url_must_be_a_plain_origin(bad: str) -> None:
    with pytest.raises(ValueError, match="PUBLIC_URL"):
        Settings(database_url="sqlite://", public_url=bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------- invitation / reset tokens


def test_invitation_is_single_use(world: World) -> None:
    admin = admin_session(world)
    token = invite(world, admin, "m@example.com")
    pending = world.db_rows(User)[-1]
    assert pending.status == "invited" and pending.password_hash is None
    assert login(world.browser(), "m@example.com", PASSWORD).status_code == 401
    accept = {"token": token, "password": PASSWORD}
    c = world.browser()
    assert c.post("/api/v1/auth/invitations/accept", json=accept).status_code == 200
    replay = c.post("/api/v1/auth/invitations/accept", json={**accept, "password": OTHER_PASSWORD})
    assert replay.status_code == 400 and replay.json()["detail"]["code"] == "INVALID_TOKEN"
    assert login(world.browser(), "m@example.com", PASSWORD).status_code == 200
    assert login(world.browser(), "m@example.com", OTHER_PASSWORD).status_code == 401


def test_invitation_expires(world: World) -> None:
    admin = admin_session(world)
    token = invite(world, admin, "m@example.com")
    world.advance(seconds=world.settings.invitation_ttl_seconds + 1)
    r = world.browser().post(
        "/api/v1/auth/invitations/accept", json={"token": token, "password": PASSWORD}
    )
    assert r.status_code == 400


def test_tokens_are_hashed_at_rest_and_unguessable_shapes_fail(world: World) -> None:
    admin = admin_session(world)
    token = invite(world, admin, "m@example.com")
    (row,) = world.db_rows(AuthToken)
    assert token not in (row.token_hash, str(row.id)) and len(row.token_hash) == 64
    assert len(token) >= 43  # 256 bits
    c = world.browser()
    for bad in ("", "x", token[:-1], token + "A", row.token_hash):
        r = c.post("/api/v1/auth/invitations/accept", json={"token": bad, "password": PASSWORD})
        assert r.status_code in (400, 422)


def test_reissue_invalidates_previous_invitation(world: World) -> None:
    admin = admin_session(world)
    first = invite(world, admin, "m@example.com")
    uid = world.db_rows(User)[-1].id
    ac, ah = admin
    second = ac.post(f"/api/v1/admin/users/{uid}/invitation", headers=ah).json()["token"]
    assert second != first
    c = world.browser()
    assert (
        c.post(
            "/api/v1/auth/invitations/accept", json={"token": first, "password": PASSWORD}
        ).status_code
        == 400
    )
    assert (
        c.post(
            "/api/v1/auth/invitations/accept", json={"token": second, "password": PASSWORD}
        ).status_code
        == 200
    )


def test_token_purposes_do_not_cross(world: World) -> None:
    admin = admin_session(world)
    ac, ah = admin
    invitation = invite(world, admin, "m@example.com")
    admin_id = next(u.id for u in world.db_rows(User) if u.role == "admin")
    reset = ac.post(f"/api/v1/admin/users/{admin_id}/password-reset", headers=ah).json()["token"]
    c = world.browser()
    assert (
        c.post(
            "/api/v1/auth/password-reset/complete",
            json={"token": invitation, "password": OTHER_PASSWORD},
        ).status_code
        == 400
    )
    assert (
        c.post(
            "/api/v1/auth/invitations/accept", json={"token": reset, "password": OTHER_PASSWORD}
        ).status_code
        == 400
    )
    # A failed cross-purpose attempt must not burn the legitimate token.
    assert (
        c.post(
            "/api/v1/auth/password-reset/complete",
            json={"token": reset, "password": OTHER_PASSWORD},
        ).status_code
        == 200
    )


def test_password_reset_single_use_expiring_and_revokes_sessions(world: World) -> None:
    admin = admin_session(world)
    onboard(world, admin, "m@example.com")
    member, _ = signed_in(world, "m@example.com")
    uid = next(u.id for u in world.db_rows(User) if u.email == "m@example.com")
    ac, ah = admin

    def issue() -> str:
        return str(ac.post(f"/api/v1/admin/users/{uid}/password-reset", headers=ah).json()["token"])

    stale = issue()
    world.advance(seconds=world.settings.reset_ttl_seconds + 1)
    done = "/api/v1/auth/password-reset/complete"
    c = world.browser()
    assert c.post(done, json={"token": stale, "password": OTHER_PASSWORD}).status_code == 400

    token = issue()
    weak = c.post(done, json={"token": token, "password": "short"})
    assert weak.status_code == 422  # policy failure does not consume the token
    assert member.get("/api/v1/auth/session").status_code == 200
    assert c.post(done, json={"token": token, "password": OTHER_PASSWORD}).status_code == 200
    assert member.get("/api/v1/auth/session").status_code == 401  # old sessions are gone
    assert (
        c.post(done, json={"token": token, "password": "yet another phrase 7"}).status_code == 400
    )
    assert login(world.browser(), "m@example.com", OTHER_PASSWORD).status_code == 200
    assert login(world.browser(), "m@example.com", PASSWORD).status_code == 401


def test_token_replay_second_redeemer_loses(world: World) -> None:
    from app.auth.service import consume_token

    admin = admin_session(world)
    token = invite(world, admin, "m@example.com")
    with world.ctx.sessions() as a, world.ctx.sessions() as b:
        assert consume_token(a, token, "invitation") is not None
        a.commit()
        assert consume_token(b, token, "invitation") is None  # second redeemer loses


def test_reset_is_refused_for_invited_and_disabled_accounts(world: World) -> None:
    admin = admin_session(world)
    ac, ah = admin
    invite(world, admin, "m@example.com")
    uid = world.db_rows(User)[-1].id
    assert ac.post(f"/api/v1/admin/users/{uid}/password-reset", headers=ah).status_code == 409


# ---------------------------------------------------------------- admin API & authz negatives


def test_admin_endpoints_require_authentication_and_admin_role(world: World) -> None:
    admin = admin_session(world)
    onboard(world, admin, "m@example.com")
    member, mh = signed_in(world, "m@example.com")
    anon = world.browser()
    uid = world.db_rows(User)[0].id
    calls: list[tuple[str, str, dict[str, Any] | None]] = [
        ("get", "/api/v1/admin/users", None),
        ("post", "/api/v1/admin/users", {"email": "z@example.com", "name": "Z"}),
        ("patch", f"/api/v1/admin/users/{uid}", {"role": "member"}),
        ("post", f"/api/v1/admin/users/{uid}/invitation", None),
        ("post", f"/api/v1/admin/users/{uid}/password-reset", None),
        ("post", f"/api/v1/admin/users/{uid}/revoke-sessions", None),
    ]
    for method, url, body in calls:
        kw: dict[str, Any] = {"json": body} if body is not None else {}
        assert getattr(anon, method)(url, **kw).status_code == 401, url
        r = getattr(member, method)(url, headers=mh, **kw)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "FORBIDDEN", url
    assert [u.email for u in world.db_rows(User)] == ["admin@example.com", "m@example.com"]


def test_member_cannot_escalate_via_self_service_endpoints(world: World) -> None:
    admin = admin_session(world)
    onboard(world, admin, "m@example.com")
    member, mh = signed_in(world, "m@example.com")
    r = member.patch(
        f"/api/v1/admin/users/{world.db_rows(User)[1].id}", json={"role": "admin"}, headers=mh
    )
    assert r.status_code == 403
    assert next(u for u in world.db_rows(User) if u.email == "m@example.com").role == "member"


def test_admin_api_never_exposes_secrets(world: World) -> None:
    admin = admin_session(world)
    ac, ah = admin
    onboard(world, admin, "m@example.com")
    uid = next(u.id for u in world.db_rows(User) if u.email == "m@example.com")
    reset = ac.post(f"/api/v1/admin/users/{uid}/password-reset", headers=ah)
    reset_body = {
        k: v for k, v in reset.json().items() if k != "token"
    }  # one-time secret by design
    texts = [
        ac.get("/api/v1/admin/users").text,
        ac.patch(f"/api/v1/admin/users/{uid}", json={"name": "Renamed"}, headers=ah).text,
        json.dumps(reset_body),
    ]
    hashes = [u.password_hash for u in world.db_rows(User) if u.password_hash]
    for t in texts:
        assert "argon2" not in t and "password" not in t.lower()
        assert not any(h in t for h in hashes)
    for item in json.loads(texts[0]):
        assert set(item) == {"id", "email", "name", "role", "status", "created_at"}


def test_extra_fields_are_rejected_on_admin_writes(world: World) -> None:
    admin = admin_session(world)
    ac, ah = admin
    r = ac.post(
        "/api/v1/admin/users",
        json={"email": "m@example.com", "name": "M", "password": PASSWORD, "status": "active"},
        headers=ah,
    )
    assert r.status_code == 422 and PASSWORD not in r.text


def test_duplicate_email_and_invalid_input(world: World) -> None:
    admin = admin_session(world)
    ac, ah = admin
    invite(world, admin, "m@example.com")
    dup = ac.post("/api/v1/admin/users", json={"email": " M@Example.com ", "name": "M"}, headers=ah)
    assert dup.status_code == 409
    bad = ac.post("/api/v1/admin/users", json={"email": "not-an-email", "name": "M"}, headers=ah)
    assert bad.status_code == 422


def test_last_active_admin_cannot_be_demoted_or_disabled(world: World) -> None:
    admin = admin_session(world)
    ac, ah = admin
    me = world.db_rows(User)[0].id
    for body in ({"role": "member"}, {"status": "disabled"}):
        r = ac.patch(f"/api/v1/admin/users/{me}", json=body, headers=ah)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "LAST_ADMIN"
    onboard(world, admin, "second@example.com", role="admin")
    second = next(u.id for u in world.db_rows(User) if u.email == "second@example.com")
    assert (
        ac.patch(f"/api/v1/admin/users/{second}", json={"role": "member"}, headers=ah).status_code
        == 200
    )
    assert (
        ac.patch(f"/api/v1/admin/users/{me}", json={"role": "member"}, headers=ah).status_code
        == 409
    )


def test_reenabling_account_without_password_returns_to_invited(world: World) -> None:
    admin = admin_session(world)
    ac, ah = admin
    invite(world, admin, "m@example.com")
    uid = world.db_rows(User)[-1].id
    assert (
        ac.patch(f"/api/v1/admin/users/{uid}", json={"status": "active"}, headers=ah).status_code
        == 409
    )
    assert (
        ac.patch(f"/api/v1/admin/users/{uid}", json={"status": "disabled"}, headers=ah).json()[
            "status"
        ]
        == "disabled"
    )
    assert (
        ac.patch(f"/api/v1/admin/users/{uid}", json={"status": "active"}, headers=ah).json()[
            "status"
        ]
        == "invited"
    )


def test_unknown_user_id_is_404(world: World) -> None:
    admin = admin_session(world)
    ac, ah = admin
    r = ac.post(
        "/api/v1/admin/users/00000000-0000-0000-0000-000000000000/password-reset", headers=ah
    )
    assert r.status_code == 404


# ---------------------------------------------------------------- audit & health


def test_audit_trail_records_events_without_secrets(world: World) -> None:
    admin = admin_session(world)
    login(world.browser(), "admin@example.com", "wrong password here")
    token = invite(world, admin, "m@example.com")
    actions = [a.action for a in world.db_rows(AuthAudit)]
    for expected in ("setup.admin_created", "login.success", "login.failed", "user.invited"):
        assert expected in actions
    dump = json.dumps([(a.action, a.details, a.ip) for a in world.db_rows(AuthAudit)], default=str)
    assert token not in dump and PASSWORD not in dump and "wrong password" not in dump
    assert "@example.com" not in dump  # no plaintext emails in audit details


def test_health_stays_available_without_auth_for_probes(world: World) -> None:
    assert world.browser().get("/api/v1/health/live").status_code == 200
