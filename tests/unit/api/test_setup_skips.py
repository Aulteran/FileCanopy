"""Skipped steps of the first-run server setup are saved on the server, for administrators only."""

from typing import Any

from test_acl import Fx
from test_files import Api


def put(api: Api, body: dict[str, Any]) -> Any:
    return api.client.put("/api/v1/admin/setup-skips", json=body, headers=api.headers)


def test_skips_are_saved_listed_and_undone(tmp_path: Any, monkeypatch: Any) -> None:
    from test_auth import World

    fx = Fx(World(tmp_path, monkeypatch))
    admin = Api(fx, "admin")
    assert admin.get("/admin/setup-skips").json() == {"skipped": []}
    assert put(admin, {"step": "smtp", "skipped": True}).json() == {"skipped": ["smtp"]}
    assert put(admin, {"step": "ai", "skipped": True}).json() == {"skipped": ["ai", "smtp"]}
    assert put(admin, {"step": "ai", "skipped": True}).json() == {"skipped": ["ai", "smtp"]}
    assert admin.get("/admin/setup-skips").json() == {"skipped": ["ai", "smtp"]}
    assert put(admin, {"step": "ai", "skipped": False}).json() == {"skipped": ["smtp"]}


def test_only_administrators_and_known_steps(tmp_path: Any, monkeypatch: Any) -> None:
    from test_auth import World

    fx = Fx(World(tmp_path, monkeypatch))
    admin, alice = Api(fx, "admin"), Api(fx, "alice")
    assert alice.get("/admin/setup-skips").status_code == 403
    assert put(alice, {"step": "ai", "skipped": True}).status_code == 403
    assert put(admin, {"step": "files", "skipped": True}).status_code == 422
    assert put(admin, {"step": "mail", "skipped": True}).status_code == 422  # merged into smtp
