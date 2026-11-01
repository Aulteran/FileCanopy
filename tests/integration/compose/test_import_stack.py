"""M06b against the real stack: migration 0006, setup import and folder upload through nginx on
PostgreSQL, a folder manifest far above nginx's JSON body limit, parallel entry uploads into new
folders, background zip extraction, zip-slip and zip-bomb archives, resume after an API restart,
the setup window closing when the reconciler finds content, and authorization.

Run with the rest of the compose suite (`npm run test:compose`). Random credentials and generated
bytes only.
"""

import io
import json
import os
import struct
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from test_auth_stack import Client
from test_files_stack import js, raw
from test_stack import Stack
from test_storage_stack import invite_and_login, root_node, setup_admin, user_id

pytestmark = pytest.mark.compose


@pytest.fixture
def stack(app_stack: Stack) -> Stack:
    return app_stack


class Member:
    def __init__(self, stack: Stack, admin: Client, name: str) -> None:
        tag = uuid.uuid4().hex[:6]
        self.mail = f"{name}{tag}@example.com"
        self.client = invite_and_login(stack, admin, self.mail)
        self.stack = stack
        self.uid = user_id(stack, self.mail)
        self.root = root_node(stack, f"personal/{self.uid}")
        self.disk = stack.library / "personal" / self.uid

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        return js(self.client, method, f"/api/v1{path}", body)

    def put(self, path: str, data: bytes) -> tuple[int, Any]:
        status, out, _ = raw(
            self.client,
            "PUT",
            f"/api/v1{path}",
            data,
            **{"Content-Type": "application/octet-stream"},
        )
        return status, json.loads(out or b"{}")


def manifest(files: dict[str, bytes], mtime: int = 1_700_000_000_000) -> list[dict[str, Any]]:
    return [{"path": p, "kind": "file", "size": len(b), "mtime": mtime} for p, b in files.items()]


def pending(m: Member, batch: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    after = -1
    while True:
        status, page = m.call(
            "GET", f"/imports/{batch}/entries?status=pending&after={after}&limit=500"
        )
        assert status == 200, page
        out += [e for e in page["items"] if e["kind"] == "file"]
        if page["next_after"] is None:
            return out
        after = page["next_after"]


def wait_status(m: Member, batch: str, wanted: tuple[str, ...], timeout: float = 120) -> Any:
    deadline = time.monotonic() + timeout
    got: Any = {}
    while time.monotonic() < deadline:
        status, got = m.call("GET", f"/imports/{batch}")
        assert status == 200, got
        if got["status"] in wanted:
            return got
        time.sleep(0.5)
    raise AssertionError(f"batch never reached {wanted}: {got}")


def test_migration_0006_on_postgres(stack: Stack) -> None:
    assert stack.sql("select version_num from alembic_version") == "0014"
    assert (
        stack.sql(
            "select count(*) from information_schema.tables "
            "where table_name in ('import_batch','import_entry')"
        )
        == "2"
    )
    assert (
        stack.sql(
            "select column_default from information_schema.columns "
            "where table_name = 'storage_root' and column_name = 'setup_state'"
        )
        == "'open'::character varying"
    )
    # the check constraints reject unknown states
    result = stack.compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "filecanopy",
        "-d",
        "filecanopy",
        "-c",
        "update storage_root set setup_state = 'ajar'",
        check=False,
    )
    assert result.returncode != 0 and "ck_storage_root_setup_state" in result.stderr


def test_large_folder_manifest_and_parallel_entries_through_nginx(stack: Stack) -> None:
    admin = setup_admin(stack)
    m = Member(stack, admin, "dana")
    deep = "/".join(["x" * 100, "y" * 100, "z" * 100])
    files = {f"{deep}/file-{i:05d}.txt": f"entry {i}".encode() * (1 + i % 7) for i in range(3600)}
    files["Top Level/readme.txt"] = b"hello"
    entries = manifest(files)
    body = {"entries": entries}
    assert len(json.dumps(body)) > 1_100_000  # far above nginx's 1m limit for ordinary JSON

    status, batch = m.call("POST", f"/nodes/{m.root}/setup/import", body)
    assert status == 201, batch
    assert batch["plan"]["files"] == 3601 and batch["plan"]["fits"] is True
    assert not m.disk.exists() or list(m.disk.iterdir()) == []  # nothing written yet
    assert m.call("POST", f"/imports/{batch['id']}/start")[0] == 200

    work = pending(m, batch["id"])
    assert len(work) == 3601

    def send(entry: dict[str, Any]) -> int:
        return m.put(f"/imports/{batch['id']}/entries/{entry['index']}", files[entry["path"]])[0]

    with ThreadPoolExecutor(max_workers=6) as pool:
        codes = list(pool.map(send, work))
    assert set(codes) == {200}
    done = wait_status(m, batch["id"], ("done",))
    assert done["counts"]["done"] == 3601 and done["counts"]["failed"] == 0
    sample = m.disk / deep / "file-01234.txt"
    assert sample.read_bytes() == files[f"{deep}/file-01234.txt"]
    assert len(list((m.disk / deep).iterdir())) == 3600
    # six parallel uploads into three new folders still made exactly one of each
    assert (
        stack.sql(
            f"select count(*) from node n join node p on p.id = n.parent_id "
            f"where p.id = '{m.root}' and n.kind = 'dir' and n.name = '{'x' * 100}'"
        )
        == "1"
    )
    assert (
        stack.sql(
            "select count(*) from import_entry "
            f"where batch_id = '{batch['id']}' and status <> 'done'"
        )
        == "0"
    )
    assert stack.sql(f"select setup_state from storage_root where owner_user_id = '{m.uid}'") == (
        "open"
    )  # the import itself did not close the window
    status, closed = m.call("POST", f"/nodes/{m.root}/setup/finish", {"action": "finish"})
    assert status == 200 and closed["state"] == "closed"
    assert m.call("POST", f"/nodes/{m.root}/setup/import", body)[0] == 409
    assert stack.sql("select count(*) from file_event where action = 'imported'") != "0"
    assert list(stack.library.rglob(".filecanopy-tmp-*")) == []


def test_zip_import_slip_bomb_and_authorization_on_postgres(stack: Stack) -> None:
    admin = setup_admin(stack)
    m = Member(stack, admin, "erin")
    other = Member(stack, admin, "frank")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("keep/one.txt", b"one")
        zf.writestr("keep/sub/two.bin", os.urandom(200_000))
        zf.writestr("../escape-one.txt", b"evil")
        zf.writestr("a/../../escape-two.txt", b"evil")
        zf.writestr("/abs-three.txt", b"evil")
        zf.writestr(zipfile.ZipInfo("bomb.bin"), b"\0" * (16 * 1024 * 1024), zipfile.ZIP_BZIP2)
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        zf.writestr(link, "/etc/passwd")
    status, out, _ = raw(
        m.client,
        "POST",
        f"/api/v1/nodes/{m.root}/setup/import-zip",
        buf.getvalue(),
        **{"Content-Type": "application/zip"},
    )
    assert status == 201, out
    batch = json.loads(out)
    assert batch["plan"]["skipped"] == {"INVALID_PATH": 3, "SYMLINK": 1}
    assert not m.disk.exists() or list(m.disk.iterdir()) == []  # only listed so far
    staged = list(stack.app_data.rglob("*.zip"))
    assert len(staged) == 1 and not list(stack.library.rglob("*.zip"))

    # someone else cannot see, start or cancel it; neither can the administrator
    for who in (other.client, admin):
        assert js(who, "GET", f"/api/v1/imports/{batch['id']}")[0] == 403
        assert js(who, "POST", f"/api/v1/imports/{batch['id']}/start")[0] == 403
        assert js(who, "POST", f"/api/v1/imports/{batch['id']}/cancel")[0] == 403
        assert js(who, "GET", f"/api/v1/nodes/{m.root}/setup")[0] == 403

    assert m.call("POST", f"/imports/{batch['id']}/start")[0] == 200
    done = wait_status(m, batch["id"], ("done",))
    assert done["counts"]["done"] == 2 and done["counts"]["failed"] == 1  # the bomb
    assert (m.disk / "keep" / "one.txt").read_bytes() == b"one"
    assert not (m.disk / "bomb.bin").exists()
    names = sorted(p.name for p in stack.library.rglob("*") if p.is_file())
    assert not any(n.startswith("escape") or n.startswith("abs-") for n in names)
    assert (
        stack.sql(
            "select reason from import_entry "
            f"where batch_id = '{batch['id']}' and status = 'failed'"
        )
        == "DECOMPRESSION_RATIO"
    )
    assert list(stack.app_data.rglob("*.zip")) == []  # the staged archive is deleted at the end

    # the administrator may set up the shared tree, and only the administrator
    shared = root_node(stack, "shared")
    member_zip = io.BytesIO()
    with zipfile.ZipFile(member_zip, "w") as zf:
        zf.writestr("x.txt", b"x")
    denied, _, _ = raw(
        m.client,
        "POST",
        f"/api/v1/nodes/{shared}/setup/import-zip",
        member_zip.getvalue(),
        **{"Content-Type": "application/zip"},
    )
    assert denied == 403
    assert js(m.client, "GET", f"/api/v1/nodes/{shared}/setup")[0] == 403
    assert js(admin, "GET", f"/api/v1/nodes/{shared}/setup")[0] == 200


def test_zip_import_resumes_after_the_api_restarts(stack: Stack) -> None:
    admin = setup_admin(stack)
    m = Member(stack, admin, "gina")
    items = {f"batch/f{i:04d}.bin": os.urandom(30_000) for i in range(500)}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in items.items():
            zf.writestr(name, data)
    status, out, _ = raw(
        m.client,
        "POST",
        f"/api/v1/nodes/{m.root}/setup/import-zip",
        buf.getvalue(),
        **{"Content-Type": "application/zip"},
    )
    assert status == 201, out
    batch = json.loads(out)
    assert m.call("POST", f"/imports/{batch['id']}/start")[0] == 200
    time.sleep(1.0)
    stack.compose("restart", "api")
    stack.wait_health("ok", timeout=120)
    # the page shows the unfinished import; if the restart caught it mid-way it is `stopped`
    status, shown = m.call("GET", f"/nodes/{m.root}/setup")
    assert status == 200
    current = shown["batch"]
    if current is not None:
        assert current["status"] in ("stopped", "running", "done")
        if current["status"] == "stopped":
            assert current["error_code"] == "INTERRUPTED"
            assert m.call("POST", f"/imports/{batch['id']}/start")[0] == 200
    done = wait_status(m, batch["id"], ("done",))
    assert done["counts"]["done"] == 500 and done["counts"]["failed"] == 0
    present = sorted(p.name for p in (m.disk / "batch").iterdir())
    assert present == sorted(Path(n).name for n in items)  # every file exactly once
    assert (m.disk / "batch" / "f0123.bin").read_bytes() == items["batch/f0123.bin"]
    assert list(stack.library.rglob(".filecanopy-tmp-*")) == []


def test_folder_import_resumes_by_reselecting_the_folder(stack: Stack) -> None:
    admin = setup_admin(stack)
    m = Member(stack, admin, "hugo")
    files = {f"Album/p{i:03d}.bin": os.urandom(1000 + i) for i in range(40)}
    status, batch = m.call("POST", f"/nodes/{m.root}/setup/import", {"entries": manifest(files)})
    assert status == 201, batch
    m.call("POST", f"/imports/{batch['id']}/start")
    todo = pending(m, batch["id"])
    for entry in todo[:15]:
        assert (
            m.put(f"/imports/{batch['id']}/entries/{entry['index']}", files[entry["path"]])[0]
            == 200
        )
    stack.compose("restart", "api")
    stack.wait_health("ok", timeout=120)
    status, resume = m.call("POST", f"/imports/{batch['id']}/resume", {"entries": manifest(files)})
    assert status == 200, resume
    assert len(resume["pending"]) == 25 and resume["unmatched"] == 0
    for entry in resume["pending"]:
        assert (
            m.put(f"/imports/{batch['id']}/entries/{entry['index']}", files[entry["path"]])[0]
            == 200
        )
    assert wait_status(m, batch["id"], ("done",))["counts"]["done"] == 40
    assert sorted(p.name for p in (m.disk / "Album").iterdir()) == sorted(
        Path(n).name for n in files
    )
    assert (
        stack.sql(
            "select count(*) from node where parent_id = (select id from node "
            f"where name = 'Album' and root_id = (select id from storage_root "
            f"where owner_user_id = '{m.uid}'))"
        )
        == "40"
    )


def test_content_found_by_a_scan_closes_the_setup_window(stack: Stack) -> None:
    admin = setup_admin(stack)
    m = Member(stack, admin, "iris")
    assert m.call("GET", f"/nodes/{m.root}/setup")[1]["state"] == "open"
    m.disk.mkdir(parents=True, exist_ok=True)
    (m.disk / "from-the-host.txt").write_bytes(b"copied in on the host")
    assert m.call("POST", f"/nodes/{m.root}/rescan")[0] == 202
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if m.call("GET", f"/nodes/{m.root}/setup")[1]["state"] == "closed":
            break
        time.sleep(1)
    assert m.call("GET", f"/nodes/{m.root}/setup")[1]["state"] == "closed"
    assert (
        stack.sql(
            "select details->>'reason' from auth_audit where action = 'storage.setup_closed' "
            "and details->>'root_id' = (select id::text from storage_root "
            f"where owner_user_id = '{m.uid}')"
        )
        == "content"
    )
    status, out = m.call(
        "POST", f"/nodes/{m.root}/setup/import", {"entries": manifest({"a.txt": b"a"})}
    )
    assert status == 409 and out["detail"]["code"] == "SETUP_CLOSED"


def test_folder_upload_merges_and_keeps_both_on_postgres(stack: Stack) -> None:
    admin = setup_admin(stack)
    m = Member(stack, admin, "june")
    assert m.call("POST", f"/nodes/{m.root}/folders", {"name": "Docs"})[0] == 201
    status, first = m.call(
        "POST",
        f"/nodes/{m.root}/folder-uploads",
        {"entries": manifest({"Docs/a.txt": b"one", "Docs/sub/b.txt": b"b"})},
    )
    assert status == 201, first
    for entry in pending(m, first["id"]):
        data = {"Docs/a.txt": b"one", "Docs/sub/b.txt": b"b"}[entry["path"]]
        assert m.put(f"/imports/{first['id']}/entries/{entry['index']}", data)[0] == 200
    second = m.call(
        "POST",
        f"/nodes/{m.root}/folder-uploads",
        {"entries": manifest({"Docs/a.txt": b"two"})},
    )[1]
    for entry in pending(m, second["id"]):
        assert m.put(f"/imports/{second['id']}/entries/{entry['index']}", b"two")[0] == 200
    assert (m.disk / "Docs" / "a.txt").read_bytes() == b"one"
    assert (m.disk / "Docs" / "a (2).txt").read_bytes() == b"two"
    assert sorted(p.name for p in m.disk.iterdir()) == ["Docs"]


def lying_zip(records: int) -> bytes:
    """One stored empty file and `records` central-directory records, under an end record that
    declares one entry (the M06b review's zip bomb)."""
    local = struct.pack("<4sHHHHHIIIHH", b"PK\x03\x04", 20, 0, 0, 0, 0x21, 0, 0, 0, 1, 0) + b"a"
    record = struct.pack(
        "<4sHHHHHHIIIHHHHHII", b"PK\x01\x02", 20, 20, 0, 0, 0, 0x21, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0
    )
    directory = (record + b"a") * records
    end = struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, 1, 1, len(directory), len(local), 0)
    return local + directory + end


def test_review_fixes_through_nginx_on_postgres(stack: Stack) -> None:
    admin = setup_admin(stack)
    m = Member(stack, admin, "ewa")
    other = Member(stack, admin, "finn")

    # a zip that declares one entry but holds far more than the limit is refused, not loaded
    status, out, _ = raw(
        m.client,
        "POST",
        f"/api/v1/nodes/{m.root}/setup/import-zip",
        lying_zip(110_000),
        **{"Content-Type": "application/zip"},
    )
    assert status == 413 and json.loads(out)["detail"]["code"] == "TOO_MANY_ENTRIES"
    assert m.call("GET", f"/nodes/{m.root}/setup")[1]["batch"] is None

    # a manifest is not read before the caller is signed in; above nginx's 32m it never arrives
    anon = Client(stack)
    big = b'{"entries":[' + b'{"path":"a","kind":"dir"},' * 600_000 + b'{"path":"a","kind":"dir"}]}'
    assert 15_000_000 < len(big) < 32 * 1024 * 1024
    status, _, _ = raw(
        anon,
        "POST",
        f"/api/v1/nodes/{m.root}/setup/import",
        big,
        **{"Content-Type": "application/json"},
    )
    assert status == 401
    huge = b" " * (33 * 1024 * 1024)
    try:
        status, _, _ = raw(
            m.client,
            "POST",
            f"/api/v1/nodes/{m.root}/setup/import",
            huge,
            **{"Content-Type": "application/json"},
        )
    except OSError:  # nginx answers 413 and closes while the client is still sending
        status = 413
    assert status == 413

    # one unfinished setup import per root, enforced by the database as well
    status, batch = m.call(
        "POST", f"/nodes/{m.root}/setup/import", {"entries": manifest({"a": b"a"})}
    )
    assert status == 201, batch
    assert (
        stack.sql("select count(*) from pg_indexes where indexname = 'uq_import_batch_open_setup'")
        == "1"
    )
    twin = stack.compose(
        "exec", "-T", "postgres", "psql", "-U", "filecanopy", "-d", "filecanopy", "-c",
        "insert into import_batch (id, root_id, target_node_id, actor_user_id, kind, status, plan, "
        "created_at, updated_at) select gen_random_uuid(), root_id, target_node_id, "
        f"actor_user_id, 'setup_zip', 'planned', '{{}}', now(), now() from import_batch "
        f"where id = '{batch['id']}'",
        check=False,
    )  # fmt: skip
    assert twin.returncode != 0 and "uq_import_batch_open_setup" in twin.stderr
    assert m.call("POST", f"/imports/{batch['id']}/cancel")[0] == 200

    # a folder upload creates content, so it closes the setup window
    assert other.call("GET", f"/nodes/{other.root}/setup")[1]["state"] == "open"
    files = {"Docs/a.txt": b"alpha"}
    status, up = other.call(
        "POST", f"/nodes/{other.root}/folder-uploads", {"entries": manifest(files)}
    )
    assert status == 201, up
    assert other.put(f"/imports/{up['id']}/entries/1", b"alpha")[0] == 200
    assert other.call("GET", f"/nodes/{other.root}/setup")[1]["state"] == "closed"
    assert other.call("GET", "/imports")[1] == []  # nothing unfinished
    status, unfinished = other.call(
        "POST", f"/nodes/{other.root}/folder-uploads", {"entries": manifest({"b.txt": b"b"})}
    )
    assert status == 201
    listed = other.call("GET", "/imports")[1]
    assert [b["id"] for b in listed] == [unfinished["id"]]
    assert m.call("GET", "/imports")[1] == []  # another person's batches are never listed
    assert Client(stack).call("GET", "/api/v1/imports")[0] == 401

    # retention: the per-file report goes 90 days after completion, the batch row stays
    stack.sql(
        f"update import_batch set finished_at = now() - interval '91 days' where id = '{up['id']}'"
    )
    stack.compose("restart", "api")  # the startup pass prunes with the periodic work's function
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        left = stack.sql(f"select count(*) from import_entry where batch_id = '{up['id']}'")
        if left == "0":
            break
        time.sleep(1)
    assert stack.sql(f"select count(*) from import_entry where batch_id = '{up['id']}'") == "0"
    assert stack.sql(f"select count(*) from import_batch where id = '{up['id']}'") == "1"
    assert (
        stack.sql(f"select entries_pruned_at is not null from import_batch where id = '{up['id']}'")
        == "t"
    )
