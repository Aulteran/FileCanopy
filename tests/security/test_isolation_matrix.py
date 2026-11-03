"""M23: persona x surface isolation matrix (09 "ACL matrix", 03 mandatory tests 1, 2, 4, 7, 10).

Alice owns an open folder shared with Bob (viewer) and Carol (editor), a folder nobody else may
open, a Private-labelled folder and a Confidential-labelled folder. For each persona and each of
the four files the same question is asked on every surface that can answer it: metadata, content,
view, extract, thumbnail, activity, folder listing, filename search, full-text search,
collections and the MCP tools. A denied file must show up nowhere: not in a name, a snippet, an id
or a count. The surfaces must also agree with one another ("semantics consistency"): what the
content endpoint refuses, search and MCP never mention.
"""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from test_acl import Fx
from test_ai import file_in, label
from test_ai_organize import index
from test_auth import World
from test_files import Api
from test_mcp import ALL_SCOPES, Client, mint

FILES = {  # key -> (file name, body word)
    "open": ("openname.txt", "openbodyword"),
    "hidden": ("hiddenname.txt", "hiddenbodyword"),
    "diary": ("diaryname.txt", "diarybodyword"),
    "vault": ("vaultname.txt", "vaultbodyword"),
}
PERSONAS = ("alice", "bob", "carol", "dave")
# Who may read what (Confidential is locked for everyone, the owner included, until unlocked).
READABLE = {
    "alice": {"open", "hidden", "diary"},
    "bob": {"open"},
    "carol": {"open"},
    "dave": set(),
}
NORMAL = {"open", "hidden"}  # the only labels an MCP client is ever offered


class Matrix:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.fx = Fx(World(tmp_path, monkeypatch, mcp_enabled=True))
        fx = self.fx
        self.api = {p: Api(fx, p) for p in PERSONAS}
        self.admin = Api(fx, "admin")  # signed-in administrator without any grant
        alice = self.api["alice"]
        fx.grant(fx.alice, fx.taxes, "bob@example.com", "viewer")
        fx.grant(fx.alice, fx.taxes, "carol@example.com", "editor")
        hidden_dir = fx.folder(fx.alice_root, "Medical")
        diary_dir = fx.folder(fx.alice_root, "Diary")
        vault_dir = fx.folder(fx.alice_root, "Vault")
        self.dirs = {
            "open": fx.taxes,
            "hidden": hidden_dir,
            "diary": diary_dir,
            "vault": vault_dir,
        }
        self.node: dict[str, uuid.UUID] = {}
        for key, (
            name,
            word,
        ) in FILES.items():  # files first: a locked folder refuses uploads
            self.node[key] = file_in(alice, self.dirs[key], name)
            index(fx, self.node[key], f"quarterly figures {word}")
        label(alice, diary_dir, "private")
        label(alice, vault_dir, "confidential")
        self.mcp = {p: Client(fx, mint(self.api[p], ALL_SCOPES)[0]) for p in PERSONAS}
        # a collection of everything, shared with Bob as a viewer
        made = alice.post(
            "/collections",
            json={
                "title": "All",
                "node_ids": [str(self.node[k]) for k in ("open", "hidden")],
            },
        )
        assert made.status_code == 201, made.text
        self.collection = made.json()["id"]
        r = alice.client.put(
            f"/api/v1/collections/{self.collection}/shares/{self.api['bob'].uid}",
            json={"level": "viewer"},
            headers=alice.headers,
        )
        assert r.status_code == 200, r.text

    def all_text(self, persona: str) -> str:
        """Everything the persona is told by the list-style surfaces, as one string."""
        api = self.api[persona]
        parts: list[str] = []
        for q in [n for n, _ in FILES.values()] + [w for _, w in FILES.values()]:
            parts.append(api.get("/search", params={"q": q}).text)
        parts.append(api.get("/collections").text)
        parts.append(api.get(f"/collections/{self.collection}").text)
        parts.append(api.get("/roots").text)
        for d in self.dirs.values():
            parts.append(api.get(f"/nodes/{d}/children").text)
            parts.append(api.get(f"/nodes/{d}").text)
        parts.append(api.get("/activity/mine").text)
        parts.append(api.get("/trash").text)
        m = self.mcp[persona]
        for tool, args in [
            ("search_files", {"query": "openname"}),
            ("search_files", {"query": "hiddenname"}),
            ("search_files", {"query": "hiddenbodyword"}),
            ("list_collections", {}),
        ]:
            parts.append(json.dumps(m.call(tool, **args)))
        return "\n".join(parts)


@pytest.fixture
def mx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Matrix:
    return Matrix(tmp_path, monkeypatch)


def surfaces_of(api: Api, nid: uuid.UUID | str) -> dict[str, int]:
    return {
        "metadata": api.get(f"/nodes/{nid}").status_code,
        "content": api.get(f"/nodes/{nid}/content").status_code,
        "view": api.get(f"/nodes/{nid}/view").status_code,
        "extract": api.get(f"/nodes/{nid}/extract").status_code,
        "thumbnail": api.get(f"/nodes/{nid}/thumbnail").status_code,
        "activity": api.get(f"/nodes/{nid}/activity").status_code,
    }


@pytest.mark.parametrize("persona", PERSONAS)
def test_every_node_surface_agrees_with_the_policy(mx: Matrix, persona: str) -> None:
    api = mx.api[persona]
    missing = surfaces_of(api, uuid.uuid4())
    for key in FILES:
        got = surfaces_of(api, mx.node[key])
        if key in READABLE[persona]:
            assert got["metadata"] == 200 and got["content"] == 200, (persona, key, got)
        else:
            for surface, status in got.items():
                assert status in (401, 403, 404, 423), (persona, key, surface, status)
                if persona != "alice":
                    # Not different from "no such id" in a way that confirms existence.
                    assert status == missing[surface] or status == 403, (
                        persona,
                        key,
                        surface,
                    )


@pytest.mark.parametrize("persona", PERSONAS)
def test_nothing_unreadable_appears_in_any_list_search_or_count(mx: Matrix, persona: str) -> None:
    text = mx.all_text(persona)
    for key, (name, word) in FILES.items():
        stem = name.removesuffix(".txt")
        if key in READABLE[persona] and key in NORMAL:
            assert stem in text, (
                persona,
                key,
            )  # sanity: the surfaces do show what is readable
        elif key not in READABLE[persona]:
            assert stem not in text, (persona, key, "name")
            assert word not in text, (persona, key, "body")
            assert str(mx.node[key]) not in text, (persona, key, "id")


def _walk(value: Any) -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            found.append((k, v))
            found.extend(_walk(v))
    elif isinstance(value, list):
        for v in value:
            found.extend(_walk(v))
    return found


def test_shared_collection_hides_unreadable_members_without_a_trace(mx: Matrix) -> None:
    bob = mx.api["bob"]
    view = bob.get(f"/collections/{mx.collection}")
    assert view.status_code == 200
    body = view.json()
    members = json.dumps(body)
    assert "openname" in members  # Bob can read this one
    for stem in ("hiddenname", "diaryname", "vaultname"):
        assert stem not in members
    counts = [v for k, v in _walk(body) if k in ("count", "member_count", "total")]
    assert all(c <= 1 for c in counts if isinstance(c, int)), counts
    assert mx.api["dave"].get(f"/collections/{mx.collection}").status_code == 404


@pytest.mark.parametrize("persona", PERSONAS)
def test_mcp_offers_exactly_the_readable_normal_files(mx: Matrix, persona: str) -> None:
    client = mx.mcp[persona]
    for key in FILES:
        res = client.call("get_file_metadata", node_id=str(mx.node[key]))
        should = key in READABLE[persona] and key in NORMAL
        assert (res["isError"] is False) == should, (persona, key, res)
        if not should:
            assert res["content"][0]["text"] == "Not found."
        text = client.call("get_text_excerpt", node_id=str(mx.node[key]))
        assert (text["isError"] is False) == should, (persona, key)


def test_the_administrator_without_a_grant_sees_nothing_of_personal_content(
    mx: Matrix,
) -> None:
    admin = mx.admin
    for key in FILES:
        for path in ("", "/content", "/view", "/extract", "/thumbnail", "/activity"):
            r = admin.get(f"/nodes/{mx.node[key]}{path}")
            assert r.status_code in (403, 404), (key, path, r.status_code)
            assert FILES[key][0] not in r.text
    for q in ("openname", "hiddenname", "hiddenbodyword"):
        assert admin.get("/search", params={"q": q}).json()["hits"] == []
    assert admin.get(f"/nodes/{mx.dirs['hidden']}/children").status_code in (403, 404)


def test_revoking_a_grant_closes_every_surface_for_an_open_session(mx: Matrix) -> None:
    bob, alice = mx.api["bob"], mx.api["alice"]
    assert surfaces_of(bob, mx.node["open"])["content"] == 200
    assert "openname" in bob.get("/search", params={"q": "openname"}).text
    r = alice.client.delete(f"/api/v1/nodes/{mx.fx.taxes}/grants/{bob.uid}", headers=alice.headers)
    assert r.status_code == 200, r.text
    after = surfaces_of(bob, mx.node["open"])
    assert all(s in (403, 404) for s in after.values()), after
    assert "openname" not in bob.get("/search", params={"q": "openname"}).text
    assert "openname" not in json.dumps(mx.mcp["bob"].call("search_files", query="openname"))
    assert "openname" not in bob.get(f"/collections/{mx.collection}").text


def test_raising_a_label_keeps_access_but_closes_the_machine_surfaces(
    mx: Matrix,
) -> None:
    """Private changes who a machine may show the file to, not who may open it (03 label table)."""
    alice, bob = mx.api["alice"], mx.api["bob"]
    assert mx.mcp["bob"].call("get_file_metadata", node_id=str(mx.node["open"]))["isError"] is False
    label(alice, mx.dirs["open"], "private")
    assert surfaces_of(bob, mx.node["open"])["content"] == 200  # access follows the ACL
    for who in ("alice", "bob"):
        client = mx.mcp[who]
        for tool in ("get_file_metadata", "get_text_excerpt"):
            res = client.call(tool, node_id=str(mx.node["open"]))
            assert res["isError"] is True, (who, tool)
        assert "openname" not in json.dumps(client.call("search_files", query="openname"))
