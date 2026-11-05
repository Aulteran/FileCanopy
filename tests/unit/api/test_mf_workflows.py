"""MF: the branch promotion and publish workflows keep their safety properties. Reads the YAML
files and the release plan; no GitHub, Docker or registry access."""

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github/workflows"
PUBLISH = WORKFLOWS / "publish.yml"


def load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text())
    # PyYAML reads the bare key `on` as the boolean True.
    data["on"] = data.pop(True) if True in data else data.get("on")
    return data


def jobs_with_permission(workflow: dict, key: str, value: str) -> set[str]:
    out = set()
    for name, job in workflow["jobs"].items():
        if job.get("permissions", {}).get(key) == value:
            out.add(name)
    return out


def test_ci_checks_all_promotion_branches() -> None:
    ci = load(WORKFLOWS / "ci.yml")
    assert set(ci["on"]["push"]["branches"]) >= {"main", "dev", "beta", "release"}
    assert "pull_request" in ci["on"]


def test_publish_triggers_only_on_beta_release_or_dry_run() -> None:
    wf = load(PUBLISH)
    assert set(wf["on"]) == {"push", "workflow_dispatch"}
    assert wf["on"]["push"]["branches"] == ["beta", "release"]
    assert "pull_request" not in wf["on"]
    assert "pull_request_target" not in wf["on"]


def test_publish_is_gated_by_owner_variable_and_repository() -> None:
    wf = load(PUBLISH)
    gate = wf["jobs"]["plan"]["if"]
    assert "MF_PUBLISH_ENABLED" in gate
    assert "github.repository == 'Aulteran/FileCanopy'" in gate
    assert (
        "github.ref_name == 'beta'" in gate and "github.ref_name == 'release'" in gate
    )


def test_default_permissions_are_read_only() -> None:
    wf = load(PUBLISH)
    assert wf["permissions"] == {"contents": "read"}
    assert jobs_with_permission(wf, "contents", "write") == {"github-release"}
    assert jobs_with_permission(wf, "packages", "write") == {"images"}


def test_environments_follow_the_branch_and_never_fall_back() -> None:
    wf = load(PUBLISH)
    assert wf["jobs"]["images"]["environment"] == "${{ github.ref_name }}"
    assert wf["jobs"]["github-release"]["environment"] == "release"
    assert wf["jobs"]["github-release"]["if"].startswith("github.ref_name == 'release'")


def test_only_the_github_token_secret_is_used() -> None:
    text = PUBLISH.read_text()
    secrets = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text))
    assert secrets == {"GITHUB_TOKEN"}


def test_dry_run_never_pushes_or_releases() -> None:
    wf = load(PUBLISH)
    build = next(s for s in wf["jobs"]["images"]["steps"] if s.get("id") == "build")
    assert "needs.plan.outputs.dry_run" in build["with"]["push"]
    assert wf["jobs"]["github-release"]["if"].endswith("dry_run != 'true'")


def test_beta_never_gets_latest() -> None:
    text = PUBLISH.read_text()
    beta_block = text.split('if [ "$REF_NAME" = "beta" ]; then', 1)[1].split("else", 1)[
        0
    ]
    assert "latest" not in beta_block
    assert "beta-${SHORT_SHA}" in beta_block


def test_no_moving_tag_on_release_for_non_release_refs() -> None:
    text = PUBLISH.read_text()
    assert text.count(":latest") == 1


def test_images_matrix_matches_release_plan() -> None:
    wf = load(PUBLISH)
    matrix = wf["jobs"]["images"]["strategy"]["matrix"]["include"]
    plan_text = (REPO / "scripts/release-plan.mjs").read_text()
    block = plan_text.split("const specs = [", 1)[1].split("];", 1)[0]
    specs = re.findall(r'\["(\w+)", "([^"]+)", "([^"]+)"(?:, "(\w+)")?\]', block)
    expected = {(s[0], s[1], s[2], s[3]) for s in specs}
    assert len(expected) == 5
    actual = {
        (m["name"], m["file"], m["context"], m.get("target") or "") for m in matrix
    }
    assert actual == expected


def test_every_publish_job_checks_out_without_persisted_credentials() -> None:
    wf = load(PUBLISH)
    for name, job in wf["jobs"].items():
        checkout = [
            s
            for s in job["steps"]
            if str(s.get("uses", "")).startswith("actions/checkout")
        ]
        assert checkout, name
        for step in checkout:
            assert step["with"]["persist-credentials"] is False, name


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_every_workflow_parses(path: Path) -> None:
    assert load(path)["jobs"]
