"""M22: the release packaging stays pinned and consistent. Reads the repository's Dockerfiles,
Compose files, stack file and scripts; no Docker needed."""

import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "deploy"
DOCKERFILES = [
    REPO / "apps/api/Dockerfile",
    REPO / "apps/web/Dockerfile",
    REPO / "deploy/postgres/Dockerfile",
    REPO / "converter/Dockerfile",
    REPO / "ocr/Dockerfile",
]
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}")


def from_lines(path: Path) -> list[tuple[str, str | None]]:
    """(image, stage alias) for every FROM line."""
    out = []
    for line in path.read_text().splitlines():
        m = re.match(r"FROM\s+(\S+)(?:\s+AS\s+(\S+))?", line, re.IGNORECASE)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.parent.name)
def test_every_base_image_is_pinned_by_digest(dockerfile: Path) -> None:
    stages = {alias.lower() for _, alias in from_lines(dockerfile) if alias}
    external = [image for image, _ in from_lines(dockerfile) if image.lower() not in stages]
    assert external, dockerfile
    for image in external:
        assert DIGEST.search(image), f"{dockerfile.relative_to(REPO)}: {image} is not pinned"
        assert ":latest" not in image


def deploy_compose_files() -> list[Path]:
    return [DEPLOY / "compose.yaml", *sorted((DEPLOY / "optional").glob("compose.*.yaml"))]


def test_third_party_compose_images_are_pinned_by_digest() -> None:
    for path in deploy_compose_files():
        for image in re.findall(r"^\s+image:\s+(\S+)", path.read_text(), re.MULTILINE):
            if "filecanopy" in image:
                continue  # ours: tagged with the release version
            assert DIGEST.search(image), f"{path.name}: {image} is not pinned"


def test_our_images_follow_one_prefix_and_version_and_never_latest() -> None:
    for path in deploy_compose_files():
        text = path.read_text()
        for image in re.findall(r"^\s+image:\s+(\S+filecanopy\S*)", text, re.MULTILINE):
            assert image.startswith("${FILECANOPY_IMAGE_PREFIX:-ghcr.io/aulteran/filecanopy}-"), (
                image
            )
            assert image.endswith(":${FILECANOPY_VERSION:-beta}"), image
        code = "\n".join(x for x in text.splitlines() if not x.lstrip().startswith("#"))
        assert "latest" not in code
        assert not re.search(r"^\s+build:", text, re.MULTILINE), "builds live in contrib/build.yaml"


def hardening_block(path: Path) -> str:
    m = re.search(r"^x-hardening: &hardening\n((?:  .*\n)+)", path.read_text(), re.MULTILINE)
    assert m, f"{path.name}: no x-hardening anchor"
    return m.group(1)


def test_compose_core_is_short_and_every_container_is_hardened() -> None:
    text = (DEPLOY / "compose.yaml").read_text()
    assert len(text.splitlines()) <= 110, "the part a beginner reads must stay short"
    assert text.count("<<: *hardening") == 4  # init, migrate, api, web
    assert "profiles:" not in text, "optional containers live in optional/compose.extras.yaml"
    assert "POSTGRES_PASSWORD:" not in text, "the database password is generated, never typed"
    assert text.count("FILECANOPY_") < 45, "defaults belong in apps/api/app/config.py"


def test_optional_extras_are_profile_gated_and_hardened() -> None:
    path = DEPLOY / "optional/compose.extras.yaml"
    text = path.read_text()
    assert text.count("<<: *hardening") == 3  # the sealed anchor, redis, worker
    assert text.count("<<: *sealed") == 2  # converter, ocr
    assert text.count("profiles: [") == 4  # converter, ocr, redis, worker
    body = text.split("\nservices:")[1]
    services = re.findall(r"^  ([a-z][a-z0-9_-]*):\s*$", body, re.MULTILINE)
    assert services == ["converter", "ocr", "redis", "worker"]
    # YAML anchors do not cross files, so the extras file carries its own copy of the core one.
    assert hardening_block(path) == hardening_block(DEPLOY / "compose.yaml")


def test_stack_file_uses_published_images_and_opens_no_ports() -> None:
    stack = (DEPLOY / "contrib/swarm/docker-stack.yml").read_text()
    code = "\n".join(line for line in stack.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"^\s+build:", code, re.MULTILINE)
    assert not re.search(r"^\s+ports:", code, re.MULTILINE)
    assert "FILECANOPY_VERSION:?" in code and ":latest" not in code
    assert "POSTGRES_PASSWORD_FILE" in code and "POSTGRES_PASSWORD:" not in code
    assert code.count("replicas: 1") == 3
    assert "node.labels.filecanopy.data == true" in code


def test_requirements_are_exactly_pinned() -> None:
    for name in ("requirements.txt", "requirements-dev.txt"):
        for line in (REPO / "apps/api" / name).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "-")):
                assert "==" in line, f"{name}: {line} is not pinned"


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX shell")
@pytest.mark.parametrize("script", ["lib.sh", "backup.sh", "restore.sh"])
def test_scripts_parse(script: str) -> None:
    r = subprocess.run(  # noqa: S603
        ["sh", "-n", str(DEPLOY / "scripts" / script)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr


# --------------------------------------------------------------- script behaviour (stubbed docker)


def stage_scripts(tmp_path: Path, env_file: str | None = None) -> Path:
    deploy = tmp_path / "deploy"
    shutil.copytree(DEPLOY / "scripts", deploy / "scripts")
    if env_file is not None:
        (deploy / ".env").write_text(env_file)
    return deploy


def run_probe(deploy: Path, body: str, **env: str) -> str:
    probe = deploy / "scripts" / "probe.sh"
    probe.write_text('. "$(dirname -- "$0")/lib.sh"\n' + body + "\n")
    base = {"PATH": "/usr/bin:/bin", "HOME": str(deploy)}
    r = subprocess.run(  # noqa: S603
        ["sh", str(probe)],  # noqa: S607
        env={**base, **env},
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX shell")
def test_scripts_use_compose_yaml_unless_told_otherwise(tmp_path: Path) -> None:
    core = run_probe(stage_scripts(tmp_path / "a"), 'echo "$COMPOSE_FILE"')
    assert core.endswith("deploy/compose.yaml")
    explicit = run_probe(
        stage_scripts(tmp_path / "d"), 'echo "$COMPOSE_FILE"', COMPOSE_FILE="/x/mine.yml"
    )
    assert explicit == "/x/mine.yml"


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX shell")
def test_dotenv_values_are_read_like_compose(tmp_path: Path) -> None:
    env = (
        "  export A = spaced # comment\n"
        'B="quoted # kept" # comment\n'
        "C='single'\n"
        "D=plain\r\n"
        "# E=commented\n"
        "F=first\nF=last\n"
    )
    deploy = stage_scripts(tmp_path, env)
    out = run_probe(
        deploy,
        'for k in A B C D E F; do echo "$k=$(env_value $k none)"; done',
    ).splitlines()
    assert out == ["A=spaced", "B=quoted # kept", "C=single", "D=plain", "E=none", "F=last"]


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX shell")
def test_a_failing_database_query_is_not_hidden(tmp_path: Path) -> None:
    deploy = stage_scripts(tmp_path)
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "docker").write_text("#!/bin/sh\nexit 3\n")
    (stub / "docker").chmod(0o755)
    probe = deploy / "scripts" / "probe.sh"
    probe.write_text('. "$(dirname -- "$0")/lib.sh"\nset -e\npsql_value "select 1"\necho reached\n')
    r = subprocess.run(  # noqa: S603
        ["sh", str(probe)],  # noqa: S607
        env={"PATH": f"{stub}:/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode != 0 and "reached" not in r.stdout
    assert "database query failed" in r.stderr


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX shell")
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_an_interrupted_backup_starts_the_app_again(tmp_path: Path, sig: signal.Signals) -> None:
    deploy = stage_scripts(tmp_path)
    library, app_data, dest = tmp_path / "library", tmp_path / "app-data", tmp_path / "dest"
    for d in (library, app_data, dest):
        d.mkdir()
    stub = tmp_path / "bin"
    stub.mkdir()
    log, ready = tmp_path / "calls.log", tmp_path / "ready"
    (stub / "docker").write_text(
        "#!/bin/sh\n"
        'echo "$@" >> "$STUB_LOG"\n'
        'case "$*" in\n'
        '  *"ps --status running --services"*) printf "postgres\napi\nweb\n" ;;\n'
        '  *pg_dump*) touch "$STUB_READY"; exec sleep 30 ;;\n'
        "esac\n"
    )
    (stub / "docker").chmod(0o755)
    env = {
        "PATH": f"{stub}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "STUB_LOG": str(log),
        "STUB_READY": str(ready),
        "FILECANOPY_LIBRARY_PATH": str(library),
        "FILECANOPY_APP_DATA_PATH": str(app_data),
    }
    proc = subprocess.Popen(  # noqa: S603
        ["sh", str(deploy / "scripts" / "backup.sh"), str(dest)],  # noqa: S607
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,  # like a terminal: the signal reaches the whole process group
    )
    for _ in range(100):
        if ready.exists():
            break
        time.sleep(0.1)
    assert ready.exists(), "backup never reached the database dump"
    os.killpg(proc.pid, sig)
    proc.wait(timeout=20)
    calls = log.read_text()
    assert "stop web" in calls and "stop api" in calls
    assert "start api" in calls and "start web" in calls, calls


def fake_backup(tmp_path: Path) -> Path:
    backup = tmp_path / "backup"
    backup.mkdir()
    for name in ("db.dump", "app-data.tar.gz", "library.tar.gz"):
        (backup / name).write_bytes(b"x")
    (backup / "MANIFEST").write_text("format=1\nschema=0023\n")
    sums = subprocess.run(  # noqa: S603
        ["sha256sum", "db.dump", "app-data.tar.gz", "library.tar.gz", "MANIFEST"],  # noqa: S607
        cwd=backup,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    (backup / "SHA256SUMS").write_text(sums)
    return backup


def run_restore(
    tmp_path: Path, app_data: Path, docker_body: str
) -> subprocess.CompletedProcess[str]:
    deploy = stage_scripts(tmp_path / "stage")
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "docker").write_text(f"#!/bin/sh\n{docker_body}\n")
    (stub / "docker").chmod(0o755)
    env = {
        "PATH": f"{stub}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "FILECANOPY_LIBRARY_PATH": str(tmp_path / "library"),
        "FILECANOPY_APP_DATA_PATH": str(app_data),
    }
    return subprocess.run(  # noqa: S603
        ["sh", str(deploy / "scripts" / "restore.sh"), str(fake_backup(tmp_path))],  # noqa: S607
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX shell")
def test_restore_refuses_occupied_or_unreadable_targets(tmp_path: Path) -> None:
    occupied = tmp_path / "app-data"
    occupied.mkdir()
    (occupied / "x").write_text("x")
    r = run_restore(tmp_path / "a", occupied, "exit 0")
    assert r.returncode != 0 and "is not empty" in r.stderr
    not_a_folder = tmp_path / "file"
    not_a_folder.write_text("x")
    r = run_restore(tmp_path / "b", not_a_folder, "exit 0")
    assert r.returncode != 0 and "is not a folder" in r.stderr


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX shell")
def test_a_failed_restore_says_how_to_start_over(tmp_path: Path) -> None:
    body = 'case "$*" in *pg_restore*) exit 1 ;; *psql*) echo 0 ;; esac'
    r = run_restore(tmp_path, tmp_path / "app-data", body)
    assert r.returncode != 0
    assert "The restore did not finish" in r.stderr
    assert "docker compose down -v" in r.stderr


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX shell")
def test_restore_into_a_live_database_gives_no_delete_advice(tmp_path: Path) -> None:
    r = run_restore(tmp_path, tmp_path / "app-data", 'case "$*" in *psql*) echo 12 ;; esac')
    assert r.returncode != 0
    assert "already has tables" in r.stderr and "DELETES" in r.stderr
    assert "The restore did not finish" not in r.stderr, "nothing was written, so no cleanup advice"


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX shell")
def test_scripts_also_load_the_extras_file_when_present(tmp_path: Path) -> None:
    flat = stage_scripts(tmp_path / "a")
    (flat / "compose.extras.yaml").write_text("services: {}\n")
    files = run_probe(flat, 'echo "$COMPOSE_FILE"').split(":")
    assert [Path(f).name for f in files] == ["compose.yaml", "compose.extras.yaml"]
    repo = stage_scripts(tmp_path / "b")
    (repo / "optional").mkdir()
    (repo / "optional" / "compose.extras.yaml").write_text("services: {}\n")
    files = run_probe(repo, 'echo "$COMPOSE_FILE"').split(":")
    assert files[-1].endswith("optional/compose.extras.yaml")
