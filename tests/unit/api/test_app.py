import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app import main as main_module
from app.config import Settings
from app.db.migrate import head_revision
from app.logging_config import JsonFormatter


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    lib, data = tmp_path / "library", tmp_path / "app-data"
    lib.mkdir()
    data.mkdir()
    # StaticPool: endpoints run in a worker thread and must see the same in-memory database.
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
        conn.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": head_revision()})
    monkeypatch.setattr(main_module, "make_engine", lambda _: engine)
    cfg = Settings(  # type: ignore[call-arg]
        database_url="postgresql+psycopg://u:p@h/db", library_path=lib, app_data_path=data
    )
    return TestClient(main_module.create_app(cfg))


def test_live_touches_no_dependency(client: TestClient) -> None:
    assert client.get("/api/v1/health/live").json() == {"status": "ok"}


def test_health_ok_without_workers_or_ai(client: TestClient) -> None:
    body = client.get("/api/v1/health").json()
    assert client.get("/api/v1/health").status_code == 200
    assert body["status"] == "ok"
    assert set(body["core"]) == {"database", "library", "app_data"}
    assert set(body["optional"]) == {"redis", "worker"}
    assert "ai" not in json.dumps(body).lower().replace("unavailable", "")


def test_health_503_when_core_down(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "library").rmdir()
    r = client.get("/api/v1/health")
    assert r.status_code == 503
    assert r.json()["core"]["library"]["reason"] == "library_missing"


def test_request_id_is_server_generated(client: TestClient) -> None:
    r = client.get("/api/v1/health/live", headers={"X-Request-ID": "evil\ninjected"})
    assert "injected" not in r.headers["x-request-id"]


def test_interactive_docs_not_exposed(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404


def test_json_log_drops_exception_text() -> None:
    try:
        raise RuntimeError("postgresql://user:hunter2@db/x")
    except RuntimeError:
        import sys

        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    out = JsonFormatter().format(record)
    assert "hunter2" not in out
    assert json.loads(out)["exc_type"] == "RuntimeError"
