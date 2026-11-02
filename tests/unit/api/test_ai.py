"""M15: AI capability layer. Providers are mocked at the HTTP transport; nothing here reaches the
network or a paid API."""

import base64
import copy
import dataclasses
import ipaddress
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from test_acl import Fx
from test_auth import PASSWORD, World
from test_files import Api, code

from app.ai import budget
from app.ai.gateway import AiCaller, AiError
from app.ai.providers import EndpointError
from app.ai.routing import AiRefused, ClearedInput, assert_deliverable, clear, resolve_route
from app.ai.secrets import SecretBox, SecretsError, aad_for, parse_key
from app.db.models import (
    AiBudget,
    AiProvider,
    AiUsage,
    AuthAudit,
    AuthSession,
    Base,
    ConfidentialUnlock,
    User,
)

KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()
API_KEY = "sk-test-SECRETVALUE-1234567890"
PRICE = {"price_in_micro": 3_000_000, "price_out_micro": 15_000_000}


class Out(BaseModel):
    ok: bool


class Net:
    """Mock provider network: records requests, answers like Anthropic or Ollama."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.text = '{"ok": true}'
        self.usage = (1000, 100)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, text=f"echo of prompt {API_KEY}")
        if request.url.host == "api.anthropic.com":
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": self.text}],
                    "usage": {"input_tokens": self.usage[0], "output_tokens": self.usage[1]},
                },
            )
        return httpx.Response(
            200,
            json={
                "message": {"content": self.text},
                "prompt_eval_count": self.usage[0],
                "eval_count": self.usage[1],
            },
        )


HOSTS = {"ollama.internal": ["10.0.0.5"], "evil.example": ["93.184.216.34"]}


def fake_resolver(host: str, port: int) -> list[str]:
    """No real DNS in tests: a fixed table, like a zone the test controls."""
    if host in HOSTS:
        return HOSTS[host]
    try:
        ipaddress.ip_address(host)
    except ValueError:
        raise EndpointError("unresolvable") from None
    return [host]


@pytest.fixture(autouse=True)
def reset_hosts() -> Any:
    saved = {k: list(v) for k, v in HOSTS.items()}
    yield
    HOSTS.clear()
    HOSTS.update(saved)


def with_resolver(fx: Fx) -> Fx:
    fx.w.app.state.ai._resolver = fake_resolver
    return fx


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fx:
    return with_resolver(Fx(World(tmp_path, monkeypatch, secrets_key=KEY)))


@pytest.fixture
def net(fx: Fx) -> Net:
    n = Net()
    fx.w.app.state.ai._transport = httpx.MockTransport(n)
    return n


def put(api: Api, path: str, body: dict[str, Any]) -> Any:
    return api.client.put(f"/api/v1/ai{path}", json=body, headers=api.headers)


def cloud_body(**extra: Any) -> dict[str, Any]:
    return {
        "kind": "anthropic",
        "model": "claude-test",
        "api_key": API_KEY,
        "enabled": True,
        "acknowledge_cloud": True,
        **PRICE,
        **extra,
    }


def local_body(**extra: Any) -> dict[str, Any]:
    return {
        "kind": "ollama",
        "model": "llama-test",
        "endpoint": "http://ollama.internal:11434",
        "enabled": True,
        **extra,
    }


def gateway(fx: Fx) -> Any:
    return fx.w.app.state.ai


def caller(fx: Fx, email: str, *, job: bool = False) -> AiCaller:
    uid = fx.uid(f"{email}@example.com")
    with fx.w.ctx.sessions() as db:
        # the session that holds the person's unlock, else any of their sessions
        sid = (
            db.scalars(
                select(ConfidentialUnlock.session_id).where(ConfidentialUnlock.user_id == uid)
            ).first()
            or db.scalars(select(AuthSession.id).where(AuthSession.user_id == uid)).first()
        )
    return AiCaller(uid, None if job else sid, "job" if job else "user")


def run(
    fx: Fx, who: AiCaller, node: uuid.UUID | list[uuid.UUID], text: str = "invoice text"
) -> Any:
    return gateway(fx).generate(
        who,
        [node] if isinstance(node, uuid.UUID) else node,
        purpose="test",
        instructions="Classify.",
        text=text,
        output_model=Out,
    )


def file_in(api: Api, parent: uuid.UUID, name: str = "a.txt") -> uuid.UUID:
    r = api.upload(parent, name, b"hello")
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["node"]["id"])


def label(api: Api, node: uuid.UUID, value: str) -> None:
    r = api.client.put(f"/api/v1/nodes/{node}/privacy", json={"label": value}, headers=api.headers)
    assert r.status_code == 200, r.text


def set_pref(api: Api, value: str) -> None:
    body = {"preference": value, "acknowledge_instance": True}
    assert put(api, "/me/preference", body).status_code == 200


def usage_rows(fx: Fx) -> list[AiUsage]:
    return fx.w.db_rows(AiUsage)


# --- zero AI --------------------------------------------------------------------------------
def test_nothing_configured_nothing_sent(fx: Fx, net: Net) -> None:
    alice = Api(fx, "alice")
    st = alice.get("/ai/status").json()
    assert st["instance_available"] is False and st["personal_tree_ai"] is False
    assert st["preference"] == "off"
    f = file_in(alice, fx.y2026)
    with pytest.raises(AiRefused) as e:
        run(fx, caller(fx, "alice"), f)
    assert e.value.reason == "ai_off"
    shared = file_in(alice, fx.shared_sub, "s.txt")
    with pytest.raises(AiRefused) as e:
        run(fx, caller(fx, "alice"), shared)
    assert e.value.reason == "ai_not_configured"
    assert net.requests == []
    assert alice.download(f).status_code == 200  # core operations are unaffected


# --- policy table ---------------------------------------------------------------------------
@pytest.mark.parametrize("label_", ["normal", "private", "confidential", "weird"])
@pytest.mark.parametrize("mode", ["cloud", "local"])
@pytest.mark.parametrize("opt_in", [False, True])
@pytest.mark.parametrize("unlocked", [False, True])
def test_gate_table(label_: str, mode: str, opt_in: bool, unlocked: bool) -> None:
    expected = label_ == "normal" or (
        label_ in ("private", "confidential")
        and mode == "local"
        and opt_in
        and (label_ == "private" or unlocked)
    )
    try:
        cleared = clear(
            "x", label=label_, provider_mode=mode, local_opt_in=opt_in, unlocked_by_person=unlocked
        )
        allowed = True
        assert_deliverable(cleared, mode)
    except AiRefused:
        allowed = False
    assert allowed is expected


def test_cleared_input_cannot_be_forged_and_adapters_recheck() -> None:
    with pytest.raises(TypeError):
        ClearedInput(text="x", label="normal", provider_mode="cloud")
    with pytest.raises(AiRefused):
        assert_deliverable("raw text", "cloud")
    local = clear(
        "x", label="private", provider_mode="local", local_opt_in=True, unlocked_by_person=False
    )
    with pytest.raises(AiRefused) as e:
        assert_deliverable(local, "cloud")  # cleared for local, offered to a cloud adapter
    assert e.value.reason == "mode_mismatch"


def test_route_table(fx: Fx) -> None:
    admin = Api(fx, "admin")
    alice = Api(fx, "alice")
    with fx.w.ctx.sessions() as db:
        a = db.get(User, alice.uid)
        assert a is not None
        assert resolve_route(db, root_kind="shared", owner=None).reason == "ai_not_configured"
        assert resolve_route(db, root_kind="personal", owner=a).reason == "ai_off"
    assert put(admin, "/instance/provider", cloud_body()).status_code == 200
    for pref, reason, scope in [
        ("off", "ai_off", "instance"),
        ("instance_default", "ok", "instance"),
        ("instance_default_local_only", "instance_not_local", "instance"),
        ("my_provider", "no_own_provider", f"user:{alice.uid}"),
    ]:
        set_pref(alice, pref)
        with fx.w.ctx.sessions() as db:
            a = db.get(User, alice.uid)
            r = resolve_route(db, root_kind="personal", owner=a)
            assert (r.reason, r.scope_key) == (reason, scope), pref
            # the shared tree always answers with the instance provider, whatever the person chose
            assert resolve_route(db, root_kind="shared", owner=None).reason == "ok"


# --- configuration API ----------------------------------------------------------------------
def test_key_is_sealed_and_never_returned(
    fx: Fx, net: Net, caplog: pytest.LogCaptureFixture
) -> None:
    admin = Api(fx, "admin")
    r = put(admin, "/instance/provider", cloud_body())
    assert r.status_code == 200 and r.json()["key_set"] is True
    assert API_KEY not in r.text
    for path in ("/instance", "/status"):
        assert API_KEY not in admin.get(f"/ai{path}").text
    with fx.w.ctx.sessions() as db:
        row = db.scalars(select(AiProvider)).one()
        assert row.secret_ciphertext and API_KEY.encode() not in row.secret_ciphertext
        assert API_KEY not in json.dumps([a.details for a in db.scalars(select(AuthAudit))])
        # a ciphertext moved onto another row does not open
        with pytest.raises(SecretsError):
            fx.w.app.state.ai_box.open(row.secret_ciphertext, aad_for(uuid.uuid4(), None))
    # the key is not echoed by a failing provider either
    net.status = 500
    caplog.set_level(logging.DEBUG)
    with pytest.raises(AiError) as e:
        run(fx, caller(fx, "admin"), file_in(Api(fx, "alice"), fx.shared_sub))
    assert API_KEY not in str(e.value) and API_KEY not in caplog.text


def test_cloud_needs_secrets_key_consent_prices_and_a_key(
    fx: Fx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = Api(fx, "admin")
    assert code(put(admin, "/instance/provider", cloud_body(acknowledge_cloud=False))) == (
        "CONSENT_REQUIRED"
    )
    assert code(
        put(admin, "/instance/provider", cloud_body(price_in_micro=0, price_out_micro=0))
    ) == ("PRICES_REQUIRED")
    assert code(put(admin, "/instance/provider", cloud_body(api_key=None))) == "API_KEY_REQUIRED"
    assert code(put(admin, "/instance/provider", cloud_body(endpoint="https://evil.example"))) == (
        "INVALID_PROVIDER"
    )
    assert code(put(admin, "/instance/provider", cloud_body(model="a b;c"))) == "INVALID_PROVIDER"
    (tmp_path / "x").mkdir()
    bare = with_resolver(Fx(World(tmp_path / "x", monkeypatch)))
    r = put(Api(bare, "admin"), "/instance/provider", cloud_body())
    assert r.status_code == 503 and code(r) == "SECRETS_KEY_NOT_CONFIGURED"
    # local providers need no key and no secrets key
    assert put(Api(bare, "admin"), "/instance/provider", local_body()).status_code == 200


def test_people_may_not_add_local_endpoints_and_admin_cannot_see_theirs(fx: Fx) -> None:
    alice, admin = Api(fx, "alice"), Api(fx, "admin")
    assert code(put(alice, "/me/provider", local_body())) == "LOCAL_ENDPOINT_ADMIN_ONLY"
    assert put(alice, "/me/provider", cloud_body()).status_code == 200
    assert put(admin, "/instance/provider", local_body()).status_code == 200
    assert admin.get("/ai/instance").json()["provider"]["kind"] == "ollama"
    assert Api(fx, "bob").get("/ai/status").json()["my_provider"] is None
    non_admin = put(alice, "/instance/provider", local_body())
    assert non_admin.status_code == 403


def test_billed_test_needs_confirmation(fx: Fx, net: Net) -> None:
    admin = Api(fx, "admin")
    put(admin, "/instance/provider", cloud_body())
    path = "/api/v1/ai/instance/provider/test"
    r = admin.client.post(path, json={}, headers=admin.headers)
    assert r.status_code == 409 and net.requests == []
    r = admin.client.post(path, json={"confirm_billed": True}, headers=admin.headers)
    assert r.status_code == 200, r.text
    assert len(net.requests) == 1 and net.requests[0].headers["x-api-key"] == API_KEY


# --- privacy: protected content never goes to a cloud provider -----------------------------
def test_protected_never_reaches_cloud(fx: Fx, net: Net) -> None:
    alice, admin = Api(fx, "alice"), Api(fx, "admin")
    put(admin, "/instance/provider", cloud_body())
    put(alice, "/me/provider", cloud_body())
    me = caller(fx, "alice")
    for pref in ("instance_default", "my_provider"):
        set_pref(alice, pref)
        normal = file_in(alice, fx.y2026, f"n-{pref}.txt")
        assert run(fx, me, normal).value.ok is True
        sent = len(net.requests)
        assert sent >= 1
        for lab in ("private", "confidential"):
            # derived data counts too: extracted text of a protected file is just as protected
            folder = fx.folder(fx.alice_root, f"{lab}-{pref}")
            label(alice, folder, lab)
            if lab == "confidential":
                assert (
                    alice.post(f"/nodes/{folder}/unlock", json={"password": PASSWORD}).status_code
                    == 200
                )
            f = file_in(alice, folder)
            with pytest.raises(AiRefused) as e:
                run(fx, caller(fx, "alice"), f, text="OCR text of a passport")
            assert e.value.reason == "protected_never_cloud"
        assert len(net.requests) == sent  # nothing more left the server


def test_local_provider_for_protected_needs_opt_in_and_unlock(fx: Fx, net: Net) -> None:
    alice, admin = Api(fx, "alice"), Api(fx, "admin")
    put(admin, "/instance/provider", local_body())
    set_pref(alice, "instance_default_local_only")
    priv = fx.folder(fx.alice_root, "P")
    conf = fx.folder(fx.alice_root, "C")
    label(alice, priv, "private")
    label(alice, conf, "confidential")
    alice.post(f"/nodes/{conf}/unlock", json={"password": PASSWORD})
    fp, fc = file_in(alice, priv), file_in(alice, conf)
    alice.post(f"/nodes/{conf}/lock")
    me = caller(fx, "alice")
    with pytest.raises(AiRefused) as e:
        run(fx, me, fp)
    assert e.value.reason == "local_opt_in_required"
    r = alice.client.put(
        "/api/v1/preferences/local-processing", json={"enabled": True}, headers=alice.headers
    )
    assert r.status_code == 200, r.text
    assert run(fx, me, fp).value.ok is True  # Private, local, opted in
    with pytest.raises(AiRefused):  # Confidential is locked (node unreadable): same answer
        run(fx, me, fc)
    assert alice.post(f"/nodes/{conf}/unlock", json={"password": PASSWORD}).status_code == 200
    assert run(fx, caller(fx, "alice"), fc).value.ok is True
    # a background job is never unlocked
    with pytest.raises(AiRefused):
        run(fx, caller(fx, "alice", job=True), fc)
    assert all(req.headers["host"] == "ollama.internal:11434" for req in net.requests)


def test_no_fallback_from_local_to_cloud_on_outage(fx: Fx, net: Net) -> None:
    alice, admin = Api(fx, "alice"), Api(fx, "admin")
    put(alice, "/me/provider", cloud_body())
    put(admin, "/instance/provider", local_body())
    set_pref(alice, "instance_default_local_only")
    f = file_in(alice, fx.y2026)
    net.status = 503
    with pytest.raises(AiError) as e:
        run(fx, caller(fx, "alice"), f)
    assert e.value.code == "OUTAGE"
    assert {r.headers["host"] for r in net.requests} == {
        "ollama.internal:11434"
    }  # the cloud was never tried


def test_other_peoples_personal_files_and_admin(fx: Fx, net: Net) -> None:
    alice, admin = Api(fx, "alice"), Api(fx, "admin")
    put(admin, "/instance/provider", cloud_body())
    set_pref(alice, "instance_default")
    f = file_in(alice, fx.y2026)
    for who in ("bob", "admin"):
        with pytest.raises(AiRefused) as e:
            run(fx, caller(fx, who), f)
        assert e.value.reason == "node_unavailable"
    # shared with bob: bob can read it, but it is still sent only on its owner's account
    assert alice.client.put(
        f"/api/v1/nodes/{fx.y2026}/grants/{fx.uid('bob@example.com')}",
        json={"level": "viewer"},
        headers=alice.headers,
    ).status_code in (200, 201)
    with pytest.raises(AiRefused) as e:
        run(fx, caller(fx, "bob"), f)
    assert e.value.reason == "owner_only"
    assert net.requests == []


# --- budget ---------------------------------------------------------------------------------
def test_cap_blocks_and_usage_is_visible(fx: Fx, net: Net) -> None:
    admin, alice = Api(fx, "admin"), Api(fx, "alice")
    put(admin, "/instance/provider", cloud_body())
    f = file_in(alice, fx.shared_sub)
    me = caller(fx, "alice")
    run(fx, me, f)
    u = admin.get("/ai/instance").json()["usage"]
    assert u["spent_micro"] == 3000 + 1500 and u["reserved_micro"] == 0  # 1000 in, 100 out
    cap = put(admin, "/instance/cap", {"cap_micro": u["spent_micro"]})
    assert cap.status_code == 200
    sent = len(net.requests)
    with pytest.raises(AiRefused) as e:
        run(fx, me, f)
    assert e.value.reason == "budget_exceeded" and len(net.requests) == sent
    assert put(admin, "/instance/cap", {"cap_micro": 0}).status_code == 200
    assert code(put(admin, "/instance/cap", {"cap_micro": -1})) == "VALIDATION_ERROR"
    assert any(u.status == "refused" and u.reason == "budget_exceeded" for u in usage_rows(fx))
    # the ledger holds no document text
    assert "invoice" not in json.dumps(
        [
            {c.name: str(getattr(u, c.name)) for c in AiUsage.__table__.columns}
            for u in usage_rows(fx)
        ]
    )


def test_failure_releases_the_reservation(fx: Fx, net: Net) -> None:
    admin, alice = Api(fx, "admin"), Api(fx, "alice")
    put(admin, "/instance/provider", cloud_body())
    f = file_in(alice, fx.shared_sub)
    net.status = 500
    with pytest.raises(AiError):
        run(fx, caller(fx, "alice"), f)
    u = admin.get("/ai/instance").json()["usage"]
    assert u["reserved_micro"] == 0 and u["spent_micro"] == 0
    net.status, net.text = 200, "not json"
    with pytest.raises(AiError) as e:
        run(fx, caller(fx, "alice"), f)
    assert e.value.code == "BAD_OUTPUT"  # nothing parsed from model text is acted on


def test_breaker_stops_hammering_a_dead_provider(fx: Fx, net: Net) -> None:
    admin, alice = Api(fx, "admin"), Api(fx, "alice")
    put(admin, "/instance/provider", cloud_body())
    f = file_in(alice, fx.shared_sub)
    net.status = 500
    for _ in range(fx.w.settings.ai_breaker_failures):
        with pytest.raises(AiError):
            run(fx, caller(fx, "alice"), f)
    n = len(net.requests)
    with pytest.raises(AiRefused) as e:
        run(fx, caller(fx, "alice"), f)
    assert e.value.reason == "provider_unavailable" and len(net.requests) == n


def test_stale_reservations_are_given_back(fx: Fx, net: Net) -> None:
    admin = Api(fx, "admin")
    put(admin, "/instance/provider", cloud_body())
    with fx.w.ctx.sessions() as db:
        assert budget.reserve(db, "instance", 1_000_000, 3_000_000)
        db.add(
            AiUsage(
                at=budget.clock.utcnow(),
                month=budget.month_key(),
                scope_key="instance",
                provider_kind="anthropic",
                model="m",
                purpose="x",
                status="reserved",
                reserved_micro=1_000_000,
            )
        )
        db.commit()
    fx.w.advance(minutes=30)
    with fx.w.ctx.sessions() as db:
        assert budget.sweep_stale(db, "instance") == 1
        assert db.scalars(select(AiBudget.reserved_micro)).one() == 0


def test_budget_race(tmp_path: Path) -> None:
    """Many parallel reservations: the sum that gets through never passes the cap."""
    engine = create_engine(f"sqlite:///{tmp_path / 'race.db'}", connect_args={"timeout": 30})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as db:
        budget.ensure(db, "instance", 1_000_000)
    wins: list[bool] = []

    def worker() -> None:
        with sessions() as db:
            wins.append(budget.reserve(db, "instance", 150_000, 1_000_000) is not None)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(wins) == 6  # 6 x 150k = 900k fits, a seventh would pass 1M
    with sessions() as db:
        assert db.scalars(select(AiBudget.reserved_micro)).one() == 900_000


def test_new_month_inherits_the_cap(fx: Fx) -> None:
    admin = Api(fx, "admin")
    put(admin, "/instance/cap", {"cap_micro": 7_000_000})
    fx.w.advance(days=40)
    with fx.w.ctx.sessions() as db:
        assert budget.ensure(db, "instance", 3_000_000).cap_micro == 7_000_000


def test_secret_key_parsing() -> None:
    assert len(parse_key(KEY)) == 32
    with pytest.raises(SecretsError):
        parse_key("short")
    box = SecretBox.from_setting("")
    assert not box.configured


# --- review fixes ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "endpoint",
    [
        "http://evil.example:8080",  # a name that resolves to a public address
        "https://93.184.216.34",
        "http://169.254.169.254",  # cloud metadata
        "http://[fd00:ec2::254]",
        "http://[::ffff:169.254.169.254]",
        "http://0.0.0.0:11434",
        "http://nowhere.invalid",  # does not resolve: cannot be verified, so refused
    ],
)
def test_local_endpoint_must_be_local(fx: Fx, endpoint: str) -> None:
    admin = Api(fx, "admin")
    r = put(admin, "/instance/provider", local_body(endpoint=endpoint))
    assert r.status_code == 422 and code(r) == "ENDPOINT_NOT_LOCAL", endpoint


def test_local_endpoint_accepts_private_and_loopback(fx: Fx) -> None:
    admin = Api(fx, "admin")
    for ep in ("http://ollama.internal:11434", "http://127.0.0.1:11434", "http://192.168.1.9"):
        assert put(admin, "/instance/provider", local_body(endpoint=ep)).status_code == 200


def test_dns_rebinding_is_refused_at_connect_time(fx: Fx, net: Net) -> None:
    admin, alice = Api(fx, "admin"), Api(fx, "alice")
    assert put(admin, "/instance/provider", local_body()).status_code == 200  # private when saved
    set_pref(alice, "instance_default")
    with fx.w.ctx.sessions() as db:
        u = db.get(User, alice.uid)
        assert u is not None
        u.local_processing_opt_in = True
        db.commit()
    f = file_in(alice, fx.shared_sub, "x.txt")
    shared_private = fx.folder(fx.alice_root, "Priv")
    label(alice, shared_private, "private")
    secret = file_in(alice, shared_private)
    HOSTS["ollama.internal"] = ["93.184.216.34"]  # the name now points at the internet
    with pytest.raises(AiError) as e:
        run(fx, caller(fx, "alice"), secret, text="passport number")
    assert e.value.code == "OUTAGE" and net.requests == []
    HOSTS["ollama.internal"] = ["169.254.169.254"]
    with pytest.raises(AiError):
        run(fx, caller(fx, "alice"), secret)
    assert net.requests == [] and f  # nothing was sent anywhere


def test_operator_allow_list_narrows_local_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx2 = with_resolver(Fx(World(tmp_path, monkeypatch, secrets_key=KEY, ai_local_hosts="gpu.lan")))
    HOSTS["gpu.lan"] = ["10.1.1.1"]
    admin = Api(fx2, "admin")
    assert code(put(admin, "/instance/provider", local_body())) == "ENDPOINT_NOT_LOCAL"
    ok = put(admin, "/instance/provider", local_body(endpoint="http://gpu.lan:8080"))
    assert ok.status_code == 200
    # even a listed host cannot be link-local
    HOSTS["gpu.lan"] = ["169.254.169.254"]
    bad = put(admin, "/instance/provider", local_body(endpoint="http://gpu.lan:8080"))
    assert code(bad) == "ENDPOINT_NOT_LOCAL"


def test_cleared_input_cannot_be_copied_or_replaced() -> None:
    c = clear(
        "secret-body",
        label="normal",
        provider_mode="cloud",
        local_opt_in=False,
        unlocked_by_person=False,
    )
    with pytest.raises(TypeError):
        dataclasses.replace(c, label="normal")  # type: ignore[type-var]
    with pytest.raises(TypeError):
        copy.copy(c)
    with pytest.raises(TypeError):
        copy.deepcopy(c)
    with pytest.raises(AttributeError):
        c.label = "private"  # type: ignore[misc]
    assert "secret-body" not in repr(c)


def test_request_is_as_protected_as_its_strictest_source(fx: Fx, net: Net) -> None:
    alice, admin = Api(fx, "alice"), Api(fx, "admin")
    put(admin, "/instance/provider", cloud_body())
    set_pref(alice, "instance_default")
    plain = file_in(alice, fx.y2026, "plain.txt")
    folder = fx.folder(fx.alice_root, "Medical")
    label(alice, folder, "private")
    secret = file_in(alice, folder, "scan.txt")
    me = caller(fx, "alice")
    assert run(fx, me, plain).value.ok
    sent = len(net.requests)
    # Private-derived text under a Normal node id is no longer enough: list every source.
    with pytest.raises(AiRefused) as e:
        run(fx, me, [plain, secret], text="text extracted from both")
    assert e.value.reason == "protected_never_cloud" and len(net.requests) == sent
    with pytest.raises(AiRefused) as e:
        run(fx, me, [])
    assert e.value.reason == "node_unavailable"
    other = file_in(Api(fx, "bob"), fx.root_of("bob@example.com"))
    with pytest.raises(AiRefused):
        run(fx, me, [plain, other])
    assert len(net.requests) == sent


def test_person_must_agree_to_the_servers_cloud_ai(fx: Fx, net: Net) -> None:
    alice, admin = Api(fx, "alice"), Api(fx, "admin")
    put(admin, "/instance/provider", local_body())
    assert put(alice, "/me/preference", {"preference": "instance_default"}).status_code == 200
    f = file_in(alice, fx.y2026)
    assert run(fx, caller(fx, "alice"), f).value.ok  # local: nothing to agree to
    # the administrator now switches the server to a cloud vendor
    assert put(admin, "/instance/provider", cloud_body()).status_code == 200
    with pytest.raises(AiRefused) as e:
        run(fx, caller(fx, "alice"), f)
    assert e.value.reason == "instance_consent_required"
    assert alice.get("/ai/status").json()["instance_needs_ack"] is True
    assert put(alice, "/me/preference", {"preference": "instance_default"}).status_code == 409
    assert (
        put(
            alice,
            "/me/preference",
            {"preference": "instance_default", "acknowledge_instance": True},
        ).status_code
        == 200
    )
    assert run(fx, caller(fx, "alice"), f).value.ok
    # a different vendor needs a new agreement
    assert put(admin, "/instance/provider", cloud_body(kind="openai")).status_code == 200
    with pytest.raises(AiRefused) as e:
        run(fx, caller(fx, "alice"), f)
    assert e.value.reason == "instance_consent_required"


def test_late_finish_does_not_return_the_reservation_twice(fx: Fx) -> None:
    with fx.w.ctx.sessions() as db:
        assert budget.reserve(db, "instance", 1_000_000, 3_000_000)
        row = AiUsage(
            at=budget.clock.utcnow(),
            month=budget.month_key(),
            scope_key="instance",
            provider_kind="anthropic",
            model="m",
            purpose="x",
            status="reserved",
            reserved_micro=1_000_000,
        )
        db.add(row)
        db.commit()
        fx.w.advance(minutes=30)
        assert budget.sweep_stale(db, "instance") == 1
        assert budget.claim(db, row.id) is False  # the sweep owns it now: no second release
        b = db.scalars(select(AiBudget)).one()
        assert b.reserved_micro == 0
        budget.settle(db, "instance", row.month, 0, 250_000)  # the late result is still counted
        db.refresh(b)
        assert (b.reserved_micro, b.spent_micro) == (0, 250_000)


def test_reservation_estimate_is_a_byte_bound() -> None:
    cjk = "漢字" * 500  # 1000 characters, 3000 bytes; real tokenisers can spend 1+ per character
    assert budget.estimate_tokens(len(cjk.encode())) >= 3000
    assert budget.estimate_tokens(0) > 0


def test_one_provider_per_scope_is_enforced_by_the_database(fx: Fx) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.auth import clock

    def row(scope: str, owner: uuid.UUID | None, mode: str = "local") -> AiProvider:
        return AiProvider(
            scope=scope,
            owner_user_id=owner,
            kind="ollama",
            privacy_mode=mode,
            model="m",
            created_at=clock.utcnow(),
        )

    uid = fx.uid("alice@example.com")
    for bad in (
        [row("instance", None), row("instance", None)],
        [row("user", uid), row("user", uid)],
        [row("instance", uid)],
        [row("user", None)],
        [row("planet", None)],
        [row("instance", None, "elsewhere")],
    ):
        with fx.w.ctx.sessions() as db, pytest.raises(IntegrityError):
            db.add_all(bad)
            db.commit()
