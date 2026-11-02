"""Email address handling for the inbound pipeline (M13, ADR-007).

One rule for every address the system compares: ASCII lower case, IDNA domain, plain
`local@domain` with no display name, comments or groups. Anything else is not an address here.
"""

import re
import unicodedata
from email.headerregistry import Address, AddressHeader
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings

_LOCAL = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}$")
_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def normalize_address(raw: str) -> str | None:
    """`Alice@Example.COM ` -> `alice@example.com`; None when it is not a plain address."""
    raw = raw.strip()
    if not raw or len(raw) > 254 or raw.count("@") != 1:
        return None
    if any(ord(c) < 33 or ord(c) == 127 for c in raw):
        return None
    local, _, domain = raw.rpartition("@")
    local = local.lower()
    try:
        domain = domain.lower().encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if not _LOCAL.match(local) or local.startswith(".") or local.endswith(".") or ".." in local:
        return None
    if not _DOMAIN.match(domain):
        return None
    return f"{local}@{domain}"


def address_forms(address: str) -> list[str]:
    """Both spellings of an address with an internationalised domain: sign-in emails were stored
    with a Unicode domain, mail addresses are compared as punycode (review L3). Compare against
    every form so one mailbox cannot belong to two accounts under two spellings."""
    local, _, domain = address.rpartition("@")
    forms = [address]
    try:
        forms.append(f"{local}@{domain.encode('ascii').decode('idna')}")
    except UnicodeError:
        pass
    return list(dict.fromkeys(forms))


def reserved_addresses(settings: "Settings") -> set[str]:
    """Addresses nobody may claim as a sender identity: the two intake addresses (their mail
    lands in the mailbox) and the system's own sender address (review L2)."""
    out = set()
    for raw in (settings.imap_personal_address, settings.imap_ingest_address, settings.smtp_from):
        norm = normalize_address(raw) if raw else None
        if norm:
            out.add(norm)
    return out


def domain_of(address: str) -> str:
    return address.rpartition("@")[2]


def clean_text(raw: str, limit: int) -> str:
    """Display text from an untrusted header: no controls or bidirectional overrides, one line."""
    out = []
    for c in unicodedata.normalize("NFC", raw):
        cat = unicodedata.category(c)
        if c in "\r\n\t":
            out.append(" ")
        elif cat in ("Cc", "Cf", "Cs", "Co", "Cn") or cat in ("Zl", "Zp"):
            continue
        else:
            out.append(c)
    return " ".join("".join(out).split())[:limit]


def single_from(values: list[object]) -> str | None:
    """The one mailbox in the one `From` header, or None for anything unusual: no header, several
    headers, several addresses, group syntax, parser defects. `values` are parsed header objects
    from the `default` email policy (never raw strings)."""
    if len(values) != 1:
        return None
    header = values[0]
    if not isinstance(header, AddressHeader) or getattr(header, "defects", None):
        return None
    groups = header.groups
    if len(groups) != 1 or groups[0].display_name is not None or len(groups[0].addresses) != 1:
        return None
    addr = groups[0].addresses[0]
    if not isinstance(addr, Address):
        return None
    try:
        spec = f"{addr.username}@{addr.domain}"
    except Exception:
        return None
    return normalize_address(spec)
