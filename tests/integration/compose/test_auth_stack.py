"""M02 against the real stack: PostgreSQL migration 0002, nginx proxying, real client IPs.

Run with the rest of the compose suite (`npm run test:compose`). Uses random credentials and
temp directories only.
"""

import json
import re
import urllib.error
import urllib.request
from typing import Any

import pytest
from test_stack import Stack

pytestmark = pytest.mark.compose

PASSWORD = "correct horse battery staple"


class Client:
    def __init__(self, stack: Stack) -> None:
        self.base = f"http://127.0.0.1:{stack.port}"
        self.cookie: str | None = None
        self.csrf: str | None = None

    def call(
        self, method: str, path: str, body: dict[str, Any] | None = None, **headers: str
    ) -> tuple[int, dict[str, Any], Any]:
        hdrs = {"Content-Type": "application/json", **headers}
        if self.cookie:
            hdrs["Cookie"] = self.cookie
        if self.csrf and method != "GET":
            hdrs.setdefault("X-CSRF-Token", self.csrf)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(  # noqa: S310
            self.base + path, data=data, method=method, headers=hdrs
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310
                return r.status, json.loads(r.read() or b"{}"), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), e.headers

    def login(self, email: str, password: str = PASSWORD) -> int:
        status, body, headers = self.call(
            "POST", "/api/v1/auth/login", {"email": email, "password": password}
        )
        if status == 200:
            self.cookie = headers["Set-Cookie"].split(";")[0]
            self.csrf = body["csrf_token"]
        return status


@pytest.fixture
def stack(app_stack: Stack) -> Stack:
    return app_stack


def test_auth_flow_on_real_postgres_behind_nginx(stack: Stack) -> None:
    assert stack.sql("select version_num from alembic_version") == "0003"
    anon = Client(stack)
    assert anon.call("GET", "/api/v1/setup/status")[1] == {"setup_required": True}

    logs = stack.compose("logs", "--no-color", "api").stdout
    tokens = re.findall(r"FIRST-RUN SETUP TOKEN[^:]*: ([\w-]+)", logs)
    assert len(tokens) == 1
    stored = stack.sql("select value::text from app_setting where key='bootstrap_token_hash'")
    assert tokens[0] not in stored

    setup = {"email": "admin@example.com", "name": "Ada", "password": PASSWORD}
    assert anon.call("POST", "/api/v1/setup/admin", {**setup, "setup_token": "wrong"})[0] == 403
    assert anon.call("POST", "/api/v1/setup/admin", {**setup, "setup_token": tokens[0]})[0] == 201
    assert anon.call("POST", "/api/v1/setup/admin", {**setup, "setup_token": tokens[0]})[0] == 409
    assert stack.sql("select count(*) from app_user") == "1"
    assert stack.sql("select password_hash from app_user").startswith("$argon2id$")

    admin = Client(stack)
    assert admin.login("admin@example.com") == 200
    assert admin.cookie and admin.cookie.startswith("__Host-fc_session=")
    assert admin.call("POST", "/api/v1/auth/logout", None, Origin="https://evil.example")[0] == 403

    status, created, _ = admin.call(
        "POST", "/api/v1/admin/users", {"email": "m@example.com", "name": "M"}
    )
    assert status == 201
    invitation = created["token"]["token"]
    assert invitation not in stack.sql("select string_agg(token_hash, ',') from auth_token")
    accept = {"token": invitation, "password": PASSWORD}
    assert anon.call("POST", "/api/v1/auth/invitations/accept", accept)[0] == 200
    assert anon.call("POST", "/api/v1/auth/invitations/accept", accept)[0] == 400

    member = Client(stack)
    assert member.login("m@example.com") == 200
    assert member.call("GET", "/api/v1/admin/users")[0] == 403
    assert member.call("POST", "/api/v1/auth/logout")[0] == 200
    stale = Client(stack)
    stale.cookie = member.cookie
    assert stale.call("GET", "/api/v1/auth/session")[0] == 401

    # The member is locked, not the admin: the storage suite reuses this stack and the admin.
    # Throttling keys on the real client address forwarded by nginx, not nginx's own address.
    codes = [Client(stack).login("m@example.com", f"wrong password {i}") for i in range(6)]
    assert codes == [401] * 5 + [429]
    assert PASSWORD not in stack.compose("logs", "--no-color").stdout
