"""Reading the mailbox provider's `Authentication-Results` header (RFC 8601) and deciding whether
it vouches for the sender (M13, ADR-007).

Nothing here validates SPF, DKIM or DMARC: it trusts the *one* header written by our own provider
(the configured authserv-id), and only when that header is the topmost one and there is no second
header claiming the same id. Everything a sender can write sits below the provider's header.
"""

import re
from dataclasses import dataclass, field

from app.mail.address import _DOMAIN

_COMMENT = re.compile(r"\([^()]*\)")
_RESULT = re.compile(r"^([a-z0-9-]+)(?:/\d+)?\s*=\s*([a-z]+)", re.IGNORECASE)
_PROP = re.compile(r'([a-z0-9-]+\.[a-z0-9-]+)\s*=\s*("[^"]*"|[^\s;]+)', re.IGNORECASE)
KNOWN_RESULTS = {"pass", "fail", "none", "neutral", "softfail", "temperror", "permerror", "policy"}


@dataclass
class MethodResult:
    method: str
    result: str
    props: dict[str, str]


@dataclass
class AuthResults:
    authserv_id: str
    results: list[MethodResult] = field(default_factory=list)


def _strip_comments(value: str) -> str:
    for _ in range(8):
        new = _COMMENT.sub(" ", value)
        if new == value:
            break
        value = new
    return value


def _split_semicolons(value: str) -> list[str]:
    parts: list[str] = []
    cur: list[str] = []
    quoted = False
    for c in value:
        if c == '"':
            quoted = not quoted
        if c == ";" and not quoted:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    parts.append("".join(cur))
    return parts


def parse(value: str) -> AuthResults | None:
    if len(value) > 4096:
        return None
    parts = _split_semicolons(_strip_comments(value))
    head = parts[0].split()
    if not head:
        return None
    out = AuthResults(authserv_id=head[0].lower())
    for chunk in parts[1:]:
        chunk = chunk.strip()
        m = _RESULT.match(chunk)
        if m is None:
            continue
        props = {k.lower(): v.strip('"').lower() for k, v in _PROP.findall(chunk)}
        out.results.append(MethodResult(m.group(1).lower(), m.group(2).lower(), props))
    return out


@dataclass
class AuthDecision:
    ok: bool
    reason: str | None
    summary: dict[str, str]


def _domain_of_mailfrom(value: str) -> str:
    """The domain of `smtp.mailfrom`, or "" when the value is not a plain domain or address."""
    domain = value.rpartition("@")[2].lower()
    return domain if _DOMAIN.match(domain) else ""


def _word(result: str | None) -> str:
    return result if result in KNOWN_RESULTS else "other"


def evaluate(
    header_values: list[str], authserv_id: str, policy: str, from_domain: str
) -> AuthDecision:
    """`header_values` are the raw `Authentication-Results` values in message order (topmost
    first)."""
    mine = authserv_id.strip().lower()
    summary: dict[str, str] = {}
    if not mine:
        return AuthDecision(False, "AUTH_UNCONFIGURED", summary)
    if not header_values:
        return AuthDecision(False, "AUTH_MISSING", summary)
    parsed = [parse(v) for v in header_values]
    first = parsed[0]
    if first is None or first.authserv_id != mine:
        return AuthDecision(False, "AUTH_MISSING", summary)
    if any(p is not None and p.authserv_id == mine for p in parsed[1:]):
        return AuthDecision(False, "AUTH_AMBIGUOUS", summary)
    for method in ("dmarc", "dkim", "spf"):
        words = {_word(r.result) for r in first.results if r.method == method}
        if words:
            summary[method] = "pass" if words == {"pass"} else sorted(words)[0]

    dmarc = [r for r in first.results if r.method == "dmarc"]
    spf = [r for r in first.results if r.method == "spf"]
    # More than one SPF verdict in the provider's header means a hostile envelope sender may have
    # injected a second one (review L1): never guess which is real.
    if len(spf) > 1 or len(dmarc) > 1:
        return AuthDecision(False, "AUTH_AMBIGUOUS", summary)
    if dmarc and (dmarc[0].result != "pass" or dmarc[0].props.get("header.from") != from_domain):
        return AuthDecision(False, "AUTH_FAILED", summary)
    # DMARC can pass on SPF alone, and SPF alignment is weak on shared sending platforms, so a
    # DKIM signature from the sender's own domain is required on every accepting path (review M1).
    dkim_ok = any(
        r.method == "dkim" and r.result == "pass" and r.props.get("header.d") == from_domain
        for r in first.results
    )
    if not dkim_ok:
        return AuthDecision(False, "AUTH_FAILED", summary)
    if dmarc:
        return AuthDecision(True, None, summary)
    if policy == "dmarc":
        return AuthDecision(False, "AUTH_FAILED", summary)
    spf_ok = bool(spf) and (
        spf[0].result == "pass"
        and _domain_of_mailfrom(spf[0].props.get("smtp.mailfrom", "")) == from_domain
    )
    if spf_ok:
        return AuthDecision(True, None, summary)
    return AuthDecision(False, "AUTH_FAILED", summary)
