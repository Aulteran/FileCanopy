"""M07d in-app viewer (ADR-019): `/view` access, type allow-list and content check, Range edge
cases, hostile content served inert, and one `viewed` event per open."""

import uuid
from pathlib import Path
from typing import Any

import pytest
from test_acl import Fx
from test_auth import World
from test_files import Api, code, nid
from test_trash import events_of

from app.storage import viewable

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fx:
    return Fx(World(tmp_path, monkeypatch))


@pytest.fixture
def alice(fx: Fx) -> Api:
    return Api(fx, "alice")


def view(api: Api, node: uuid.UUID, **headers: str) -> Any:
    return api.get(f"/nodes/{node}/view", headers=headers)


def test_view_serves_inline_with_a_server_chosen_type_and_safe_headers(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.alice_root, "doc.PDF", PDF))
    r = view(alice, f)
    assert r.status_code == 200 and r.content == PDF
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == "inline; filename*=UTF-8''doc.PDF"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["accept-ranges"] == "bytes"
    assert "sandbox" in r.headers["content-security-policy"]
    assert "no-store" in r.headers["cache-control"]
    # the download endpoint is unchanged
    d = alice.download(f)
    assert d.headers["content-disposition"].startswith("attachment")
    assert d.headers["content-type"] == "application/octet-stream"


def test_view_denied_for_other_users_and_for_the_administrator(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.alice_root, "p.txt", b"private"))
    dave = Api(fx, "dave")
    for r in (view(dave, f), view(dave, uuid.uuid4())):
        assert r.status_code == 403 and r.json() == {"detail": {"code": "FORBIDDEN"}}
    c, _ = fx.admin
    r = c.get(f"/api/v1/nodes/{f}/view")
    assert r.status_code == 403 and b"private" not in r.content
    assert fx.w.browser().get(f"/api/v1/nodes/{f}/view").status_code == 401
    # a viewer grant reads through /view, exactly like download
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "viewer")
    g = nid(alice.upload(fx.taxes, "t.txt", b"taxes"))
    assert view(Api(fx, "bob"), g).content == b"taxes"


def test_unlisted_and_mismatched_types_and_folders_are_refused(fx: Fx, alice: Api) -> None:
    exe = nid(alice.upload(fx.alice_root, "tool.exe", b"MZ"))
    html = nid(alice.upload(fx.alice_root, "page.html", b"<script>alert(1)</script>"))
    none = nid(alice.upload(fx.alice_root, "README", b"hello"))
    for n in (exe, html, none):
        r = view(alice, n)
        assert r.status_code == 415 and code(r) == "NOT_VIEWABLE"
    fake_pdf = nid(alice.upload(fx.alice_root, "a.pdf", b"<html><script>x</script></html>"))
    fake_png = nid(alice.upload(fx.alice_root, "a.png", PDF))
    binary_txt = nid(alice.upload(fx.alice_root, "a.txt", b"abc\x00\x01\x02"))
    bad_utf8 = nid(alice.upload(fx.alice_root, "b.txt", b"\xff\xfe\xfa" * 40))
    plain_svg = nid(alice.upload(fx.alice_root, "a.svg", b"just words"))
    for n in (fake_pdf, fake_png, binary_txt, bad_utf8, plain_svg):
        r = view(alice, n)
        assert r.status_code == 415 and code(r) == "TYPE_MISMATCH", r.text
    r = view(alice, fx.taxes)  # a folder
    assert r.status_code == 422 and code(r) == "NOT_A_FILE"


def test_scripted_svg_and_html_like_text_are_served_inert(fx: Fx, alice: Api) -> None:
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    s = nid(alice.upload(fx.alice_root, "x.svg", svg))
    r = view(alice, s)
    assert r.status_code == 200 and r.headers["content-type"] == "image/svg+xml"
    csp = r.headers["content-security-policy"]
    assert "sandbox" in csp and "default-src 'none'" in csp  # scripts cannot run even if opened
    t = nid(alice.upload(fx.alice_root, "n.md", b"<script>alert(1)</script>"))
    r = view(alice, t)
    assert r.headers["content-type"].startswith("text/plain")  # never text/html


def test_range_edge_cases(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.alice_root, "r.txt", b"0123456789" * 10))
    body = b"0123456789" * 10

    def ranged(spec: str) -> Any:
        return view(alice, f, Range=spec)

    r = ranged("bytes=0-9")
    assert r.status_code == 206 and r.content == body[:10]
    assert r.headers["content-range"] == "bytes 0-9/100" and r.headers["content-length"] == "10"
    r = ranged("bytes=90-")
    assert r.status_code == 206 and r.content == body[90:]
    r = ranged("bytes=-5")
    assert r.status_code == 206 and r.content == body[-5:]
    r = ranged("bytes=-500")  # suffix larger than the file: the whole file
    assert r.status_code == 206 and r.content == body
    r = ranged("bytes=95-5000")  # end clamped
    assert r.content == body[95:] and r.headers["content-range"] == "bytes 95-99/100"
    for bad in ("bytes=100-", "bytes=500-600", "bytes=-0"):
        r = ranged(bad)
        assert r.status_code == 416 and r.headers["content-range"] == "bytes */100", bad
    for ignored in ("bytes=5-2", "bytes=a-b", "bytes=0-1,5-6", "items=0-1", "bytes=", "garbage"):
        r = ranged(ignored)
        assert r.status_code == 200 and r.content == body, ignored
    etag = view(alice, f).headers["etag"]
    r = view(alice, f, Range="bytes=0-9", **{"If-Range": '"other"'})
    assert r.status_code == 200 and r.content == body
    r = view(alice, f, Range="bytes=0-9", **{"If-Range": etag})
    assert r.status_code == 206


def test_empty_file_and_range(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.alice_root, "empty.txt", b""))
    r = view(alice, f)
    assert r.status_code == 200 and r.content == b""
    assert view(alice, f, Range="bytes=0-5").status_code == 416


def test_viewed_is_logged_once_per_open_and_not_for_refusals(fx: Fx, alice: Api) -> None:
    f = nid(alice.upload(fx.alice_root, "v.pdf", PDF))
    view(alice, f)  # open
    view(alice, f, Range="bytes=10-20")  # later chunks of the same open: nothing new
    view(alice, f, Range="bytes=0-9")  # PDF.js opening the file again within a minute
    actions = [e.action for e in events_of(fx, f)]
    assert actions.count("viewed") == 1
    assert "downloaded" not in actions
    ev = [e for e in events_of(fx, f) if e.action == "viewed"][0]
    assert ev.source == "ui" and ev.actor_user_id == alice.uid
    # refusals and mid-file reads never log
    bad = nid(alice.upload(fx.alice_root, "z.pdf", b"nope"))
    view(alice, bad)
    view(alice, f, Range="bytes=1000-")
    assert not [e for e in events_of(fx, bad) if e.action == "viewed"]
    # another person's open is their own event
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "viewer")
    g = nid(alice.upload(fx.taxes, "t.png", PNG))
    view(alice, g)
    view(Api(fx, "bob"), g)
    assert [e.action for e in events_of(fx, g)].count("viewed") == 2


def test_viewed_dedupe_expires(fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import timedelta

    from app.api import files as files_api

    f = nid(alice.upload(fx.alice_root, "v.pdf", PDF))
    view(alice, f)
    monkeypatch.setattr(files_api, "VIEW_DEDUPE", timedelta(seconds=-1))
    view(alice, f)
    assert [e.action for e in events_of(fx, f)].count("viewed") == 2


def test_parse_range_unit() -> None:
    assert viewable.parse_range("bytes=0-0", 1) == (0, 0)
    assert viewable.parse_range("bytes=0-", 0) == "unsatisfiable"
    assert viewable.parse_range("bytes=-3", 0) == "unsatisfiable"
    assert viewable.parse_range("bytes=٣-5", 10) is None  # non-ASCII digits are not accepted
    for odd in ("bytes=+1-5", "bytes=1 -5", "bytes=1_0-5", "bytes=-", "bytes=1--5"):
        assert viewable.parse_range(odd, 100) is None, odd
