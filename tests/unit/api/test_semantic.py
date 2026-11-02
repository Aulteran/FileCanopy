"""M19 optional local semantic search: the embedding endpoint is a mock at the HTTP transport, so
nothing here needs a model, a GPU or the network. The fake embedder maps words onto topic
dimensions, which lets the relevance tests find a document that shares no word with the query."""

import json
import math
import uuid
import zlib
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_acl import Fx
from test_auth import PASSWORD, World
from test_files import Api, nid
from test_previews import put_optin
from test_privacy import label, unlock
from test_search import docs, rewrite_on_disk, run_index

from app.ai.routing import clear
from app.db.models import SearchChunk, SearchEmbed, User
from app.search import embed, semantic
from app.search.semantic import SemanticIndexer, chunk_text

ENDPOINT = "http://127.0.0.1:8081"

# Topic words per dimension. The first dimension of the vector is a small constant so no text
# embeds to the zero vector.
TOPICS = [
    {"invoice", "bill", "payment", "receipt", "amount"},
    {"holiday", "vacation", "trip", "travel", "beach"},
    {"roof", "repair", "leak", "builder", "tiles"},
    {"insurance", "claim", "policy", "premium"},
    {"recipe", "cooking", "dinner", "flour"},
    {"passport", "visa", "border", "identity"},
]


def vector_for(text: str, dim: int = 16) -> list[float]:
    """Topic words add weight to their topic's dimension; every word also adds a little hashed
    weight, so unrelated texts differ and no text embeds to the zero vector."""
    vec = [0.0] * max(dim, len(TOPICS))
    for raw in text.split():
        w = raw.strip(".,:;!?").lower()
        vec[zlib.crc32(w.encode()) % dim] += 0.3
        for i, topic in enumerate(TOPICS):
            if w in topic:
                vec[i] += 3.0
    return vec[:dim]


class Net:
    """A mock llama.cpp / Ollama embedding server."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.texts: list[str] = []
        self.dim = 16
        self.status = 200
        self.zero = False
        self.same = False
        self.noise = False
        self.swapped = False
        self.model_scale = 1.0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, text="echo " + request.content.decode())
        body = json.loads(request.content)
        texts = body["input"]
        self.texts.extend(texts)
        vecs = []
        for t in texts:
            v = vector_for(t, self.dim)
            if self.same:
                v = [1.0] + [0.0] * (self.dim - 1)
            if self.zero:
                v = [0.0] * self.dim
            if self.swapped:
                v = v[len(v) // 2 :] + v[: len(v) // 2]  # another model, same size
            if self.noise and len(self.requests) % 2:
                v = list(reversed(v))  # the same text, a different answer each call
            vecs.append(v)
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": vecs})
        return httpx.Response(
            200, json={"data": [{"index": i, "embedding": v} for i, v in enumerate(vecs)]}
        )


@pytest.fixture
def net() -> Net:
    return Net()


@pytest.fixture
def make_fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Fx]:
    def factory(**overrides: Any) -> Fx:
        return Fx(World(tmp_path, monkeypatch, **{"semantic_pause_ms": 0, **overrides}))

    return factory


@pytest.fixture
def fx(make_fx: Callable[..., Fx]) -> Fx:
    return make_fx()


@pytest.fixture
def alice(fx: Fx) -> Api:
    return Api(fx, "alice")


@pytest.fixture
def admin(fx: Fx) -> Api:
    return Api(fx, "admin")


def put(api: Api, path: str, body: dict[str, Any]) -> Any:
    return api.client.put(f"/api/v1{path}", json=body, headers=api.headers)


def sem_of(fx: Fx) -> semantic.Semantic:
    sem: semantic.Semantic = fx.w.app.state.semantic
    return sem


def indexer(fx: Fx) -> SemanticIndexer:
    return SemanticIndexer(sem_of(fx))


def configure(
    fx: Fx,
    admin: Api,
    net: Net,
    *,
    kind: str = "openai_compatible",
    model: str = "embedding-model",
    enable: bool = True,
) -> Any:
    sem_of(fx).transport = httpx.MockTransport(net)
    r = put(admin, "/admin/semantic", {"kind": kind, "endpoint": ENDPOINT, "model": model})
    assert r.status_code == 200, r.text
    v = admin.post("/admin/semantic/verify")
    assert v.status_code == 200, v.text
    if enable:
        e = put(admin, "/admin/semantic/enabled", {"enabled": True})
        assert e.status_code == 200, e.text
        return e.json()
    return v.json()


def run_semantic(fx: Fx) -> int:
    """Text indexing first (M11), then one embedding pass."""
    run_index(fx)
    return indexer(fx).tick()


def search_json(api: Api, q: str, **params: Any) -> dict[str, Any]:
    r = api.get("/search", params={"q": q, **params})
    assert r.status_code == 200, r.text
    return dict(r.json())


def sem_names(api: Api, q: str, mode: str = "semantic", **params: Any) -> list[str]:
    return sorted(h["name"] for h in search_json(api, q, mode=mode, **params)["hits"])


def chunks(fx: Fx) -> list[SearchChunk]:
    return fx.w.db_rows(SearchChunk)


def seed(fx: Fx, alice: Api) -> dict[str, uuid.UUID]:
    ids = {
        "invoice": nid(alice.upload(fx.taxes, "march.txt", b"Payment due: the bill amount is 40")),
        "holiday": nid(alice.upload(fx.taxes, "summer.txt", b"A trip to the beach, great travel")),
        "roof": nid(alice.upload(fx.taxes, "house.txt", b"The builder will repair the leak")),
    }
    return ids


# ---------------------------------------------------------------- off by default, no AI


def test_it_is_off_until_verified_and_full_text_search_works_without_it(
    fx: Fx, alice: Api, admin: Api, net: Net
) -> None:
    seed(fx, alice)
    run_semantic(fx)
    assert chunks(fx) == [] and net.requests == []  # nothing runs, nothing is sent anywhere
    state = admin.get("/admin/semantic").json()
    assert state["state"] == "not_configured" and not state["active"]
    assert alice.get("/search/status").json()["semantic_active"] is False
    out = search_json(alice, "bill")
    assert out["mode"] == "fulltext" and [h["name"] for h in out["hits"]] == ["march.txt"]
    # asking for a semantic mode while it is off quietly gives the full-text answer
    out = search_json(alice, "bill", mode="semantic")
    assert out["mode"] == "fulltext" and [h["name"] for h in out["hits"]] == ["march.txt"]
    # saving an endpoint and switching on without verifying is refused
    sem_of(fx).transport = httpx.MockTransport(net)
    r = put(admin, "/admin/semantic", {"kind": "ollama", "endpoint": ENDPOINT, "model": "m"})
    assert r.status_code == 200 and r.json()["state"] == "not_verified"
    assert put(admin, "/admin/semantic/enabled", {"enabled": True}).status_code == 409
    assert net.requests == []


def test_only_administrators_configure_it_and_only_local_endpoints_are_accepted(
    fx: Fx, alice: Api, admin: Api
) -> None:
    body = {"kind": "openai_compatible", "endpoint": ENDPOINT, "model": "m"}
    assert put(alice, "/admin/semantic", body).status_code == 403
    assert alice.post("/admin/semantic/verify").status_code == 403
    assert alice.get("/admin/semantic").status_code == 403
    for host in ("http://8.8.8.8:80", "http://169.254.169.254", "http://0.0.0.0:80"):
        r = put(admin, "/admin/semantic", {**body, "endpoint": host})
        assert r.status_code == 422, host
    for kind in ("anthropic", "openai", "gemini"):  # no cloud kind exists for embeddings
        assert put(admin, "/admin/semantic", {**body, "kind": kind}).status_code == 422
    with pytest.raises(ValueError):
        embed.validate_spec(
            "openai", "", "m", sem_of(fx).resolver, sem_of(fx).allow_hosts
        )  # the module itself refuses too
    assert put(admin, "/admin/semantic", {**body, "model": "bad model!"}).status_code == 422
    assert admin.get("/admin/semantic").json()["state"] == "not_configured"


# ---------------------------------------------------------------- health and dimension


def test_verify_checks_health_and_a_stable_dimension(fx: Fx, admin: Api, net: Net) -> None:
    state = configure(fx, admin, net, enable=False)
    assert state["healthy"] and state["dimension"] == 16 and state["state"] == "off"
    assert state["generation"] == 1 and not state["enabled"]
    # the check sent only the fixed synthetic probes, never a document
    assert set(net.texts) == set(embed.PROBES)
    assert put(admin, "/admin/semantic/enabled", {"enabled": True}).json()["active"] is True
    assert put(admin, "/admin/semantic/enabled", {"enabled": False}).json()["active"] is False


@pytest.mark.parametrize(
    ("break_it", "code"),
    [
        (lambda n: setattr(n, "zero", True), "EMBEDDING_BAD_VECTOR"),
        (lambda n: setattr(n, "dim", 3), "EMBEDDING_DIMENSION_UNSUPPORTED"),
        (lambda n: setattr(n, "dim", 5000), "EMBEDDING_DIMENSION_UNSUPPORTED"),
        (lambda n: setattr(n, "same", True), "EMBEDDING_DEGENERATE"),
        (lambda n: setattr(n, "noise", True), "EMBEDDING_UNSTABLE"),
        (lambda n: setattr(n, "status", 503), "EMBEDDING_OUTAGE"),
        (lambda n: setattr(n, "status", 401), "EMBEDDING_AUTH"),
    ],
)
def test_a_bad_endpoint_never_becomes_healthy(
    fx: Fx, admin: Api, net: Net, break_it: Callable[[Net], None], code: str
) -> None:
    break_it(net)
    sem_of(fx).transport = httpx.MockTransport(net)
    put(admin, "/admin/semantic", {"kind": "openai_compatible", "endpoint": ENDPOINT, "model": "m"})
    r = admin.post("/admin/semantic/verify")
    assert r.status_code == 502 and r.json()["detail"]["code"] == code
    state = admin.get("/admin/semantic").json()
    assert not state["healthy"] and not state["active"] and state["error"]
    assert put(admin, "/admin/semantic/enabled", {"enabled": True}).status_code == 409
    assert "echo" not in r.text  # the server's reply body is never copied into an error


def test_ollama_wire_format_works_too(fx: Fx, admin: Api, net: Net) -> None:
    state = configure(fx, admin, net, kind="ollama")
    assert state["active"] and net.requests[0].url.path == "/api/embed"


def test_pgvector_missing_keeps_it_off_and_changes_nothing_else(
    fx: Fx, alice: Api, admin: Api, net: Net, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(semantic, "pgvector_ready", lambda db: False)
    sem_of(fx).transport = httpx.MockTransport(net)
    put(admin, "/admin/semantic", {"kind": "ollama", "endpoint": ENDPOINT, "model": "m"})
    assert admin.post("/admin/semantic/verify").status_code == 409
    state = admin.get("/admin/semantic").json()
    assert state["state"] == "pgvector_missing" and state["pgvector"] is False
    alice.upload(fx.taxes, "a.txt", b"still searchable")
    run_index(fx)
    assert [h["name"] for h in search_json(alice, "searchable")["hits"]] == ["a.txt"]


def test_the_endpoint_is_checked_again_at_every_request(
    fx: Fx, admin: Api, net: Net, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(fx, admin, net)
    addresses = {"embed.internal": ["127.0.0.1"]}
    sem = sem_of(fx)
    sem.resolver = lambda host, port: addresses[host]
    put(
        admin,
        "/admin/semantic",
        {"kind": "ollama", "endpoint": "http://embed.internal:8081", "model": "m"},
    )
    admin.post("/admin/semantic/verify")
    assert all(r.url.host == "127.0.0.1" for r in net.requests)  # connects to the checked address
    addresses["embed.internal"] = ["169.254.169.254"]  # the name now points at metadata
    sem.resolver = lambda host, port: addresses[host]
    net.requests.clear()
    with sem.sessions() as db:
        sem.recheck(db)
        assert semantic.load_config(db).healthy is False
    assert net.requests == []  # refused before any packet was sent


# ---------------------------------------------------------------- search quality


def test_semantic_finds_related_text_that_shares_no_words_and_hybrid_keeps_keywords(
    fx: Fx, alice: Api, admin: Api, net: Net
) -> None:
    seed(fx, alice)
    alice.upload(fx.taxes, "unrelated.txt", b"Nothing about the topics: giraffe zebra")
    configure(fx, admin, net)
    assert run_semantic(fx) > 0
    assert len(chunks(fx)) == 4
    # "vacation" and "holiday" share no word: only the vector finds the beach document
    assert alice.get("/search", params={"q": "vacation", "mode": "fulltext"}).json()["hits"] == []
    assert sem_names(alice, "vacation holiday") == ["summer.txt"]
    assert sem_names(alice, "invoice for payment") == ["march.txt"]
    assert sem_names(alice, "leak repair roof") == ["house.txt"]
    assert sem_names(alice, "zzz qqq") == []  # below the similarity floor
    # hybrid: the file with the keyword ranks first (it is in both lists), the related one follows
    alice.upload(fx.taxes, "keyword.txt", b"holiday camp giraffe")
    run_semantic(fx)
    out = search_json(alice, "holiday", mode="hybrid")
    assert out["mode"] == "hybrid"
    assert [h["name"] for h in out["hits"]] == ["keyword.txt", "summer.txt"]
    assert "holiday" in out["hits"][0]["snippet"]
    assert search_json(alice, "payment")["mode"] == "hybrid"  # auto picks hybrid once active


def test_a_document_in_both_lists_appears_once_and_a_name_hit_takes_a_semantic_snippet() -> None:
    a = uuid.uuid4()
    from app.search.query import Hit

    ft = [Hit(a, "invoice.txt", "file", None, "name", None)]
    sm = [Hit(a, "invoice.txt", "file", None, "semantic", "the bill amount")]
    merged = semantic.fuse(ft, sm, 10)
    assert len(merged) == 1 and merged[0].snippet == "the bill amount"
    assert merged[0].match == "name"


def test_chunking_is_bounded_overlapping_and_covers_the_text() -> None:
    body = " ".join(f"word{i}" for i in range(2000))
    spans = chunk_text(body, 500, 100, 1000)
    assert spans[0][0] == 0 and spans[-1][1] == len(body)
    assert all(0 < e - s <= 500 for s, e in spans)
    assert all(b[0] < a[1] for a, b in zip(spans, spans[1:], strict=False))  # overlap
    assert len(chunk_text(body, 500, 100, 3)) == 3  # per-file cap
    assert chunk_text("   ", 500, 100, 10) == [] and chunk_text("", 500, 100, 10) == []
    assert chunk_text("tiny", 500, 100, 10) == [(0, 4)]


# ---------------------------------------------------------------- access control


def test_results_follow_the_acl_and_never_hint_at_hidden_files(
    fx: Fx, alice: Api, admin: Api, net: Net
) -> None:
    seed(fx, alice)
    bob, carol = Api(fx, "bob"), Api(fx, "carol")
    configure(fx, admin, net)
    run_semantic(fx)
    for who in (bob, carol, admin):  # not theirs: nothing, and the same empty answer as any miss
        out = search_json(who, "vacation holiday", mode="semantic")
        assert out["hits"] == [] and set(out) == {"hits", "mode"}
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "viewer")
    assert sem_names(bob, "vacation holiday") == ["summer.txt"]
    assert sem_names(carol, "vacation holiday") == []
    r = alice.client.delete(
        f"/api/v1/nodes/{fx.taxes}/grants/{fx.uid('bob@example.com')}", headers=alice.headers
    )
    assert r.status_code in (200, 204)
    assert sem_names(bob, "vacation holiday") == []  # decided again now: no re-index needed


def test_a_folder_filter_never_opens_a_folder_the_person_cannot_see(
    fx: Fx, alice: Api, admin: Api, net: Net
) -> None:
    seed(fx, alice)
    bob = Api(fx, "bob")
    configure(fx, admin, net)
    run_semantic(fx)
    assert sem_names(alice, "vacation", folder=str(fx.taxes)) == ["summer.txt"]
    assert sem_names(alice, "vacation", folder=str(fx.private)) == []
    assert sem_names(bob, "vacation", folder=str(fx.taxes)) == []


def test_trashed_and_changed_files_drop_out_and_are_embedded_again(
    fx: Fx, alice: Api, admin: Api, net: Net
) -> None:
    ids = seed(fx, alice)
    configure(fx, admin, net)
    run_semantic(fx)
    assert sem_names(alice, "vacation") == ["summer.txt"]
    rewrite_on_disk(fx, ids["holiday"], b"now about a recipe for dinner with flour")
    assert sem_names(alice, "vacation") == []  # the stale vector is hidden at once
    run_semantic(fx)
    assert sem_names(alice, "vacation") == []
    assert sem_names(alice, "dinner recipe") == ["summer.txt"]
    r = alice.client.delete(
        f"/api/v1/nodes/{ids['invoice']}", params={"expected_revision": 1}, headers=alice.headers
    )
    assert r.status_code in (200, 204)
    assert sem_names(alice, "bill payment") == []
    with fx.w.ctx.sessions() as db:
        assert indexer(fx).sweep(db) >= 1
    assert ids["invoice"] not in {c.node_id for c in chunks(fx)}


def test_shared_tree_files_are_found_by_members(fx: Fx, alice: Api, admin: Api, net: Net) -> None:
    bob = Api(fx, "bob")
    bob.upload(fx.shared_sub, "family.txt", b"our holiday trip to the beach")
    configure(fx, admin, net)
    run_semantic(fx)
    assert sem_names(alice, "vacation") == ["family.txt"]
    assert sem_names(bob, "vacation") == ["family.txt"]


# ---------------------------------------------------------------- Private and Confidential


def test_confidential_text_is_never_embedded_and_a_raised_label_hides_old_vectors(
    fx: Fx, alice: Api, admin: Api, net: Net
) -> None:
    label(alice, fx.y2026, "confidential")
    assert unlock(alice, fx.y2026, PASSWORD).status_code == 200
    put_optin(alice, True)
    alice.upload(fx.y2026, "vault.txt", b"vault: passport and visa for the border trip")
    alice.upload(fx.other, "plain.txt", b"ordinary: bill and payment amount")
    configure(fx, admin, net)
    run_semantic(fx)
    assert "vault" not in " ".join(net.texts)  # not even sent to the local endpoint
    assert {c.node_id for c in chunks(fx)} == {
        n for n in docs(fx) if n not in {j.node_id for j in fx.w.db_rows(SearchEmbed) if j.error}
    } - set()
    assert sem_names(alice, "passport visa") == []  # unlocked, opt-in on: still nothing
    # text that was embedded as Normal stops being returned the moment the label is raised
    assert sem_names(alice, "bill payment") == ["plain.txt"]
    label(alice, fx.taxes, "confidential")
    assert sem_names(alice, "bill payment") == []
    with fx.w.ctx.sessions() as db:
        indexer(fx).sweep(db)
    assert chunks(fx) == []


def test_private_text_needs_the_local_processing_opt_in_and_the_persons_own_switch(
    fx: Fx, alice: Api, admin: Api, net: Net
) -> None:
    label(alice, fx.private, "private")
    n = nid(alice.upload(fx.private, "diary.txt", b"dear diary: a holiday trip"))
    configure(fx, admin, net)
    run_semantic(fx)  # no opt-in: not even text-indexed, so nothing to embed
    assert chunks(fx) == [] and "diary" not in " ".join(net.texts)
    put_optin(alice, True)
    fx.w.advance(hours=1, seconds=1)
    run_semantic(fx)
    assert {c.node_id for c in chunks(fx)} == {n}
    assert sem_names(alice, "vacation") == ["diary.txt"]
    put_optin(alice, False)  # withdrawing consent hides it before any sweep
    assert sem_names(alice, "vacation") == []
    put_optin(alice, True)
    assert sem_names(alice, "vacation") == ["diary.txt"]
    # the separate "no semantic indexing" switch removes the vectors immediately
    me = alice.get("/search/semantic/me").json()
    assert me == {"available": True, "opt_out": False, "indexed_files": 1, "waiting_files": 0}
    off = put(alice, "/search/semantic/me", {"opt_out": True}).json()
    assert off["opt_out"] is True and off["indexed_files"] == 0
    assert chunks(fx) == [] and sem_names(alice, "vacation") == []
    alice.upload(fx.taxes, "later.txt", b"another holiday")
    run_semantic(fx)
    assert chunks(fx) == []  # new files are skipped while opted out
    put(alice, "/search/semantic/me", {"opt_out": False})
    fx.w.advance(hours=1, seconds=1)
    run_semantic(fx)
    assert len(chunks(fx)) >= 2
    assert sem_names(alice, "vacation holiday") == ["diary.txt", "later.txt"]


def test_the_opt_out_of_one_person_does_not_touch_another_persons_vectors(
    fx: Fx, alice: Api, admin: Api, net: Net
) -> None:
    bob = Api(fx, "bob")
    bob_root = fx.root_of("bob@example.com")
    alice.upload(fx.taxes, "a.txt", b"holiday trip")
    bob.upload(bob_root, "b.txt", b"holiday beach")
    configure(fx, admin, net)
    run_semantic(fx)
    put(alice, "/search/semantic/me", {"opt_out": True})
    assert sem_names(bob, "vacation") == ["b.txt"]
    assert sem_names(alice, "vacation") == []


# ---------------------------------------------------------------- model swap and reindexing


def test_changing_the_model_starts_a_new_generation_and_rebuilds(
    fx: Fx, alice: Api, admin: Api, net: Net
) -> None:
    seed(fx, alice)
    configure(fx, admin, net, model="model-a")
    run_semantic(fx)
    old = {c.generation for c in chunks(fx)}
    assert old == {1} and sem_names(alice, "vacation") == ["summer.txt"]
    # edit only: it is unverified, so search turns off (full-text answers) until verified again
    put(
        admin,
        "/admin/semantic",
        {"kind": "openai_compatible", "endpoint": ENDPOINT, "model": "model-b"},
    )
    assert search_json(alice, "vacation", mode="semantic")["mode"] == "fulltext"
    net.dim = 24  # the new model has another dimension
    v = admin.post("/admin/semantic/verify").json()
    assert v["generation"] == 2 and v["dimension"] == 24 and v["enabled"] is False
    assert chunks(fx) == []  # vectors of another model are never compared with new ones
    put(admin, "/admin/semantic/enabled", {"enabled": True})
    assert sem_names(alice, "vacation") == []  # reindexing has not run yet: no stale answers
    indexer(fx).tick()
    assert {c.generation for c in chunks(fx)} == {2}
    assert all(len(c.embedding or []) == 24 for c in chunks(fx))
    assert sem_names(alice, "vacation") == ["summer.txt"]
    # verifying the same model again keeps the generation and the vectors
    again = admin.post("/admin/semantic/verify").json()
    assert again["generation"] == 2 and len(chunks(fx)) == 3


def test_an_endpoint_that_changes_dimension_underneath_turns_search_off_safely(
    fx: Fx, alice: Api, admin: Api, net: Net
) -> None:
    seed(fx, alice)
    configure(fx, admin, net)
    run_semantic(fx)
    sem = sem_of(fx)
    net.dim = 32  # somebody swapped the model behind the same name
    with sem.sessions() as db:
        sem.recheck(db)
    state = admin.get("/admin/semantic").json()
    assert not state["healthy"] and state["error"] == "DIMENSION_CHANGED" and not state["active"]
    out = search_json(alice, "bill", mode="hybrid")  # full-text keeps answering
    assert out["mode"] == "fulltext" and [h["name"] for h in out["hits"]] == ["march.txt"]
    net.dim = 16  # and it recovers on its own when it is the same model again
    with sem.sessions() as db:
        sem.recheck(db)
    assert admin.get("/admin/semantic").json()["active"] is True


def test_editing_the_prefixes_forces_a_new_generation_on_the_next_verify(
    fx: Fx, alice: Api, admin: Api, net: Net
) -> None:
    seed(fx, alice)
    configure(fx, admin, net)
    run_semantic(fx)
    put(
        admin,
        "/admin/semantic",
        {
            "kind": "openai_compatible",
            "endpoint": ENDPOINT,
            "model": "embedding-model",
            "query_prefix": "query: ",
            "doc_prefix": "passage: ",
        },
    )
    assert admin.get("/admin/semantic").json()["active"] is False
    assert admin.post("/admin/semantic/verify").json()["generation"] == 2
    put(admin, "/admin/semantic/enabled", {"enabled": True})
    net.texts.clear()
    indexer(fx).tick()
    assert net.texts and all(t.startswith("passage: ") for t in net.texts)
    sem_names(alice, "vacation")
    assert net.texts[-1] == "query: vacation"


# ---------------------------------------------------------------- outages, retries, leases, CPU


def test_an_endpoint_outage_leaves_full_text_search_untouched_and_jobs_retry(
    fx: Fx, alice: Api, admin: Api, net: Net
) -> None:
    seed(fx, alice)
    configure(fx, admin, net)
    run_index(fx)
    net.status = 503
    assert indexer(fx).tick() >= 1
    jobs = fx.w.db_rows(SearchEmbed)
    assert jobs and all(j.state == "queued" and j.error == "OUTAGE" for j in jobs)
    assert all(j.attempts == 1 for j in jobs)
    assert indexer(fx).tick() == 0  # backed off: not due yet
    out = search_json(alice, "bill", mode="hybrid")  # query embedding fails: plain full-text
    assert out["mode"] == "fulltext" and [h["name"] for h in out["hits"]] == ["march.txt"]
    net.status = 200
    fx.w.advance(hours=2)
    assert indexer(fx).tick() >= 1
    assert {j.state for j in fx.w.db_rows(SearchEmbed)} == {"done"} and len(chunks(fx)) == 3


def test_a_job_gives_up_after_the_attempt_limit_and_a_dead_worker_lease_is_reclaimed(
    make_fx: Callable[..., Fx], net: Net
) -> None:
    fx = make_fx(semantic_max_attempts=2)
    alice, admin = Api(fx, "alice"), Api(fx, "admin")
    alice.upload(fx.taxes, "a.txt", b"holiday trip")
    configure(fx, admin, net)
    run_index(fx)
    net.status = 500
    for _ in range(3):
        indexer(fx).tick()
        fx.w.advance(hours=2)
    job = fx.w.db_rows(SearchEmbed)[0]
    assert job.state == "failed" and job.error == "GAVE_UP"
    # a worker that died holding the lease: the job is taken again once the lease ran out
    net.status = 200
    with fx.w.ctx.sessions() as db:
        row = db.get(SearchEmbed, job.node_id)
        assert row is not None
        row.state, row.attempts, row.error = "running", 0, None
        row.lease_until = row.run_after + timedelta(hours=10)
        db.commit()
    fx.w.offset = timedelta(0)
    idx = indexer(fx)
    with fx.w.ctx.sessions() as db:
        assert idx.claim(db) is None  # lease still valid
    fx.w.advance(days=3)
    with fx.w.ctx.sessions() as db:
        assert idx.claim(db) == job.node_id


def test_background_work_is_throttled_for_cpu_only_hosts(
    make_fx: Callable[..., Fx], net: Net, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = make_fx(semantic_pause_ms=250, semantic_embed_batch=2, semantic_chunk_chars=200)
    alice, admin = Api(fx, "alice"), Api(fx, "admin")
    alice.upload(fx.taxes, "long.txt", ("holiday trip to the beach and back " * 40).encode())
    configure(fx, admin, net)
    run_index(fx)
    sleeps: list[float] = []
    monkeypatch.setattr(semantic.time, "sleep", lambda s: sleeps.append(s))
    net.requests.clear()
    indexer(fx).tick()
    assert len(net.requests) >= 2 and len(sleeps) == len(net.requests)
    assert set(sleeps) == {0.25}
    assert all(len(json.loads(r.content)["input"]) <= 2 for r in net.requests)  # small batches
    # one request at a time and no GPU switch of ours in the call: the server decides its threads
    assert all("n_gpu_layers" not in r.content.decode() for r in net.requests)


# ---------------------------------------------------------------- no cloud path, gates


def test_the_embedding_client_refuses_anything_not_cleared_for_local_use(fx: Fx, net: Net) -> None:
    sem = sem_of(fx)
    client = sem.client(embed.EndpointSpec("ollama", ENDPOINT, "m"))
    sem.transport = httpx.MockTransport(net)
    client = sem.client(embed.EndpointSpec("ollama", ENDPOINT, "m"))
    with pytest.raises(embed.EmbedError, match="NOT_CLEARED"):
        client.embed(["plain string"])  # type: ignore[list-item]
    cloud = clear(
        "x", label="normal", provider_mode="cloud", local_opt_in=False, unlocked_by_person=False
    )
    with pytest.raises(embed.EmbedError, match="NOT_CLEARED"):
        client.embed([cloud])
    assert net.requests == []
    with pytest.raises(Exception):  # noqa: B017 - the gate itself refuses protected text
        clear(
            "x",
            label="confidential",
            provider_mode="local",
            local_opt_in=True,
            unlocked_by_person=False,
        )


def test_there_is_no_cloud_kind_and_no_fallback_when_the_local_endpoint_is_down(
    fx: Fx, alice: Api, admin: Api, net: Net
) -> None:
    assert embed.KINDS == ("openai_compatible", "ollama")
    seed(fx, alice)
    configure(fx, admin, net)
    run_semantic(fx)
    hosts_before = {r.url.host for r in net.requests}
    net.status = 500
    assert search_json(alice, "vacation", mode="semantic")["mode"] == "fulltext"
    assert {r.url.host for r in net.requests} | hosts_before == {"127.0.0.1"}


def test_a_configured_ai_provider_is_not_used_for_embeddings(
    fx: Fx, alice: Api, admin: Api, net: Net
) -> None:
    """M15 providers (cloud or local chat models) have nothing to do with the embedding endpoint."""
    seed(fx, alice)
    configure(fx, admin, net)
    run_semantic(fx)
    assert all(r.url.port == 8081 for r in net.requests)
    assert fx.w.db_rows(User)  # sanity: the fixture is real


def test_zero_vectors_and_odd_answers_are_rejected_not_stored() -> None:
    with pytest.raises(embed.EmbedError):
        embed.normalise([0.0, 0.0])
    with pytest.raises(embed.EmbedError):
        embed.normalise([math.inf, 1.0])
    v = embed.normalise([3.0, 4.0])
    assert abs(math.sqrt(sum(x * x for x in v)) - 1.0) < 1e-9


def test_workers_do_not_start_when_search_is_disabled(make_fx: Callable[..., Fx]) -> None:
    fx = make_fx(search_enabled=False)
    idx = indexer(fx)
    idx.start()
    assert idx._thread is None  # noqa: SLF001
    assert idx.tick() == 0
    with fx.w.ctx.sessions() as db:
        assert semantic.inactive_reason(db, fx.w.settings) == "search_disabled"
        assert not semantic.is_active(db, fx.w.settings)


# ---------------------------------------------------------------- Opus review round 1


def test_a_model_swapped_behind_the_same_address_and_name_is_detected(
    fx: Fx, alice: Api, admin: Api, net: Net
) -> None:
    seed(fx, alice)
    configure(fx, admin, net)
    run_semantic(fx)
    assert sem_names(alice, "vacation") == ["summer.txt"]
    sem = sem_of(fx)
    net.swapped = True  # same kind, address, model name and dimension; different vectors
    with sem.sessions() as db:
        sem.recheck(db)
    state = admin.get("/admin/semantic").json()
    assert state["error"] == "MODEL_CHANGED" and not state["healthy"] and not state["active"]
    assert search_json(alice, "vacation", mode="semantic")["mode"] == "fulltext"
    # it does not recover on its own: the administrator must verify, which starts a new generation
    with sem.sessions() as db:
        sem.recheck(db)
    assert admin.get("/admin/semantic").json()["error"] == "MODEL_CHANGED"
    assert admin.post("/admin/semantic/verify").json()["generation"] == 2
    assert chunks(fx) == []
    put(admin, "/admin/semantic/enabled", {"enabled": True})
    indexer(fx).tick()
    assert {c.generation for c in chunks(fx)} == {2}
    # the old model coming back is detected the same way
    net.swapped = False
    with sem.sessions() as db:
        sem.recheck(db)
    assert admin.get("/admin/semantic").json()["error"] == "MODEL_CHANGED"


def test_queries_are_rate_limited_per_person_and_only_when_they_call_the_model(
    make_fx: Callable[..., Fx], net: Net
) -> None:
    fx = make_fx(semantic_query_per_minute=3)
    alice, admin, bob = Api(fx, "alice"), Api(fx, "admin"), Api(fx, "bob")
    seed(fx, alice)
    configure(fx, admin, net)
    run_semantic(fx)
    for _ in range(3):
        assert alice.get("/search", params={"q": "vacation"}).status_code == 200
    r = alice.get("/search", params={"q": "vacation"})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "SEARCH_TOO_FAST"
    assert alice.get("/search", params={"q": "vacation", "mode": "fulltext"}).status_code == 200
    assert bob.get("/search", params={"q": "vacation"}).status_code == 200  # per person
    fx.w.advance(seconds=61)
    sem_of(fx)._recent.clear()  # the limiter uses the monotonic clock
    assert alice.get("/search", params={"q": "vacation"}).status_code == 200


def test_query_embedding_has_its_own_short_timeout_and_a_global_cap(
    fx: Fx, alice: Api, admin: Api, net: Net, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(fx, alice)
    configure(fx, admin, net)
    run_semantic(fx)
    sem = sem_of(fx)
    seen: list[float | None] = []
    real = sem.client
    monkeypatch.setattr(
        sem, "client", lambda spec, timeout=None: (seen.append(timeout), real(spec, timeout))[1]
    )
    assert sem_names(alice, "vacation") == ["summer.txt"]
    assert seen == [float(fx.w.settings.semantic_query_timeout_seconds)] or seen == [
        fx.w.settings.semantic_query_timeout_seconds
    ]
    assert fx.w.settings.semantic_query_timeout_seconds < fx.w.settings.semantic_timeout_seconds
    # every slot busy: the query quietly uses full-text instead of queueing behind the model
    held = [sem._slots.acquire() for _ in range(fx.w.settings.semantic_query_concurrency)]
    assert held
    out = search_json(alice, "bill", mode="hybrid")
    assert out["mode"] == "fulltext" and [h["name"] for h in out["hits"]] == ["march.txt"]
    for _ in held:
        sem._slots.release()
    assert search_json(alice, "bill", mode="hybrid")["mode"] == "hybrid"


def test_normalise_survives_huge_values_without_overflowing() -> None:
    v = embed.normalise([1e200, 1e200, 0.0])
    assert all(math.isfinite(x) for x in v)
    assert abs(math.sqrt(sum(x * x for x in v)) - 1.0) < 1e-9
    with pytest.raises(embed.EmbedError):
        embed.normalise([1e-300, 0.0])
