"""M11 filename and full-text search: indexing, policy-filtered results, invalidation, retries."""

import json
import threading
import uuid
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from test_acl import Fx
from test_auth import PASSWORD, World
from test_files import Api, nid
from test_previews import encrypted_pdf, make_docx, make_pdf, make_png, put_optin
from test_privacy import label, unlock

from app.auth import clock
from app.db.models import Node, SearchDoc, SearchJob, StorageRoot
from app.preview import ocr
from app.preview.sandbox import PreviewError
from app.preview.service import PreviewService
from app.search import query
from app.search.indexer import Indexer

# ---------------------------------------------------------------- fixtures


class FakeOcr:
    """Stands in for the sealed OCR container."""

    def __init__(self) -> None:
        self.hits = 0
        self.reply: bytes = json.dumps({"text": "scanned invoice 4711 Müller"}).encode()
        self.status = 200
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                outer.hits += 1
                self.send_response(outer.status)
                self.send_header("Content-Length", str(len(outer.reply)))
                self.end_headers()
                self.wfile.write(outer.reply)

            def log_message(self, *_: object) -> None:
                return

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_ocr() -> Iterator[FakeOcr]:
    o = FakeOcr()
    yield o
    o.close()


@pytest.fixture
def make_fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Fx]:
    def factory(**overrides: Any) -> Fx:
        return Fx(World(tmp_path, monkeypatch, **overrides))

    return factory


@pytest.fixture
def fx(make_fx: Callable[..., Fx]) -> Fx:
    return make_fx()


@pytest.fixture
def alice(fx: Fx) -> Api:
    return Api(fx, "alice")


def indexer_for(fx: Fx) -> Indexer:
    st = fx.w.settings
    return Indexer(st, fx.w.ctx.sessions, PreviewService(st, fx.w.ctx.sessions))


def run_index(fx: Fx) -> int:
    """One scan plus every due job, exactly what the background thread does."""
    return indexer_for(fx).tick()


def find(api: Api, q: str, **params: Any) -> list[dict[str, Any]]:
    r = api.get("/search", params={"q": q, **params})
    assert r.status_code == 200, r.text
    return list(r.json()["hits"])


def names(hits: list[dict[str, Any]]) -> list[str]:
    return sorted(h["name"] for h in hits)


def docs(fx: Fx) -> dict[uuid.UUID, SearchDoc]:
    return {d.node_id: d for d in fx.w.db_rows(SearchDoc)}


# ---------------------------------------------------------------- basics, no AI


def test_search_finds_names_and_text_with_every_ai_feature_off(fx: Fx, alice: Api) -> None:
    alice.upload(fx.alice_root, "notes.txt", b"The quarterly budget needs review.\nSecond line.")
    alice.upload(fx.alice_root, "refund.pdf", make_pdf("Tax refund 2026"))
    alice.upload(fx.alice_root, "memo.docx", make_docx(["Meeting about the roof repair"]))
    alice.upload(fx.alice_root, "holiday.jpg", b"\xff\xd8\xff not really")
    assert run_index(fx) >= 3
    assert names(find(alice, "budget")) == ["notes.txt"]
    assert names(find(alice, "refund")) == ["refund.pdf"]
    assert names(find(alice, "roof")) == ["memo.docx"]
    hit = find(alice, "budget")[0]
    assert hit["match"] == "content" and "quarterly budget" in hit["snippet"]
    assert names(find(alice, "holiday")) == ["holiday.jpg"]  # by name only
    assert find(alice, "holiday")[0]["match"] == "name"
    assert names(find(alice, "Taxes")) == ["Taxes"]  # folders by name
    assert find(alice, "zzzznothing") == []
    assert names(find(alice, "quarterly review")) == ["notes.txt"]  # every word must match
    assert find(alice, "quarterly zzzz") == []
    assert find(alice, "100%") == [] and find(alice, "a_b") == []  # wildcards are plain text
    status = alice.get("/search/status").json()
    assert status["enabled"] and not status["ocr_available"] and status["indexed"] >= 3


def test_blank_and_oversized_queries_are_refused_cleanly(alice: Api) -> None:
    assert alice.get("/search", params={"q": ""}).status_code == 422
    assert alice.get("/search", params={"q": "x" * 201}).status_code == 422
    assert find(alice, "   ") == []


def test_search_can_be_limited_to_a_folder(fx: Fx, alice: Api) -> None:
    alice.upload(fx.alice_root, "top.txt", b"needle at the top")
    alice.upload(fx.taxes, "deep.txt", b"needle in taxes")
    run_index(fx)
    assert names(find(alice, "needle")) == ["deep.txt", "top.txt"]
    assert names(find(alice, "needle", folder=str(fx.taxes))) == ["deep.txt"]
    bob = Api(fx, "bob")
    assert find(bob, "needle", folder=str(fx.taxes)) == []  # not his folder: same as empty


# ---------------------------------------------------------------- access


def test_results_follow_the_acl_and_a_revoked_share_vanishes_at_once(fx: Fx, alice: Api) -> None:
    alice.upload(fx.taxes, "secret-plan.txt", b"launch codes are swordfish")
    run_index(fx)
    bob, carol = Api(fx, "bob"), Api(fx, "carol")
    for who in (bob, carol):
        assert find(who, "swordfish") == [] and find(who, "secret-plan") == []
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "viewer")
    assert names(find(bob, "swordfish")) == ["secret-plan.txt"]
    assert find(carol, "swordfish") == []
    r = alice.client.delete(
        f"/api/v1/nodes/{fx.taxes}/grants/{fx.uid('bob@example.com')}", headers=alice.headers
    )
    assert r.status_code in (200, 204)
    # no re-index happened: the query decides again every time
    assert find(bob, "swordfish") == [] and find(bob, "secret-plan") == []


def test_an_administrator_finds_nothing_in_personal_folders(fx: Fx, alice: Api) -> None:
    alice.upload(fx.alice_root, "mine.txt", b"administrator must not see: marmalade")
    run_index(fx)
    admin = Api(fx, "admin")
    assert find(admin, "marmalade") == [] and find(admin, "mine") == []


def test_shared_tree_files_are_found_by_members(fx: Fx, alice: Api) -> None:
    bob = Api(fx, "bob")
    bob.upload(fx.shared_sub, "family.txt", b"reunion in june at the lake")
    run_index(fx)
    assert names(find(alice, "reunion")) == ["family.txt"]
    assert names(find(bob, "lake")) == ["family.txt"]


def test_trashed_files_drop_out_at_once_and_the_sweep_deletes_their_text(
    fx: Fx, alice: Api
) -> None:
    n = nid(alice.upload(fx.alice_root, "gone.txt", b"findable words here"))
    run_index(fx)
    assert names(find(alice, "findable")) == ["gone.txt"]
    r = alice.client.delete(
        f"/api/v1/nodes/{n}", params={"expected_revision": 1}, headers=alice.headers
    )
    assert r.status_code in (200, 204)
    assert find(alice, "findable") == [] and find(alice, "gone") == []
    assert n in docs(fx)
    with fx.w.ctx.sessions() as db:
        assert indexer_for(fx).sweep(db) >= 1
    assert n not in docs(fx)


# ---------------------------------------------------------------- privacy


def test_confidential_is_hidden_while_locked_and_its_text_is_never_indexed(
    fx: Fx, alice: Api
) -> None:
    label(alice, fx.y2026, "confidential")
    assert unlock(alice, fx.y2026, PASSWORD).status_code == 200
    n = nid(alice.upload(fx.y2026, "vault.txt", b"vault contents: kumquat"))
    put_optin(alice, True)
    run_index(fx)
    assert n not in docs(fx)  # background work has no unlock
    assert find(alice, "kumquat") == []  # so no text matches, even unlocked
    assert names(find(alice, "vault")) == ["vault.txt"]  # the name is findable while unlocked
    fx.w.advance(minutes=5, seconds=1)
    assert find(alice, "vault") == []  # locked: not even the name


def test_private_text_needs_the_opt_in_at_index_time_and_at_query_time(fx: Fx, alice: Api) -> None:
    label(alice, fx.private, "private")
    n = nid(alice.upload(fx.private, "diary.txt", b"dear diary: persimmon"))
    run_index(fx)
    assert n not in docs(fx)
    job = fx.w.db_rows(SearchJob)[0]
    assert job.state == "failed" and job.error == "LOCAL_PROCESSING_OPT_IN_REQUIRED"
    assert find(alice, "persimmon") == [] and names(find(alice, "diary")) == ["diary.txt"]
    put_optin(alice, True)
    run_index(fx)  # a deferred job waits an hour before the scanner looks again
    assert n not in docs(fx)
    fx.w.advance(hours=1, seconds=1)
    run_index(fx)
    assert n in docs(fx)
    assert names(find(alice, "persimmon")) == ["diary.txt"]
    put_optin(alice, False)  # withdrawing consent hides it before the sweeper runs
    assert find(alice, "persimmon") == [] and names(find(alice, "diary")) == ["diary.txt"]
    with fx.w.ctx.sessions() as db:
        indexer_for(fx).sweep(db)
    assert n not in docs(fx)


def test_raising_a_label_removes_the_text_of_files_inside(fx: Fx, alice: Api) -> None:
    n = nid(alice.upload(fx.other, "plain.txt", b"ordinary words: guava"))
    run_index(fx)
    assert names(find(alice, "guava")) == ["plain.txt"]
    label(alice, fx.taxes, "private")  # no opt-in
    assert find(alice, "guava") == []
    with fx.w.ctx.sessions() as db:
        indexer_for(fx).sweep(db)
    assert n not in docs(fx)


# ---------------------------------------------------------------- invalidation


def rewrite_on_disk(fx: Fx, node_id: uuid.UUID, data: bytes) -> None:
    """Changes a file behind the app's back and records what a rescan would record."""
    from app.domain import nodes

    with fx.w.ctx.sessions() as db:
        node = db.get(Node, node_id)
        assert node is not None
        root = db.get(StorageRoot, node.root_id)
        assert root is not None
        path = fx.w.settings.library_path / root.relative_dir
        for part in nodes.path_components(db, node):
            path = path / part
        path.write_bytes(data)
        node.size = len(data)
        node.mtime_ns = (node.mtime_ns or 0) + 1_000_000_000
        node.revision += 1
        db.commit()


def test_a_changed_file_is_hidden_until_it_is_indexed_again(fx: Fx, alice: Api) -> None:
    n = nid(alice.upload(fx.alice_root, "draft.txt", b"first version: apricot"))
    run_index(fx)
    assert names(find(alice, "apricot")) == ["draft.txt"]
    rewrite_on_disk(fx, n, b"second version: blueberry")
    assert find(alice, "apricot") == [] and find(alice, "blueberry") == []  # stale row hidden
    run_index(fx)
    assert find(alice, "apricot") == [] and names(find(alice, "blueberry")) == ["draft.txt"]


def test_a_rename_keeps_the_text_and_changes_the_name_hit(fx: Fx, alice: Api) -> None:
    n = nid(alice.upload(fx.alice_root, "before.txt", b"contents: tangerine"))
    run_index(fx)
    assert alice.rename(n, "after.txt", 1).status_code == 200
    run_index(fx)
    assert names(find(alice, "tangerine")) == ["after.txt"]
    assert find(alice, "before") == [] and names(find(alice, "after")) == ["after.txt"]


# ---------------------------------------------------------------- jobs, leases and retries


def only_job(fx: Fx) -> SearchJob:
    jobs = fx.w.db_rows(SearchJob)
    assert len(jobs) == 1
    return jobs[0]


def test_a_crashed_worker_leaves_a_job_that_is_taken_again(fx: Fx, alice: Api) -> None:
    alice.upload(fx.alice_root, "crash.txt", b"survives a crash: lychee")
    ix = indexer_for(fx)
    with fx.w.ctx.sessions() as db:
        ix.scan_once(db)
        first = ix.claim(db)  # a worker takes the job and dies without finishing
        assert first is not None
        assert ix.claim(db) is None  # leased: nobody else touches it meanwhile
    fx.w.advance(seconds=fx.w.settings.search_lease_seconds + 1)
    with fx.w.ctx.sessions() as db:
        second = ix.claim(db)
    assert second == first and only_job(fx).attempts == 2
    assert ix.run_job(second) == "indexed"
    assert names(find(alice, "lychee")) == ["crash.txt"]
    assert fx.w.db_rows(SearchJob) == []


def test_transient_errors_back_off_and_give_up_after_the_cap(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice.upload(fx.alice_root, "flaky.pdf", make_pdf("flaky words"))

    def boom(*_: object, **__: object) -> str:
        raise PreviewError("TIMEOUT", 504)

    monkeypatch.setattr(PreviewService, "extract_text", boom)
    ix = indexer_for(fx)
    cap = fx.w.settings.search_max_attempts
    for attempt in range(1, cap + 1):
        fx.w.advance(hours=2)  # past any back-off
        ix.tick()
        job = only_job(fx)
        if attempt < cap:
            assert job.state == "queued" and job.attempts == attempt and job.error == "TIMEOUT"
    job = only_job(fx)
    assert job.state == "failed" and job.error == "GAVE_UP"
    monkeypatch.undo()
    fx.w.advance(days=2)  # it is tried again daily, and now succeeds
    ix.tick()
    assert names(find(Api(fx, "alice"), "flaky")) == ["flaky.pdf"]  # a new login: days have passed
    assert fx.w.db_rows(SearchJob) == []


def test_permanent_errors_are_not_retried_until_the_file_changes(fx: Fx, alice: Api) -> None:
    n = nid(alice.upload(fx.alice_root, "locked.pdf", encrypted_pdf()))
    ix = indexer_for(fx)
    ix.tick()
    job = only_job(fx)
    assert job.state == "failed" and job.error == "ENCRYPTED"
    for _ in range(3):
        fx.w.advance(hours=5)
        assert ix.tick() == 0  # nothing is claimed again
    assert only_job(fx).attempts == job.attempts
    rewrite_on_disk(fx, n, make_pdf("now readable: papaya"))
    ix.tick()
    assert names(find(Api(fx, "alice"), "papaya")) == ["locked.pdf"]


def test_a_file_that_changes_while_queued_is_indexed_in_its_new_version(fx: Fx, alice: Api) -> None:
    n = nid(alice.upload(fx.alice_root, "race.txt", b"old words: durian"))
    ix = indexer_for(fx)
    with fx.w.ctx.sessions() as db:
        ix.scan_once(db)
        job_node = ix.claim(db)
    assert job_node == n
    rewrite_on_disk(fx, n, b"new words: jackfruit")
    assert ix.run_job(n) == "stale"  # the job was for a version that no longer exists
    ix.tick()
    assert names(find(alice, "jackfruit")) == ["race.txt"] and find(alice, "durian") == []


def test_search_can_be_switched_off_without_touching_names(make_fx: Callable[..., Fx]) -> None:
    fx = make_fx(search_enabled=False)
    alice = Api(fx, "alice")
    alice.upload(fx.alice_root, "named.txt", b"words")
    ix = indexer_for(fx)
    ix.start()
    assert ix._thread is None  # the background thread never starts
    assert names(find(alice, "named")) == ["named.txt"]
    assert alice.get("/search/status").json()["enabled"] is False


# ---------------------------------------------------------------- OCR


def test_scanned_pdfs_and_pictures_become_searchable_with_ocr(
    make_fx: Callable[..., Fx], fake_ocr: FakeOcr
) -> None:
    fx = make_fx(ocr_url=fake_ocr.url)
    alice = Api(fx, "alice")
    from test_previews import blank_pdf

    alice.upload(fx.alice_root, "scan.pdf", blank_pdf(1))  # a PDF with no text layer
    alice.upload(fx.alice_root, "receipt.png", make_png())
    alice.upload(fx.alice_root, "typed.pdf", make_pdf("A long enough typed sentence here"))
    run_index(fx)
    assert fake_ocr.hits == 2  # the PDF that has text never goes to OCR
    assert sorted(names(find(alice, "4711"))) == ["receipt.png", "scan.pdf"]
    assert names(find(alice, "müller")) == ["receipt.png", "scan.pdf"]
    assert {d.source for d in docs(fx).values() if d.body.startswith("scanned")} == {"ocr"}
    assert names(find(alice, "typed sentence")) == ["typed.pdf"]
    assert alice.get("/search/status").json()["ocr_available"] is True


def test_pictures_are_left_alone_without_the_ocr_container(fx: Fx, alice: Api) -> None:
    alice.upload(fx.alice_root, "receipt.png", make_png())
    run_index(fx)
    assert fx.w.db_rows(SearchDoc) == [] and fx.w.db_rows(SearchJob) == []
    assert names(find(alice, "receipt")) == ["receipt.png"]


def test_private_pictures_are_not_sent_to_ocr_without_the_opt_in(
    make_fx: Callable[..., Fx], fake_ocr: FakeOcr
) -> None:
    fx = make_fx(ocr_url=fake_ocr.url)
    alice = Api(fx, "alice")
    label(alice, fx.private, "private")
    alice.upload(fx.private, "id-card.png", make_png())
    run_index(fx)
    assert fake_ocr.hits == 0


def test_ocr_failures_are_classified(make_fx: Callable[..., Fx], fake_ocr: FakeOcr) -> None:
    fx = make_fx(ocr_url=fake_ocr.url)
    alice = Api(fx, "alice")
    alice.upload(fx.alice_root, "bad.png", make_png())
    fake_ocr.status, fake_ocr.reply = 422, json.dumps({"error": "OCR_FAILED"}).encode()
    run_index(fx)
    assert only_job(fx).state == "failed" and only_job(fx).error == "OCR_FAILED"


def call_ocr(url: str) -> str:
    import io

    return ocr.ocr_text(
        url, io.BytesIO(b"\x89PNG\r\n\x1a\nxx"), 10, "png", timeout=5, max_pages=1, max_chars=100
    )


def test_ocr_replies_are_treated_as_untrusted(fake_ocr: FakeOcr) -> None:
    fake_ocr.reply = json.dumps({"text": "ok\x00\x1b[31mred\x07" + "x" * 500}).encode()
    text = call_ocr(fake_ocr.url)
    assert len(text) == 100 and "\x00" not in text and "\x1b" not in text and "\x07" not in text
    fake_ocr.reply = b"not json"
    with pytest.raises(PreviewError) as e1:
        call_ocr(fake_ocr.url)
    assert e1.value.code == "OCR_FAILED"
    fake_ocr.reply = json.dumps({"text": 5}).encode()
    with pytest.raises(PreviewError) as e2:
        call_ocr(fake_ocr.url)
    assert e2.value.code == "OCR_FAILED"
    fake_ocr.status, fake_ocr.reply = 500, json.dumps({"error": "rm -rf /"}).encode()
    with pytest.raises(PreviewError) as e3:
        call_ocr(fake_ocr.url)
    assert e3.value.code == "OCR_FAILED"  # an unknown code never reaches the person
    fake_ocr.status, fake_ocr.reply = 200, b"x" * (ocr.MAX_REPLY_BYTES + 5)
    with pytest.raises(PreviewError):
        call_ocr(fake_ocr.url)
    with pytest.raises(PreviewError) as e4:
        call_ocr("http://127.0.0.1:1")  # nothing listens
    assert e4.value.code == "OCR_UNAVAILABLE"


# ---------------------------------------------------------------- helpers


def test_snippets_and_terms() -> None:
    assert query.terms_of("  Hello   WORLD ") == ["hello", "world"]
    assert len(query.terms_of("a " * 20)) == query.MAX_TERMS
    body = "x" * 300 + " needle " + "y" * 300
    snip = query.snippet_of(body, ["needle"])
    assert "needle" in snip and snip.startswith("… ") and snip.endswith(" …") and len(snip) < 400
    assert query.snippet_of("short text", ["zzz"]) == "short text"


def test_status_counts_only_the_callers_own_folder(fx: Fx, alice: Api) -> None:
    alice.upload(fx.alice_root, "a.txt", b"one")
    run_index(fx)
    bob = Api(fx, "bob")
    assert alice.get("/search/status").json()["indexed"] == 1
    assert bob.get("/search/status").json()["indexed"] == 0
    assert clock.utcnow() is not None
    _ = select  # keep the import used in case of future queries
