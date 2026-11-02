"""M17: PDF split proposals, the deterministic split, and duplicate suggestions. The provider is
mocked at the HTTP transport (as in M15/M16); nothing here reaches the network."""

import ast
import hashlib
import io
import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from pypdf import PdfReader, PdfWriter
from test_acl import Fx
from test_ai import KEY, Net, cloud_body, label, put, set_pref, with_resolver
from test_auth import World
from test_files import Api, code, disk

from app.ai import split
from app.db.models import AuthAudit, FileEvent, Node, SearchDoc, SplitProposal, TrashItem
from app.preview import pdfsplit
from app.preview.extract import Refuse
from app.preview.sandbox import PreviewError
from app.preview.service import PreviewService
from app.search import duplicates

APP = Path(__file__).resolve().parents[3] / "apps" / "api" / "app"


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fx:
    return with_resolver(Fx(World(tmp_path, monkeypatch, secrets_key=KEY)))


@pytest.fixture
def net(fx: Fx) -> Net:
    n = Net()
    fx.w.app.state.ai._transport = httpx.MockTransport(n)
    return n


def pdf(pages: list[str | None], rotate: dict[int, int] | None = None) -> bytes:
    """A real PDF: one page per entry (None = a blank page), optional /Rotate by 1-based page."""
    rotate = rotate or {}
    objs: list[bytes] = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids: list[str] = []
    for i, text in enumerate(pages, start=1):
        stream = b"" if text is None else f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
        page_no, content_no = 4 + 2 * (i - 1), 5 + 2 * (i - 1)
        kids.append(f"{page_no} 0 R")
        rot = f" /Rotate {rotate[i]}" if i in rotate else ""
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents {content_no} 0 R{rot} "
            f"/Resources << /Font << /F1 3 0 R >> >> >>".encode()
        )
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


TWO_DOCS = [
    "Invoice 100 Page 1 of 2",
    "Invoice 100 Page 2 of 2",
    "Letter to Bob Page 1 of 1",
]


def up(api: Api, parent: uuid.UUID, name: str, data: bytes) -> uuid.UUID:
    r = api.upload(parent, name, data)
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["node"]["id"])


def vault(fx: Fx, api: Api, name: str = "Vault") -> uuid.UUID:
    """A folder labelled Private: everything inside it is Private."""
    folder = uuid.UUID(api.mkdir(fx.alice_root, name).json()["node"]["id"])
    label(api, folder, "private")
    return folder


def sp(api: Api, path: str, body: dict[str, Any] | None = None, method: str = "post") -> Any:
    return getattr(api.client, method)(
        f"/api/v1/ai/split{path}", json=body, headers={**api.headers, "Idempotency-Key": "k" + path}
    )


def suggest(api: Api, node: uuid.UUID, use_ai: bool = False) -> dict[str, Any]:
    r = sp(api, "/suggest", {"node_id": str(node), "use_ai": use_ai})
    assert r.status_code == 200, r.text
    return dict(r.json())


def pending(api: Api) -> list[dict[str, Any]]:
    r = api.client.get("/api/v1/ai/split/proposals?status=pending")
    assert r.status_code == 200, r.text
    return list(r.json()["items"])


def kids(api: Api, folder: uuid.UUID) -> dict[str, dict[str, Any]]:
    return {i["name"]: i for i in api.children(folder)}


def pages_of(path: Path) -> PdfReader:
    return PdfReader(io.BytesIO(path.read_bytes()))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def alice_setup(fx: Fx) -> Api:
    alice, admin = Api(fx, "alice"), Api(fx, "admin")
    assert put(admin, "/instance/provider", cloud_body()).status_code == 200
    set_pref(alice, "instance_default")
    return alice


def answer(*parts: tuple[int, int, str | None], **kw: Any) -> str:
    body = {
        "parts": [{"start": a, "end": b, "title": t} for a, b, t in parts],
        "confidence": 0.9,
        "reason": "two documents",
    }
    return json.dumps({**body, **kw})


# --- pure functions --------------------------------------------------------------------
def d(n: int, *, blank: bool = False, marker: list[int] | None = None) -> dict[str, Any]:
    return {"n": n, "blank": blank, "marker": marker, "chars": 0 if blank else 10}


def test_heuristic_cuts_on_restart_and_blank_separator() -> None:
    desc = [d(1, marker=[1, 2]), d(2, marker=[2, 2]), d(3, marker=[1, 1])]
    assert split.heuristic_cuts(desc) == [1, 3]
    desc = [d(1), d(2), d(3, blank=True), d(4), d(5, blank=True)]
    assert split.heuristic_cuts(desc) == [1, 4]  # a blank page stays with the part it follows
    assert split.heuristic_cuts([d(1, blank=True), d(2), d(3)]) == [
        1
    ]  # leading blank: no empty part
    assert split.heuristic_cuts([d(1), d(2), d(3)]) == [1]


@pytest.mark.parametrize(
    "parts",
    [
        [(1, 2), (4, 5)],  # gap
        [(1, 3), (3, 5)],  # overlap
        [(1, 2), (3, 4)],  # short of the end
        [(1, 2), (3, 9)],  # past the end
        [(2, 3), (4, 5)],  # does not start at 1
        [(1, 5)],  # not a split
        [(3, 5), (1, 2)],  # out of order
        [(1, 0), (1, 5)],
    ],
)
def test_validate_parts_rejects_anything_but_a_partition(parts: list[tuple[int, int]]) -> None:
    with pytest.raises(split.Rejected):
        split.validate_parts([{"start": a, "end": b, "title": None} for a, b in parts], 5)


def test_every_partition_accounts_for_every_page() -> None:
    for n in range(2, 9):
        for cut in range(2, n + 1):
            parts = split.parts_from_cuts([1, cut], n)
            split.validate_parts(parts, n)
            for blank in (set(), {1}, {n}, {1, cut - 1, n}, set(range(1, n + 1))):
                for drop in (False, True):
                    try:
                        kept, left = split.plan_pages(parts, blank, n, drop)
                    except split.Rejected:
                        assert drop and blank >= set(range(1, n + 1))  # nothing left to write
                        continue
                    assert sorted([p for k in kept for p in k] + left) == list(range(1, n + 1))


def test_titles_become_safe_distinct_names() -> None:
    names = split.part_names(
        "scan.pdf", ["../etc/passwd", "Tax 2026", "tax 2026", None, "  ", "a" * 300]
    )
    assert names[0] == "etc passwd.pdf" and "/" not in "".join(names)
    assert names[1] == "Tax 2026.pdf" and names[2] == "scan - part 3.pdf"
    assert names[3] == "scan - part 4.pdf" and names[4] == "scan - part 5.pdf"
    assert names[5] == "scan - part 6.pdf"
    assert all(n.endswith(".pdf") for n in names)


def test_worker_refuses_bad_pages_and_verifies_output() -> None:
    data = pdf(["a", "b", "c"])
    for pages in ([], [0], [4], [1, 1]):
        with pytest.raises(Refuse):
            pdfsplit.extract_part(io.BytesIO(data), pages)
    out = pdfsplit.extract_part(io.BytesIO(data), [3, 1])
    r = PdfReader(io.BytesIO(out))
    assert [p.extract_text().strip() for p in r.pages] == ["c", "a"]


# --- the flow --------------------------------------------------------------------------
def test_two_doc_sample_splits_and_keeps_the_source(fx: Fx, net: Net) -> None:
    alice = Api(fx, "alice")
    f = up(alice, fx.alice_root, "scan.pdf", pdf(TWO_DOCS))
    before = sha(disk(fx, f))
    got = suggest(alice, f)  # no AI configured, none asked for
    assert got["status"] == "pending" and net.requests == []
    item = pending(alice)[0]
    assert [(p["start"], p["end"]) for p in item["parts"]] == [(1, 2), (3, 3)]
    assert item["source"] == "heuristic" and item["page_count"] == 3
    assert "scan - part 1.pdf" not in kids(alice, fx.alice_root)  # nothing written before approval

    r = sp(alice, f"/proposals/{got['proposal_id']}/approve", {})
    assert r.status_code == 200, r.text
    names = kids(alice, fx.alice_root)
    assert sha(disk(fx, f)) == before and "scan.pdf" in names  # source preserved byte for byte
    one, two = names["scan - part 1.pdf"], names["scan - part 2.pdf"]
    texts = [
        [p.extract_text().strip() for p in pages_of(disk(fx, uuid.UUID(x["id"]))).pages]
        for x in (one, two)
    ]
    assert texts == [[TWO_DOCS[0], TWO_DOCS[1]], [TWO_DOCS[2]]]  # all pages, in order, once each
    row = fx.w.db_rows(SplitProposal)[0]
    assert row.status == "applied" and len(row.outputs) == 2 and row.left_out == []
    assert {"ai.split_proposed", "ai.split_approved"} <= {a.action for a in fx.w.db_rows(AuthAudit)}
    ev = [e for e in fx.w.db_rows(FileEvent) if e.node_id == f and e.action == "split"]
    assert (
        len(ev) == 1 and ev[0].actor_kind == "ai_agent" and ev[0].on_behalf_of_user_id == alice.uid
    )
    # a second approval is refused and creates nothing
    assert code(sp(alice, f"/proposals/{got['proposal_id']}/approve", {})) == "NOT_PENDING"
    assert len(kids(alice, fx.alice_root)) == len(names)


def test_blank_separator_pages_and_leaving_them_out(fx: Fx) -> None:
    alice = Api(fx, "alice")
    f = up(alice, fx.alice_root, "mix.pdf", pdf(["Doc A", "Doc A more", None, "Doc B", None]))
    pid = suggest(alice, f)["proposal_id"]
    item = pending(alice)[0]
    assert [(p["start"], p["end"]) for p in item["parts"]] == [(1, 3), (4, 5)]
    assert item["blank_pages"] == [3, 5]
    r = sp(alice, f"/proposals/{pid}/approve", {"drop_blank": True})
    assert r.status_code == 200, r.text
    assert r.json()["left_out"] == [3, 5]  # accounted for, and still in the source
    names = kids(alice, fx.alice_root)
    counts = [
        len(pages_of(disk(fx, uuid.UUID(names[n]["id"]))).pages)
        for n in ("mix - part 1.pdf", "mix - part 2.pdf")
    ]
    assert counts == [2, 1]
    assert len(pages_of(disk(fx, f)).pages) == 5


def test_blank_pages_kept_by_default(fx: Fx) -> None:
    alice = Api(fx, "alice")
    f = up(alice, fx.alice_root, "mix.pdf", pdf(["Doc A", None, "Doc B"]))
    pid = suggest(alice, f)["proposal_id"]
    assert sp(alice, f"/proposals/{pid}/approve", {}).status_code == 200
    names = kids(alice, fx.alice_root)
    counts = [
        len(pages_of(disk(fx, uuid.UUID(names[n]["id"]))).pages)
        for n in ("mix - part 1.pdf", "mix - part 2.pdf")
    ]
    assert counts == [2, 1]


def test_rotation_is_preserved(fx: Fx) -> None:
    alice = Api(fx, "alice")
    f = up(alice, fx.alice_root, "rot.pdf", pdf([*TWO_DOCS], rotate={2: 90, 3: 270}))
    pid = suggest(alice, f)["proposal_id"]
    assert sp(alice, f"/proposals/{pid}/approve", {}).status_code == 200
    names = kids(alice, fx.alice_root)
    first = pages_of(disk(fx, uuid.UUID(names["rot - part 1.pdf"]["id"])))
    second = pages_of(disk(fx, uuid.UUID(names["rot - part 2.pdf"]["id"])))
    assert [int(p.rotation) for p in first.pages] == [0, 90]
    assert [int(p.rotation) for p in second.pages] == [270]


def test_one_page_empty_and_single_document_pdfs_are_not_split(fx: Fx) -> None:
    alice = Api(fx, "alice")
    one = up(alice, fx.alice_root, "one.pdf", pdf(["Only page"]))
    assert suggest(alice, one)["skipped"] == "too_short"
    blank = up(alice, fx.alice_root, "empty.pdf", pdf([None, None, None]))
    assert suggest(alice, blank)["skipped"] == "nothing_to_split"
    single = up(alice, fx.alice_root, "single.pdf", pdf(["Report p1", "Report p2", "Report p3"]))
    assert suggest(alice, single)["skipped"] == "nothing_to_split"
    assert pending(alice) == []


def test_not_a_pdf_and_wrong_content(fx: Fx) -> None:
    alice = Api(fx, "alice")
    txt = up(alice, fx.alice_root, "a.txt", b"hello")
    assert suggest(alice, txt)["skipped"] == "not_pdf"
    fake = up(alice, fx.alice_root, "fake.pdf", b"not really a pdf at all, just text")
    assert suggest(alice, fake)["skipped"] == "type_mismatch"


def test_name_collision_never_overwrites(fx: Fx) -> None:
    alice = Api(fx, "alice")
    taken = up(alice, fx.alice_root, "scan - part 1.pdf", b"precious")
    f = up(alice, fx.alice_root, "scan.pdf", pdf(TWO_DOCS))
    pid = suggest(alice, f)["proposal_id"]
    assert sp(alice, f"/proposals/{pid}/approve", {}).status_code == 200
    assert disk(fx, taken).read_bytes() == b"precious"
    names = kids(alice, fx.alice_root)
    assert len([n for n in names if n.startswith("scan - part 1")]) == 2  # the new one got a suffix
    assert len([n for n in names if n.endswith(".pdf")]) == 4


def test_cuts_override_is_validated_the_same_way(fx: Fx) -> None:
    alice = Api(fx, "alice")
    f = up(alice, fx.alice_root, "scan.pdf", pdf(["a 1", "a 2", "b 1", "b 2"]))
    pid = suggest(alice, f)
    assert pid.get("skipped") == "nothing_to_split"
    f2 = up(alice, fx.alice_root, "two.pdf", pdf(TWO_DOCS))
    pid2 = suggest(alice, f2)["proposal_id"]
    for bad in ([5], [0], [2, 2, 9]):
        r = sp(alice, f"/proposals/{pid2}/approve", {"cuts": bad})
        assert r.status_code == 422, (bad, r.text)
    assert pending(alice)[0]["id"] == pid2  # a bad request leaves it pending
    r = sp(alice, f"/proposals/{pid2}/approve", {"cuts": [1, 2]})
    assert r.status_code == 200, r.text
    names = kids(alice, fx.alice_root)
    counts = [
        len(pages_of(disk(fx, uuid.UUID(names[n]["id"]))).pages)
        for n in ("two - part 1.pdf", "two - part 2.pdf")
    ]
    assert counts == [1, 2]


def test_warnings_for_bookmarks_and_attachments(fx: Fx) -> None:
    alice = Api(fx, "alice")
    w = PdfWriter(clone_from=io.BytesIO(pdf(TWO_DOCS)))
    w.add_outline_item("Start", 0)
    w.add_attachment("note.txt", b"attached")
    buf = io.BytesIO()
    w.write(buf)
    f = up(alice, fx.alice_root, "book.pdf", buf.getvalue())
    suggest(alice, f)
    warn = pending(alice)[0]["warnings"]
    assert "bookmarks_stay_in_source" in warn and "attachments_stay_in_source" in warn


# --- the AI part -----------------------------------------------------------------------
def test_ai_proposal_uses_only_this_files_text(fx: Fx, net: Net) -> None:
    alice = alice_setup(fx)
    up(alice, fx.alice_root, "sibling.txt", b"SECRET-SIBLING-BODY")
    f = up(alice, fx.alice_root, "scan.pdf", pdf(TWO_DOCS))
    net.text = answer((1, 1, "Invoice 100"), (2, 3, "Everything else"))
    got = suggest(alice, f, use_ai=True)
    assert got["status"] == "pending"
    sent = json.dumps(json.loads(net.requests[-1].content))
    assert "Invoice 100 Page 1 of 2" in sent and "Letter to Bob" in sent
    assert "SECRET-SIBLING-BODY" not in sent and "sibling.txt" not in sent
    item = pending(alice)[0]
    assert item["source"] == "ai" and item["confidence"] == 900
    assert [p["name"] for p in item["parts"]] == ["Invoice 100.pdf", "Everything else.pdf"]
    assert sp(alice, f"/proposals/{got['proposal_id']}/approve", {}).status_code == 200
    assert {"Invoice 100.pdf", "Everything else.pdf", "scan.pdf"} <= set(kids(alice, fx.alice_root))


@pytest.mark.parametrize(
    "text",
    [
        answer((1, 1, "A"), (3, 3, "B")),  # gap
        answer((1, 2, "A"), (2, 3, "B")),  # overlap
        answer((1, 2, "A"), (3, 9, "B")),  # invented page
        answer((1, 3, "A")),  # not a split
        answer((1, 1, "A"), (2, 3, "B"), delete_source=True),  # extra field
        answer((1, 1, "A"), (2, 3, "B"), confidence=7),  # confidence out of range
        "I think pages 1-2 and 3",  # not JSON
        '{"parts": "all of them"}',
    ],
)
def test_malformed_model_answers_fall_back_to_the_heuristic(fx: Fx, net: Net, text: str) -> None:
    alice = alice_setup(fx)
    f = up(alice, fx.alice_root, "scan.pdf", pdf(TWO_DOCS))
    net.text = text
    suggest(alice, f, use_ai=True)
    item = pending(alice)[0]
    assert item["source"] == "heuristic" and item["warnings"]
    assert [(p["start"], p["end"]) for p in item["parts"]] == [(1, 2), (3, 3)]
    assert len(fx.w.db_rows(SplitProposal)) == 1


def test_no_provider_still_gives_a_heuristic_proposal(fx: Fx, net: Net) -> None:
    alice = Api(fx, "alice")
    f = up(alice, fx.alice_root, "scan.pdf", pdf(TWO_DOCS))
    suggest(alice, f, use_ai=True)
    item = pending(alice)[0]
    assert item["source"] == "heuristic" and any(w.startswith("ai") for w in item["warnings"])
    assert net.requests == []


def test_protected_files_are_not_read_or_sent(fx: Fx, net: Net) -> None:
    alice = alice_setup(fx)
    f = up(alice, vault(fx, alice), "scan.pdf", pdf(TWO_DOCS))
    assert suggest(alice, f, use_ai=True)["skipped"] == "protected"
    assert net.requests == [] and fx.w.db_rows(SplitProposal) == []


def test_proposal_for_a_file_that_became_private_is_retired(fx: Fx) -> None:
    alice = Api(fx, "alice")
    folder = uuid.UUID(alice.mkdir(fx.alice_root, "Later").json()["node"]["id"])
    f = up(alice, folder, "scan.pdf", pdf(TWO_DOCS))
    pid = suggest(alice, f)["proposal_id"]
    label(alice, folder, "private")
    assert pending(alice) == []
    assert code(sp(alice, f"/proposals/{pid}/approve", {})) in ("NOT_PENDING", "STALE")
    assert [n for n in kids(alice, folder) if "part" in n] == []


# --- races, other people, undo ---------------------------------------------------------
def test_changed_source_makes_the_proposal_stale(fx: Fx) -> None:
    alice = Api(fx, "alice")
    f = up(alice, fx.alice_root, "scan.pdf", pdf(TWO_DOCS))
    pid = suggest(alice, f)["proposal_id"]
    assert alice.rename(f, "renamed.pdf", 1).status_code == 200
    r = sp(alice, f"/proposals/{pid}/approve", {})
    assert r.status_code == 409 and code(r) == "STALE"
    assert [n for n in kids(alice, fx.alice_root) if "part" in n] == []
    assert fx.w.db_rows(SplitProposal)[0].status == "stale"


def test_dismiss_is_final(fx: Fx) -> None:
    alice = Api(fx, "alice")
    f = up(alice, fx.alice_root, "scan.pdf", pdf(TWO_DOCS))
    pid = suggest(alice, f)["proposal_id"]
    assert sp(alice, f"/proposals/{pid}/dismiss").status_code == 200
    assert code(sp(alice, f"/proposals/{pid}/approve", {})) == "NOT_PENDING"
    assert code(sp(alice, f"/proposals/{pid}/dismiss")) == "NOT_PENDING"
    assert "ai.split_dismissed" in {a.action for a in fx.w.db_rows(AuthAudit)}


def test_other_people_cannot_propose_see_or_decide(fx: Fx) -> None:
    alice, bob = Api(fx, "alice"), Api(fx, "bob")
    f = up(alice, fx.alice_root, "scan.pdf", pdf(TWO_DOCS))
    assert suggest(bob, f)["skipped"] == "node_unavailable"
    pid = suggest(alice, f)["proposal_id"]
    assert pending(bob) == []
    assert sp(bob, f"/proposals/{pid}/approve", {}).status_code in (404, 409)
    assert sp(bob, f"/proposals/{pid}/dismiss").status_code in (404, 409)
    assert sp(bob, f"/proposals/{pid}/undo").status_code in (404, 409)
    assert fx.w.db_rows(SplitProposal)[0].status == "pending"


def test_a_second_open_proposal_for_the_same_file_is_replaced_not_duplicated(
    fx: Fx, net: Net
) -> None:
    alice = alice_setup(fx)
    f = up(alice, fx.alice_root, "scan.pdf", pdf(TWO_DOCS))
    first = suggest(alice, f)["proposal_id"]
    assert suggest(alice, f)["proposal_id"] == first  # unchanged: the same proposal
    net.text = answer((1, 1, None), (2, 3, None))
    second = suggest(alice, f, use_ai=True)["proposal_id"]
    assert second != first
    rows = {r.id: r.status for r in fx.w.db_rows(SplitProposal)}
    assert rows[uuid.UUID(first)] == "superseded" and rows[uuid.UUID(second)] == "pending"


def test_failure_half_way_leaves_no_partial_set_and_the_source_alone(
    fx: Fx, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = Api(fx, "alice")
    f = up(alice, fx.alice_root, "scan.pdf", pdf(TWO_DOCS))
    before = sha(disk(fx, f))
    pid = suggest(alice, f)["proposal_id"]
    real = PreviewService.pdf_part
    calls = {"n": 0}

    def flaky(self: PreviewService, file: Any, size: int, pages: list[int]) -> bytes:
        calls["n"] += 1
        if calls["n"] == 2:
            raise PreviewError("SPLIT_VERIFY_FAILED")
        return real(self, file, size, pages)

    monkeypatch.setattr(PreviewService, "pdf_part", flaky)
    r = sp(alice, f"/proposals/{pid}/approve", {})
    assert r.status_code == 422 and code(r) == "SPLIT_VERIFY_FAILED"
    assert sha(disk(fx, f)) == before
    assert [n for n in kids(alice, fx.alice_root) if "part" in n] == []  # the first part was undone
    assert len(fx.w.db_rows(TrashItem)) == 1  # ... into the trash, so nothing is lost
    assert fx.w.db_rows(SplitProposal)[0].status == "failed"


def test_undo_moves_parts_to_trash_and_keeps_changed_ones(fx: Fx) -> None:
    alice = Api(fx, "alice")
    f = up(alice, fx.alice_root, "scan.pdf", pdf(TWO_DOCS))
    pid = suggest(alice, f)["proposal_id"]
    assert sp(alice, f"/proposals/{pid}/approve", {}).status_code == 200
    names = kids(alice, fx.alice_root)
    assert (
        alice.rename(uuid.UUID(names["scan - part 2.pdf"]["id"]), "kept.pdf", 1).status_code == 200
    )
    r = sp(alice, f"/proposals/{pid}/undo")
    assert r.status_code == 200, r.text
    assert r.json()["trashed"] == 1 and len(r.json()["left_in_place"]) == 1
    after = kids(alice, fx.alice_root)
    assert "scan.pdf" in after and "scan - part 1.pdf" not in after and "kept.pdf" in after
    item = alice.client.get("/api/v1/ai/split/proposals?status=undone").json()["items"]
    assert len(item) == 1
    trash = fx.w.db_rows(TrashItem)
    assert [t.reason for t in trash] == ["split_undone"]
    assert code(sp(alice, f"/proposals/{pid}/undo")) == "NOT_REVERSIBLE"


# --- what the module can reach ---------------------------------------------------------
def imports_of(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
        elif isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
    return out


def test_split_module_cannot_change_permissions_labels_or_delete() -> None:
    found = imports_of(APP / "ai" / "split.py")
    banned = ("app.domain.grants", "app.domain.trash", "app.policy.privacy.set", "shutil")
    assert not [i for i in found if i in banned or i.startswith("app.domain.grants")]
    src = (APP / "ai" / "split.py").read_text()
    for word in ("os.remove", "os.unlink", "shutil", ".rmtree", "NodeGrant(", "privacy_label ="):
        assert word not in src, word
    model = split.ModelSplit.model_json_schema()
    assert set(model["properties"]) == {"parts", "confidence", "reason"}


def test_duplicates_module_is_read_only() -> None:
    found = imports_of(APP / "search" / "duplicates.py")
    assert not [i for i in found if i.startswith(("app.domain", "app.storage", "shutil", "os"))]
    src = (APP / "search" / "duplicates.py").read_text()
    for word in ("delete(", ".delete", "unlink", "commit()", "db.add", "update("):
        assert word not in src, word


# --- duplicates ------------------------------------------------------------------------
def actor_of(fx: Fx, email: str) -> Any:
    from app.policy.model import Actor

    return Actor(fx.uid(email), "user", None)


def dup_groups(api: Api, **params: Any) -> list[dict[str, Any]]:
    r = api.client.get("/api/v1/duplicates", params=params)
    assert r.status_code == 200, r.text
    return list(r.json()["groups"])


def test_exact_duplicates_are_reported_and_nothing_is_removed(fx: Fx) -> None:
    alice = Api(fx, "alice")
    a = up(alice, fx.alice_root, "a.bin", b"same bytes " * 20)
    b = up(alice, fx.taxes, "copy of a.bin", b"same bytes " * 20)
    up(alice, fx.alice_root, "other.bin", b"different " * 20)
    groups = dup_groups(alice, near=False)
    assert len(groups) == 1 and groups[0]["kind"] == "exact" and groups[0]["similarity"] == 1000
    assert {m["node_id"] for m in groups[0]["members"]} == {str(a), str(b)}
    assert {m["parent_name"] for m in groups[0]["members"]} >= {"Taxes"}
    with fx.w.ctx.sessions() as db:
        assert all(db.get(Node, n).status == "active" for n in (a, b))  # type: ignore[union-attr]
    assert disk(fx, a).exists() and disk(fx, b).exists()


def test_empty_files_are_never_duplicates(fx: Fx) -> None:
    alice = Api(fx, "alice")
    up(alice, fx.alice_root, "e1", b"")
    up(alice, fx.alice_root, "e2", b"")
    assert dup_groups(alice) == []


def test_unreadable_copies_do_not_leak(fx: Fx) -> None:
    alice, bob = Api(fx, "alice"), Api(fx, "bob")
    up(alice, fx.alice_root, "mine.bin", b"payload " * 50)
    up(bob, fx.root_of("bob@example.com"), "bobs.bin", b"payload " * 50)
    assert dup_groups(alice) == [] and dup_groups(bob) == []  # each only sees their own copy
    up(bob, fx.root_of("bob@example.com"), "bobs2.bin", b"payload " * 50)
    groups = dup_groups(bob)
    assert len(groups) == 1 and {m["name"] for m in groups[0]["members"]} == {
        "bobs.bin",
        "bobs2.bin",
    }
    assert dup_groups(alice) == []
    other = alice.client.get(f"/api/v1/duplicates?folder_id={fx.root_of('bob@example.com')}")
    assert other.status_code == 200 and other.json() == {"groups": []}


def index_text(fx: Fx, node: uuid.UUID, body: str) -> None:
    with fx.w.ctx.sessions() as db:
        n = db.get(Node, node)
        assert n is not None
        db.add(
            SearchDoc(
                node_id=n.id,
                root_id=n.root_id,
                revision=n.revision,
                mtime_ns=n.mtime_ns or 0,
                size=n.size or 0,
                source="text",
                body=body,
                indexed_at=n.created_at,
            )
        )
        db.commit()


LONG = " ".join(f"word{i}" for i in range(400))


def test_near_duplicates_by_text_and_unrelated_files_are_not_grouped(fx: Fx) -> None:
    alice = Api(fx, "alice")
    a = up(alice, fx.alice_root, "report v1.txt", b"v1 " + LONG.encode())
    b = up(alice, fx.alice_root, "report v2.txt", b"v2 " + LONG.encode())
    c = up(alice, fx.alice_root, "unrelated.txt", b"x")
    index_text(fx, a, LONG)
    index_text(fx, b, LONG.replace("word200", "changed") + " extra closing line")
    index_text(fx, c, " ".join(f"other{i}" for i in range(400)))
    groups = dup_groups(alice)
    assert len(groups) == 1 and groups[0]["kind"] == "near" and groups[0]["similarity"] >= 850
    assert {m["node_id"] for m in groups[0]["members"]} == {str(a), str(b)}
    assert dup_groups(alice, near=False) == []


def test_private_files_are_never_compared_by_text(fx: Fx) -> None:
    alice = Api(fx, "alice")
    a = up(alice, fx.alice_root, "a.txt", b"a " + LONG.encode())
    b = up(alice, vault(fx, alice), "b.txt", b"b " + LONG.encode())
    index_text(fx, a, LONG)
    index_text(fx, b, LONG)
    assert dup_groups(alice) == []


def test_sketch_similarity_behaves() -> None:
    a = duplicates.sketch_of(LONG)
    assert duplicates.similarity(a, a) == 1000
    assert (
        duplicates.similarity(a, duplicates.sketch_of(" ".join(f"z{i}" for i in range(400)))) == 0
    )
    assert duplicates.similarity([], a) == 0


def test_node_rows_exist_after_listing(fx: Fx) -> None:
    alice = Api(fx, "alice")
    up(alice, fx.alice_root, "a.bin", b"same " * 40)
    up(alice, fx.alice_root, "b.bin", b"same " * 40)
    dup_groups(alice)
    rows = fx.w.db_rows(Node)
    assert len([n for n in rows if n.name in ("a.bin", "b.bin") and n.status == "active"]) == 2


# --- OCR assist ----------------------------------------------------------------------------------
def test_ocr_fills_scanned_pages_without_text_and_survives_failures(
    fx: Fx, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = fx.w.settings
    monkeypatch.setattr(split.ocr, "configured", lambda url: True)
    seen: list[list[int]] = []

    class FakePreviews:
        def pdf_part(self, file: Any, size: int, pages: list[int]) -> bytes:
            seen.append(pages)
            if pages == [3]:
                raise PreviewError("SPLIT_FAILED")
            return b"%PDF-1.4 fake"

    def fake_ocr(url: str, source: Any, size: int, ext: str, **kw: Any) -> str:
        assert ext == "pdf" and kw["max_pages"] == 1
        return "Scanned letter Page 1 of 1"

    monkeypatch.setattr(split.ocr, "ocr_text", fake_ocr)
    scan = {"blank": False, "chars": 0, "has_image": True, "head": "", "marker": None}
    desc = [
        {**scan, "n": 1},
        {**scan, "n": 2, "chars": 5, "head": "text"},  # has a text layer: not sent to OCR
        {**scan, "n": 3},  # OCR fails: stays without text
        {**scan, "n": 4, "has_image": False},  # nothing to read
    ]
    done = split._fill_ocr(settings, FakePreviews(), None, 10, desc)  # type: ignore[arg-type]
    assert done == 1 and seen == [[1], [3]]
    assert desc[0]["head"] == "Scanned letter Page 1 of 1" and desc[0]["marker"] == [1, 1]
    assert desc[2]["head"] == "" and desc[3]["head"] == ""
