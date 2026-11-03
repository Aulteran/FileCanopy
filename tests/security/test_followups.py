"""M23: follow-ups from earlier milestones that belong to hardening."""

from pathlib import Path

import pytest
from test_acl import Fx
from test_auth import OTHER_PASSWORD, PASSWORD, World
from test_files import Api
from test_mcp import Client, mint

from app.db.models import McpToken


def make_fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **over: object) -> Fx:
    return Fx(World(tmp_path, monkeypatch, mcp_enabled=True, **over))


def alive(fx: Fx) -> int:
    return sum(1 for t in fx.w.db_rows(McpToken) if t.revoked_at is None)


def test_changing_the_password_ends_the_persons_api_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = make_fx(tmp_path, monkeypatch)
    alice, bob = Api(fx, "alice"), Api(fx, "bob")
    a_token, _ = mint(alice)
    b_token, _ = mint(bob)
    assert Client(fx, a_token).rpc("ping").status_code == 200
    r = alice.post(
        "/auth/password",
        json={"current_password": PASSWORD, "new_password": OTHER_PASSWORD},
    )
    assert r.status_code == 200, r.text
    assert Client(fx, a_token).rpc("ping").status_code == 401
    assert Client(fx, b_token).rpc("ping").status_code == 200  # someone else's token is untouched
    assert alive(fx) == 1


def test_forced_sign_out_and_disabling_end_the_persons_api_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = make_fx(tmp_path, monkeypatch)
    alice, bob = Api(fx, "alice"), Api(fx, "bob")
    a_token, _ = mint(alice)
    b_token, _ = mint(bob)
    c, h = fx.admin
    r = c.post(f"/api/v1/admin/users/{fx.uid('alice@example.com')}/revoke-sessions", headers=h)
    assert r.status_code == 200, r.text
    assert Client(fx, a_token).rpc("ping").status_code == 401
    r = c.patch(
        f"/api/v1/admin/users/{fx.uid('bob@example.com')}",
        json={"status": "disabled"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert Client(fx, b_token).rpc("ping").status_code == 401
    assert alive(fx) == 0


def test_a_completed_password_reset_ends_the_persons_api_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = make_fx(tmp_path, monkeypatch)
    alice = Api(fx, "alice")
    value, _ = mint(alice)
    c, h = fx.admin
    r = c.post(f"/api/v1/admin/users/{fx.uid('alice@example.com')}/password-reset", headers=h)
    assert r.status_code in (200, 201), r.text
    token = r.json()["token"]
    done = fx.w.browser().post(
        "/api/v1/auth/password-reset/complete",
        json={"token": token, "password": OTHER_PASSWORD},
    )
    assert done.status_code == 200, done.text
    assert Client(fx, value).rpc("ping").status_code == 401


def test_bad_tokens_from_one_address_do_not_lock_out_a_good_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = make_fx(tmp_path, monkeypatch, throttle_max_per_ip=3)
    good, _ = mint(Api(fx, "alice"))
    for _ in range(3):
        assert Client(fx, "fcmcp_" + "q" * 40).rpc("ping").status_code == 401
    assert Client(fx, "fcmcp_" + "q" * 40).rpc("ping").status_code == 429
    assert Client(fx, good).rpc("ping").status_code == 200


def test_search_is_rate_limited_per_person(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = Fx(World(tmp_path, monkeypatch, search_per_minute=3))
    alice, bob = Api(fx, "alice"), Api(fx, "bob")
    codes = [alice.get("/search", params={"q": "invoice"}).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    assert (
        alice.get("/search", params={"q": "invoice"}).json()["detail"]["code"] == "SEARCH_TOO_FAST"
    )
    assert bob.get("/search", params={"q": "invoice"}).status_code == 200  # own window


def test_invitation_accept_and_reset_complete_refuse_a_cross_site_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = make_fx(tmp_path, monkeypatch)
    browser = fx.w.browser()
    body = {"token": "x" * 32, "password": PASSWORD}
    for path in (
        "/api/v1/auth/invitations/accept",
        "/api/v1/auth/password-reset/complete",
    ):
        for headers in (
            {"Origin": "https://evil.example"},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            r = browser.post(path, json=body, headers=headers)
            assert r.status_code == 403, (path, headers, r.text)
