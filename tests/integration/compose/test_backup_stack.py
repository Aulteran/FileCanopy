"""M06c against the real stack: migration 0008 on PostgreSQL, the optional /backups mount, the
admin settings and a run through nginx, encrypted per-root archives on the bind-mounted volume
(verified and decrypted by the shipped command line), a restore into a new folder, volume
validation at startup, and the stack without the override.

Run with the rest of the compose suite (`npm run test:compose`). Random credentials and generated
bytes only; no cloud service is involved.
"""

import json
import secrets
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_auth_stack import Client
from test_files_stack import js, raw
from test_stack import Stack
from test_storage_stack import root_node, setup_admin

pytestmark = pytest.mark.compose

OVERRIDE = "compose.backup.yaml"


class BackupStack(Stack):
    """The standard stack plus the optional backup override and a separate backup directory."""

    def __init__(self, tmp: Path, name: str) -> None:
        super().__init__(tmp, name)
        self.backups = tmp / "backups"
        self.backups.mkdir()
        self.env["FILECANOPY_BACKUP_PATH"] = str(self.backups)
        self.files.append(self.dir / OVERRIDE)


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[BackupStack]:
    s = BackupStack(tmp_path_factory.mktemp("backup"), f"fcbk{secrets.token_hex(3)}")
    s.up()
    yield s
    s.down(volumes=True)


SETTINGS: dict[str, Any] = {
    "enabled": True,
    "interval_minutes": 60,
    "full_every": 3,
    "min_chains_kept": 2,
    "max_size_bytes": None,
    "encrypt": True,
    "acknowledge_unencrypted": False,
}


def wait_run(admin: Client, kind: str, timeout: float = 90) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        _, status, _ = admin.call("GET", "/api/v1/admin/backups")
        shared = next((r for r in status["roots"] if r["kind"] == "shared"), None)
        last = shared["last_run"] if shared else None
        if last and last["status"] == "ok" and last["kind"] == kind:
            return status
        time.sleep(1)
    raise AssertionError(f"no {kind} run finished: {status}")


def test_backups_end_to_end_on_postgres_behind_nginx(stack: BackupStack) -> None:
    assert stack.sql("select version_num from alembic_version") == "0027"
    admin = setup_admin(stack)
    status = admin.call("GET", "/api/v1/admin/backups")[1]
    assert status["settings"]["enabled"] is False  # off by default
    assert status["volume"]["usable"] is True
    assert "same_device" in status["warnings"] or status["volume"]["same_device"] is False

    shared = root_node(stack, "shared")
    for name, data in (("a.txt", b"alpha " * 50), ("b.bin", secrets.token_bytes(50_000))):
        code, _, _ = raw(
            admin,
            "POST",
            f"/api/v1/nodes/{shared}/files?name={name}&on_conflict=reject",
            data,
            **{"Content-Type": "application/octet-stream"},
        )
        assert code == 201

    code, body, _ = admin.call("PUT", "/api/v1/admin/backups/settings", SETTINGS)
    assert code == 200, body
    key = body["recovery_key"]
    assert key.startswith("fcbk1-")
    assert admin.call("GET", "/api/v1/admin/backups")[1]["recovery_key"] is None  # shown once
    # the instance holds no private key
    assert key.removeprefix("fcbk1-") not in stack.sql(
        "select string_agg(value::text, ' ') from app_setting"
    )

    code, _, _ = admin.call("POST", "/api/v1/admin/backups/run")
    assert code in (202, 429)
    status = wait_run(admin, "full")
    assert not any(w in status["warnings"] for w in ("last_run_failed", "key_missing"))

    archives = sorted((stack.backups / "filecanopy-backups").rglob("*.tar.gz.fcb"))
    assert archives and archives[0].read_bytes()[:4] == b"FCB2"
    assert b"alpha alpha" not in archives[0].read_bytes()  # encrypted, not just compressed
    assert stack.sql("select count(*) from backup_run where status = 'ok'") != "0"
    assert stack.sql("select count(*) from backup_chain_state") != "0"

    # the shipped command line verifies and decrypts inside the container, key via environment
    inside = "/backups/filecanopy-backups/" + "/".join(archives[0].parts[-3:])
    r = stack.compose(
        "exec", "-T", "-e", f"FILECANOPY_RECOVERY_KEY={key}", "api",
        "python", "-m", "app.backup.cli", "verify", inside,
    )  # fmt: skip
    assert r.stdout.startswith("OK")

    # restore into a new folder of the shared tree
    code, body, _ = admin.call(
        "POST", f"/api/v1/nodes/{shared}/backups/restore", {"recovery_key": key}
    )
    assert code == 200, body
    restored = stack.library / "shared" / body["folder_name"]
    assert (restored / "a.txt").read_bytes() == b"alpha " * 50
    assert (stack.library / "shared" / "a.txt").read_bytes() == b"alpha " * 50  # untouched
    wrong = admin.call(
        "POST",
        f"/api/v1/nodes/{shared}/backups/restore",
        {"recovery_key": "fcbk1-" + "A" * 43},
    )
    assert wrong[0] == 422 and wrong[1]["detail"]["code"] == "BACKUP_KEY_MISMATCH"

    # a member cannot see or restore the shared tree's backups, nor change settings
    member = Client(stack)
    token = admin.call(
        "POST", "/api/v1/admin/users", {"email": "mem@example.com", "name": "M", "role": "member"}
    )[1]["token"]["token"]
    member.call(
        "POST",
        "/api/v1/auth/invitations/accept",
        {"token": token, "password": "correct horse battery staple"},
    )
    assert member.login("mem@example.com") == 200
    assert member.call("GET", "/api/v1/admin/backups")[0] == 403
    assert js(member, "POST", f"/api/v1/nodes/{shared}/backups/restore", {})[0] == 403

    # ADR-016: the member's own folder has its own key; the administrator cannot use or see it
    mem_root = stack.sql(
        "select n.id from node n join storage_root r on r.id = n.root_id "
        "join app_user u on u.id = r.owner_user_id "
        "where n.parent_id is null and u.email = 'mem@example.com'"
    )
    secret = {"password": "correct horse battery staple"}
    assert admin.call("POST", f"/api/v1/nodes/{mem_root}/backup-key", secret)[0] == 403
    code, made, _ = member.call(
        "POST", f"/api/v1/nodes/{mem_root}/backup-key", {**secret, "expected_fingerprint": None}
    )
    assert code == 200 and made["state"] == "pending", made
    owner_key = made["recovery_key"]
    assert owner_key.startswith("fcbk1-") and owner_key != key
    code, done, _ = member.call(
        "POST",
        f"/api/v1/nodes/{mem_root}/backup-key/confirm",
        {**secret, "recovery_key": owner_key, "escrow_consent": False},
    )
    assert code == 200 and done["state"] == "active" and done["escrow"] is False, done
    assert member.call("GET", f"/api/v1/nodes/{mem_root}/backup-key")[1]["recovery_key"] is None
    stored = stack.sql(
        "select coalesce(string_agg(value::text, ' '), '') from app_setting"
    ) + stack.sql(
        "select coalesce(string_agg(public || fingerprint || coalesce(escrow_blob::text, ''), ' '),"
        " '') from backup_root_key"
    )
    assert owner_key.removeprefix("fcbk1-") not in stored  # only the public half is kept
    assert admin.call("GET", f"/api/v1/nodes/{mem_root}/backups")[0] == 403

    # the API restarts with the configuration and chain state intact
    stack.compose("restart", "api")
    stack.wait_health("ok")
    admin2 = Client(stack)
    assert admin2.login("admin@example.com") == 200
    again = admin2.call("GET", "/api/v1/admin/backups")[1]
    assert again["settings"]["enabled"] is True and again["key"]["present"] is True


def test_nested_backup_path_is_refused_at_startup(stack: BackupStack) -> None:
    for bad in ("/library", "/library/inside", "/app-data/x", "/"):
        r = stack.compose(
            "run", "--rm", "--no-deps", "-e", f"FILECANOPY_BACKUP_PATH={bad}", "api",
            "python", "-c", "from app.config import load_settings; load_settings()",
            check=False,
        )  # fmt: skip
        assert r.returncode != 0 and "backup_path" in (r.stderr + r.stdout), bad


def test_stack_without_the_override_has_no_backup_mount(tmp_path: Path) -> None:
    s = Stack(tmp_path, "fcbknone")
    out = json.loads(s.compose("config", "--format", "json").stdout)
    api = out["services"]["api"]
    assert "FILECANOPY_BACKUP_PATH" not in api["environment"]
    assert all(v["target"] != "/backups" for v in api["volumes"])
