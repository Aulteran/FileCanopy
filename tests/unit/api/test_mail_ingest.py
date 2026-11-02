"""M14: filing verified mail, notices, retries, archive limits and quarantine approval.

Same fake mailbox as the M13 tests; the library is real files in the test world's temp folder.
"""

import gzip
import io
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from test_acl import Fx
from test_auth import World
from test_files import Api, code
from test_mail import (
    AUTHSERV,
    IMAP,
    INGEST,
    PERSONAL,
    FakeBox,
    build,
    mail_world,
    messages,
    only,
    passing,
    run_poll,
    verify_alias,
)

from app.db.models import (
    AuthAudit,
    FileEvent,
    FileOperation,
    InboundAttachment,
    InboundMessage,
    Node,
    NodeGrant,
    Notification,
    StorageRoot,
    User,
    UserEmailAlias,
)
from app.mail import archives, authres, ingest
from app.mail import poller as poller_module


@pytest.fixture
def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., World]:
    def factory(**overrides: Any) -> World:
        return World(tmp_path, monkeypatch, **overrides)

    return factory


@pytest.fixture
def fx(make_world: Callable[..., World]) -> Fx:
    return Fx(mail_world(make_world, **IMAP))


def notes(fx: Fx, email: str, *kinds: str) -> list[Notification]:
    uid = fx.uid(email)
    return [
        n
        for n in fx.w.db_rows(Notification)
        if n.recipient_user_id == uid and (not kinds or n.kind in kinds)
    ]


def mail_notes(fx: Fx, email: str) -> list[Notification]:
    return notes(fx, email, *sorted(ingest.notices.MAIL_KINDS))


def inbox_files(fx: Fx, who: str) -> list[str]:
    api = Api(fx, who)
    top = next(
        c for c in api.children(fx.root_of(f"{who}@example.com")) if c["name"] == "Email inbox"
    )
    return sorted(c["name"] for c in api.children(uuid.UUID(top["id"])))


def zip_of(files: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def attachments_of(m: InboundMessage, fx: Fx) -> list[InboundAttachment]:
    return [a for a in fx.w.db_rows(InboundAttachment) if a.message_id == m.id]


def pause_filing(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = SimpleNamespace(process_pending=lambda *a, **k: 0)
    monkeypatch.setattr(poller_module, "ingest", stub)


def file_now(fx: Fx) -> int:
    return ingest.process_pending(fx.w.ctx.sessions, fx.w.settings)


# ------------------------------------------------------------------ filing like an upload


def test_personal_mail_is_filed_like_an_upload(fx: Fx) -> None:
    run_poll(fx.w, FakeBox({1: build(attachments=[("scan.pdf", b"%PDF-1.4 hello")])}))
    m = only(fx)
    assert (m.processing_state, m.filed_count, m.process_error) == ("filed", 1, None)
    assert inbox_files(fx, "alice") == ["scan.pdf"]
    alice = Api(fx, "alice")
    folder = m.filed_node_id
    assert folder is not None
    (child,) = alice.children(folder)
    assert alice.download(uuid.UUID(child["id"])).content == b"%PDF-1.4 hello"
    # the same journal and history rows as a web upload, history says it came by email
    assert [o.action for o in fx.w.db_rows(FileOperation) if o.action == "upload"] == ["upload"]
    events = [e for e in fx.w.db_rows(FileEvent) if e.action == "uploaded"]
    assert [(e.source, e.actor_user_id) for e in events] == [("email", alice.uid)]
    # nothing else in anyone else's tree
    bob = Api(fx, "bob")
    assert all(c["name"] != "Email inbox" for c in bob.children(fx.root_of("bob@example.com")))
    # staged bytes are gone
    assert not (fx.w.settings.app_data_path / "mail-quarantine" / str(m.id)).exists()
    assert all(a.node_id is not None and a.outcome == "filed" for a in attachments_of(m, fx))


def test_names_are_sanitised_and_collisions_keep_both(fx: Fx) -> None:
    raw = build(
        attachments=[
            ("../../etc/passwd", b"1"),
            ("a.pdf", b"2"),
            ("A.pdf", b"3"),
            ("CON.txt", b"4"),
        ]
    )
    run_poll(fx.w, FakeBox({1: raw}))
    names = inbox_files(fx, "alice")
    assert len(names) == 4 and all("/" not in n and not n.startswith(".") for n in names)
    run_poll(fx.w, FakeBox({2: build(attachments=[("a.pdf", b"other")])}, uidvalidity=8))
    assert len(inbox_files(fx, "alice")) == 5  # "a (2).pdf" style, nothing overwritten


def test_two_aliases_for_one_account_file_to_the_same_person(fx: Fx) -> None:
    alice = Api(fx, "alice")
    verify_alias(alice, "alice@work.example.net")
    verify_alias(alice, "alice@home.example.net")
    box = FakeBox(
        {
            1: build(frm="alice@work.example.net", auth=passing("work.example.net")),
            2: build(
                frm="alice@home.example.net",
                auth=passing("home.example.net"),
                attachments=[("home.pdf", b"h")],
            ),
            3: build(frm="alice@example.com", attachments=[("main.pdf", b"m")]),
        }
    )
    run_poll(fx.w, box)
    assert {m.resolved_user_id for m in messages(fx)} == {alice.uid}
    assert inbox_files(fx, "alice") == ["home.pdf", "main.pdf", "scan.pdf"]
    # and nobody else's folders changed
    assert all(
        c["name"] != "Email inbox"
        for who in ("bob", "carol")
        for c in Api(fx, who).children(fx.root_of(f"{who}@example.com"))
    )


def test_two_senders_stay_in_their_own_trees(fx: Fx) -> None:
    run_poll(
        fx.w,
        FakeBox(
            {
                1: build(frm="alice@example.com", attachments=[("a.pdf", b"a")]),
                2: build(frm="bob@example.com", attachments=[("b.pdf", b"b")]),
            }
        ),
    )
    assert inbox_files(fx, "alice") == ["a.pdf"]
    assert inbox_files(fx, "bob") == ["b.pdf"]


def test_a_message_without_attachments_files_nothing(fx: Fx) -> None:
    run_poll(fx.w, FakeBox({1: build(attachments=[])}))
    m = only(fx)
    assert (m.processing_state, m.filed_count) == ("filed", 0)
    assert not mail_notes(fx, "alice@example.com")
    assert not [n for n in fx.w.db_rows(Node) if n.name == "Email inbox"]


def test_replayed_mail_is_filed_once(fx: Fx) -> None:
    raw = build()
    box = FakeBox({1: raw, 2: raw})
    run_poll(fx.w, box)
    run_poll(fx.w, box)
    file_now(fx)
    assert inbox_files(fx, "alice") == ["scan.pdf"]
    assert sorted(m.status for m in messages(fx)) == ["duplicate", "verified"]


def test_existing_intake_name_is_reused_and_a_file_blocks_it(fx: Fx) -> None:
    alice = Api(fx, "alice")
    root = fx.root_of("alice@example.com")
    assert alice.mkdir(root, "email INBOX").status_code == 201  # case variant is the same folder
    run_poll(fx.w, FakeBox({1: build()}))
    only(fx)
    names = [c["name"] for c in alice.children(root)]
    assert names.count("email INBOX") == 1 and "Email inbox" not in names

    # a file where the folder should be
    bob = Api(fx, "bob")
    assert bob.upload(fx.root_of("bob@example.com"), "Email inbox", b"file").status_code == 201
    run_poll(fx.w, FakeBox({2: build(frm="bob@example.com")}, uidvalidity=9))
    m = [r for r in messages(fx) if r.from_address == "bob@example.com"][0]
    assert (m.processing_state, m.process_error) == ("failed", "INTAKE_UNAVAILABLE")


# ------------------------------------------------------------------ shared intake


def set_intake(fx: Fx, node: uuid.UUID | None, who: tuple[Any, Any] | None = None) -> Any:
    client, h = who or fx.admin
    return client.put(
        "/api/v1/admin/mailbox/shared-intake",
        json={"node_id": str(node) if node else None},
        headers=h,
    )


def test_shared_mail_waits_for_an_intake_then_files(fx: Fx) -> None:
    run_poll(fx.w, FakeBox({1: build(to=INGEST, attachments=[("minutes.pdf", b"m")])}))
    m = only(fx)
    assert (m.processing_state, m.process_error) == ("failed", "SHARED_INTAKE_UNSET")
    assert [n.detail for n in notes(fx, "alice@example.com", "mail_failed")] == [
        "SHARED_INTAKE_UNSET"
    ]
    for admin in ("admin@example.com", "admin2@example.com"):
        assert [n.count for n in notes(fx, admin, "mail_intake_unset")] == [1]
    assert not notes(fx, "bob@example.com")

    r = set_intake(fx, fx.shared_sub)
    assert r.status_code == 200 and r.json()["usable"] is True and r.json()["name"] == "Family"
    file_now(fx)
    m = only(fx)
    assert (m.processing_state, m.filed_count, m.filed_node_id) == ("filed", 1, fx.shared_sub)
    alice = Api(fx, "alice")
    assert [c["name"] for c in alice.children(fx.shared_sub)] == ["minutes.pdf"]


def test_shared_notices_go_to_uploader_and_admins_only_without_names(fx: Fx) -> None:
    assert set_intake(fx, fx.shared_sub).status_code == 200
    run_poll(
        fx.w,
        FakeBox({1: build(to=INGEST, attachments=[("secret-name.pdf", b"s"), ("b.pdf", b"b")])}),
    )
    assert only(fx).processing_state == "filed"
    (mine,) = notes(fx, "alice@example.com", "mail_filed")
    assert (mine.count, mine.node_id) == (2, fx.shared_sub)
    for admin in ("admin@example.com", "admin2@example.com"):
        (n,) = notes(fx, admin, "mail_shared")
        assert (n.count, n.node_id) == (2, fx.shared_sub)
    assert not mail_notes(fx, "bob@example.com") and not mail_notes(fx, "carol@example.com")
    blob = str([(n.kind, n.detail, n.level) for n in fx.w.db_rows(Notification)])
    assert "secret-name" not in blob and "example.com" not in blob


def test_an_admin_who_cannot_open_the_folder_sees_no_name(fx: Fx) -> None:
    assert set_intake(fx, fx.shared_sub).status_code == 200
    run_poll(fx.w, FakeBox({1: build(to=INGEST)}))
    admin2 = Api(fx, "admin2")
    shown = admin2.client.get("/api/v1/notifications").json()["items"]
    assert [(i["kind"], i["node_name"], i["count"]) for i in shown] == [
        ("mail_shared", "Family", 1)
    ]
    with fx.w.ctx.sessions() as db:
        db.execute(
            NodeGrant.__table__.delete().where(
                NodeGrant.grantee_user_id == fx.uid("admin2@example.com")
            )
        )
        db.commit()
    shown = admin2.client.get("/api/v1/notifications").json()["items"]
    assert [(i["kind"], i["node_name"], i["node_id"]) for i in shown] == [
        ("mail_shared", None, None)
    ]


def test_sender_needs_write_access_to_the_shared_intake(fx: Fx) -> None:
    assert set_intake(fx, fx.shared_sub).status_code == 200
    with fx.w.ctx.sessions() as db:
        for g in db.scalars(
            select(NodeGrant).where(NodeGrant.grantee_user_id == fx.uid("bob@example.com"))
        ):
            g.level = "viewer"
        db.commit()
    run_poll(fx.w, FakeBox({1: build(frm="bob@example.com", to=INGEST)}))
    m = only(fx)
    assert (m.processing_state, m.process_error) == ("failed", "INTAKE_FORBIDDEN")
    assert Api(fx, "bob").children(fx.shared_sub) == []
    # only the sender hears about it, and administrators learn of no failure for known mail
    assert [n.detail for n in notes(fx, "bob@example.com", "mail_failed")] == ["INTAKE_FORBIDDEN"]
    assert not notes(fx, "admin@example.com", "mail_failed")


def test_only_a_writable_shared_folder_can_be_the_intake(fx: Fx) -> None:
    alice = Api(fx, "alice")
    assert set_intake(fx, fx.shared_sub, (alice.client, alice.headers)).status_code == 403
    assert set_intake(fx, fx.alice_root).status_code == 403  # a personal root
    assert set_intake(fx, fx.private).status_code == 403
    assert set_intake(fx, uuid.uuid4()).status_code == 403  # same answer as for a real one
    with fx.w.ctx.sessions() as db:
        db.execute(
            NodeGrant.__table__.delete().where(
                NodeGrant.grantee_user_id == fx.uid("admin@example.com")
            )
        )
        db.commit()
    assert set_intake(fx, fx.shared_sub).status_code == 403  # the admin cannot write there
    admin, h = fx.admin
    assert admin.get("/api/v1/admin/mailbox/shared-intake").json()["node_id"] is None


def test_clearing_the_intake(fx: Fx) -> None:
    assert set_intake(fx, fx.shared_sub).status_code == 200
    r = set_intake(fx, None)
    assert r.status_code == 200 and r.json()["node_id"] is None
    run_poll(fx.w, FakeBox({1: build(to=INGEST)}))
    assert only(fx).process_error == "SHARED_INTAKE_UNSET"


# ------------------------------------------------------------------ identity at processing time


def test_alias_removed_before_filing_stops_it(fx: Fx, monkeypatch: pytest.MonkeyPatch) -> None:
    alice = Api(fx, "alice")
    verify_alias(alice, "alice@work.example.net")
    pause_filing(monkeypatch)
    run_poll(
        fx.w,
        FakeBox({1: build(frm="alice@work.example.net", auth=passing("work.example.net"))}),
    )
    assert only(fx).processing_state == "pending"
    with fx.w.ctx.sessions() as db:
        db.execute(UserEmailAlias.__table__.delete())
        db.commit()
    assert file_now(fx) == 1
    m = only(fx)
    assert (m.status, m.reason, m.processing_state, m.resolved_user_id) == (
        "quarantined",
        "SENDER_CHANGED",
        None,
        None,
    )
    # personal mail from an unrecognised sender keeps no bytes
    assert m.content_state == "purged"
    assert not [n for n in fx.w.db_rows(Node) if n.name == "Email inbox"]
    assert notes(fx, "admin@example.com", "mail_quarantine")


def test_alias_that_moved_to_another_account_files_nowhere(
    fx: Fx, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice, bob = Api(fx, "alice"), Api(fx, "bob")
    verify_alias(alice, "shared@work.example.net")
    pause_filing(monkeypatch)
    run_poll(
        fx.w,
        FakeBox({1: build(frm="shared@work.example.net", auth=passing("work.example.net"))}),
    )
    with fx.w.ctx.sessions() as db:
        db.execute(UserEmailAlias.__table__.delete())
        db.commit()
    verify_alias(bob, "shared@work.example.net")
    file_now(fx)
    assert only(fx).status == "quarantined"
    assert not [n for n in fx.w.db_rows(Node) if n.name == "Email inbox"]
    assert not mail_notes(fx, "alice@example.com") and not mail_notes(fx, "bob@example.com")


def test_disabled_account_is_not_filed_for(fx: Fx, monkeypatch: pytest.MonkeyPatch) -> None:
    pause_filing(monkeypatch)
    run_poll(fx.w, FakeBox({1: build()}))
    with fx.w.ctx.sessions() as db:
        alice = db.get(User, fx.uid("alice@example.com"))
        assert alice is not None
        alice.status = "disabled"
        db.commit()
    file_now(fx)
    assert only(fx).status == "quarantined"
    assert not [n for n in fx.w.db_rows(Node) if n.name == "Email inbox"]


def test_shared_mail_of_a_changed_sender_keeps_its_bytes_for_triage(
    fx: Fx, monkeypatch: pytest.MonkeyPatch
) -> None:
    pause_filing(monkeypatch)
    run_poll(fx.w, FakeBox({1: build(to=INGEST)}))
    with fx.w.ctx.sessions() as db:
        alice = db.get(User, fx.uid("alice@example.com"))
        assert alice is not None
        alice.status = "disabled"
        db.commit()
    file_now(fx)
    m = only(fx)
    assert (m.status, m.content_state) == ("quarantined", "stored")


# ------------------------------------------------------------------ retries and dead letters


def test_transient_failures_retry_then_dead_letter(fx: Fx, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def boom(*a: Any, **k: Any) -> None:
        calls.append(1)
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(ingest, "_file_all", boom)
    run_poll(fx.w, FakeBox({1: build()}))
    m = only(fx)
    assert (m.processing_state, m.process_attempts) == ("pending", 1)
    assert file_now(fx) == 0  # not due yet
    for expected in (2, 3, 4, 5):
        fx.w.advance(hours=2)
        file_now(fx)
        assert only(fx).process_attempts == expected
    m = only(fx)
    assert (m.processing_state, m.process_error) == ("failed", "PROCESSING_FAILED")
    assert len(calls) == 5
    assert [n.detail for n in notes(fx, "alice@example.com", "mail_failed")] == [
        "PROCESSING_FAILED"
    ]
    assert "mail.dead_letter" in [a.action for a in fx.w.db_rows(AuthAudit)]
    fx.w.advance(hours=3)
    file_now(fx)
    assert len(calls) == 5 and len(notes(fx, "alice@example.com", "mail_failed")) == 1  # no spam

    # the sender may retry once; staged bytes were kept
    monkeypatch.undo()
    alice = Api(fx, "alice")
    mine = alice.client.get("/api/v1/me/inbox").json()
    assert mine[0]["can_retry"] is True and mine[0]["processing_state"] == "failed"
    r = alice.client.post(f"/api/v1/me/inbox/{m.id}/retry", headers=alice.headers)
    assert r.status_code == 202
    file_now(fx)
    assert only(fx).processing_state == "filed"
    assert inbox_files(fx, "alice") == ["scan.pdf"]
    assert (
        code(alice.client.post(f"/api/v1/me/inbox/{m.id}/retry", headers=alice.headers))
        == "NOT_RETRYABLE"
    )


def test_retry_is_the_senders_alone(fx: Fx, monkeypatch: pytest.MonkeyPatch) -> None:
    run_poll(fx.w, FakeBox({1: build(to=INGEST)}))  # fails: no intake yet
    m = only(fx)
    bob = Api(fx, "bob")
    assert bob.client.post(f"/api/v1/me/inbox/{m.id}/retry", headers=bob.headers).status_code == 404
    admin, h = fx.admin
    assert admin.post(f"/api/v1/me/inbox/{m.id}/retry", headers=h).status_code == 404


def test_crash_between_upload_and_bookkeeping_does_not_duplicate(
    fx: Fx, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = ingest.file_attachment
    state = {"armed": True}

    def flaky(*a: Any, **k: Any) -> Any:
        out = real(*a, **k)
        if state["armed"]:
            state["armed"] = False
            raise RuntimeError("crash after the upload committed")
        return out

    monkeypatch.setattr(ingest, "file_attachment", flaky)
    run_poll(fx.w, FakeBox({1: build()}))
    assert only(fx).processing_state == "pending"
    fx.w.advance(hours=1)
    file_now(fx)
    assert only(fx).processing_state == "filed"
    assert inbox_files(fx, "alice") == ["scan.pdf"]  # one file, no "scan (2).pdf"


def test_a_claimed_message_that_never_finishes_is_not_taken_twice(
    fx: Fx, monkeypatch: pytest.MonkeyPatch
) -> None:
    pause_filing(monkeypatch)
    run_poll(fx.w, FakeBox({1: build()}))
    mid = only(fx).id
    with fx.w.ctx.sessions() as db:
        assert ingest._claim(db, mid) is True
    with fx.w.ctx.sessions() as db:
        assert ingest._claim(db, mid) is False  # leased


def test_daily_cap_per_sender(make_world: Callable[..., World]) -> None:
    fx = Fx(mail_world(make_world, **IMAP, mail_daily_messages_per_sender=2))
    run_poll(fx.w, FakeBox({i: build(attachments=[(f"{i}.pdf", b"x")]) for i in (1, 2, 3)}))
    states = sorted((m.uid, m.processing_state, m.process_error) for m in messages(fx))
    assert states == [(1, "filed", None), (2, "filed", None), (3, "failed", "RATE_LIMITED")]


def test_full_staging_quota_fails_verified_mail_with_a_clear_reason(
    make_world: Callable[..., World],
) -> None:
    fx = Fx(mail_world(make_world, **IMAP, mail_quarantine_quota_bytes=10))
    run_poll(fx.w, FakeBox({1: build(attachments=[("big.pdf", b"x" * 100)])}))
    m = only(fx)
    assert (m.processing_state, m.process_error, m.content_state) == (
        "failed",
        "STORAGE_FULL",
        "dropped",
    )


# ------------------------------------------------------------------ untrusted attachments


def test_archive_limits_unit(tmp_path: Path) -> None:
    limits = archives.ArchiveLimits(max_entries=5, max_uncompressed_bytes=10_000_000, max_ratio=100)

    def check(data: bytes, name: str = "a.bin") -> str | None:
        p = tmp_path / "f"
        p.write_bytes(data)
        try:
            archives.check(p, name, limits)
        except archives.ArchiveRefused as exc:
            return exc.code
        return None

    assert check(b"%PDF-1.4 plain") is None
    assert check(zip_of({"a.txt": b"hello", "b.txt": b"world"})) is None
    assert check(zip_of({f"{i}.txt": b"x" for i in range(6)})) == "ARCHIVE_TOO_MANY_ENTRIES"
    assert check(zip_of({"zeros": b"\0" * 5_000_000})) == "ARCHIVE_RATIO"
    assert (
        check(zip_of({"a": b"\0" * 9_000_000, "b": b"\0" * 9_000_000}, zipfile.ZIP_STORED))
        == "ARCHIVE_TOO_LARGE"
    )
    assert check(zip_of({"inner.zip": zip_of({"a": b"x"})})) == "ARCHIVE_NESTED"
    assert check(zip_of({"renamed.dat": zip_of({"a": b"x"})})) == "ARCHIVE_NESTED"
    assert check(b"PK\x03\x04 not really a zip") == "ARCHIVE_INVALID"
    assert check(gzip.compress(b"\0" * 5_000_000)) == "ARCHIVE_RATIO"
    assert check(gzip.compress(b"fine text " * 10)) is None
    assert check(gzip.compress(zip_of({"a": b"x"}))) == "ARCHIVE_NESTED"
    assert check(b"7z\xbc\xaf\x27\x1c" + b"\0" * 20) is None  # opaque: stored, never opened


def test_hostile_attachments_do_not_stop_the_good_ones(fx: Fx) -> None:
    bomb = zip_of({"zeros": b"\0" * 5_000_000})
    nested = zip_of({"inner.zip": zip_of({"a": b"x"})})
    spoof = b"MZ\x90\x00 pretending to be a pdf"
    raw = build(
        attachments=[
            ("bomb.zip", bomb),
            ("nested.zip", nested),
            ("invoice.pdf.exe", spoof),
            ("report.docx", zip_of({"word/document.xml": b"<w/>"})),
            ("../../../etc/cron.d/evil", b"x"),
        ]
    )
    run_poll(fx.w, FakeBox({1: raw}))
    m = only(fx)
    assert (m.processing_state, m.filed_count) == ("partial", 3)
    outcomes = {a.display_name: a.outcome for a in attachments_of(m, fx)}
    assert outcomes["bomb.zip"] == "ARCHIVE_RATIO" and outcomes["nested.zip"] == "ARCHIVE_NESTED"
    names = inbox_files(fx, "alice")
    assert "report.docx" in names and "invoice.pdf.exe" in names and "bomb.zip" not in names
    assert all("/" not in n for n in names)
    (refused,) = notes(fx, "alice@example.com", "mail_refused")
    assert (refused.count, refused.detail) == (2, "ARCHIVE_RATIO")
    assert notes(fx, "alice@example.com", "mail_filed")[0].count == 3
    # the stored file is exactly what was sent, never executed or rewritten
    api = Api(fx, "alice")
    folder = m.filed_node_id
    assert folder is not None
    data = {c["name"]: api.download(uuid.UUID(c["id"])).content for c in api.children(folder)}
    assert data["invoice.pdf.exe"] == spoof


def test_a_message_of_only_refused_attachments_fails_once(fx: Fx) -> None:
    run_poll(
        fx.w, FakeBox({1: build(attachments=[("bomb.zip", zip_of({"z": b"\0" * 5_000_000}))])})
    )
    m = only(fx)
    assert (m.processing_state, m.process_error) == ("failed", "ATTACHMENTS_REFUSED")
    assert [n.detail for n in notes(fx, "alice@example.com", "mail_failed")] == [
        "ATTACHMENTS_REFUSED"
    ]
    alice = Api(fx, "alice")
    assert alice.client.get("/api/v1/me/inbox").json()[0]["can_retry"] is False


def test_oversized_attachment_is_refused_by_the_upload_cap(
    make_world: Callable[..., World],
) -> None:
    fx = Fx(mail_world(make_world, **IMAP, max_upload_bytes=1000))
    run_poll(
        fx.w,
        FakeBox({1: build(attachments=[("big.pdf", b"x" * 5000), ("ok.pdf", b"fine")])}),
    )
    m = only(fx)
    assert m.processing_state == "partial"
    assert {a.display_name: a.outcome for a in attachments_of(m, fx)}["big.pdf"] == "TOO_LARGE"


# ------------------------------------------------------------------ quarantine and approval


def quarantined(fx: Fx, to: str = INGEST, **kw: Any) -> InboundMessage:
    uid = len(messages(fx)) + 1
    run_poll(
        fx.w,
        FakeBox({uid: build(frm="stranger@example.com", to=to, **kw)}, uidvalidity=50 + uid),
    )
    return [m for m in messages(fx) if m.uid == uid][0]


def test_quarantine_notice_merges_into_one_unread_count(fx: Fx) -> None:
    for _ in range(3):
        quarantined(fx)
    for admin in ("admin@example.com", "admin2@example.com"):
        (n,) = notes(fx, admin, "mail_quarantine")
        assert n.count == 3
    assert not mail_notes(fx, "alice@example.com") and not mail_notes(fx, "bob@example.com")


def test_personal_destination_quarantine_keeps_metadata_only(fx: Fx) -> None:
    m = quarantined(fx, to=PERSONAL, attachments=[("tax-return.pdf", b"private")])
    assert (m.status, m.content_state) == ("quarantined", "none")
    assert all(a.stored_name is None for a in attachments_of(m, fx))
    assert not (fx.w.settings.app_data_path / "mail-quarantine" / str(m.id)).exists()
    # unknown destination too
    m2 = quarantined(fx, to="somebody@example.org")
    assert m2.content_state == "none"
    # and nothing an administrator could approve
    admin, h = fx.admin
    att = attachments_of(m, fx)[0]
    assert set_intake(fx, fx.shared_sub).status_code == 200
    r = admin.post(
        f"/api/v1/admin/quarantine/{m.id}/approve",
        json={"destination_id": str(fx.shared_sub), "attachment_ids": [str(att.id)]},
        headers=h,
    )
    assert r.status_code == 409 and code(r) == "CONTENT_NOT_KEPT"


def approve(fx: Fx, m: InboundMessage, dest: uuid.UUID, who: tuple[Any, Any] | None = None) -> Any:
    client, h = who or fx.admin
    ids = [str(a.id) for a in attachments_of(m, fx)]
    return client.post(
        f"/api/v1/admin/quarantine/{m.id}/approve",
        json={"destination_id": str(dest), "attachment_ids": ids},
        headers=h,
    )


def test_admin_approval_files_into_a_shared_folder_as_the_admin(fx: Fx) -> None:
    m = quarantined(fx, attachments=[("offer.pdf", b"%PDF offer")])
    assert m.content_state == "stored"
    r = approve(fx, m, fx.shared_sub)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved" and body["filed_count"] == 1
    api = Api(fx, "alice")
    (child,) = api.children(fx.shared_sub)
    assert child["name"] == "offer.pdf"
    events = [e for e in fx.w.db_rows(FileEvent) if e.action == "uploaded"]
    admin_id = fx.uid("admin@example.com")
    assert [(e.source, e.actor_user_id) for e in events] == [("email", admin_id)]
    assert not (fx.w.settings.app_data_path / "mail-quarantine" / str(m.id)).exists()
    assert "mail.quarantine_approved" in [a.action for a in fx.w.db_rows(AuthAudit)]
    # a second approval has nothing left to approve
    assert approve(fx, m, fx.shared_sub).status_code in (404, 409)


def test_approval_cannot_reach_a_personal_folder(fx: Fx) -> None:
    m = quarantined(fx, attachments=[("offer.pdf", b"x")])
    for personal in (fx.alice_root, fx.private, fx.root_of("admin@example.com")):
        r = approve(fx, m, personal)
        assert r.status_code == 403, personal
    assert approve(fx, m, uuid.uuid4()).status_code == 403
    # even where the administrator has been given a grant inside someone's personal tree
    alice = Api(fx, "alice")
    fx.grant((alice.client, alice.headers), fx.private, "admin@example.com", "editor")
    assert approve(fx, m, fx.private).status_code == 403
    assert Api(fx, "alice").children(fx.private) == []
    assert only_status(fx, m) == "quarantined"


def only_status(fx: Fx, m: InboundMessage) -> str:
    return [r for r in fx.w.db_rows(InboundMessage) if r.id == m.id][0].status


def test_approval_needs_an_administrator_with_write_access(fx: Fx) -> None:
    m = quarantined(fx, attachments=[("offer.pdf", b"x")])
    bob = Api(fx, "bob")
    assert approve(fx, m, fx.shared_sub, (bob.client, bob.headers)).status_code == 403
    with fx.w.ctx.sessions() as db:
        db.execute(
            NodeGrant.__table__.delete().where(
                NodeGrant.grantee_user_id == fx.uid("admin@example.com")
            )
        )
        db.commit()
    assert approve(fx, m, fx.shared_sub).status_code == 403  # admin role grants no folder access
    assert Api(fx, "alice").children(fx.shared_sub) == []


def test_approval_gives_the_unknown_sender_nothing(fx: Fx) -> None:
    m = quarantined(fx, attachments=[("offer.pdf", b"x")])
    assert approve(fx, m, fx.shared_sub).status_code == 200
    # no account, alias or grant appeared for the stranger; a later message is still held
    assert not fx.w.db_rows(UserEmailAlias)
    assert "stranger@example.com" not in [u.email for u in fx.w.db_rows(User)]
    again = quarantined(fx, attachments=[("again.pdf", b"y")])
    assert (again.status, again.reason) == ("quarantined", "SENDER_UNKNOWN")


def test_approval_applies_the_archive_limits_and_reject_still_works(fx: Fx) -> None:
    bomb = zip_of({"z": b"\0" * 5_000_000})
    m = quarantined(fx, attachments=[("bomb.zip", bomb), ("ok.pdf", b"ok")])
    r = approve(fx, m, fx.shared_sub)
    assert r.status_code == 200
    outcomes = {a["display_name"]: a["outcome"] for a in r.json()["attachments"]}
    assert outcomes == {"bomb.zip": "ARCHIVE_RATIO", "ok.pdf": "filed"}
    assert [c["name"] for c in Api(fx, "alice").children(fx.shared_sub)] == ["ok.pdf"]

    m2 = quarantined(fx, attachments=[("x.pdf", b"x")])
    admin, h = fx.admin
    r = admin.post(f"/api/v1/admin/quarantine/{m2.id}/reject", json={"confirm": True}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert approve(fx, m2, fx.shared_sub).status_code == 409


def test_admins_never_see_verified_mail_or_why_it_failed(fx: Fx) -> None:
    run_poll(fx.w, FakeBox({1: build(to=INGEST, attachments=[("private-looking.pdf", b"x")])}))
    m = only(fx)
    assert m.process_error == "SHARED_INTAKE_UNSET"
    admin, h = fx.admin
    assert admin.get("/api/v1/admin/quarantine").json() == []
    assert admin.get(f"/api/v1/admin/quarantine/{m.id}").status_code == 404
    status = admin.get("/api/v1/admin/mailbox").json()
    assert (status["waiting_for_intake"], status["failed_to_file"]) == (1, 1)
    assert "private-looking" not in str(status)
    assert [i["subject"] for i in Api(fx, "alice").client.get("/api/v1/me/inbox").json()] == [
        "Tax papers"
    ]


def test_personal_mail_failures_stay_between_the_sender_and_the_system(fx: Fx) -> None:
    # alice's folder is gone: filing fails for her, and no administrator is told anything
    with fx.w.ctx.sessions() as db:
        root = db.scalars(
            select(StorageRoot).where(StorageRoot.owner_user_id == fx.uid("alice@example.com"))
        ).one()
        root.status = "disabled"
        db.commit()
    run_poll(fx.w, FakeBox({1: build()}))
    m = only(fx)
    assert m.processing_state == "failed"
    assert notes(fx, "alice@example.com", "mail_failed")
    for admin in ("admin@example.com", "admin2@example.com"):
        assert not notes(fx, admin, "mail_failed", "mail_filed", "mail_shared", "mail_refused")


# ------------------------------------------------------------------ SPF follow-up


def test_smtp_mailfrom_must_be_an_address_under_the_aligned_policy() -> None:
    def ev(mailfrom: str) -> bool:
        header = f"{AUTHSERV}; dkim=pass header.d=example.com; spf=pass smtp.mailfrom={mailfrom}"
        return authres.evaluate([header], AUTHSERV, "aligned", "example.com").ok

    assert ev("bounce@example.com") is True
    assert ev("example.com") is False  # a bare domain no longer counts as aligned SPF
    assert ev("@example.com") is False
