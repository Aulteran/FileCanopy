"""Health model. Core components decide readiness; optional components only report status."""

import os
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import redis
from sqlalchemy import Engine, text

from app.config import Settings

State = Literal["ok", "unavailable", "not_configured"]
WORKER_HEARTBEAT_PREFIX = "filecanopy:worker:heartbeat:"
RedisFactory = Callable[[str], "redis.Redis[str]"]


@dataclass
class Component:
    state: State
    reason: str | None = None  # short fixed codes only; never exception text, paths or DSNs

    def as_dict(self) -> dict[str, str]:
        out: dict[str, str] = {"state": self.state}
        if self.reason:
            out["reason"] = self.reason
        return out


def check_database(engine: Engine, expected_head: str) -> Component:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:
        return Component("unavailable", "database_unreachable_or_unmigrated")
    if current != expected_head:
        return Component("unavailable", "migration_not_at_head")
    return Component("ok")


def check_library(path: Path) -> Component:
    # Permission probe only: the API must not create files in the user's document tree.
    if not path.is_dir():
        return Component("unavailable", "library_missing")
    if not os.access(path, os.R_OK | os.X_OK):
        return Component("unavailable", "library_not_readable")
    if not os.access(path, os.W_OK):
        return Component("unavailable", "library_not_writable")
    return Component("ok")


def check_app_data(path: Path) -> Component:
    if not path.is_dir():
        return Component("unavailable", "app_data_missing")
    probe = path / f".health-{uuid.uuid4().hex}"
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError:
        return Component("unavailable", "app_data_not_writable")
    return Component("ok")


def _redis_client(url: str) -> "redis.Redis[str]":
    return redis.Redis.from_url(
        url, socket_connect_timeout=1, socket_timeout=1, decode_responses=True
    )


# DNS resolution is not covered by socket timeouts, so an unresolvable Redis host could stall
# the endpoint for seconds and fail the container healthcheck. The optional tier therefore runs
# under a hard deadline and can never delay or fail the core answer.
OPTIONAL_DEADLINE_SECONDS = 1.5
_optional_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="health-optional")


def _probe_optional(url: str, factory: RedisFactory) -> tuple[Component, Component]:
    try:
        client = factory(url)
        client.ping()
    except Exception:
        down = Component("unavailable", "redis_unreachable")
        return down, down
    try:
        found = next(client.scan_iter(match=WORKER_HEARTBEAT_PREFIX + "*", count=100), None)
    except Exception:
        return Component("ok"), Component("unavailable", "redis_unreachable")
    worker = Component("ok") if found else Component("unavailable", "no_worker_heartbeat")
    return Component("ok"), worker


def check_optional(url: str, factory: RedisFactory = _redis_client) -> tuple[Component, Component]:
    """Return (redis, worker). Empty url means the optional tier is not configured."""
    if not url:
        return Component("not_configured"), Component("not_configured")
    future = _optional_pool.submit(_probe_optional, url, factory)
    try:
        return future.result(timeout=OPTIONAL_DEADLINE_SECONDS)
    except FutureTimeout:
        timed_out = Component("unavailable", "redis_timeout")
        return timed_out, timed_out


def build_report(settings: Settings, engine: Engine, expected_head: str) -> dict[str, object]:
    core = {
        "database": check_database(engine, expected_head),
        "library": check_library(settings.library_path),
        "app_data": check_app_data(settings.app_data_path),
    }
    redis_state, worker_state = check_optional(settings.redis_url)
    optional = {"redis": redis_state, "worker": worker_state}
    core_ok = all(c.state == "ok" for c in core.values())
    optional_ok = all(c.state in ("ok", "not_configured") for c in optional.values())
    status = "unavailable" if not core_ok else ("ok" if optional_ok else "degraded")
    return {
        "status": status,
        "core": {k: v.as_dict() for k, v in core.items()},
        "optional": {k: v.as_dict() for k, v in optional.items()},
    }
