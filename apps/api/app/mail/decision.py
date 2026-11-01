"""Who sent a message and where it was addressed (M13, ADR-007).

The rule is deliberately narrow. A message is `verified` only when all of these hold:

* exactly one `From` mailbox that belongs to exactly one active account (its own email or a
  verified alias);
* our mailbox provider's `Authentication-Results` vouches for the From domain;
* the provider's recipient header names exactly one of the two intake addresses.

Anything else is `quarantined` with a reason code. `revalidate` repeats the account part at
processing time, so a removed alias or a disabled account stops a queued message.
"""

import uuid
from dataclasses import dataclass, field
from email.utils import getaddresses

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import InboundMessage, User, UserEmailAlias
from app.mail import authres
from app.mail.address import domain_of, normalize_address
from app.mail.mime import ParsedMessage


@dataclass
class Decision:
    status: str  # verified | quarantined
    reason: str | None
    destination: str  # personal | shared | unknown
    user_id: uuid.UUID | None
    summary: dict[str, str] = field(default_factory=dict)


def resolve_sender(db: Session, address: str) -> tuple[User | None, str | None]:
    """The one active account that owns `address`, or (None, reason)."""
    owners: dict[uuid.UUID, User] = {}
    for user in db.scalars(select(User).where(User.email == address)):
        owners[user.id] = user
    alias_users = db.scalars(
        select(User)
        .join(UserEmailAlias, UserEmailAlias.user_id == User.id)
        .where(UserEmailAlias.email == address, UserEmailAlias.verified_at.is_not(None))
    )
    for user in alias_users:
        owners[user.id] = user
    if not owners:
        return None, "SENDER_UNKNOWN"
    if len(owners) > 1:
        return None, "SENDER_AMBIGUOUS"
    (user,) = owners.values()
    if user.status != "active":
        return None, "USER_INACTIVE"
    return user, None


def resolve_destination(
    recipient_headers: dict[str, list[str]], settings: Settings
) -> tuple[str, str | None]:
    """(destination, reason). Reads only the provider-added headers, topmost value first; two
    different addresses for the same header or across headers mean the destination is ambiguous."""
    personal = normalize_address(settings.imap_personal_address)
    ingest = normalize_address(settings.imap_ingest_address)
    wanted = [h.strip().lower() for h in settings.imap_recipient_headers.split(",") if h.strip()]
    seen: set[str] = set()
    for name in wanted:
        for value in recipient_headers.get(name, []):
            found = [a for _, a in getaddresses([value])]
            if len(found) != 1:
                return "unknown", "RECIPIENT_AMBIGUOUS"
            address = normalize_address(found[0])
            if address is None:
                return "unknown", "RECIPIENT_AMBIGUOUS"
            seen.add(address)
    if not seen:
        return "unknown", "RECIPIENT_UNKNOWN"
    if len(seen) > 1:
        return "unknown", "RECIPIENT_AMBIGUOUS"
    (address,) = seen
    if address == personal:
        return "personal", None
    if address == ingest:
        return "shared", None
    return "unknown", "RECIPIENT_UNKNOWN"


def decide(db: Session, parsed: ParsedMessage, settings: Settings) -> Decision:
    destination, dest_reason = resolve_destination(parsed.recipient_headers, settings)
    if parsed.from_address is None:
        return Decision("quarantined", "FROM_INVALID", destination, None)
    auth = authres.evaluate(
        parsed.auth_results,
        settings.imap_authserv_id,
        settings.imap_auth_policy,
        domain_of(parsed.from_address),
    )
    if not auth.ok:
        return Decision("quarantined", auth.reason, destination, None, auth.summary)
    user, reason = resolve_sender(db, parsed.from_address)
    if user is None:
        return Decision("quarantined", reason, destination, None, auth.summary)
    if dest_reason is not None:
        return Decision("quarantined", dest_reason, destination, None, auth.summary)
    return Decision("verified", None, destination, user.id, auth.summary)


def revalidate(db: Session, message: InboundMessage) -> User | None:
    """The account a stored `verified` message still belongs to, or None. M14 calls this when it
    processes a message: the mapping seen on arrival is never trusted later."""
    if message.status != "verified" or message.from_address is None:
        return None
    user, _ = resolve_sender(db, message.from_address)
    if user is None or user.id != message.resolved_user_id:
        return None
    return user
