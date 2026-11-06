"""M23: nothing secret or personal reaches a log stream.

Every secret-bearing position (query string, JSON body, bearer header, cookie, path id) carries a
unique canary while a person signs in, searches, uses a token and fails; no log record produced
by the API may contain any canary. The container-level streams (uvicorn, nginx) are checked
against the files that configure them.
"""

import json
import logging
import re
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_auth import PASSWORD, World, invite, signed_in
from uvicorn.config import Config

from app.config import Settings
from app.logging_config import configure_logging

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., World]:
    def factory(**overrides: Any) -> World:
        return World(tmp_path, monkeypatch, **overrides)

    return factory


def canary(label: str) -> str:
    return f"CANARY-{label}-{uuid.uuid4().hex}"


class Capture(logging.Handler):
    """Collects what the *server side* logs. The app replaces the root handlers when it starts,
    so this attaches after the world exists. The test client's own `httpx` lines are the
    client's, not the server's, and are left out."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith(("httpx", "httpcore")):
            self.records.append(record)

    def access(self) -> list[str]:
        return [r.getMessage() for r in self.records if r.name == "filecanopy.access"]

    def text(self) -> str:
        return "\n".join(r.getMessage() for r in self.records)

    def attach(self) -> None:
        root = logging.getLogger()
        root.addHandler(self)
        root.setLevel(logging.DEBUG)


@pytest.fixture
def capture() -> Iterator[Capture]:
    handler = Capture()
    try:
        yield handler
    finally:
        logging.getLogger().removeHandler(handler)


def test_requests_with_secrets_everywhere_leave_no_trace_in_logs(
    make_world: Callable[..., World], capture: Capture
) -> None:
    w = make_world()
    w.create_admin()
    admin = signed_in(w)
    capture.attach()  # after the app has set its own handlers up
    c = w.browser()
    seen = {
        name: canary(name)
        for name in (
            "query-code",
            "query-state",
            "search",
            "password",
            "token",
            "bearer",
            "cookie",
            "apikey",
            "pathid",
        )
    }

    # Sign-in: wrong password, unknown account, canary password in the body.
    c.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": seen["password"]},
    )
    c.post(
        "/api/v1/auth/login",
        json={
            "email": "nobody-" + seen["token"].lower() + "@example.com",
            "password": PASSWORD,
        },
    )
    # OIDC callback with provider secrets in the query (the M12 follow-up).
    c.get(f"/api/v1/auth/oidc/callback?code={seen['query-code']}&state={seen['query-state']}")
    # Invitation and reset tokens in bodies.
    c.post(
        "/api/v1/auth/invitations/accept",
        json={"token": seen["token"], "password": PASSWORD},
    )
    c.post(
        "/api/v1/auth/password-reset/complete",
        json={"token": seen["token"], "password": PASSWORD},
    )
    # Search terms and an id-shaped path.
    ac, ah = admin
    ac.get(f"/api/v1/search?q={seen['search']}", headers=ah)
    ac.get(f"/api/v1/nodes/{seen['pathid']}", headers=ah)
    # Bearer token, bad cookie, API key in a body.
    c.post(
        "/api/v1/mcp",
        headers={"Authorization": f"Bearer {seen['bearer']}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    c.cookies.set("fc_session", seen["cookie"])
    c.get("/api/v1/auth/session")
    ac.put("/api/v1/ai/me/provider", json={"api_key": seen["apikey"]}, headers=ah)
    # A request that fails in validation: the 422 path echoes values by default.
    ac.post("/api/v1/admin/users", json={"email": seen["token"], "role": 7}, headers=ah)

    text = capture.text()
    leaked = {name: value for name, value in seen.items() if value in text}
    assert leaked == {}
    # The access log exists and has the shape promised: method, route template, status, time.
    access = capture.access()
    assert access
    assert all(re.fullmatch(r"[A-Z]+ \S+ \d{3} \d+ms", line) for line in access), access
    assert any("/api/v1/search 200" in line for line in access)
    assert not any("?" in line or "q=" in line for line in access)


def test_access_log_uses_route_templates_not_ids(
    make_world: Callable[..., World], capture: Capture
) -> None:
    w = make_world()
    w.create_admin()
    admin = signed_in(w)
    capture.attach()  # after the app has set its own handlers up
    token = invite(w, admin, "bob@example.com")
    ac, ah = admin
    node = uuid.uuid4()
    ac.get(f"/api/v1/nodes/{node}", headers=ah)
    w.browser().post("/api/v1/auth/invitations/accept", json={"token": token, "password": PASSWORD})
    text = capture.text()
    assert str(node) not in text
    assert token not in text
    assert "/api/v1/nodes/{node_id}" in text


def test_a_crashing_handler_is_still_logged_without_its_message(
    make_world: Callable[..., World], capture: Capture
) -> None:
    w = make_world()
    secret = canary("exception")
    capture.attach()

    @w.app.get("/api/v1/_boom")
    def boom() -> None:
        raise RuntimeError(f"database password is {secret}")

    r = TestClient(w.app, raise_server_exceptions=False).get(f"/api/v1/_boom?x={secret}")
    assert r.status_code == 500
    assert secret not in r.text
    access = capture.access()
    assert access == [access[0]]
    assert access[0].startswith("GET /api/v1/_boom 500 ")


def test_settings_never_print_their_secrets(tmp_path: Path) -> None:
    s = Settings(
        database_url="postgresql+psycopg://user:hunter2-db@h/db",  # type: ignore[arg-type]
        library_path=tmp_path / "library",  # type: ignore[arg-type]
        app_data_path=tmp_path / "app-data",  # type: ignore[arg-type]
        secrets_key="hunter2-key",  # type: ignore[arg-type]
    )
    for text in (repr(s), str(s), repr(s.model_dump()), s.model_dump_json()):
        assert "hunter2" not in text


# ---------------------------------------------------------------- container streams


def test_uvicorn_exception_stream_uses_the_redacting_formatter(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Match server startup: uvicorn installs handlers first, then loads the application factory.
    Config("app.main:create_app", factory=True, access_log=False)
    configure_logging()
    secret = canary("uvicorn-exception")
    try:
        raise RuntimeError(secret)
    except RuntimeError:
        logging.getLogger("uvicorn.error").exception("Exception in ASGI application")
    access = logging.getLogger("uvicorn.access")
    assert not access.hasHandlers()  # must not undo uvicorn's --no-access-log decision
    access.info("GET /?code=%s", secret)
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    assert "Traceback" not in output.err
    record = json.loads(output.err)
    assert record["logger"] == "uvicorn.error"
    assert record["exc_type"] == "RuntimeError"


def test_the_image_and_stack_switch_uvicorn_access_log_off() -> None:
    """uvicorn prints the full request line (with the query string) unless told not to."""
    for path in ("apps/api/Dockerfile", "deploy/contrib/swarm/docker-stack.yml"):
        text = (ROOT / path).read_text(encoding="utf-8")
        starts = [ln for ln in text.splitlines() if "uvicorn" in ln and "app.main" in ln]
        assert starts, path
        assert all("--no-access-log" in ln for ln in starts), path


def test_nginx_access_logs_omit_query_strings() -> None:
    conf = (ROOT / "apps/web/nginx.conf").read_text(encoding="utf-8")
    formats = re.findall(r"log_format\s+(\w+)\s+([^;]+);", conf)
    assert formats
    for _name, body in formats:
        assert not re.search(r"\$(request\b|request_uri|args|query_string|http_referer)", body)
    uses = re.findall(r"^\s*access_log\s+(?!off)(\S+)\s*(\S*);", conf, re.M)
    assert uses
    assert all(fmt for _path, fmt in uses)  # an access_log without a format would use "combined"
    assert "$request " not in conf.split("server {", 1)[0]
