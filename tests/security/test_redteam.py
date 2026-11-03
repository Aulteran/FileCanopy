"""M23: red-team name and path corpus fed to every operation that takes a name.

Each hostile string goes through create-folder, upload, rename and copy. The outcome must be a
clean refusal or a harmless success; after the whole corpus nothing may exist outside the library
and app-data, the database must hold no node whose name contains a separator or control
character, and a second person must see none of it.
"""

import os
from pathlib import Path

import pytest
from sqlalchemy import select
from test_acl import Fx
from test_auth import World
from test_files import Api

from app.db.models import Node

HOSTILE = [
    "..",
    ".",
    "../escape",
    "..\\escape",
    "a/../../escape",
    "a\\..\\..\\escape",
    "/etc/passwd",
    "C:\\Windows\\win.ini",
    "\\\\server\\share\\x",
    "CON",
    "nul.txt",
    "AUX.",
    "name\x00.txt",
    "line\nbreak",
    "tab\tname",
    "bell\x07",
    "del\x7f",
    "trailing.",
    "trailing ",
    " leading",
    "x" * 300,
    "\u202egpj.txt",  # right-to-left override
    "\u200bzero-width",
    "e\u0301.txt",  # decomposed accent
    "..%2f..%2fescape",
    "%00.txt",
    "~root",
    "$(touch pwned)",
    "`touch pwned`",
    "; rm -rf /",
    "' OR '1'='1",
    "-rf",
    "--help",
    ".filecanopy-trash",
    ".git",
    "Thumbs.db:stream",
    "file::$DATA",
]


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fx:
    return Fx(World(tmp_path, monkeypatch))


def test_hostile_names_never_escape_or_corrupt_the_tree(fx: Fx, tmp_path: Path) -> None:
    alice, bob = Api(fx, "alice"), Api(fx, "bob")
    before = {p for p in tmp_path.rglob("*")}
    target = fx.folder(fx.alice_root, "Target")
    seed = alice.upload(target, "seed.txt", b"seed")
    assert seed.status_code == 201, seed.text
    seed_id = seed.json()["node"]["id"]
    for name in HOSTILE:
        calls = (
            alice.mkdir(target, name),
            alice.upload(target, name, b"data"),
            alice.post(
                f"/nodes/{seed_id}/copy", json={"parent_id": str(target), "name": name}
            ),
            alice.post(f"/nodes/{seed_id}/rename", json={"name": name}),
        )
        for r in calls:
            assert r.status_code < 500, (name, r.status_code, r.text)
            assert "Traceback" not in r.text
        # put the seed back under a safe name so the next round starts the same
        alice.post(f"/nodes/{seed_id}/rename", json={"name": "seed.txt"})
    new = {p for p in tmp_path.rglob("*")} - before
    inside = (tmp_path / "library", tmp_path / "app-data")
    stray = [str(p) for p in new if not any(root in p.parents for root in inside)]
    assert stray == []
    assert not list(tmp_path.rglob("pwned")) and not list(tmp_path.rglob("passwd"))
    assert not (tmp_path / "escape").exists()
    with fx.w.ctx.sessions() as db:
        names = list(db.scalars(select(Node.name)))
    for n in names:
        assert "/" not in n and "\\" not in n and "\x00" not in n, repr(n)
        assert not any(ord(ch) < 32 or ord(ch) == 127 for ch in n), repr(n)
        assert n not in ("..", ".")
    # Bob, who has no grant on Alice's tree, learns nothing about any of it.
    assert bob.get(f"/nodes/{target}/children").status_code in (403, 404)
    assert bob.get("/search", params={"q": "seed"}).json()["hits"] == []


@pytest.mark.parametrize(
    "bad_id",
    [
        "..%2fx",
        "x" * 5000,
        "0" * 36,
        "%7B%7D",
        "%00",
        "null",
        "1;%20DROP%20TABLE%20node",
    ],
)
def test_hostile_ids_are_refused_on_every_node_route(fx: Fx, bad_id: str) -> None:
    alice = Api(fx, "alice")
    for suffix in (
        "",
        "/children",
        "/content",
        "/view",
        "/extract",
        "/thumbnail",
        "/activity",
    ):
        r = alice.get(f"/nodes/{bad_id}{suffix}")
        assert r.status_code in (400, 404, 422), (bad_id, suffix, r.status_code)
        assert "Traceback" not in r.text


def test_a_symlink_planted_in_a_root_is_not_followed(fx: Fx, tmp_path: Path) -> None:
    """The host owner is outside the trust model, but a link dropped into a root must not let the
    API read or write through it (03: MVP does not follow symlinks)."""
    alice = Api(fx, "alice")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("TOPSECRET-OUTSIDE")
    taxes_dir = next((tmp_path / "library").rglob("Taxes"))
    link = taxes_dir.parent / "planted.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    listing = alice.get(f"/nodes/{fx.alice_root}/children")
    assert "TOPSECRET" not in listing.text
    up = alice.upload(fx.alice_root, "planted.txt", b"overwrite attempt")
    assert outside.read_text() == "TOPSECRET-OUTSIDE", up.text
    assert (
        os.path.islink(link)
        or not link.exists()
        or link.read_text() != "TOPSECRET-OUTSIDE"
    )
