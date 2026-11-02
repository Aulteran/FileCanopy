"""M13: sender aliases, the inbound mailbox, MIME limits, sender decisions, quarantine, dedupe.

No network: the mailbox is a fake client, outbound mail is the fake SMTP class from M12.
"""

import hashlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from test_acl import Fx
from test_auth import PASSWORD, World, signed_in
from test_files import Api, code
from test_oidc_mail import SMTP, FakeSMTP, mail_world, token_of

from app.auth import clock
from app.config import Settings
from app.db.models import (
    AuthAudit,
    InboundAttachment,
    InboundMessage,
    MailboxState,
    User,
    UserEmailAlias,
)
from app.mail import authres, decision, store
from app.mail.address import normalize_address, single_from
from app.mail.imap import MailboxError
from app.mail.mime import Limits, MimeRefused, parse_message, safe_filename
from app.mail.poller import poll_once

AUTHSERV = "mx.example.org"
PERSONAL = "personaldocs@example.org"
INGEST = "ingest@example.org"
IMAP: dict[str, Any] = {
    "imap_host": "imap.example.org",
    "imap_username": "box",
    "imap_password": "box-secret",
    "imap_personal_address": PERSONAL,
    "imap_ingest_address": INGEST,
    "imap_authserv_id": AUTHSERV,
}
LIMITS = Limits(max_bytes=1_000_000, max_parts=20, max_depth=4, max_attachments=5)


def passing(domain: str = "example.com", authserv: str = AUTHSERV) -> str:
    return (
        f"{authserv}; dmarc=pass (p=none) header.from={domain}; "
        f"dkim=pass header.d={domain}; spf=pass smtp.mailfrom=bounce@{domain}"
    )


def build(
    frm: str = "Alice <alice@example.com>",
    to: str = PERSONAL,
    auth: str | None = None,
    attachments: list[tuple[str, bytes]] | None = None,
    subject: str = "Tax papers",
    before: dict[str, str] | None = None,
    after: dict[str, str] | None = None,
    body: str = "see attached",
    recipient_header: str = "X-Original-To",
) -> bytes:
    m = EmailMessage()
    m["Authentication-Results"] = auth if auth is not None else passing()
    if recipient_header:
        m[recipient_header] = to
    m["From"] = frm
    m["To"] = "someone-else@example.org"
    m["Subject"] = subject
    if "Message-ID" not in (before or {}):
        m["Message-ID"] = f"<{uuid.uuid4()}@example.com>"
    m.set_content(body)
    for name, data in attachments if attachments is not None else [("scan.pdf", b"%PDF-1.4 x")]:
        m.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    raw = m.as_bytes()
    # Headers the stdlib builder refuses to repeat are injected as raw lines.
    head, sep, rest = raw.partition(b"\n\n")
    top = "".join(f"{k}: {v}\n" for k, v in (before or {}).items()).encode()
    low = "".join(f"\n{k}: {v}" for k, v in (after or {}).items()).encode()
    return top + head + low + sep + rest


class FakeBox:
    def __init__(self, messages: dict[int, bytes] | None = None, uidvalidity: int = 7) -> None:
        self.messages = dict(messages or {})
        self.uidvalidity = uidvalidity
        self.fail_fetch: set[int] = set()
        self.fetched: list[int] = []
        self.closed = False

    def select(self, folder: str) -> int:
        return self.uidvalidity

    def uids_after(self, last_uid: int, limit: int) -> list[int]:
        return sorted(u for u in self.messages if u > last_uid)[:limit]

    def size_of(self, uid: int) -> int:
        return len(self.messages[uid])

    def fetch(self, uid: int) -> tuple[bytes, datetime | None]:
        if uid in self.fail_fetch:
            raise MailboxError("FETCH_FAILED")
        self.fetched.append(uid)
        return self.messages[uid], datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

    def close(self) -> None:
        self.closed = True


def run_poll(w: World, box: FakeBox) -> int:
    return poll_once(w.ctx.sessions, w.settings, factory=lambda: box)


@pytest.fixture
def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., World]:
    def factory(**overrides: Any) -> World:
        return World(tmp_path, monkeypatch, **overrides)

    return factory


@pytest.fixture
def fx(make_world: Callable[..., World]) -> Fx:
    w = mail_world(make_world, **IMAP)
    return Fx(w)


def messages(fx: Fx) -> list[InboundMessage]:
    return fx.w.db_rows(InboundMessage)


def only(fx: Fx) -> InboundMessage:
    rows = messages(fx)
    assert len(rows) == 1, [(r.status, r.reason) for r in rows]
    return rows[0]


def add_alias(api: Api, email: str, **proof: Any) -> Any:
    body = {"email": email, **(proof or {"password": PASSWORD})}
    return api.client.post("/api/v1/me/emails", json=body, headers=api.headers)


def verify_alias(api: Api, email: str) -> Any:
    FakeSMTP.sent = []
    assert add_alias(api, email).status_code == 202
    token = token_of(FakeSMTP.sent[-1])
    r = api.client.post("/api/v1/me/emails/verify", json={"token": token}, headers=api.headers)
    assert r.status_code == 200, r.text
    return r


# ------------------------------------------------------------------ addresses


def test_address_normalisation() -> None:
    assert normalize_address(" Alice@Example.COM ") == "alice@example.com"
    assert normalize_address("bob@bücher.example") == "bob@xn--bcher-kva.example"
    for bad in (
        "",
        "a@b",
        "a b@example.com",
        "a@@example.com",
        "<a@example.com>",
        ".a@example.com",
    ):
        assert normalize_address(bad) is None, bad


def test_from_header_tricks_are_refused() -> None:
    def frm(*values: str) -> list[object]:
        raw = "".join(f"From: {v}\n" for v in values) + "\nbody"
        msg = BytesParser(policy=policy.default).parsebytes(raw.encode())
        return [v for k, v in msg.items() if k.lower() == "from"]

    assert single_from(frm("Alice <alice@example.com>")) == "alice@example.com"
    assert single_from(frm('"alice@example.com" <evil@example.net>')) == "evil@example.net"
    assert single_from(frm("a@example.com, b@example.com")) is None
    assert single_from(frm("Team: a@example.com;")) is None
    assert single_from(frm("a@example.com", "b@example.com")) is None
    assert single_from([]) is None
    assert single_from(frm("not an address")) is None


# ------------------------------------------------------------------ Authentication-Results


def ev(headers: list[str], domain: str = "example.com", policy: str = "aligned") -> Any:
    return authres.evaluate(headers, AUTHSERV, policy, domain)


def test_authentication_results_policy() -> None:
    assert ev([passing()]).ok
    dmarc_dkim = f"{AUTHSERV}; dmarc=pass header.from=example.com; dkim=pass header.d=example.com"
    assert ev([dmarc_dkim]).ok and ev([dmarc_dkim], policy="dmarc").ok
    # M1: DMARC can pass on SPF alone (shared sending platforms): a DKIM signature from the
    # sender's own domain is required on every accepting path
    spf_only = (
        f"{AUTHSERV}; dmarc=pass header.from=example.com; spf=pass smtp.mailfrom=example.com; "
        "dkim=none"
    )
    assert ev([spf_only]).reason == "AUTH_FAILED"
    assert ev([spf_only], policy="dmarc").reason == "AUTH_FAILED"
    assert ev([f"{AUTHSERV}; dmarc=pass header.from=example.com"]).reason == "AUTH_FAILED"
    third_party = f"{AUTHSERV}; dmarc=pass header.from=example.com; dkim=pass header.d=esp.example"
    assert ev([third_party]).reason == "AUTH_FAILED"
    both = f"{AUTHSERV}; dkim=pass header.d=example.com; spf=pass smtp.mailfrom=a@example.com"
    assert ev([both]).ok
    assert not ev([both], policy="dmarc").ok
    only_dkim = f"{AUTHSERV}; dkim=pass header.d=example.com; spf=fail smtp.mailfrom=a@example.com"
    assert ev([only_dkim]).reason == "AUTH_FAILED"
    assert ev([f"{AUTHSERV}; dmarc=fail header.from=example.com"]).reason == "AUTH_FAILED"
    # a pass for some other domain says nothing about the From domain
    assert ev([passing("evil.example")]).reason == "AUTH_FAILED"
    assert (
        ev(
            [
                f"{AUTHSERV}; dkim=pass header.d=mail.example.com; spf=pass "
                "smtp.mailfrom=a@mail.example.com"
            ]
        ).reason
        == "AUTH_FAILED"
    )  # exact alignment only
    assert ev([]).reason == "AUTH_MISSING"
    assert ev([f"{AUTHSERV}; none"]).reason == "AUTH_FAILED"


def test_hostile_envelope_sender_cannot_forge_verdicts() -> None:
    # L1: a provider that writes an unquoted hostile smtp.mailfrom into its header
    hostile = (
        f"{AUTHSERV}; spf=pass smtp.mailfrom="
        "x;dkim=pass header.d=example.com;spf=pass smtp.mailfrom=example.com;y=z@evil.example"
    )
    assert ev([hostile]).reason == "AUTH_AMBIGUOUS"
    two_spf = (
        f"{AUTHSERV}; dkim=pass header.d=example.com; spf=fail smtp.mailfrom=a@x.example; "
        "spf=pass smtp.mailfrom=a@example.com"
    )
    assert ev([two_spf]).reason == "AUTH_AMBIGUOUS"
    two_dmarc = (
        f"{AUTHSERV}; dkim=pass header.d=example.com; dmarc=pass header.from=example.com; "
        "dmarc=pass header.from=example.com"
    )
    assert ev([two_dmarc]).reason == "AUTH_AMBIGUOUS"
    junk = f"{AUTHSERV}; dkim=pass header.d=example.com; spf=pass smtp.mailfrom=not a domain"
    assert ev([junk]).reason == "AUTH_FAILED"


def test_authentication_results_only_from_our_provider() -> None:
    forged = passing(authserv="attacker.example")
    assert ev([forged]).reason == "AUTH_MISSING"
    # the provider's header is topmost; a forged copy of our id below it is ambiguity
    assert ev([passing(), passing()]).reason == "AUTH_AMBIGUOUS"
    # a forged header on top hides ours
    assert ev([forged, passing()]).reason == "AUTH_MISSING"
    # a pass placed in a comment does not count
    assert not ev([f"{AUTHSERV}; dmarc=fail (dmarc=pass) header.from=example.com"]).ok
    assert authres.evaluate([passing()], "", "aligned", "example.com").reason == "AUTH_UNCONFIGURED"


# ------------------------------------------------------------------ MIME


def test_attachments_and_names() -> None:
    raw = build(attachments=[("../../etc/passwd", b"a"), ("CON.txt", b"b"), ("x.pdf", b"c")])
    parsed = parse_message(raw, LIMITS)
    names = [a.display_name for a in parsed.attachments]
    assert names[0] == "passwd" or "/" not in names[0]
    assert all("/" not in n and "\\" not in n and not n.startswith(".") for n in names)
    assert names[1].lower().startswith("_con")
    assert parsed.from_address == "alice@example.com"
    assert parsed.subject == "Tax papers"
    assert parsed.attachments[0].sha256 == hashlib.sha256(parsed.attachments[0].data).hexdigest()


def test_safe_filename_cases() -> None:
    taken: set[str] = set()
    assert safe_filename("a.pdf", 1, taken) == "a.pdf"
    assert safe_filename("A.pdf", 2, taken) == "A (2).pdf"
    assert safe_filename("‮gpj.exe", 3, taken) == "gpj.exe"
    assert safe_filename(None, 4, taken) == "attachment-4"
    assert safe_filename("...", 5, taken) == "attachment-5"
    long = safe_filename("x" * 400 + ".pdf", 6, taken)
    assert len(long.encode()) <= 200 and long.endswith(".pdf")
    assert safe_filename("a\x00b\r\n.txt", 7, taken) == "ab.txt"


def test_size_parts_depth_and_attachment_limits() -> None:
    with pytest.raises(MimeRefused) as e:
        parse_message(b"x" * 2_000_000, LIMITS)
    assert e.value.reason == "TOO_LARGE"

    many = build(attachments=[(f"{i}.pdf", b"x") for i in range(6)])
    with pytest.raises(MimeRefused) as e:
        parse_message(many, LIMITS)
    assert e.value.reason == "TOO_MANY_ATTACHMENTS"

    deep = b"Content-Type: text/plain\r\n\r\nx"
    for i in range(30):
        deep = (
            f'Content-Type: multipart/mixed; boundary="b{i}"\r\n\r\n--b{i}\r\n'.encode()
            + deep
            + f"\r\n--b{i}--\r\n".encode()
        )
    with pytest.raises(MimeRefused) as e:
        parse_message(deep, LIMITS)
    assert e.value.reason in ("TOO_DEEP", "TOO_MANY_PARTS")

    shallow = Limits(1_000_000, 1000, 3, 5)
    nest = b"Content-Type: application/pdf\r\n\r\nx"
    for i in range(5):
        nest = (
            f'Content-Type: multipart/mixed; boundary="c{i}"\r\n\r\n--c{i}\r\n'.encode()
            + nest
            + f"\r\n--c{i}--\r\n".encode()
        )
    with pytest.raises(MimeRefused) as e:
        parse_message(nest, shallow)
    assert e.value.reason == "TOO_DEEP"

    with pytest.raises(MimeRefused) as e:
        parse_message(b"Subject: x\r\n" + b"X-A: b\r\n" * 20000 + b"\r\nbody", LIMITS)
    assert e.value.reason == "HEADERS_TOO_LARGE"


def test_nested_message_is_opaque_and_body_is_not_kept() -> None:
    inner = build(attachments=[("inner.pdf", b"deep")])
    outer = EmailMessage()
    outer["From"] = "alice@example.com"
    outer.set_content("hello body text")
    outer.add_attachment(inner, maintype="message", subtype="rfc822", filename="fwd.eml")
    parsed = parse_message(outer.as_bytes(), LIMITS)
    assert [a.display_name for a in parsed.attachments] == ["fwd.eml"]
    assert parsed.attachments[0].content_type == "message/rfc822"


def test_garbage_does_not_crash() -> None:
    for blob in (b"", b"\x00\xff" * 50, b"Content-Type: multipart/mixed\r\n\r\nno boundary"):
        try:
            parsed = parse_message(blob, LIMITS)
            assert parsed.from_address is None
        except MimeRefused:
            pass


# ------------------------------------------------------------------ aliases


def test_alias_lifecycle(fx: Fx) -> None:
    alice = Api(fx, "alice")
    listed = alice.client.get("/api/v1/me/emails").json()
    assert listed["primary"] == "alice@example.com" and listed["can_add"] is True
    verify_alias(alice, "alice.work@example.net")
    verify_alias(alice, "Alice.Home@Example.Net")
    out = alice.client.get("/api/v1/me/emails").json()
    assert [a["email"] for a in out["aliases"]] == [
        "alice.work@example.net",
        "alice.home@example.net",
    ]
    assert all(a["verified"] for a in out["aliases"])
    alias_id = out["aliases"][0]["id"]
    r = alice.client.post(
        f"/api/v1/me/emails/{alias_id}/remove", json={"password": PASSWORD}, headers=alice.headers
    )
    assert r.status_code == 200
    assert len(alice.client.get("/api/v1/me/emails").json()["aliases"]) == 1
    verify_alias(alice, "alice.work@example.net")  # can be added again


def test_alias_needs_proof_and_mail(
    fx: Fx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = Api(fx, "alice")
    for body in (
        {"email": "x@example.net"},
        {"email": "x@example.net", "password": "wrong-password-1"},
        {"email": "x@example.net", "password": PASSWORD, "reauth": True},
    ):
        r = alice.client.post("/api/v1/me/emails", json=body, headers=alice.headers)
        assert r.status_code in (403, 422), body
    assert not fx.w.db_rows(UserEmailAlias)
    assert code(add_alias(alice, "bad address")) == "INVALID_EMAIL"
    assert code(add_alias(alice, "alice@example.com")) == "ALREADY_YOUR_ADDRESS"
    no_csrf = alice.client.post(
        "/api/v1/me/emails", json={"email": "x@example.net", "password": PASSWORD}
    )
    assert no_csrf.status_code == 403

    # SMTP unset: a link shown to the requester would prove nothing, so aliases are unavailable
    (tmp_path / "second").mkdir()
    w2 = World(tmp_path / "second", monkeypatch, **IMAP)
    fx2 = Fx(w2)
    carol = Api(fx2, "carol")
    assert carol.client.get("/api/v1/me/emails").json()["can_add"] is False
    assert code(add_alias(carol, "c2@example.net")) == "MAIL_NOT_CONFIGURED"
    assert not w2.db_rows(UserEmailAlias)


def test_alias_token_rules(fx: Fx) -> None:
    alice, bob = Api(fx, "alice"), Api(fx, "bob")
    FakeSMTP.sent = []
    add_alias(alice, "shared@example.net")
    token = token_of(FakeSMTP.sent[-1])
    # another signed-in person cannot redeem it
    r = bob.client.post("/api/v1/me/emails/verify", json={"token": token}, headers=bob.headers)
    assert r.status_code == 400 and code(r) == "INVALID_TOKEN"
    r = alice.client.post(
        "/api/v1/me/emails/verify", json={"token": "nonsense"}, headers=alice.headers
    )
    assert r.status_code == 400
    ok = alice.client.post("/api/v1/me/emails/verify", json={"token": token}, headers=alice.headers)
    assert ok.status_code == 200
    again = alice.client.post(
        "/api/v1/me/emails/verify", json={"token": token}, headers=alice.headers
    )
    assert again.status_code == 400  # single use

    fx.w.advance(hours=25)
    FakeSMTP.sent = []
    add_alias(alice, "later@example.net")
    stale = token_of(FakeSMTP.sent[-1])
    fx.w.advance(hours=25)
    r = alice.client.post("/api/v1/me/emails/verify", json={"token": stale}, headers=alice.headers)
    assert r.status_code == 400  # expired

    FakeSMTP.sent = []
    add_alias(alice, "later@example.net")
    first = token_of(FakeSMTP.sent[-1])
    add_alias(alice, "later@example.net")
    second = token_of(FakeSMTP.sent[-1])
    assert first != second
    r = alice.client.post("/api/v1/me/emails/verify", json={"token": first}, headers=alice.headers)
    assert r.status_code == 400  # a new request replaced the old link


def test_alias_collisions(fx: Fx) -> None:
    alice, bob = Api(fx, "alice"), Api(fx, "bob")
    verify_alias(alice, "shared@example.net")
    sent = len(FakeSMTP.sent)
    # Bob asks for Alice's verified alias and for Alice's account address: same quiet answer,
    # no mail, no row.
    assert add_alias(bob, "shared@example.net").status_code == 202
    assert add_alias(bob, "alice@example.com").status_code == 202
    assert len(FakeSMTP.sent) == sent
    assert not [a for a in fx.w.db_rows(UserEmailAlias) if a.user_id == bob.uid]

    # Both ask for a fresh address; only the first to verify keeps it.
    FakeSMTP.sent = []
    add_alias(alice, "race@example.net")
    a_token = token_of(FakeSMTP.sent[-1])
    add_alias(bob, "race@example.net")
    b_token = token_of(FakeSMTP.sent[-1])
    assert (
        bob.client.post("/api/v1/me/emails/verify", json={"token": b_token}, headers=bob.headers)
    ).status_code == 200
    r = alice.client.post(
        "/api/v1/me/emails/verify", json={"token": a_token}, headers=alice.headers
    )
    assert r.status_code == 409 and code(r) == "EMAIL_IN_USE"
    owners = [a.user_id for a in fx.w.db_rows(UserEmailAlias) if a.email == "race@example.net"]
    assert owners == [bob.uid]


def test_admin_cannot_invite_an_address_that_is_a_verified_alias(fx: Fx) -> None:
    alice = Api(fx, "alice")
    verify_alias(alice, "alice.work@example.net")
    admin, h = fx.admin
    r = admin.post(
        "/api/v1/admin/users",
        json={"email": "alice.work@example.net", "name": "X", "role": "member"},
        headers=h,
    )
    assert r.status_code == 409 and code(r) == "EMAIL_EXISTS"


def test_intake_and_system_addresses_cannot_be_claimed(fx: Fx) -> None:
    alice = Api(fx, "alice")
    for reserved in (PERSONAL, INGEST.upper(), SMTP["smtp_from"]):
        assert code(add_alias(alice, reserved)) == "ADDRESS_RESERVED"
    admin, h = fx.admin
    r = admin.post(
        "/api/v1/admin/users",
        json={"email": INGEST, "name": "X", "role": "member"},
        headers=h,
    )
    assert r.status_code == 409
    assert not fx.w.db_rows(UserEmailAlias)


def test_internationalised_domains_are_one_identity(fx: Fx) -> None:
    # L3: sign-in emails keep a Unicode domain; mail addresses are punycode
    with fx.w.ctx.sessions() as db:
        bob = db.scalars(select(User).where(User.email == "bob@example.com")).one()
        bob.email = "bob@bücher.example"
        db.commit()
    alice = Api(fx, "alice")
    assert add_alias(alice, "bob@xn--bcher-kva.example").status_code == 202
    assert not [a for a in fx.w.db_rows(UserEmailAlias)]  # quietly refused: Bob's mailbox
    admin, h = fx.admin
    r = admin.post(
        "/api/v1/admin/users",
        json={"email": "bob@xn--bcher-kva.example", "name": "X", "role": "member"},
        headers=h,
    )
    assert r.status_code in (409, 422)
    # and Bob's own mail is recognised under the punycode spelling
    raw = build(frm="bob@xn--bcher-kva.example", auth=passing("xn--bcher-kva.example"))
    assert reason_for(fx, raw) == ("verified", None)


def test_alias_limits_and_throttle(fx: Fx) -> None:
    alice = Api(fx, "alice")
    for i in range(10):
        assert add_alias(alice, f"a{i}@example.net").status_code == 202
    assert code(add_alias(alice, "one-more@example.net")) == "ALIAS_LIMIT"
    for _ in range(4):
        add_alias(alice, "a0@example.net")
    assert add_alias(alice, "a0@example.net").status_code == 429


def test_alias_audit_holds_no_address(fx: Fx) -> None:
    alice = Api(fx, "alice")
    verify_alias(alice, "alice.work@example.net")
    rows = [a for a in fx.w.db_rows(AuthAudit) if a.action.startswith("mail.alias")]
    assert {r.action for r in rows} >= {"mail.alias_requested", "mail.alias_verified"}
    assert "example.net" not in str([r.details for r in rows])


# ------------------------------------------------------------------ decisions through the poller


def test_verified_personal_message(fx: Fx) -> None:
    box = FakeBox({1: build()})
    assert run_poll(fx.w, box) == 1
    m = only(fx)
    assert (m.status, m.destination, m.reason) == ("verified", "personal", None)
    assert m.resolved_user_id == fx.uid("alice@example.com")
    assert m.from_address == "alice@example.com" and m.content_state == "none"
    assert m.auth_summary["dmarc"] == "pass"
    atts = fx.w.db_rows(InboundAttachment)
    assert [a.display_name for a in atts] == ["scan.pdf"] and atts[0].stored_name is None
    assert not (fx.w.settings.app_data_path / "mail-quarantine").exists()
    assert box.closed


def test_shared_destination_and_alias_sender(fx: Fx) -> None:
    alice = Api(fx, "alice")
    verify_alias(alice, "alice@work.example.net")
    box = FakeBox(
        {
            1: build(to=INGEST),
            2: build(frm="alice@work.example.net", to=PERSONAL, auth=passing("work.example.net")),
        }
    )
    run_poll(fx.w, box)
    rows = {m.uid: m for m in messages(fx)}
    assert (rows[1].status, rows[1].destination) == ("verified", "shared")
    assert (rows[2].status, rows[2].resolved_user_id) == ("verified", alice.uid)


def reason_for(fx: Fx, raw: bytes) -> tuple[str, str | None]:
    uid = len(messages(fx)) + 1
    run_poll(fx.w, FakeBox({uid: raw}, uidvalidity=100 + uid))
    m = [r for r in messages(fx) if r.uid == uid][0]
    return m.status, m.reason


def test_spoofed_and_unauthenticated_senders_quarantine(fx: Fx) -> None:
    cases = {
        "failed": build(auth=f"{AUTHSERV}; dmarc=fail header.from=example.com"),
        "other-domain": build(auth=passing("evil.example")),
        "no-header": build(auth=""),
        "wrong-provider": build(auth=passing(authserv="attacker.example")),
        "injected-below": build(after={"Authentication-Results": passing()}),
        "unknown-sender": build(frm="stranger@example.com"),
        "from-trick": build(
            frm='"alice@example.com" <evil@example.net>', auth=passing("example.net")
        ),
        "two-froms": build(after={"From": "bob@example.com"}),
        "group": build(frm="Team: alice@example.com;"),
    }
    results = {name: reason_for(fx, raw) for name, raw in cases.items()}
    assert all(status == "quarantined" for status, _ in results.values()), results
    assert results["failed"][1] == "AUTH_FAILED"
    assert results["other-domain"][1] == "AUTH_FAILED"
    assert results["no-header"][1] == "AUTH_MISSING"
    assert results["wrong-provider"][1] == "AUTH_MISSING"
    assert results["injected-below"][1] == "AUTH_AMBIGUOUS"
    assert results["unknown-sender"][1] == "SENDER_UNKNOWN"
    assert results["from-trick"][1] == "SENDER_UNKNOWN"
    assert results["two-froms"][1] == "FROM_INVALID"
    assert results["group"][1] == "FROM_INVALID"
    assert not [m for m in messages(fx) if m.status == "verified"]


def test_recipient_must_be_unambiguous(fx: Fx) -> None:
    cases = {
        "wrong-to": build(to="somebody@example.org"),
        "no-header": build(recipient_header=""),
        "to-only": build(recipient_header="", after={}),  # `To:` is someone-else@example.org
        "conflict": build(after={"X-Original-To": INGEST}),
        "across-headers": build(after={"Delivered-To": INGEST}),
        "plus-address": build(to="personaldocs+x@example.org"),
        "list": build(to=f"{PERSONAL}, {INGEST}"),
    }
    results = {name: reason_for(fx, raw) for name, raw in cases.items()}
    assert all(status == "quarantined" for status, _ in results.values()), results
    assert results["wrong-to"][1] == "RECIPIENT_UNKNOWN"
    assert results["no-header"][1] == "RECIPIENT_UNKNOWN"
    assert results["to-only"][1] == "RECIPIENT_UNKNOWN"
    assert results["conflict"][1] == "RECIPIENT_AMBIGUOUS"
    assert results["across-headers"][1] == "RECIPIENT_AMBIGUOUS"
    assert results["plus-address"][1] == "RECIPIENT_UNKNOWN"
    assert results["list"][1] == "RECIPIENT_AMBIGUOUS"


def test_delivered_to_is_used_when_the_first_header_is_absent(fx: Fx) -> None:
    assert reason_for(fx, build(recipient_header="Delivered-To", to=INGEST)) == ("verified", None)


def test_inactive_and_ambiguous_accounts_quarantine(fx: Fx) -> None:
    with fx.w.ctx.sessions() as db:
        bob = db.scalars(select(User).where(User.email == "bob@example.com")).one()
        bob.status = "disabled"
        db.commit()
    assert reason_for(fx, build(frm="bob@example.com", auth=passing())) == (
        "quarantined",
        "USER_INACTIVE",
    )
    # Two accounts claiming one address (the admin created a user with a verified alias's address
    # after the fact): never routed.
    with fx.w.ctx.sessions() as db:
        carol = db.scalars(select(User).where(User.email == "carol@example.com")).one()
        db.add(
            UserEmailAlias(
                user_id=carol.id,
                email="dave@example.com",
                created_at=clock.utcnow(),
                verified_at=clock.utcnow(),
            )
        )
        db.commit()
    assert reason_for(fx, build(frm="dave@example.com")) == ("quarantined", "SENDER_AMBIGUOUS")


def test_unverified_alias_is_not_an_identity(fx: Fx) -> None:
    alice = Api(fx, "alice")
    assert add_alias(alice, "pending@example.net").status_code == 202
    assert reason_for(fx, build(frm="pending@example.net", auth=passing("example.net"))) == (
        "quarantined",
        "SENDER_UNKNOWN",
    )


def test_no_authserv_id_means_everything_quarantines(make_world: Callable[..., World]) -> None:
    w = mail_world(make_world, **{**IMAP, "imap_authserv_id": ""})
    Fx(w)
    run_poll(w, FakeBox({1: build()}))
    m = w.db_rows(InboundMessage)[0]
    assert (m.status, m.reason) == ("quarantined", "AUTH_UNCONFIGURED")


def test_revalidate_at_processing_time(fx: Fx) -> None:
    alice = Api(fx, "alice")
    verify_alias(alice, "alice@work.example.net")
    run_poll(
        fx.w, FakeBox({1: build(frm="alice@work.example.net", auth=passing("work.example.net"))})
    )
    with fx.w.ctx.sessions() as db:
        m = db.scalars(select(InboundMessage)).one()
        assert decision.revalidate(db, m) is not None
        # alias removed mid-queue
        db.execute(UserEmailAlias.__table__.delete())
        db.commit()
        assert decision.revalidate(db, m) is None
    verify_alias(alice, "alice@work.example.net")
    with fx.w.ctx.sessions() as db:
        m = db.scalars(select(InboundMessage)).one()
        assert decision.revalidate(db, m) is not None
        user = db.get(User, alice.uid)
        assert user is not None
        user.status = "disabled"  # account disabled mid-queue
        db.commit()
        assert decision.revalidate(db, m) is None
        user.status = "active"
        m.resolved_user_id = fx.uid("bob@example.com")  # mapping moved to someone else
        db.commit()
        assert decision.revalidate(db, m) is None


# ------------------------------------------------------------------ quarantine store


def test_quarantine_stores_bytes_under_server_names(fx: Fx) -> None:
    run_poll(
        fx.w,
        FakeBox({1: build(frm="stranger@example.com", attachments=[("../../evil.sh", b"rm")])}),
    )
    m = only(fx)
    assert m.status == "quarantined" and m.content_state == "stored"
    base = fx.w.settings.app_data_path / "mail-quarantine" / str(m.id)
    assert [p.name for p in base.iterdir()] == ["1.bin"]
    assert (base / "1.bin").read_bytes() == b"rm"
    att = fx.w.db_rows(InboundAttachment)[0]
    assert att.stored_name == "1.bin" and "/" not in att.display_name


def test_quota_keeps_metadata_only(make_world: Callable[..., World]) -> None:
    w = mail_world(make_world, **{**IMAP, "mail_quarantine_quota_bytes": 10})
    Fx(w)
    run_poll(w, FakeBox({1: build(frm="x@example.com", attachments=[("big.pdf", b"x" * 100)])}))
    m = w.db_rows(InboundMessage)[0]
    assert m.content_state == "dropped"
    assert w.db_rows(InboundAttachment)[0].stored_name is None


def test_retention_removes_bytes_but_keeps_the_row(fx: Fx) -> None:
    run_poll(fx.w, FakeBox({1: build(frm="stranger@example.com")}))
    m = only(fx)
    base = fx.w.settings.app_data_path / "mail-quarantine" / str(m.id)
    assert base.exists()
    fx.w.advance(days=29)
    assert store.sweep_retention(fx.w.ctx.sessions, fx.w.settings) == 0
    fx.w.advance(days=2)
    assert store.sweep_retention(fx.w.ctx.sessions, fx.w.settings) == 1
    assert not base.exists()
    assert only(fx).content_state == "purged"
    assert fx.w.db_rows(InboundAttachment)[0].stored_name is None


def test_oversize_and_malformed_messages(fx: Fx) -> None:
    small = fx.w.settings.model_copy(update={"imap_max_message_bytes": 5000})
    big = build(frm="stranger@example.com", attachments=[("big.pdf", b"x" * 20000)])
    box = FakeBox({1: big})
    poll_once(fx.w.ctx.sessions, small, factory=lambda: box)
    m = only(fx)
    assert (m.status, m.reason, m.content_state) == ("quarantined", "TOO_LARGE", "none")
    assert box.fetched == []  # never downloaded


# ------------------------------------------------------------------ dedupe and retries


def test_same_uid_is_recorded_once(fx: Fx) -> None:
    box = FakeBox({1: build()})
    run_poll(fx.w, box)
    run_poll(fx.w, box)
    assert len(messages(fx)) == 1
    # even with the checkpoint lost, the unique key holds
    with fx.w.ctx.sessions() as db:
        state = db.scalars(select(MailboxState)).one()
        state.last_uid = 0
        db.commit()
    run_poll(fx.w, box)
    assert len(messages(fx)) == 1 and box.fetched.count(1) == 2


def test_same_bytes_under_a_new_uid_is_a_duplicate(fx: Fx) -> None:
    raw = build()
    run_poll(fx.w, FakeBox({1: raw, 2: raw}))
    rows = {m.uid: m for m in messages(fx)}
    assert rows[1].status == "verified"
    assert rows[2].status == "duplicate" and rows[2].duplicate_of == rows[1].id
    assert rows[2].resolved_user_id is None  # not routed twice


def test_uidvalidity_change_rereads_without_new_verified_rows(fx: Fx) -> None:
    raw = build()
    run_poll(fx.w, FakeBox({1: raw}, uidvalidity=7))
    run_poll(fx.w, FakeBox({5: raw, 6: build()}, uidvalidity=8))
    verified = [m for m in messages(fx) if m.status == "verified"]
    assert len(verified) == 2  # original and the genuinely new one
    assert [m.status for m in messages(fx) if m.uid == 5] == ["duplicate"]
    with fx.w.ctx.sessions() as db:
        state = db.scalars(select(MailboxState)).one()
        assert (state.uidvalidity, state.last_uid) == (8, 6)


def test_same_message_id_does_not_suppress_a_different_message(fx: Fx) -> None:
    first = build(frm="stranger@example.com", before={"Message-ID": "<fixed@x>"})
    real = build(before={"Message-ID": "<fixed@x>"})
    run_poll(fx.w, FakeBox({1: first, 2: real}))
    assert {m.uid: m.status for m in messages(fx)} == {1: "quarantined", 2: "verified"}


def test_failing_message_is_retried_then_dead_lettered(fx: Fx) -> None:
    box = FakeBox({1: build(), 2: build()})
    box.fail_fetch = {1}
    for _ in range(4):
        run_poll(fx.w, box)
        assert not messages(fx)  # checkpoint did not move past the failing message
    run_poll(fx.w, box)  # fifth failure
    rows = {m.uid: m for m in messages(fx)}
    assert (rows[1].status, rows[1].reason) == ("error", "PROCESSING_FAILED")
    assert rows[2].status == "verified"
    assert any(a.action == "mail.dead_letter" for a in fx.w.db_rows(AuthAudit))


def test_mailbox_down_is_recorded_and_lease_released(fx: Fx) -> None:
    def down() -> Any:
        raise MailboxError("CONNECT_FAILED")

    assert poll_once(fx.w.ctx.sessions, fx.w.settings, factory=down) == 0
    with fx.w.ctx.sessions() as db:
        state = db.scalars(select(MailboxState)).one()
        assert (state.last_error, state.failures, state.lease_until) == ("CONNECT_FAILED", 1, None)
    run_poll(fx.w, FakeBox({1: build()}))
    with fx.w.ctx.sessions() as db:
        state = db.scalars(select(MailboxState)).one()
        assert (state.last_error, state.failures) == (None, 0)


def test_a_held_lease_blocks_a_second_poller(fx: Fx) -> None:
    run_poll(fx.w, FakeBox())
    with fx.w.ctx.sessions() as db:
        state = db.scalars(select(MailboxState)).one()
        state.lease_until = clock.utcnow() + timedelta(minutes=3)
        db.commit()
    box = FakeBox({1: build()})
    assert run_poll(fx.w, box) == 0 and box.fetched == [] and not messages(fx)


def test_mailbox_read_only_calls_only() -> None:
    import app.mail.imap as imap_module

    src = Path(imap_module.__file__).read_text()
    assert "BODY.PEEK" in src and "readonly=True" in src
    for word in ("STORE", "EXPUNGE", "COPY", "DELETE", ".append(", "APPEND"):
        assert word not in src, word


# ------------------------------------------------------------------ views and privacy


def seed_two(fx: Fx) -> None:
    run_poll(
        fx.w,
        FakeBox(
            {
                1: build(),
                2: build(frm="stranger@example.com", attachments=[("evil.exe", b"MZ")]),
                3: build(frm="bob@example.com", auth=passing()),
            }
        ),
    )


def test_people_see_only_their_own_verified_mail(fx: Fx) -> None:
    seed_two(fx)
    alice, bob = Api(fx, "alice"), Api(fx, "bob")
    mine = alice.client.get("/api/v1/me/inbox").json()
    assert [m["from_address"] for m in mine] == ["alice@example.com"]
    assert mine[0]["attachments"][0]["display_name"] == "scan.pdf"
    assert [m["from_address"] for m in bob.client.get("/api/v1/me/inbox").json()] == [
        "bob@example.com"
    ]
    carol = Api(fx, "carol")
    assert carol.client.get("/api/v1/me/inbox").json() == []


def test_admin_sees_quarantine_but_never_verified_mail(fx: Fx) -> None:
    seed_two(fx)
    admin, h = fx.admin
    listed = admin.get("/api/v1/admin/quarantine").json()
    assert [m["from_address"] for m in listed] == ["stranger@example.com"]
    for m in fx.w.db_rows(InboundMessage):
        r = admin.get(f"/api/v1/admin/quarantine/{m.id}")
        assert r.status_code == (200 if m.status == "quarantined" else 404), m.status
    status = admin.get("/api/v1/admin/mailbox").json()
    assert status["configured"] and status["quarantined"] == 1 and status["verified"] == 2
    assert "box-secret" not in admin.get("/api/v1/admin/mailbox").text


def test_non_admin_cannot_use_admin_routes(fx: Fx) -> None:
    seed_two(fx)
    alice = Api(fx, "alice")
    mid = next(m.id for m in fx.w.db_rows(InboundMessage) if m.status == "quarantined")
    for path in ("/admin/mailbox", "/admin/quarantine", f"/admin/quarantine/{mid}"):
        assert alice.get(path).status_code == 403
    assert alice.post(f"/admin/quarantine/{mid}/reject", json={"confirm": True}).status_code == 403
    assert alice.post("/admin/mailbox/poll").status_code == 403
    anon = fx.w.browser()
    assert anon.get("/api/v1/me/inbox").status_code == 401
    assert anon.get("/api/v1/admin/quarantine").status_code == 401


def test_quarantine_download_is_inert_and_audited(fx: Fx) -> None:
    seed_two(fx)
    admin, h = fx.admin
    m = next(m for m in fx.w.db_rows(InboundMessage) if m.status == "quarantined")
    att = fx.w.db_rows(InboundAttachment)
    att = [a for a in att if a.message_id == m.id][0]
    r = admin.get(f"/api/v1/admin/quarantine/{m.id}/attachments/{att.id}/content")
    assert r.status_code == 200 and r.content == b"MZ"
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert "attachment" in r.headers["content-disposition"]
    assert "evil.exe" not in r.headers["content-disposition"]
    assert any(a.action == "mail.attachment_downloaded" for a in fx.w.db_rows(AuthAudit))
    # an attachment of a verified message is not reachable through the admin route
    verified = next(m for m in fx.w.db_rows(InboundMessage) if m.status == "verified")
    va = [a for a in fx.w.db_rows(InboundAttachment) if a.message_id == verified.id][0]
    assert (
        admin.get(f"/api/v1/admin/quarantine/{verified.id}/attachments/{va.id}/content").status_code
        == 404
    )
    # wrong message for the attachment
    assert (
        admin.get(f"/api/v1/admin/quarantine/{m.id}/attachments/{va.id}/content").status_code == 404
    )


def test_reject_needs_confirmation_and_removes_bytes(fx: Fx) -> None:
    seed_two(fx)
    admin, h = fx.admin
    m = next(m for m in fx.w.db_rows(InboundMessage) if m.status == "quarantined")
    base = fx.w.settings.app_data_path / "mail-quarantine" / str(m.id)
    assert (
        admin.post(f"/api/v1/admin/quarantine/{m.id}/reject", json={}, headers=h).status_code == 422
    )
    assert base.exists()
    r = admin.post(f"/api/v1/admin/quarantine/{m.id}/reject", json={"confirm": True}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert r.json()["reason"] == "SENDER_UNKNOWN" and not base.exists()
    att = [a for a in fx.w.db_rows(InboundAttachment) if a.message_id == m.id][0]
    assert (
        admin.get(f"/api/v1/admin/quarantine/{m.id}/attachments/{att.id}/content").status_code
        == 404
    )
    # a rejected message's bytes stay rejected when the same mail is fetched again
    run_poll(
        fx.w,
        FakeBox(
            {9: build(frm="stranger@example.com", attachments=[("evil.exe", b"MZ")])},
            uidvalidity=99,
        ),
    )
    assert [x.status for x in fx.w.db_rows(InboundMessage) if x.uid == 9] in (
        ["quarantined"],
        ["duplicate"],
    )


def test_poll_now_wakes_the_poller(fx: Fx) -> None:
    admin, h = fx.admin
    assert admin.post("/api/v1/admin/mailbox/poll", headers=h).status_code == 202
    assert admin.get("/api/v1/admin/mailbox").json()["authentication_unproven"] is True
    run_poll(fx.w, FakeBox({1: build()}))
    assert admin.get("/api/v1/admin/mailbox").json()["authentication_unproven"] is False


def test_mailbox_disabled_by_default(make_world: Callable[..., World]) -> None:
    w = make_world()
    assert not w.settings.imap_configured
    w.create_admin()
    c, h = signed_in(w)
    assert c.get("/api/v1/admin/mailbox").json()["configured"] is False
    assert c.post("/api/v1/admin/mailbox/poll", headers=h).status_code == 409


def test_config_validation(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "data").mkdir()

    def cfg(**kw: Any) -> Settings:
        return Settings(
            database_url="postgresql+psycopg://u:p@h/db",
            library_path=tmp_path / "lib",
            app_data_path=tmp_path / "data",
            **kw,
        )

    with pytest.raises(ValueError, match="must all be set"):
        cfg(imap_host="imap.example.org")
    with pytest.raises(ValueError, match="must differ"):
        cfg(**{**IMAP, "imap_ingest_address": PERSONAL})
    with pytest.raises(ValueError, match="loopback"):
        cfg(**{**IMAP, "imap_security": "none"})
    with pytest.raises(ValueError, match="plain email"):
        cfg(**{**IMAP, "imap_ingest_address": "Bob <x@example.org>"})
    assert cfg(**{**IMAP, "imap_host": "localhost", "imap_security": "none"}).imap_configured
    assert not cfg().imap_configured


def test_secrets_not_in_logs_or_status(fx: Fx, caplog: pytest.LogCaptureFixture) -> None:
    def down() -> Any:
        raise RuntimeError("password box-secret rejected for box")

    with caplog.at_level("DEBUG"):
        poll_once(fx.w.ctx.sessions, fx.w.settings, factory=down)
    assert "box-secret" not in caplog.text


def test_base64_attachment_decodes() -> None:
    m = EmailMessage()
    m["From"] = "alice@example.com"
    m.set_content("x")
    m.add_attachment(
        b"\x00\x01binary" * 50, maintype="application", subtype="octet-stream", filename="b.bin"
    )
    parsed = parse_message(m.as_bytes(), LIMITS)
    assert parsed.attachments[0].data == b"\x00\x01binary" * 50
