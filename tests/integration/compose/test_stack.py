"""Real Docker Compose tests (host-side). Slow: builds images and starts a full stack.

Run with:  python -m pytest -c apps/api/pyproject.toml -m compose tests/integration/compose
Uses an isolated project name, random credentials and temp directories; no AI services,
no real documents, no network access beyond image pulls.
"""

import json
import os
import secrets
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.compose

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "deploy"
COMPOSE_FILE = DEPLOY / "compose.yaml"
BUILD_FILE = DEPLOY / "contrib" / "build.yaml"
CORE_SERVICES = {"postgres", "migrate", "api", "web"}
CORE_SERVICES = CORE_SERVICES | {"init"}
ALL_SERVICES = CORE_SERVICES | {"redis", "worker", "converter", "ocr"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Stack:
    def __init__(self, tmp: Path, name: str) -> None:
        self.library = tmp / "library"
        self.app_data = tmp / "app-data"
        self.library.mkdir()
        self.app_data.mkdir()
        self.port = free_port()
        self.name = name
        # The stack runs from its own copy of the deploy files, so the project's `.env` (read by
        # the containers through env_file) is the test's own and never the operator's deploy/.env.
        self.dir = tmp / "stack"
        self.dir.mkdir()
        shutil.copy(COMPOSE_FILE, self.dir / "compose.yaml")
        for extra in (DEPLOY / "optional").glob("compose.*.yaml"):
            shutil.copy(extra, self.dir / extra.name)
        shutil.copy(BUILD_FILE, self.dir / "build.yaml")
        # a suite may add override files (e.g. an outer proxy, optional containers)
        self.files = [
            self.dir / "compose.yaml",
            self.dir / "build.yaml",
            self.dir / "compose.extras.yaml",  # every service in it is behind a profile
        ]
        self.env = {
            **{
                k: v for k, v in os.environ.items() if not k.startswith(("FILECANOPY_", "COMPOSE_"))
            },
            "FILECANOPY_IMAGE_PREFIX": "filecanopy",
            "FILECANOPY_VERSION": "dev",
            "FILECANOPY_SRC": str(REPO),
            "FILECANOPY_LIBRARY_PATH": str(self.library),
            "FILECANOPY_APP_DATA_PATH": str(self.app_data),
            "FILECANOPY_HTTP_PORT": str(self.port),
        }
        self.env.pop("FILECANOPY_REDIS_URL", None)
        # Opt-in release acceptance: use only the downloaded bundle and exact local candidate.
        # No dev-tag replacement, source build or existing installation is touched.
        self.candidate = os.environ.get("FILECANOPY_TEST_CANDIDATE")
        if self.candidate:
            plan = json.loads(Path(self.candidate).read_text())
            if not plan["built"] or not plan["sourceClean"]:
                raise AssertionError("Candidate must be built from a clean commit")
            bundle = Path(os.environ["FILECANOPY_TEST_BUNDLE"]).resolve()
            shutil.copy(bundle / "compose.yaml", self.dir / "compose.yaml")
            for extra in bundle.glob("compose.*.yaml"):
                shutil.copy(extra, self.dir / extra.name)
            self.files = [self.dir / "compose.yaml", self.dir / "compose.extras.yaml"]
            self.env["FILECANOPY_IMAGE_PREFIX"] = plan["prefix"]
            self.env["FILECANOPY_VERSION"] = plan["version"]
            for image in plan["images"]:
                actual = subprocess.run(  # noqa: S603
                    ["docker", "image", "inspect", "--format", "{{.Id}}", image["tag"]],  # noqa: S607
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                assert actual == image["imageId"], "Candidate tag changed"
        if hasattr(os, "getuid"):
            # Linux: the API must own the bind mounts, as documented in .env.advanced.example.
            self.env["FILECANOPY_UID"] = str(os.getuid())
            self.env["FILECANOPY_GID"] = str(os.getgid())

    def compose(
        self, *args: str, check: bool = True, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        files = [arg for f in self.files for arg in ("-f", str(f))]
        # Settings for the containers travel in the project's .env, exactly as for a user.
        shown = env or self.env
        dotenv = "".join(f"{k}='{v}'\n" for k, v in shown.items() if k.startswith("FILECANOPY_"))
        (self.dir / ".env").write_text(dotenv, encoding="utf-8")
        cmd = [
            "docker",
            "compose",
            "-p",
            self.name,
            *files,
            *args,
        ]
        r = subprocess.run(  # noqa: S603
            cmd,
            env=env or self.env,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if check and r.returncode != 0:
            raise AssertionError(f"{' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")
        return r

    def up(self, *extra: str) -> None:
        try:
            # CI pre-builds the images with a layer cache and sets FILECANOPY_COMPOSE_NO_BUILD.
            build = (
                []
                if self.candidate or os.environ.get("FILECANOPY_COMPOSE_NO_BUILD")
                else ["--build"]
            )
            self.compose(*extra, "up", "-d", *build, "--wait", "--wait-timeout", "180")
        except AssertionError as exc:
            logs = self.compose("--profile", "*", "logs", "--no-color", "--tail", "30", check=False)
            raise AssertionError(f"{exc}\n--- service logs ---\n{logs.stdout}") from exc

    def get(self, path: str) -> tuple[int, dict[str, object]]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def wait_health(self, status: str, timeout: float = 60) -> dict[str, object]:
        deadline = time.time() + timeout
        body: dict[str, object] = {}
        while time.time() < deadline:
            try:
                _, body = self.get("/api/v1/health")
                if body.get("status") == status:
                    return body
            except (OSError, ValueError):
                pass
            time.sleep(1)
        raise AssertionError(f"health never became {status!r}; last: {body}")

    @property
    def password(self) -> str:
        """The database password the stack generated for itself (never chosen by a person)."""
        return self.compose("exec", "-T", "postgres", "cat", "/secrets/pg_password").stdout.strip()

    def sql(self, query: str) -> str:
        r = self.compose(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "filecanopy",
            "-d",
            "filecanopy",
            "-tAc",
            query,
        )
        return r.stdout.strip()

    def instance_id(self) -> str:
        return self.sql("select value #>> '{}' from app_setting where key='instance_id'")

    def down(self, volumes: bool = False) -> None:
        flags = ["--remove-orphans", *(["-v"] if volumes else [])]
        self.compose("--profile", "*", "down", *flags, check=False)


def library_listing(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    s = Stack(tmp_path_factory.mktemp("core"), f"fctest{secrets.token_hex(3)}")
    s.up()
    yield s
    s.down(volumes=True)


def test_compose_needs_no_configuration_and_generates_its_own_password(stack: Stack) -> None:
    assert len(stack.password) >= 32
    env = {k: v for k, v in stack.env.items() if not k.startswith("FILECANOPY_")}
    assert stack.compose("config", "--quiet", check=False, env=env).returncode == 0
    # init keeps the password across restarts (it must never be regenerated under the database).
    before = stack.password
    stack.compose("run", "--rm", "init")
    assert stack.password == before


def test_no_ai_services_defined(stack: Stack) -> None:
    services = set(stack.compose("--profile", "*", "config", "--services").stdout.split())
    assert services == ALL_SERVICES
    assert not [s for s in services if any(w in s for w in ("ai", "llama", "ollama", "embed"))]
    image_lines = stack.compose("--profile", "*", "config", "--images").stdout.lower()
    assert not any(w in image_lines for w in ("llama", "ollama", "openai", "anthropic"))


def test_fresh_install_core_starts_without_workers_or_ai(stack: Stack) -> None:
    running = set(stack.compose("ps", "--services", "--status", "running").stdout.split())
    assert {"postgres", "api", "web"} <= running
    assert not running & {"redis", "worker"}
    status, body = stack.get("/api/v1/health")
    assert status == 200
    assert body["status"] == "ok"
    assert all(c["state"] == "ok" for c in body["core"].values())  # type: ignore[attr-defined]
    assert body["optional"] == {  # type: ignore[comparison-overlap]
        "redis": {"state": "not_configured"},
        "worker": {"state": "not_configured"},
    }
    with urllib.request.urlopen(f"http://127.0.0.1:{stack.port}/healthz", timeout=5) as r:
        assert r.status == 200
    with urllib.request.urlopen(f"http://127.0.0.1:{stack.port}/", timeout=5) as r:
        assert b"FileCanopy" in r.read()


def test_migration_idempotent_and_serialized(stack: Stack) -> None:
    before = stack.instance_id()
    assert len(before) == 36
    stack.compose("run", "--rm", "migrate")
    stack.compose("run", "--rm", "migrate")
    # Concurrent migrators must serialize on the advisory lock and leave one consistent state.
    procs = [
        subprocess.Popen(  # noqa: S603
            [
                "docker",
                "compose",
                "-p",
                stack.name,
                "-f",
                str(stack.files[0]),
                "-f",
                str(stack.files[1]),
                "run",
                "--rm",
                "migrate",
            ],
            env=stack.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(3)
    ]
    assert [p.wait(timeout=180) for p in procs] == [0, 0, 0]
    assert stack.instance_id() == before
    assert stack.sql("select count(*) from alembic_version") == "1"
    assert stack.sql("select version_num from alembic_version") == "0027"


def test_security_posture(stack: Stack) -> None:
    ps = json.loads(
        "[" + ",".join(stack.compose("ps", "--format", "json").stdout.split("\n")[:-1]) + "]"
    )
    publishers = {p["Service"]: [x for x in p["Publishers"] if x.get("PublishedPort")] for p in ps}
    assert not publishers["postgres"], "postgres must not publish a host port"
    assert not publishers["api"], "api is reachable only through web"
    assert publishers["web"] and all(p["URL"] == "127.0.0.1" for p in publishers["web"])
    assert stack.compose("exec", "-T", "api", "id", "-u").stdout.strip() != "0"
    probe = stack.compose("exec", "-T", "api", "sh", "-c", "touch /srv/filecanopy/x", check=False)
    assert probe.returncode != 0, "api root filesystem must be read-only"
    logs = stack.compose("logs", "--no-color").stdout
    status, body = stack.get("/api/v1/health")
    assert stack.password not in logs
    assert stack.password not in json.dumps(body)


def test_restart_persistence_and_portable_library(stack: Stack) -> None:
    doc = stack.library / "Taxes" / "2025 return.pdf"
    doc.parent.mkdir()
    payload = secrets.token_bytes(2048)
    doc.write_bytes(payload)
    (stack.library / "notes.txt").write_text("plain file\n")
    before_files = library_listing(stack.library)
    before_id = stack.instance_id()

    stack.compose("restart", "api", "postgres")
    stack.wait_health("ok")
    assert stack.instance_id() == before_id

    stack.down()  # containers removed, volumes kept
    stack.up()
    stack.wait_health("ok")
    assert stack.instance_id() == before_id
    assert library_listing(stack.library) == before_files, "app must not add/alter library files"
    assert not any(p.name.startswith(".health") for p in stack.library.rglob("*"))
    # Probe files live only in app-data, never in the document tree.
    assert not list(stack.app_data.glob(".health-*"))


def test_core_failure_is_reported_and_recovers(stack: Stack) -> None:
    stack.compose("stop", "postgres")
    body = stack.wait_health("unavailable")
    assert body["core"]["database"]["state"] == "unavailable"  # type: ignore[index]
    status, _ = stack.get("/api/v1/health")
    assert status == 503
    stack.compose("start", "postgres")
    stack.wait_health("ok", timeout=90)


def test_document_root_survives_total_database_loss(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    s = Stack(tmp_path_factory.mktemp("dbloss"), f"fctest{secrets.token_hex(3)}")
    try:
        s.up()
        (s.library / "keep.txt").write_text("still here\n")
        s.down(volumes=True)  # destroys the metadata database entirely
        assert (s.library / "keep.txt").read_text() == "still here\n"
        s.up()  # fresh database against the same documents
        s.wait_health("ok")
        assert (s.library / "keep.txt").read_text() == "still here\n"
    finally:
        s.down(volumes=True)


def test_optional_workers_degrade_independently_of_core(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    s = Stack(tmp_path_factory.mktemp("workers"), f"fctest{secrets.token_hex(3)}")
    s.env["FILECANOPY_REDIS_URL"] = "redis://redis:6379/0"
    try:
        s.env["COMPOSE_PROFILES"] = "workers"
        s.up()
        body = s.wait_health("ok", timeout=90)
        assert body["optional"] == {"redis": {"state": "ok"}, "worker": {"state": "ok"}}  # type: ignore[comparison-overlap]

        s.compose("stop", "worker")
        # Heartbeat TTL is 30s; core stays up the whole time.
        body = s.wait_health("degraded", timeout=90)
        assert body["optional"]["worker"]["reason"] == "no_worker_heartbeat"  # type: ignore[index]
        assert all(c["state"] == "ok" for c in body["core"].values())  # type: ignore[attr-defined]
        assert s.get("/api/v1/health")[0] == 200

        s.compose("stop", "redis")
        body = s.wait_health("degraded", timeout=30)
        assert body["optional"]["redis"]["state"] == "unavailable"  # type: ignore[index]
        assert s.get("/api/v1/health")[0] == 200

        s.compose("start", "redis", "worker")
        s.wait_health("ok", timeout=90)
    finally:
        s.down(volumes=True)
