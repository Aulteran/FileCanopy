"""Which provider may see what (M15, design D3-D10): routing, the privacy gate, cleared inputs.

`resolve_route` returns exactly one provider or none; there is no fallback list, so an outage can
never move a request from a local provider to a cloud one. `clear` is the one place that applies
`privacy.ai_route_allowed` and it is the only way to build a `ClearedInput`; adapters refuse
anything else and re-check the label themselves (defence in depth, 03_SECURITY_PRIVACY.md).
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AiProvider, AppSetting, User
from app.policy import privacy

PREFERENCES = ("off", "instance_default", "instance_default_local_only", "my_provider")
INSTANCE_PROVIDER_KEY = "ai.instance_provider_id"
# Bump when the wording shown before cloud AI is enabled changes; consent must match.
CONSENT_VERSION = 1


class AiRefused(Exception):
    """The request was not made. `reason` is a short code safe to show and to store."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Route:
    provider: AiProvider | None
    scope_key: str  # whose budget pays: "instance" or "user:<id>"
    reason: str  # why there is no provider; "ok" when there is one


def instance_provider(db: Session) -> AiProvider | None:
    row = db.get(AppSetting, INSTANCE_PROVIDER_KEY)
    if row is None or not isinstance(row.value, str):
        return None
    try:
        provider = db.get(AiProvider, uuid.UUID(row.value))
    except ValueError:
        return None
    if provider is None or provider.scope != "instance" or not provider.enabled:
        return None
    return provider


def user_provider(db: Session, user_id: uuid.UUID) -> AiProvider | None:
    return db.scalar(
        select(AiProvider).where(
            AiProvider.scope == "user",
            AiProvider.owner_user_id == user_id,
            AiProvider.enabled.is_(True),
        )
    )


def instance_signature(provider: AiProvider) -> str:
    """What a person agreed to when they chose the server's cloud AI: this provider row and vendor.
    Changing either asks them again (M15 review M2)."""
    return f"{provider.id}:{provider.kind}:v{CONSENT_VERSION}"


def resolve_route(db: Session, *, root_kind: str, owner: User | None) -> Route:
    """`root_kind` is the storage root the content lives in. The shared tree uses the instance
    provider and never a person's credentials; a personal tree follows its owner's choice."""
    if root_kind == "shared":
        provider = instance_provider(db)
        return Route(provider, "instance", "ok" if provider else "ai_not_configured")
    if root_kind != "personal" or owner is None:
        return Route(None, "instance", "unknown_root")
    choice = owner.ai_preference
    if choice == "instance_default":
        provider = instance_provider(db)
        if provider is None:
            return Route(None, "instance", "ai_not_configured")
        if provider.privacy_mode == "cloud" and owner.ai_ack != instance_signature(provider):
            return Route(None, "instance", "instance_consent_required")
        return Route(provider, "instance", "ok")
    if choice == "instance_default_local_only":
        provider = instance_provider(db)
        if provider is None:
            return Route(None, "instance", "ai_not_configured")
        if provider.privacy_mode != "local":
            return Route(None, "instance", "instance_not_local")
        return Route(provider, "instance", "ok")
    if choice == "my_provider":
        provider = user_provider(db, owner.id)
        return Route(provider, f"user:{owner.id}", "ok" if provider else "no_own_provider")
    return Route(None, "instance", "ai_off")  # "off" and anything unrecognised


_MINT = object()


class ClearedInput:
    """Text that passed the gate for one provider mode. Built only by `clear`.

    Deliberately not a dataclass and not copyable: `dataclasses.replace`, `copy` and pickling
    would otherwise let in-process code keep the proof and swap the label or the text (M15
    review M1). Attributes cannot be reassigned after construction."""

    __slots__ = ("text", "label", "provider_mode")
    text: str
    label: str
    provider_mode: str

    def __init__(self, token: object, text: str, label: str, provider_mode: str) -> None:
        if token is not _MINT:
            raise TypeError("ClearedInput is created by routing.clear only")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "provider_mode", provider_mode)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("ClearedInput is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("ClearedInput is immutable")

    def __replace__(self, **changes: object) -> "ClearedInput":
        raise TypeError("ClearedInput cannot be modified or copied")

    def __copy__(self) -> "ClearedInput":
        raise TypeError("ClearedInput cannot be modified or copied")

    def __deepcopy__(self, memo: object) -> "ClearedInput":
        raise TypeError("ClearedInput cannot be modified or copied")

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("ClearedInput cannot be modified or copied")

    def __repr__(self) -> str:  # never shows the text
        return f"ClearedInput(label={self.label!r}, mode={self.provider_mode!r})"


def clear(
    text: str,
    *,
    label: str,
    provider_mode: str,
    local_opt_in: bool,
    unlocked_by_person: bool,
) -> ClearedInput:
    verdict = privacy.ai_route_allowed(
        label, provider_mode, local_opt_in=local_opt_in, unlocked_by_person=unlocked_by_person
    )
    if not verdict.allowed:
        raise AiRefused(verdict.reason)
    return ClearedInput(_MINT, text, label, provider_mode)


def assert_deliverable(cleared: object, provider_mode: str) -> ClearedInput:
    """What an adapter calls before any network use: a real `ClearedInput`, cleared for this very
    provider mode, and never protected content on a cloud adapter."""
    if not isinstance(cleared, ClearedInput):
        raise AiRefused("not_cleared")
    if cleared.provider_mode != provider_mode:
        raise AiRefused("mode_mismatch")
    if provider_mode == "cloud" and cleared.label != "normal":
        raise AiRefused("protected_never_cloud")
    return cleared
