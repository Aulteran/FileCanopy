"""M27 backup jobs (ADR-021): job validation and acknowledgements, both backup types, per-job
folders and chain state, Confidential handling for unencrypted jobs, the M25 key rules for
encrypted jobs, remote destinations (upload, verification, failure, staging, restore, retention),
credential redaction and the address policy.

Remote destinations are exercised through a local double of the destination interface and through
client doubles for the S3 and SFTP classes: nothing here connects to a network or a cloud service.
All content and credentials are generated.
"""

import base64
import hashlib
import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Self

import pytest
from sqlalchemy import select
from test_acl import Fx
from test_auth import PASSWORD, World
from test_backup import (  # noqa: F401  (make_world is a fixture)
    audit_text,
    file_names,
    lib_root,
    make_owner_key,
    make_world,
    put_settings,
    service,
    volume_dir,
)
from test_files import Api, code, nid
from test_reconcile import root_id

from app.backup import destinations, retention
from app.backup import engine as engine_mod
from app.backup import restore as restore_mod
from app.backup.destinations import DestinationError, S3Destination, SftpDestination
from app.backup.jobs import JobError, JobSpec, open_credentials, seal_credentials
from app.db.models import BackupJob, BackupJobChainState, BackupRun, Node

SECRET_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode()
S3_SECRET = "TESTONLY-s3-secret-do-not-leak"
SFTP_PASSWORD = "TESTONLY-sftp-password-do-not-leak"
JOBS = "/api/v1/admin/backups/jobs"


@pytest.fixture
def fx(make_world: Callable[..., World]) -> Fx:  # noqa: F811
    return Fx(make_world(secrets_key=SECRET_KEY))


# ------------------------------------------------------------------ helpers


def body(**over: Any) -> dict[str, Any]:
    return {
        "name": "Nightly",
        "enabled": True,
        "backup_type": "chain",
        "chain_length": 3,
        "interval_minutes": 60,
        "min_chains_kept": 2,
        "encrypt": False,
        "acknowledge_unencrypted": True,
        "destination_type": "local",
        "password": PASSWORD,  # fresh admin reauthentication
        **over,
    }


def create(fx: Fx, **over: Any) -> Any:
    c, h = fx.admin
    return c.post(JOBS, json=body(**over), headers=h)


def make_job(fx: Fx, **over: Any) -> uuid.UUID:
    r = create(fx, **over)
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"])


S3: dict[str, Any] = {
    "destination_type": "s3",
    "destination": {"bucket": "fc-test-bucket", "path": "bk"},
    "credentials": {"access_key_id": "AKIATESTONLY", "secret_access_key": S3_SECRET},
}


def job_runs(fx: Fx, job_id: uuid.UUID | None, status: str | None = "ok") -> list[BackupRun]:
    with fx.w.ctx.sessions() as db:
        q = select(BackupRun).where(
            BackupRun.job_id.is_(None) if job_id is None else BackupRun.job_id == job_id
        )
        if status:
            q = q.where(BackupRun.status == status)
        rows = list(db.scalars(q.order_by(BackupRun.started_at, BackupRun.seq)))
        for r in rows:
            db.expunge(r)
        return rows


def go(fx: Fx, job_id: uuid.UUID | None, anchor: uuid.UUID | None = None) -> engine_mod.RunResult:
    result = service(fx).engine.run_root(root_id(fx, anchor or fx.shared_root), job_id)
    fx.w.advance(minutes=61)
    return result


def archives_of(fx: Fx, job: uuid.UUID | None) -> list[Path]:
    return [volume_dir(fx) / r.archive_name for r in job_runs(fx, job) if r.archive_name]


class FakeDest:
    """A destination that is a folder; failures can be switched on."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.fail_put: str | None = None  # a substring of the key whose upload fails
        self.closed = 0

    def path_of(self, key: str) -> Path:
        return self.base / key

    def test(self) -> None:
        return None

    def put(self, local: Path, key: str, sha256: str, size: int) -> None:
        if self.fail_put and self.fail_put in key:
            raise DestinationError("UPLOAD_FAILED")
        data = local.read_bytes()
        assert hashlib.sha256(data).hexdigest() == sha256 and len(data) == size
        target = self.path_of(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def get(self, key: str, local: Path) -> None:
        try:
            local.write_bytes(self.path_of(key).read_bytes())
        except OSError as exc:
            raise DestinationError("DOWNLOAD_FAILED") from exc

    def delete(self, key: str) -> None:
        self.path_of(key).unlink(missing_ok=True)

    def close(self) -> None:
        self.closed += 1

    def keys(self) -> set[str]:
        return {p.relative_to(self.base).as_posix() for p in self.base.rglob("*") if p.is_file()}


@pytest.fixture
def remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeDest:
    dest = FakeDest(tmp_path / "remote")
    dest.base.mkdir()

    def opener(spec: JobSpec, settings: Any) -> FakeDest | None:
        if spec.destination_type == "local":
            return None
        open_credentials(settings, spec)  # the real credential path runs
        return dest

    for module in (engine_mod, retention, restore_mod):
        monkeypatch.setattr(module, "open_destination", opener)
    return dest


def upload(fx: Fx, name: str, data: bytes = b"hello", where: uuid.UUID | None = None) -> uuid.UUID:
    return nid(Api(fx, "alice").upload(where or fx.shared_sub, name, data))


def label(fx: Fx, node: uuid.UUID, value: str) -> None:
    with fx.w.ctx.sessions() as db:
        row = db.get(Node, node)
        assert row is not None
        row.privacy_label = value
        db.commit()


# ------------------------------------------------------------------ validation and labels


def test_create_validates_and_labels_unencrypted_jobs(fx: Fx) -> None:
    ok = create(fx)
    assert ok.status_code == 201, ok.text
    out = ok.json()
    assert out["encrypt"] is False and out["destination_type"] == "local"
    assert {"unencrypted", "confidential_excluded"} <= set(out["warnings"])  # clearly labelled
    assert out["allow_confidential"] is False
    clash = create(fx, name="nightly")
    assert (clash.status_code, code(clash)) == (409, "JOB_NAME_TAKEN")
    for over, expected in [
        (
            {"name": "A", "acknowledge_unencrypted": False},
            "UNENCRYPTED_NOT_ACKNOWLEDGED",
        ),
        ({"name": "B", "backup_type": "weekly"}, "BACKUP_TYPE_INVALID"),
        ({"name": "C", "destination_type": "ftp"}, "DESTINATION_TYPE_INVALID"),
        ({"name": "D", "interval_minutes": 1}, "SCHEDULE_INVALID"),
        ({"name": "E", "scope": "none"}, "SCOPE_INVALID"),
        ({"name": "F", "allow_confidential": True}, "CONFIDENTIAL_NOT_ACKNOWLEDGED"),
    ]:
        r = create(fx, **over)
        assert (r.status_code, code(r)) == (422, expected), over
    listing = fx.admin[0].get(JOBS, headers=fx.admin[1]).json()
    assert [j["name"] for j in listing] == ["Nightly"]


def test_only_administrators_manage_jobs(fx: Fx) -> None:
    job = make_job(fx)
    alice = Api(fx, "alice")
    anonymous = fx.w.browser()
    ghost = uuid.uuid4()
    calls = [
        ("GET", "/admin/backups/jobs", None),
        ("POST", "/admin/backups/jobs", body(name="Other")),
        ("PUT", f"/admin/backups/jobs/{job}", body()),
        ("DELETE", f"/admin/backups/jobs/{job}", None),
        ("POST", f"/admin/backups/jobs/{job}/test", None),
        ("POST", f"/admin/backups/jobs/{job}/run", None),
        ("POST", f"/admin/backups/jobs/{ghost}/run", None),
    ]
    for method, path, payload in calls:
        url = f"/api/v1{path}"
        r = alice.client.request(method, url, json=payload, headers=alice.headers)
        assert r.status_code == 403, (method, path, r.status_code)
        r = anonymous.request(method, url, json=payload)
        assert r.status_code == 401, (method, path, r.status_code)
    listing = fx.admin[0].get(JOBS, headers=fx.admin[1]).json()
    assert [j["name"] for j in listing] == ["Nightly"]  # nothing changed or was removed


def test_full_every_time_is_a_chain_of_one(fx: Fx) -> None:
    out = create(fx, backup_type="full", chain_length=9).json()
    assert out["backup_type"] == "full" and out["chain_length"] == 1


def test_remote_jobs_need_a_secrets_key(
    make_world: Callable[..., World],  # noqa: F811
) -> None:
    no_key = Fx(make_world())
    r = create(no_key, **S3, encrypt=True, acknowledge_unencrypted=False)
    assert (r.status_code, code(r)) == (
        409,
        "SECRETS_KEY_MISSING",
    )  # nowhere safe to keep them


def test_remote_jobs_need_credentials_and_acknowledgement(fx: Fx) -> None:
    r = create(fx, **{**S3, "credentials": None}, encrypt=True, acknowledge_unencrypted=False)
    assert (r.status_code, code(r)) == (422, "CREDENTIALS_REQUIRED")
    r = create(fx, **S3)  # unencrypted to a remote needs its own, stronger acknowledgement
    assert (r.status_code, code(r)) == (422, "REMOTE_UNENCRYPTED_NOT_ACKNOWLEDGED")
    r = create(fx, **S3, acknowledge_remote_unencrypted=True)
    assert r.status_code == 201 and "remote_unencrypted" in r.json()["warnings"]
    insecure = {
        **S3,
        "name": "http",
        "destination": {"bucket": "fc-test-bucket", "endpoint_url": "http://x.example"},
    }
    plain = create(fx, **insecure, acknowledge_remote_unencrypted=True)
    assert (plain.status_code, code(plain)) == (422, "DESTINATION_INSECURE")


def test_credentials_are_sealed_never_returned_never_logged(
    fx: Fx, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    r = create(fx, **S3, encrypt=True, acknowledge_unencrypted=False)
    assert r.status_code == 201, r.text
    job_id = uuid.UUID(r.json()["id"])
    assert r.json()["credentials_set"] is True
    c, h = fx.admin
    listing = c.get(JOBS, headers=h).text
    with fx.w.ctx.sessions() as db:
        row = db.get(BackupJob, job_id)
        assert row is not None and row.credentials_sealed is not None
        assert S3_SECRET.encode() not in row.credentials_sealed  # a database copy shows nothing
        assert S3_SECRET not in repr(row.destination)
        spec = JobSpec(
            job_id=job_id,
            name="x",
            enabled=True,
            interval_minutes=60,
            full_every=1,
            min_chains_kept=2,
            max_size_bytes=None,
            encrypt=True,
            sealed=row.credentials_sealed,
        )
        assert open_credentials(fx.w.settings, spec)["secret_access_key"] == S3_SECRET
        other = JobSpec(**{**spec.__dict__, "job_id": uuid.uuid4()})  # copied onto another job
        with pytest.raises(JobError) as moved:
            open_credentials(fx.w.settings, other)
        assert moved.value.code == "CREDENTIALS_UNREADABLE"
    # an update without credentials keeps the stored ones
    again = body(**{**S3, "credentials": None}, encrypt=True, acknowledge_unencrypted=False)
    upd = c.put(f"{JOBS}/{job_id}", json=again, headers=h)
    assert upd.status_code == 200 and upd.json()["credentials_set"] is True, upd.text
    for text in (r.text, listing, upd.text, audit_text(fx), caplog.text):
        assert S3_SECRET not in text and "AKIATESTONLY" not in text


# ------------------------------------------------------------------ backup types and layout


def test_full_every_time_writes_a_full_archive_on_each_run(fx: Fx) -> None:
    upload(fx, "a.txt")
    job = make_job(fx, backup_type="full")
    for _ in range(3):
        assert go(fx, job).status == "ok"
    runs = job_runs(fx, job)
    assert [(r.kind, r.seq) for r in runs] == [("full", 1)] * 3
    assert len({r.chain_id for r in runs}) == 3  # every run is its own chain


def test_incremental_chain_is_a_full_then_incrementals_then_a_new_full(fx: Fx) -> None:
    job = make_job(fx, backup_type="chain", chain_length=3)
    for i in range(5):
        upload(fx, f"f{i}.txt", b"x" * (i + 1))
        assert go(fx, job).status == "ok"
    runs = job_runs(fx, job)
    assert [(r.kind, r.seq) for r in runs] == [
        ("full", 1),
        ("incremental", 2),
        ("incremental", 3),
        ("full", 1),
        ("incremental", 2),
    ]
    assert runs[0].chain_id == runs[2].chain_id != runs[3].chain_id


def test_jobs_have_their_own_folder_chain_state_and_the_default_job_is_unchanged(
    fx: Fx,
) -> None:
    upload(fx, "a.txt")
    c, h = fx.admin
    assert put_settings(fx).status_code == 200  # the default job: the global settings
    job = make_job(fx)
    assert go(fx, None).status == "ok" and go(fx, job).status == "ok"
    default_run, job_run = job_runs(fx, None)[0], job_runs(fx, job)[0]
    assert job_run.archive_name and default_run.archive_name
    assert job_run.archive_name.startswith(f"j-{job.hex[:8]}/")
    assert not default_run.archive_name.startswith("j-")  # unchanged layout
    with fx.w.ctx.sessions() as db:
        assert db.get(BackupJobChainState, (job, root_id(fx, fx.shared_root))) is not None
        assert {c.job_id for c in retention.list_chains(db, job)} == {job}
        assert {c.job_id for c in retention.list_chains(db, None)} == {None}
    status = c.get("/api/v1/admin/backups", headers=h).json()
    mine = next(r for r in status["roots"] if r["root_id"] == str(root_id(fx, fx.shared_root)))
    assert mine["chains"] == 1  # the default job's own list: the job's chain is not in it


def test_scheduler_runs_each_enabled_job_when_due(fx: Fx) -> None:
    upload(fx, "a.txt")
    job = make_job(fx, scope="shared")
    results = service(fx).tick()
    assert results and all(r.status == "ok" for _, r in results)
    assert job_runs(fx, job) and not job_runs(fx, None)  # the default job is off
    assert service(fx).tick() == []  # nothing due yet
    fx.w.advance(minutes=61)
    assert service(fx).tick()
    assert service(fx).tick(force=True, jobs=frozenset({None})) == []  # default job is disabled


def test_scope_limits_the_roots_a_job_covers(fx: Fx) -> None:
    job = make_job(fx, scope="shared")
    assert go(fx, job, fx.alice_root).code == "OUT_OF_SCOPE"  # alice's root is personal


def test_unencrypted_job_leaves_confidential_files_out(fx: Fx) -> None:
    upload(fx, "keep.txt")
    secret = upload(fx, "secret.txt", b"NEVER-IN-THE-CLEAR")
    label(fx, secret, "confidential")
    job = make_job(fx)
    assert go(fx, job).status == "ok"
    names = file_names(archives_of(fx, job)[0])
    assert any(n.endswith("keep.txt") for n in names)
    assert not any(n.endswith("secret.txt") for n in names)
    assert b"NEVER-IN-THE-CLEAR" not in archives_of(fx, job)[0].read_bytes()


def test_confidential_folder_is_excluded_with_everything_below_it(fx: Fx) -> None:
    upload(fx, "inside.txt")
    label(fx, fx.shared_sub, "confidential")  # the folder carries it; children inherit
    job = make_job(fx)
    assert go(fx, job).status == "ok"
    assert not any(n.endswith("inside.txt") for n in file_names(archives_of(fx, job)[0]))


def test_confidential_is_included_only_when_explicitly_allowed(fx: Fx) -> None:
    secret = upload(fx, "secret.txt")
    label(fx, secret, "confidential")
    job = make_job(fx, allow_confidential=True, acknowledge_confidential=True)
    assert go(fx, job).status == "ok"
    assert any(n.endswith("secret.txt") for n in file_names(archives_of(fx, job)[0]))


def test_raising_a_label_starts_a_new_full_chain_without_the_file(fx: Fx) -> None:
    doc = upload(fx, "doc.txt")
    job = make_job(fx, chain_length=5)
    assert go(fx, job).status == "ok"
    label(fx, doc, "confidential")
    assert go(fx, job).status == "ok"
    first, second = job_runs(fx, job)
    assert (first.kind, second.kind) == (
        "full",
        "full",
    ) and first.chain_id != second.chain_id
    assert not any(n.endswith("doc.txt") for n in file_names(archives_of(fx, job)[1]))


def test_encrypted_job_includes_confidential_and_follows_the_owner_key_rule(fx: Fx) -> None:
    secret = upload(fx, "secret.txt", b"sealed-content", fx.taxes)
    label(fx, secret, "confidential")
    job = make_job(fx, encrypt=True, acknowledge_unencrypted=False)
    refused = go(fx, job, fx.alice_root)  # a personal root waits for its owner's key (M25)
    assert (refused.status, refused.code) == ("refused", "BACKUP_OWNER_KEY_PENDING")
    key = make_owner_key(fx)
    assert go(fx, job, fx.alice_root).status == "ok"
    run = next(r for r in job_runs(fx, job) if r.root_id == root_id(fx, fx.alice_root))
    assert run.encrypted and run.key_fingerprint
    path = volume_dir(fx) / (run.archive_name or "")
    assert b"sealed-content" not in path.read_bytes()
    assert any(n.endswith("secret.txt") for n in file_names(path, key))


def test_remote_run_uploads_verifies_and_keeps_only_the_newest_archive_local(
    fx: Fx, remote: FakeDest
) -> None:
    job = make_job(fx, **S3, acknowledge_remote_unencrypted=True, chain_length=4)
    for i in range(3):
        upload(fx, f"r{i}.txt", bytes([i]) * 50)
        assert go(fx, job).status == "ok"
    runs = job_runs(fx, job)
    assert all(r.remote for r in runs) and len(runs) == 3
    expected = {r.archive_name for r in runs} | {f"{r.archive_name}.json" for r in runs}
    assert remote.keys() == expected  # nothing else, nothing partial
    newest = Path(runs[-1].archive_name or "")
    local = {p.name for p in (volume_dir(fx) / newest).parent.iterdir()}
    assert local == {newest.name, newest.name + ".json"}
    assert remote.closed >= 3


def test_failed_upload_is_not_a_restore_point_and_leaves_the_chain_untouched(
    fx: Fx, remote: FakeDest
) -> None:
    upload(fx, "a.txt")
    job = make_job(fx, **S3, acknowledge_remote_unencrypted=True)
    remote.fail_put = ".tar.gz"
    result = go(fx, job)
    assert (result.status, result.code) == ("failed", "UPLOAD_FAILED")
    assert not job_runs(fx, job) and remote.keys() == set()
    assert not archives_of(fx, job)
    with fx.w.ctx.sessions() as db:
        assert db.get(BackupJobChainState, (job, root_id(fx, fx.shared_root))) is None
    remote.fail_put = None
    assert go(fx, job).status == "ok"  # the next run starts over cleanly


def test_sidecar_upload_failure_removes_the_archive_from_the_remote(
    fx: Fx, remote: FakeDest
) -> None:
    upload(fx, "a.txt")
    job = make_job(fx, **S3, acknowledge_remote_unencrypted=True)
    remote.fail_put = ".json"
    assert go(fx, job).status == "failed"
    assert remote.keys() == set()


def test_restore_fetches_trimmed_archives_back_from_the_remote(fx: Fx, remote: FakeDest) -> None:
    c, h = fx.admin
    upload(fx, "one.txt", b"first-version")
    job = make_job(fx, **S3, acknowledge_remote_unencrypted=True, chain_length=4)
    assert go(fx, job).status == "ok"
    upload(fx, "two.txt", b"second-version")
    assert go(fx, job).status == "ok"
    first, second = job_runs(fx, job)
    assert not (volume_dir(fx) / (first.archive_name or "")).exists()  # trimmed after upload
    r = c.post(
        f"/api/v1/nodes/{fx.shared_root}/backups/restore",
        json={"run_id": str(second.id)},
        headers=h,
    )
    assert r.status_code == 200, r.text
    restored = lib_root(fx, fx.shared_root) / r.json()["folder_name"]
    texts = {p.read_bytes() for p in restored.rglob("*.txt")}
    assert {b"first-version", b"second-version"} <= texts


def test_restore_refuses_a_damaged_remote_download_and_keeps_nothing(
    fx: Fx, remote: FakeDest
) -> None:
    c, h = fx.admin
    upload(fx, "one.txt")
    job = make_job(fx, **S3, acknowledge_remote_unencrypted=True, chain_length=4)
    go(fx, job)
    upload(fx, "two.txt")
    go(fx, job)
    first, second = job_runs(fx, job)
    remote.path_of(first.archive_name or "").write_bytes(b"corrupt")
    r = c.post(
        f"/api/v1/nodes/{fx.shared_root}/backups/restore",
        json={"run_id": str(second.id)},
        headers=h,
    )
    assert (r.status_code, code(r)) == (409, "BACKUP_ARCHIVE_DAMAGED")
    assert not (volume_dir(fx) / (first.archive_name or "")).exists()


def test_retention_deletes_only_the_objects_the_job_created(fx: Fx, remote: FakeDest) -> None:
    foreign = remote.base / "bk" / "someone-elses.tar.gz"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"not ours")
    job = make_job(
        fx,
        **S3,
        acknowledge_remote_unencrypted=True,
        backup_type="full",
        min_chains_kept=1,
    )
    for i in range(3):
        upload(fx, f"g{i}.txt", bytes([i]) * 10)
        go(fx, job)
    runs = job_runs(fx, job)
    limit = max(r.size_bytes or 0 for r in runs) + 1  # room for one run only
    spec = JobSpec(
        job_id=job,
        name="x",
        enabled=True,
        interval_minutes=60,
        full_every=1,
        min_chains_kept=1,
        max_size_bytes=limit,
        encrypt=False,
        destination_type="s3",
        sealed=b"x",
    )
    with fx.w.ctx.sessions() as db:
        row = db.get(BackupJob, job)
        assert row is not None
        spec = JobSpec(**{**spec.__dict__, "sealed": row.credentials_sealed})
        removed = retention.prune_to_limit(db, fx.w.settings, spec)
    assert removed
    live = {r.archive_name for r in job_runs(fx, job) if r.pruned_at is None}
    gone = {r.archive_name for r in runs} - live
    assert gone and live
    assert all(
        not remote.path_of(n).exists() and not remote.path_of(n + ".json").exists()
        for n in gone
        if n
    )
    assert all(remote.path_of(n).exists() for n in live if n)
    assert foreign.exists()  # not created by the job: never touched


def test_deleting_a_job_with_stored_backups_is_refused(fx: Fx) -> None:
    upload(fx, "a.txt")
    job = make_job(fx)
    go(fx, job)
    c, h = fx.admin
    r = c.delete(f"{JOBS}/{job}", headers=h)
    assert (r.status_code, code(r)) == (409, "JOB_HAS_BACKUPS")
    empty = make_job(fx, name="Spare")
    assert c.delete(f"{JOBS}/{empty}", headers=h).status_code == 200


def test_test_endpoint_returns_the_host_key_to_pin_without_secrets(
    fx: Fx, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = make_job(
        fx,
        destination_type="sftp",
        destination={"host": "backup.example.test", "username": "fc", "path": "bk"},
        credentials={"password": SFTP_PASSWORD},
        acknowledge_remote_unencrypted=True,
    )

    def refuse(spec: JobSpec, settings: Any) -> Any:
        open_credentials(settings, spec)
        raise DestinationError("SFTP_HOST_KEY_UNPINNED", "SHA256:seenKeyFingerprint")

    monkeypatch.setattr("app.api.backup_jobs.open_destination", refuse)
    c, h = fx.admin
    r = c.post(f"{JOBS}/{job}/test", headers=h)
    assert r.status_code == 409
    assert r.json()["detail"] == {
        "code": "SFTP_HOST_KEY_UNPINNED",
        "host_key_sha256": "SHA256:seenKeyFingerprint",
    }
    assert SFTP_PASSWORD not in r.text + audit_text(fx)


# ------------------------------------------------------------------ destination classes


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.fail: Exception | None = None
        self.tamper = False

    def head_bucket(self, **kw: Any) -> None:
        if self.fail:
            raise self.fail

    def put_object(self, **kw: Any) -> None:
        self.objects[kw["Key"]] = (kw["Body"], {})

    def delete_object(self, **kw: Any) -> None:
        self.objects.pop(kw["Key"], None)

    def upload_file(self, path: str, bucket: str, key: str, ExtraArgs: dict[str, Any]) -> None:
        if self.fail:
            raise self.fail
        data = Path(path).read_bytes()
        self.objects[key] = (data[:-1] if self.tamper else data, ExtraArgs["Metadata"])

    def head_object(self, **kw: Any) -> dict[str, Any]:
        data, meta = self.objects[kw["Key"]]
        return {"ContentLength": len(data), "Metadata": meta}

    def download_file(self, bucket: str, key: str, path: str) -> None:
        Path(path).write_bytes(self.objects[key][0])


class AwsError(Exception):
    def __init__(self, error_code: str) -> None:
        super().__init__(f"The access key AKIASECRETKEY failed: {error_code}")  # carries a secret
        self.response = {"Error": {"Code": error_code}}


def test_s3_destination_verifies_size_and_checksum_and_hides_error_text(
    tmp_path: Path,
) -> None:
    client = FakeS3()
    dest = S3Destination.with_client(client, "fc-test-bucket", "bk")
    src = tmp_path / "a.bin"
    src.write_bytes(b"payload")
    sha = hashlib.sha256(b"payload").hexdigest()
    dest.test()
    dest.put(src, "j-1/shared/c/0001-full.tar.gz", sha, 7)
    assert "bk/j-1/shared/c/0001-full.tar.gz" in client.objects
    out = tmp_path / "back.bin"
    dest.get("j-1/shared/c/0001-full.tar.gz", out)
    assert out.read_bytes() == b"payload"
    client.tamper = True  # the stored object differs from what was sent
    with pytest.raises(DestinationError) as bad:
        dest.put(src, "j-1/x", sha, 7)
    assert bad.value.code == "UPLOAD_VERIFY_FAILED" and "bk/j-1/x" not in client.objects
    client.tamper = False
    client.fail = AwsError("InvalidAccessKeyId")
    with pytest.raises(DestinationError) as auth:
        dest.put(src, "j-1/y", sha, 7)
    assert auth.value.code == "S3_AUTH_FAILED" and "AKIASECRETKEY" not in str(auth.value)
    assert auth.value.__cause__ is None  # the original message is not chained either
    dest.delete("j-1/shared/c/0001-full.tar.gz")
    assert "bk/j-1/shared/c/0001-full.tar.gz" not in client.objects


class FakeSftp:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = set()
        self.short = False

    def stat(self, path: str) -> Any:
        if path in self.dirs:
            return type("S", (), {"st_size": 0})()
        if path in self.files:
            return type("S", (), {"st_size": len(self.files[path])})()
        raise FileNotFoundError(path)

    def mkdir(self, path: str) -> None:
        self.dirs.add(path)

    def put(self, local: str, remote: str) -> None:
        data = Path(local).read_bytes()
        self.files[remote] = data[:-1] if self.short else data

    def posix_rename(self, a: str, b: str) -> None:
        self.files[b] = self.files.pop(a)

    def remove(self, path: str) -> None:
        if path not in self.files:
            raise FileNotFoundError(path)
        del self.files[path]

    def get(self, remote: str, local: str) -> None:
        Path(local).write_bytes(self.files[remote])

    def open(self, path: str, mode: str) -> Any:
        files = self.files

        class Handle:
            def __enter__(self) -> Self:
                return self

            def __exit__(self, *a: object) -> None:
                return None

            def write(self, data: bytes) -> None:
                files[path] = data

        return Handle()


def test_sftp_destination_uploads_atomically_and_cleans_up_a_short_upload(
    tmp_path: Path,
) -> None:
    sftp = FakeSftp()
    dest = SftpDestination.with_client(sftp, "bk")
    src = tmp_path / "a.bin"
    src.write_bytes(b"payload")
    dest.test()
    dest.put(src, "j-1/shared/0001-full.tar.gz", "ignored", 7)
    assert sftp.files == {"bk/j-1/shared/0001-full.tar.gz": b"payload"}  # no .part left
    sftp.short = True
    with pytest.raises(DestinationError) as bad:
        dest.put(src, "j-1/shared/0002-full.tar.gz", "ignored", 7)
    assert bad.value.code == "UPLOAD_VERIFY_FAILED"
    assert set(sftp.files) == {"bk/j-1/shared/0001-full.tar.gz"}  # the partial one is removed
    dest.delete("j-1/shared/0001-full.tar.gz")
    dest.delete("j-1/shared/0001-full.tar.gz")  # already gone is fine
    assert sftp.files == {}


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.1.2.3", "192.168.0.9", "169.254.169.254", "::1", "fe80::1"],
)
def test_internal_addresses_are_refused_unless_the_operator_allows_them(
    address: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        destinations.socket,
        "getaddrinfo",
        lambda *a, **k: [(0, 0, 0, "", (address, 22))],
    )
    with pytest.raises(DestinationError) as blocked:
        destinations.resolve_checked("nas.example.test", 22, allow_private=False)
    assert blocked.value.code == "DESTINATION_ADDRESS_BLOCKED"
    assert destinations.resolve_checked("nas.example.test", 22, allow_private=True) == address


def test_public_addresses_pass_the_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        destinations.socket,
        "getaddrinfo",
        lambda *a, **k: [(0, 0, 0, "", ("93.184.216.34", 22))],
    )
    assert destinations.resolve_checked("example.test", 22, allow_private=False) == "93.184.216.34"


def test_a_job_without_stored_credentials_fails_cleanly(fx: Fx) -> None:
    job_id = uuid.uuid4()
    spec = JobSpec(
        job_id=job_id,
        name="x",
        enabled=True,
        interval_minutes=60,
        full_every=1,
        min_chains_kept=2,
        max_size_bytes=None,
        encrypt=True,
        destination_type="s3",
        destination={"bucket": "fc-test-bucket"},
    )
    with pytest.raises(DestinationError) as err:
        destinations.open_destination(spec, fx.w.settings)
    assert err.value.code == "CREDENTIALS_MISSING"
    sealed = seal_credentials(fx.w.settings, job_id, {"k": "TESTONLY-value"})
    assert b"TESTONLY-value" not in sealed and sealed.startswith(b"FS1")


# ------------------------------------------------------------------ review fixes (M27)


def test_unencrypted_jobs_never_back_up_personal_folders(fx: Fx) -> None:
    upload(fx, "private.txt", b"alice-private", fx.taxes)
    label(fx, fx.taxes, "private")
    job = make_job(fx)  # unencrypted, covers everything
    assert "personal_skipped" in fx.admin[0].get(JOBS, headers=fx.admin[1]).json()[0]["warnings"]
    result = go(fx, job, fx.alice_root)
    assert (result.status, result.code) == ("skipped", "UNENCRYPTED_PERSONAL_SKIPPED")
    # allow_confidential does not open personal folders either
    wide = make_job(fx, name="Wide", allow_confidential=True, acknowledge_confidential=True)
    assert go(fx, wide, fx.alice_root).code == "UNENCRYPTED_PERSONAL_SKIPPED"
    assert not archives_of(fx, job) and not archives_of(fx, wide)
    only = create(fx, name="Personal only", scope="personal")
    assert (only.status_code, code(only)) == (422, "UNENCRYPTED_PERSONAL_NOT_ALLOWED")


def test_remote_and_unencrypted_jobs_need_fresh_reauthentication(fx: Fx) -> None:
    c, h = fx.admin
    r = c.post(JOBS, json={k: v for k, v in body().items() if k != "password"}, headers=h)
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "PASSWORD_REQUIRED")
    wrong = create(fx, password="not-the-password")
    assert wrong.status_code in (401, 403)
    assert not c.get(JOBS, headers=h).json()  # nothing was created
    enc = create(fx, name="Enc", encrypt=True, acknowledge_unencrypted=False, password=None)
    assert enc.status_code == 201  # a local encrypted job needs no extra confirmation
    # changing a remote job needs it too
    job = make_job(fx, name="Remote", **S3, acknowledge_remote_unencrypted=True)
    upd = {k: v for k, v in body(name="Remote", **S3, acknowledge_remote_unencrypted=True).items()}
    upd.pop("password")
    upd["credentials"] = None
    again = c.put(f"{JOBS}/{job}", json=upd, headers=h)
    assert (again.status_code, again.json()["detail"]["code"]) == (403, "PASSWORD_REQUIRED")


def test_stored_credentials_never_follow_a_changed_destination(fx: Fx) -> None:
    c, h = fx.admin
    job = make_job(fx, **S3, acknowledge_remote_unencrypted=True)
    base = body(**S3, acknowledge_remote_unencrypted=True)
    base["credentials"] = None
    for change in (
        {"bucket": "another-bucket"},
        {"endpoint_url": "https://s3.other.example"},
        {"region": "eu-west-1"},
        {"path": "elsewhere"},
    ):
        moved = {**base, "destination": {**S3["destination"], **change}}
        r = c.put(f"{JOBS}/{job}", json=moved, headers=h)
        assert (r.status_code, code(r)) == (422, "CREDENTIALS_REQUIRED"), change
    same = c.put(f"{JOBS}/{job}", json=base, headers=h)  # unchanged destination keeps them
    assert same.status_code == 200 and same.json()["credentials_set"] is True
    sftp = make_job(
        fx,
        name="sftp",
        destination_type="sftp",
        destination={"host": "a.example.test", "username": "fc", "path": "bk"},
        credentials={"password": SFTP_PASSWORD},
        acknowledge_remote_unencrypted=True,
    )
    other_host = body(
        name="sftp",
        destination_type="sftp",
        destination={"host": "evil.example.test", "username": "fc", "path": "bk"},
        credentials=None,
        acknowledge_remote_unencrypted=True,
    )
    r = c.put(f"{JOBS}/{sftp}", json=other_host, headers=h)
    assert (r.status_code, code(r)) == (422, "CREDENTIALS_REQUIRED")


def test_destination_cannot_change_while_the_job_holds_backups(fx: Fx, remote: FakeDest) -> None:
    c, h = fx.admin
    upload(fx, "a.txt")
    job = make_job(fx, **S3, acknowledge_remote_unencrypted=True)
    assert go(fx, job).status == "ok"
    to_local = body(acknowledge_remote_unencrypted=True)
    r = c.put(f"{JOBS}/{job}", json=to_local, headers=h)
    assert (r.status_code, code(r)) == (409, "DESTINATION_LOCKED")
    other = {**body(**S3, acknowledge_remote_unencrypted=True)}
    other["destination"] = {**S3["destination"], "bucket": "another-bucket"}
    r = c.put(f"{JOBS}/{job}", json=other, headers=h)
    assert (r.status_code, code(r)) == (409, "DESTINATION_LOCKED")
    # credentials alone may be rotated; a job without backups may be moved freely
    rotate = body(**S3, acknowledge_remote_unencrypted=True)
    rotate["credentials"] = {"access_key_id": "AKIANEWKEY", "secret_access_key": "new-secret"}
    assert c.put(f"{JOBS}/{job}", json=rotate, headers=h).status_code == 200
    fresh = make_job(fx, name="Fresh", **S3, acknowledge_remote_unencrypted=True)
    moved = body(name="Fresh", acknowledge_remote_unencrypted=True)
    assert c.put(f"{JOBS}/{fresh}", json=moved, headers=h).status_code == 200


def test_sftp_private_keys_with_newlines_and_exact_passwords_are_accepted() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    pem = (
        Ed25519PrivateKey.generate()
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
        .decode()
    )
    assert "\n" in pem
    from app.backup.jobs import clean_credentials

    assert clean_credentials("sftp", {"private_key": pem}) == {"private_key": pem}
    spaced = " pass word "  # not stripped
    assert clean_credentials("sftp", {"password": spaced}) == {"password": spaced}
    for bad in ({"password": "a\x00b"}, {"password": "line\nbreak"}, {}):
        with pytest.raises(JobError):
            clean_credentials("sftp", bad)
    with pytest.raises(JobError):
        clean_credentials("s3", {"access_key_id": "AKIA", "secret_access_key": ""})


def test_private_key_job_is_stored_and_listed_without_the_key(fx: Fx) -> None:
    kind = "OPENSSH " + "PRIVATE KEY"  # built at run time: the secret scanner must not match
    pem = "\n".join([f"-----BEGIN {kind}-----", "TESTONLY-KEY-BODY", f"-----END {kind}-----", ""])
    r = create(
        fx,
        destination_type="sftp",
        destination={"host": "a.example.test", "username": "fc"},
        credentials={"private_key": pem},
        acknowledge_remote_unencrypted=True,
    )
    assert r.status_code == 201, r.text
    assert "TESTONLY-KEY-BODY" not in r.text + fx.admin[0].get(JOBS, headers=fx.admin[1]).text
    assert "TESTONLY-KEY-BODY" not in audit_text(fx)


def test_region_is_validated_and_construction_errors_have_fixed_codes(
    fx: Fx, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = {**S3, "destination": {**S3["destination"], "region": "us east/1"}}
    r = create(fx, **bad, acknowledge_remote_unencrypted=True)
    assert (r.status_code, code(r)) == (422, "DESTINATION_INVALID")

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("secret host name leaked in a library message")

    monkeypatch.setattr(destinations, "S3Destination", boom)
    job = make_job(fx, **S3, acknowledge_remote_unencrypted=True)
    c, h = fx.admin
    t = c.post(f"{JOBS}/{job}/test", headers=h)
    assert (t.status_code, t.json()["detail"]["code"]) == (409, "DESTINATION_FAILED")
    assert "leaked" not in t.text


def test_restore_chain_never_mixes_two_jobs_with_the_same_chain_id(fx: Fx) -> None:
    upload(fx, "a.txt")
    one, two = make_job(fx), make_job(fx, name="Second")
    assert go(fx, one).status == "ok" and go(fx, two).status == "ok"
    first = job_runs(fx, one)[0]
    with fx.w.ctx.sessions() as db:
        for row in db.scalars(select(BackupRun).where(BackupRun.job_id == two)):
            row.chain_id = first.chain_id  # a collision
        db.commit()
        target = db.get(BackupRun, first.id)
        assert target is not None
        chain = restore_mod.chain_for(db, target.root_id, target)
    assert [r.job_id for r in chain] == [one]


@pytest.mark.parametrize(
    "address",
    ["100.64.0.1", "100.127.255.254", "::ffff:127.0.0.1", "64:ff9b::7f00:1", "2002:7f00:1::1"],
)
def test_cgnat_and_embedded_internal_addresses_are_refused(address: str) -> None:
    assert destinations._blocked(address) is True  # noqa: SLF001


def test_global_addresses_are_allowed() -> None:
    assert destinations._blocked("93.184.216.34") is False  # noqa: SLF001
    assert destinations._blocked("2606:4700:4700::1111") is False  # noqa: SLF001


def test_s3_endpoint_must_be_a_plain_https_origin_and_requests_are_rechecked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = JobSpec(
        job_id=uuid.uuid4(),
        name="x",
        enabled=True,
        interval_minutes=60,
        full_every=1,
        min_chains_kept=2,
        max_size_bytes=None,
        encrypt=True,
        destination_type="s3",
        destination={"bucket": "fc-test-bucket", "endpoint_url": "https://user:pw@s3.example.test"},
    )
    creds = {"access_key_id": "AKIATESTONLY", "secret_access_key": "x"}
    for endpoint in (
        "https://user:pw@s3.example.test",
        "https://s3.example.test/path?x=1",
        "https://s3.example.test/#frag",
        "http://s3.example.test",
    ):
        s = JobSpec(
            **{**spec.__dict__, "destination": {**spec.destination, "endpoint_url": endpoint}}
        )
        with pytest.raises(DestinationError) as bad:
            S3Destination(s, creds, allow_private=True)
        assert bad.value.code == "DESTINATION_INVALID"
    # a name that resolves inside after the first check is refused when the request is sent
    answers = iter([[(0, 0, 0, "", ("93.184.216.34", 443))], [(0, 0, 0, "", ("10.0.0.5", 443))]])
    monkeypatch.setattr(destinations.socket, "getaddrinfo", lambda *a, **k: next(answers))
    ok = JobSpec(
        **{
            **spec.__dict__,
            "destination": {"bucket": "fc-test-bucket", "endpoint_url": "https://s3.example.test"},
        }
    )
    dest = S3Destination(ok, creds, allow_private=False)
    request = type("R", (), {"url": "https://s3.example.test/fc-test-bucket/key"})()
    with pytest.raises(DestinationError) as rebound:
        destinations._guard_request(request)  # noqa: SLF001
    assert rebound.value.code == "DESTINATION_ADDRESS_BLOCKED"
    dest.close()


def test_default_job_usage_ignores_only_real_job_folders(tmp_path: Path) -> None:
    from app.backup.volume import folder_usage

    for name, size in (("j-0123abcd", 100), ("j-my-root", 10), ("shared", 1)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "f").write_bytes(b"x" * size)
    assert folder_usage(tmp_path) == 111
    assert folder_usage(tmp_path, skip_jobs=True) == 11  # a root folder named j-... still counts
