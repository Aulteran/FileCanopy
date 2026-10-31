"""Policy vocabulary (ADR-004 §3, §6)."""

import uuid
from dataclasses import dataclass
from enum import StrEnum


class Privilege(StrEnum):
    TRAVERSE = "traverse"  # the node's own metadata is visible
    LIST = "list"  # enumerate children
    READ = "read"  # content and every derivative (previews, text, vectors)
    WRITE = "write"  # create / rename / move / trash beneath
    SHARE = "share"  # create / revoke grants beneath
    MANAGE = "manage"  # privacy label, all grants, root-level settings


ALL_PRIVILEGES = frozenset(Privilege)
VIEWER = frozenset({Privilege.TRAVERSE, Privilege.LIST, Privilege.READ})
EDITOR = VIEWER | {Privilege.WRITE}
LEVELS: dict[str, frozenset[Privilege]] = {"viewer": VIEWER, "editor": EDITOR}


@dataclass(frozen=True)
class Actor:
    """Who is acting. Always an account id: the role is deliberately not part of an actor, so a
    content decision cannot depend on it. Background jobs carry their originating user id."""

    user_id: uuid.UUID
    kind: str = "user"
