"""M22: the release packaging stays pinned and consistent. Reads the repository's Dockerfiles,
Compose files, stack file and scripts; no Docker needed."""

import re
import shutil
import subprocess
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


def test_third_party_compose_images_are_pinned_by_digest() -> None:
    text = (DEPLOY / "docker-compose.yml").read_text()
    for image in re.findall(r"^\s+image:\s+(\S+)", text, re.MULTILINE):
        if image.startswith("filecanopy-"):
            continue  # built from this repository
        assert DIGEST.search(image), f"docker-compose.yml: {image} is not pinned"


def services_with_our_images(text: str) -> set[str]:
    """Top-level service names that build from this repository (and so need a release image)."""
    services: set[str] = set()
    current = None
    in_services = False
    for line in text.splitlines():
        if line.startswith("services:"):
            in_services = True
        elif re.match(r"^\S", line) and in_services:
            in_services = False
        m = re.match(r"^  ([a-z][a-z0-9_-]*):\s*$", line)
        if in_services and m:
            current = m.group(1)
        if in_services and current and re.search(r"image:\s+filecanopy-", line):
            services.add(current)
    return services


def test_release_override_covers_every_service_built_from_source() -> None:
    ours = services_with_our_images((DEPLOY / "docker-compose.yml").read_text())
    release = (DEPLOY / "docker-compose.release.yml").read_text()
    assert ours >= {"postgres", "migrate", "api", "web", "converter", "ocr", "worker"}
    for service in ours:
        assert re.search(rf"^  {service}:\s*$", release, re.MULTILINE), f"{service} missing"
    assert "${FILECANOPY_VERSION:?" in release, "the version must be required, never defaulted"
    assert "latest" not in release.replace("no floating", "")


def test_stack_file_uses_published_images_and_opens_no_ports() -> None:
    stack = (DEPLOY / "stack/docker-stack.yml").read_text()
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
