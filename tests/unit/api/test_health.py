import os
import time
from pathlib import Path

import pytest
import redis
from sqlalchemy import create_engine, text

from app import health
from app.config import Settings
from app.health import Component, build_report


class FakeRedis:
    def __init__(self, heartbeats: list[str] | None = None, down: bool = False) -> None:
        self.heartbeats = heartbeats or []
        self.down = down

    def ping(self) -> bool:
        if self.down:
            raise redis.ConnectionError("secret-host:6379 refused")
        return True

    def scan_iter(self, match: str, count: int) -> object:
        if self.down:
            raise redis.ConnectionError("secret-host:6379 refused")
        return iter(self.heartbeats)


def engine_with_version(version: str | None):  # type: ignore[no-untyped-def]
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
        if version:
            conn.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": version})
    return engine


def settings(tmp_path: Path, redis_url: str = "") -> Settings:
    lib, data = tmp_path / "library", tmp_path / "app-data"
    lib.mkdir(exist_ok=True)
    data.mkdir(exist_ok=True)
    return Settings(  # type: ignore[call-arg]
        database_url="postgresql+psycopg://u:p@h/db",
        library_path=lib,
        app_data_path=data,
        redis_url=redis_url,
    )


def test_database_at_head_ok() -> None:
    assert health.check_database(engine_with_version("0001"), "0001").state == "ok"


def test_database_behind_head_unavailable() -> None:
    c = health.check_database(engine_with_version("0000"), "0001")
    assert (c.state, c.reason) == ("unavailable", "migration_not_at_head")


def test_database_unmigrated_unavailable() -> None:
    engine = create_engine("sqlite://")  # no alembic_version table
    assert health.check_database(engine, "0001").state == "unavailable"


def test_library_missing_and_present(tmp_path: Path) -> None:
    assert health.check_library(tmp_path / "nope").reason == "library_missing"
    assert health.check_library(tmp_path).state == "ok"


def test_library_probe_does_not_write(tmp_path: Path) -> None:
    health.check_library(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_app_data_probe_cleans_up(tmp_path: Path) -> None:
    assert health.check_app_data(tmp_path).state == "ok"
    assert list(tmp_path.iterdir()) == []
    assert health.check_app_data(tmp_path / "nope").reason == "app_data_missing"


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX non-root")
def test_app_data_not_writable(tmp_path: Path) -> None:
    tmp_path.chmod(0o500)
    try:
        assert health.check_app_data(tmp_path).reason == "app_data_not_writable"
    finally:
        tmp_path.chmod(0o700)


def test_optional_not_configured_is_not_a_failure() -> None:
    assert health.check_optional("") == (
        Component("not_configured"),
        Component("not_configured"),
    )


def test_redis_down_reason_has_no_exception_text() -> None:
    redis_c, worker_c = health.check_optional("redis://x", lambda _: FakeRedis(down=True))  # type: ignore[arg-type,return-value]
    assert redis_c == worker_c == Component("unavailable", "redis_unreachable")
    assert "secret-host" not in str(redis_c.as_dict())


def test_worker_requires_heartbeat() -> None:
    up = health.check_optional("redis://x", lambda _: FakeRedis(["k"]))  # type: ignore[arg-type,return-value]
    none = health.check_optional("redis://x", lambda _: FakeRedis([]))  # type: ignore[arg-type,return-value]
    assert up == (Component("ok"), Component("ok"))
    assert none == (Component("ok"), Component("unavailable", "no_worker_heartbeat"))


def test_optional_probe_has_hard_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: an unresolvable Redis host stalled /health for ~8s, failing the healthcheck.
    monkeypatch.setattr(health, "OPTIONAL_DEADLINE_SECONDS", 0.2)

    def hang(_: str) -> object:
        time.sleep(2)
        return FakeRedis()

    started = time.monotonic()
    redis_c, worker_c = health.check_optional("redis://x", hang)  # type: ignore[arg-type]
    assert time.monotonic() - started < 1
    assert redis_c == worker_c == Component("unavailable", "redis_timeout")


def test_report_core_ok_no_optional_configured(tmp_path: Path) -> None:
    report = build_report(settings(tmp_path), engine_with_version("0001"), "0001")
    assert report["status"] == "ok"


def test_report_degraded_when_only_optional_down(tmp_path: Path) -> None:
    # Configured but unreachable Redis (connection refused): optional tier down, core fine.
    report = build_report(
        settings(tmp_path, "redis://127.0.0.1:1/0"), engine_with_version("0001"), "0001"
    )
    assert report["status"] == "degraded"
    assert all(v["state"] == "ok" for v in report["core"].values())  # type: ignore[attr-defined]
    assert report["optional"]["worker"]["state"] == "unavailable"  # type: ignore[index]


def test_report_unavailable_when_core_down_even_if_optional_ok(tmp_path: Path) -> None:
    report = build_report(settings(tmp_path), engine_with_version("0000"), "0001")
    assert report["status"] == "unavailable"
    assert report["core"]["database"]["reason"] == "migration_not_at_head"  # type: ignore[index]
