"""M23: every route is closed to an anonymous caller and to a cross-site browser request.

The M03 guard test proves each node route depends on the policy engine. This one is blunter and
sees the whole surface: it calls every `/api/v1` route with no credentials and expects `401`
(or one of the few routes that are public on purpose, each with its reason), and it calls every
state-changing route from a signed-in browser that claims a foreign origin and expects a refusal
before anything is read or written. A route added later without auth fails here, not in review.
"""

import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from test_acl import _api_routes
from test_auth import PASSWORD, World, signed_in

from app.db.models import FileEvent, Node

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

# Public on purpose: sign-in and first-run flows, health probes, and the provider round trip.
# Nothing here reads or writes a person's content. Value: why it is public.
PUBLIC: dict[str, str] = {
    "/api/v1/health": "container probe, fixed reason codes only (ADR-012)",
    "/api/v1/health/live": "liveness probe",
    "/api/v1/setup/status": "tells the first-run page whether setup is still open",
    "/api/v1/setup/admin": "one-time first administrator, needs the setup token",
    "/api/v1/auth/login": "sign-in",
    "/api/v1/auth/config": "which sign-in options exist",
    "/api/v1/auth/invitations/accept": "invitation token in the body",
    "/api/v1/auth/password-reset/request": "uniform answer, instance mail only",
    "/api/v1/auth/password-reset/complete": "reset token in the body",
    "/api/v1/auth/oidc/login/start": "provider round trip",
    "/api/v1/auth/oidc/invitation/start": "provider round trip with an invitation token",
    "/api/v1/auth/oidc/callback": "provider redirect target; state + PKCE verified",
    "/api/v1/auth/session": "answers 200 with `authenticated: false` when signed out",
    "/api/v1/auth/logout": "idempotent sign-out",
    "/api/v1/mcp": "bearer token endpoint: answers 401 itself (checked separately)",
}


@pytest.fixture
def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., World]:
    def factory(**overrides: Any) -> World:
        return World(tmp_path, monkeypatch, **overrides)

    return factory


# Valid-shaped bodies for the public POST routes, so the origin check (made inside the handler,
# after the body is parsed) is what answers. The two provider-start routes are disabled without a
# configured provider; their origin check is covered in test_oidc_mail.
PUBLIC_BODIES: dict[str, dict[str, Any]] = {
    "/api/v1/setup/admin": {
        "setup_token": "x" * 32,
        "email": "a@example.com",
        "name": "A",
        "password": PASSWORD,
    },
    "/api/v1/auth/login": {"email": "a@example.com", "password": PASSWORD},
    "/api/v1/auth/invitations/accept": {"token": "x" * 32, "password": PASSWORD},
    "/api/v1/auth/password-reset/complete": {"token": "x" * 32, "password": PASSWORD},
    "/api/v1/auth/password-reset/request": {"email": "a@example.com"},
}
SKIP_ORIGIN = {
    "/api/v1/mcp",
    "/api/v1/auth/oidc/login/start",
    "/api/v1/auth/oidc/invitation/start",
}


def fill(path: str) -> str:
    return re.sub(
        r"\{(\w+)\}",
        lambda m: str(uuid.uuid4()) if m.group(1).endswith("id") else "7",
        path,
    )


def routes_of(w: World) -> list[tuple[str, str, APIRoute]]:
    found = [
        (method, r.path, r)
        for r in _api_routes(w.app.routes)
        if r.path.startswith("/api/v1")
        for method in sorted(r.methods or ())
        if method != "HEAD"
    ]
    assert len(found) > 150, "the route walk found too little; the app layout changed"
    return found


def test_every_route_refuses_an_anonymous_caller(
    make_world: Callable[..., World],
) -> None:
    w = make_world()
    w.create_admin()
    anon = w.browser()
    open_routes: list[str] = []
    for method, path, _route in routes_of(w):
        if path in PUBLIC:
            continue
        r = anon.request(method, fill(path), json={} if method in UNSAFE else None)
        if r.status_code != 401:
            open_routes.append(f"{method} {path} -> {r.status_code}")
    assert open_routes == []


def test_the_public_list_has_no_stale_entries(make_world: Callable[..., World]) -> None:
    w = make_world()
    real = {path for _m, path, _r in routes_of(w)}
    assert [p for p in PUBLIC if p not in real] == []


def test_public_routes_never_return_a_session_or_content_to_an_anonymous_caller(
    make_world: Callable[..., World],
) -> None:
    w = make_world()
    w.create_admin()
    anon = w.browser()
    body = anon.get("/api/v1/auth/session").json()
    assert body.get("authenticated") in (False, None)
    assert "csrf_token" not in body
    assert anon.get("/api/v1/health").json().keys() <= {
        "status",
        "core",
        "optional",
        "version",
    }
    assert (
        anon.post(
            "/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        ).status_code
        == 401
    )


def test_every_state_changing_route_refuses_a_cross_site_browser(
    make_world: Callable[..., World],
) -> None:
    w = make_world()
    w.create_admin()
    c, h = signed_in(w)
    with w.ctx.sessions() as db:
        before = (db.query(Node).count(), db.query(FileEvent).count())
    leaked: list[str] = []
    for method, path, _route in routes_of(w):
        if method not in UNSAFE or path in SKIP_ORIGIN:
            continue
        for extra in (
            {"Origin": "https://evil.example"},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            body = PUBLIC_BODIES.get(path, {})
            r = c.request(method, fill(path), json=body, headers={**h, **extra})
            code = (
                (r.json().get("detail") or {}).get("code")
                if r.status_code < 500
                else None
            )
            if r.status_code != 403 or code not in (
                "CROSS_SITE_REQUEST",
                "ORIGIN_NOT_ALLOWED",
            ):
                leaked.append(
                    f"{method} {path} {list(extra)[0]} -> {r.status_code} {code}"
                )
    print("\n".join(leaked))
    assert leaked == []
    with w.ctx.sessions() as db:
        assert (db.query(Node).count(), db.query(FileEvent).count()) == before
