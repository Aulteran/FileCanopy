"""M22 against the real stack: the published-image deploy (release override), the backup and
restore scripts (a full restore drill into a destroyed install), an upgrade from an older schema
with real data, and the non-root / read-only posture of every container.

Run with the rest of the compose suite (`npm run test:compose`). Random credentials and generated
bytes only. The "published" images are the locally built ones, tagged with the names and version a
release would carry, so the override is exercised exactly as users run it without pushing anything.
"""

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_auth_stack import PASSWORD, Client
from test_files_stack import js, raw
from test_stack import COMPOSE_FILE, REPO, Stack
from test_storage_stack import invite_and_login, root_node, setup_admin, user_id

pytestmark = pytest.mark.compose

RELEASE_FILE = COMPOSE_FILE.with_name("docker-compose.release.yml")
SCRIPTS = REPO / "deploy" / "scripts"
PREFIX = "filecanopy-m22test"
VERSION = "citest"
# Local image built by the normal compose file -> the name the release override expects.
LOCAL_TO_RELEASE = {
    "filecanopy-postgres:dev": f"{PREFIX}-postgres:{VERSION}",
    "filecanopy-api:dev": f"{PREFIX}-api:{VERSION}",
    "filecanopy-web:dev": f"{PREFIX}-web:{VERSION}",
}


def _image_present(name: str) -> bool:
    r = subprocess.run(  # noqa: S603
        ["docker", "image", "inspect", name],  # noqa: S607
        capture_output=True,
        check=False,
    )
    return r.returncode == 0


class ReleaseStack(Stack):
    """The standard stack plus the release override: images by name and version, no build."""

    def __init__(self, tmp: Path, name: str) -> None:
        super().__init__(tmp, name)
        self.tmp = tmp
        self.files = [COMPOSE_FILE, RELEASE_FILE]
        self.env["FILECANOPY_IMAGE_PREFIX"] = PREFIX
        self.env["FILECANOPY_VERSION"] = VERSION
        self.env["COMPOSE_PROJECT_NAME"] = name
        sep = ";" if os.name == "nt" else ":"
        self.env["COMPOSE_PATH_SEPARATOR"] = sep
        self.env["COMPOSE_FILE"] = sep.join(str(f) for f in self.files)

    def tag_local_images(self) -> None:
        if not all(_image_present(local) for local in LOCAL_TO_RELEASE):
            base = Stack(self.tmp / "build", "fctestbuild")
            subprocess.run(  # noqa: S603
                ["docker", "compose", "-f", str(COMPOSE_FILE), "build"],  # noqa: S607
                env=base.env,
                check=True,
                capture_output=True,
                timeout=1800,
            )
        for local, release in LOCAL_TO_RELEASE.items():
            subprocess.run(["docker", "tag", local, release], check=True)  # noqa: S603, S607

    def script(self, name: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        for key in ("FILECANOPY_LIBRARY_PATH", "FILECANOPY_APP_DATA_PATH"):
            env[key] = Path(env[key]).as_posix()  # the scripts are POSIX sh, also under Git Bash
        # No COMPOSE_FILE: the scripts must pick the release override from FILECANOPY_VERSION.
        env.pop("COMPOSE_FILE")
        r = subprocess.run(  # noqa: S603
            ["sh", str(SCRIPTS / name), *args],  # noqa: S607
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        if check and r.returncode != 0:
            raise AssertionError(f"{name} failed:\n{r.stdout}\n{r.stderr}")
        return r


@pytest.fixture(scope="module")
def release(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ReleaseStack]:
    s = ReleaseStack(tmp_path_factory.mktemp("release"), f"fcrel{secrets.token_hex(3)}")
    s.tag_local_images()
    s.up()
    yield s
    s.down(volumes=True)


def tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ------------------------------------------------------------------- published images only


def test_release_override_has_no_build_and_requires_a_version(release: ReleaseStack) -> None:
    config = release.compose("--profile", "*", "config").stdout
    assert "build:" not in config, "a release deploy must never build on the user's machine"
    images = release.compose("--profile", "*", "config", "--images").stdout.split()
    ours = [i for i in images if i.startswith(PREFIX)]
    assert ours and all(i.endswith(f":{VERSION}") for i in ours)
    assert not [i for i in images if i.endswith((":latest", ":dev"))], images
    env = {k: v for k, v in release.env.items() if k != "FILECANOPY_VERSION"}
    r = release.compose("config", check=False, env=env)
    assert r.returncode != 0 and "FILECANOPY_VERSION" in r.stderr


def test_fresh_release_deploy_runs_without_ai(release: ReleaseStack) -> None:
    body = release.wait_health("ok")
    assert all(c["state"] == "ok" for c in body["core"].values())  # type: ignore[attr-defined]
    running = set(release.compose("ps", "--services", "--status", "running").stdout.split())
    assert {"postgres", "api", "web"} <= running
    for container in release.compose("ps", "-q").stdout.split():
        image = subprocess.run(  # noqa: S603
            ["docker", "inspect", "--format", "{{.Config.Image}}", container],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert image.startswith(PREFIX), image
    admin = setup_admin(release)  # first-run setup works on a fresh, AI-less install
    assert admin.call("GET", "/api/v1/auth/session")[0] == 200


# ------------------------------------------------------------------- non-root permissions


def pid1_uid(stack: Stack, service: str) -> int:
    out = stack.compose("exec", "-T", service, "sh", "-c", "awk '/^Uid:/{print $2}' /proc/1/status")
    return int(out.stdout.strip())


def test_every_container_runs_unprivileged_and_sealed(release: ReleaseStack) -> None:
    expected = int(release.env.get("FILECANOPY_UID", "10001"))
    assert pid1_uid(release, "api") == expected
    assert pid1_uid(release, "web") != 0
    assert pid1_uid(release, "postgres") != 0
    for service in ("api", "web"):
        cid = release.compose("ps", "-q", service).stdout.strip()
        info = json.loads(
            subprocess.run(  # noqa: S603
                ["docker", "inspect", cid],  # noqa: S607
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )[0]["HostConfig"]
        assert info["ReadonlyRootfs"] is True, service
        assert "ALL" in (info["CapDrop"] or []), service
        assert "no-new-privileges:true" in (info["SecurityOpt"] or []), service
        assert not info["Privileged"], service
    # The API may write its two mounts and nothing else.
    probe = "touch /library/.w /app-data/.w && rm /library/.w /app-data/.w"
    assert release.compose("exec", "-T", "api", "sh", "-c", probe).returncode == 0
    for path in ("/srv/filecanopy/x", "/etc/x", "/usr/x"):
        r = release.compose("exec", "-T", "api", "sh", "-c", f"touch {path}", check=False)
        assert r.returncode != 0, path


def test_files_made_by_the_app_belong_to_the_configured_user(release: ReleaseStack) -> None:
    if not hasattr(os, "getuid"):
        pytest.skip("host ownership is only meaningful on Linux")
    admin = setup_admin(release)
    status, body = js(admin, "GET", "/api/v1/auth/session")
    assert status == 200, body
    made = [p for p in release.library.rglob("*")] + [p for p in release.app_data.rglob("*")]
    assert made, "setup creates the shared and personal root folders"
    assert {p.stat().st_uid for p in made} == {os.getuid()}


# ------------------------------------------------------------------- backup and restore drill


def seed(release: ReleaseStack) -> dict[str, object]:
    """Real accounts, a file made through the API and one placed beside it from the outside."""
    admin = setup_admin(release)
    tag = uuid.uuid4().hex[:6]
    email = f"alice{tag}@example.com"
    alice = invite_and_login(release, admin, email)
    uid = user_id(release, email)
    root = root_node(release, f"personal/{uid}")
    status, docs = js(alice, "POST", f"/api/v1/nodes/{root}/folders", {"name": "Docs"})
    assert status == 201, docs
    payload = secrets.token_bytes(300_000)
    status, body, _ = raw(
        alice,
        "POST",
        f"/api/v1/nodes/{docs['node']['id']}/files?name=report.bin",
        payload,
        **{"Content-Type": "application/octet-stream", "Idempotency-Key": f"seed-{tag}"},
    )
    assert status == 201, body
    (release.library / "outside.txt").write_text("placed by hand\n")
    return {
        "email": email,
        "uid": uid,
        "file_id": json.loads(body)["node"]["id"],
        "payload": payload,
        "instance_id": release.instance_id(),
        "schema": release.sql("select version_num from alembic_version"),
        "nodes": release.sql("select count(*) from node"),
        "users": release.sql("select count(*) from app_user"),
    }


def test_backup_and_restore_drill(release: ReleaseStack) -> None:
    before = seed(release)
    library_before, app_data_before = tree(release.library), tree(release.app_data)
    (backups := release.tmp / "backups").mkdir()

    out = release.script("backup.sh", str(backups)).stdout
    made = sorted(backups.iterdir())
    assert len(made) == 1 and made[0].name.startswith("filecanopy-"), out
    backup = made[0]
    assert {p.name for p in backup.iterdir()} == {
        "db.dump",
        "app-data.tar.gz",
        "library.tar.gz",
        "MANIFEST",
        "SHA256SUMS",
    }
    manifest = dict(line.split("=", 1) for line in (backup / "MANIFEST").read_text().split())
    assert manifest["schema"] == before["schema"] and manifest["consistent"] == "yes"
    if hasattr(os, "getuid"):
        assert (backup.stat().st_mode & 0o077) == 0, "the backup folder must be private"
    # The stack was stopped for the copy and is back, serving, afterwards.
    release.wait_health("ok", timeout=90)

    # Refusals leave the damaged or occupied target alone.
    assert "not empty" in release.script("restore.sh", str(backup), check=False).stderr
    tampered = release.tmp / "tampered"
    shutil.copytree(backup, tampered)
    with (tampered / "db.dump").open("ab") as f:
        f.write(b"x")
    assert "checksum" in release.script("restore.sh", str(tampered), check=False).stderr

    # The disaster: containers, database volume, app-data and library are all gone.
    release.down(volumes=True)
    shutil.rmtree(release.library)
    shutil.rmtree(release.app_data)
    release.library.mkdir()
    release.app_data.mkdir()

    release.script("restore.sh", str(backup))
    release.wait_health("ok", timeout=120)
    assert release.instance_id() == before["instance_id"]
    assert release.sql("select version_num from alembic_version") == before["schema"]
    assert release.sql("select count(*) from node") == before["nodes"]
    assert release.sql("select count(*) from app_user") == before["users"]
    assert tree(release.library) == library_before
    after = tree(release.app_data)  # the app may add files after the restart, never lose any
    assert {k: v for k, v in after.items() if k in app_data_before} == app_data_before
    # Accounts and files work again: same password, same bytes through the API.
    alice = Client(release)
    assert alice.login(str(before["email"]), PASSWORD) == 200
    status, got, _ = raw(alice, "GET", f"/api/v1/nodes/{before['file_id']}/content")
    assert status == 200 and got == before["payload"]
    assert (release.library / "outside.txt").read_text() == "placed by hand\n"
    if hasattr(os, "getuid"):
        assert {p.stat().st_uid for p in release.library.rglob("*")} == {os.getuid()}


# ------------------------------------------------------------------- upgrade from an older schema

DOWNGRADE = """
import sys
from alembic import command
from app.config import load_settings
from app.db.engine import make_engine
from app.db.migrate import alembic_config
with make_engine(load_settings()).begin() as conn:
    cfg = alembic_config()
    cfg.attributes["connection"] = conn
    command.downgrade(cfg, sys.argv[1])
"""


def test_upgrade_from_an_older_schema_keeps_files_and_metadata(release: ReleaseStack) -> None:
    before = seed(release)
    head = release.compose(
        "run", "--rm", "--no-deps", "migrate", "python", "-c",
        "from app.db.migrate import head_revision; print(head_revision())",
    ).stdout.strip()  # fmt: skip
    assert head == before["schema"]
    library_before = tree(release.library)

    # Put the database back to the schema of the release before collections (0020), as it was
    # when this data was written, then start the current release over it.
    release.compose("stop", "web", "api")
    release.compose("run", "--rm", "--no-deps", "migrate", "python", "-c", DOWNGRADE, "0020")
    assert release.sql("select version_num from alembic_version") == "0020"
    assert release.sql("select to_regclass('public.collection') is null") == "t"

    release.compose("up", "-d", "--wait")  # runs the migrate service, then api and web
    release.wait_health("ok", timeout=120)
    assert release.sql("select version_num from alembic_version") == head
    assert release.sql("select to_regclass('public.collection') is not null") == "t"
    assert release.instance_id() == before["instance_id"]
    assert release.sql("select count(*) from node") == before["nodes"]
    assert release.sql("select count(*) from app_user") == before["users"]
    assert tree(release.library) == library_before
    alice = Client(release)
    assert alice.login(str(before["email"]), PASSWORD) == 200
    status, got, _ = raw(alice, "GET", f"/api/v1/nodes/{before['file_id']}/content")
    assert status == 200 and got == before["payload"]
