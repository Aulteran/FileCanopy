"""The one door to AI for every later milestone (M15, design D4-D10).

`generate` is the only way server code may send document text to a provider. Order of events:
authorise the caller on the node, read the node's *current* effective label from the database,
pick the single provider the owner's settings allow, check consent, run the privacy gate, check
the breaker, reserve budget, call, settle, validate the output against a typed model. Any refusal
or failure leaves the file untouched; nothing here ever blocks file operations, and no code path
falls back from one provider to another.
"""

import json
import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.ai import budget
from app.ai.providers import (
    WRAPPER_OVERHEAD_BYTES,
    Adapter,
    ProviderError,
    Resolver,
    parse_allow_list,
    system_resolver,
)
from app.ai.routing import (
    CONSENT_VERSION,
    AiRefused,
    ClearedInput,
    Route,
    clear,
    resolve_route,
)
from app.ai.secrets import SecretBox, SecretsError, aad_for
from app.auth import clock
from app.config import Settings
from app.db.models import AiProvider, AiUsage, Node, StorageRoot, User
from app.policy import engine, privacy
from app.policy.model import Actor, Privilege

T = TypeVar("T", bound=BaseModel)


class AiError(Exception):
    """The provider was reached (or could not be) and the request failed. `code` is safe to show."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AiCaller:
    """Who is asking. Jobs carry no `session_id`, so they are never treated as unlocked."""

    user_id: uuid.UUID
    session_id: uuid.UUID | None = None
    kind: str = "user"


@dataclass(frozen=True)
class Generated[T: BaseModel]:
    value: T
    input_tokens: int
    output_tokens: int
    cost_micro: int
    provider_kind: str
    model: str


class Pong(BaseModel):
    ok: bool


class Breaker:
    """Stops hammering a provider that keeps failing. In memory: a restart resets it."""

    def __init__(self, failures: int, seconds: int) -> None:
        self._limit, self._seconds = failures, seconds
        self._state: dict[uuid.UUID, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def is_open(self, provider_id: uuid.UUID) -> bool:
        with self._lock:
            count, until = self._state.get(provider_id, (0, 0.0))
            return count >= self._limit and time.monotonic() < until

    def failed(self, provider_id: uuid.UUID) -> None:
        with self._lock:
            count, _ = self._state.get(provider_id, (0, 0.0))
            self._state[provider_id] = (count + 1, time.monotonic() + self._seconds)

    def succeeded(self, provider_id: uuid.UUID) -> None:
        with self._lock:
            self._state.pop(provider_id, None)


class AiGateway:
    def __init__(
        self,
        settings: Settings,
        sessions: "sessionmaker[Session]",
        box: SecretBox,
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver = system_resolver,
    ) -> None:
        self._s = settings
        self._resolver = resolver
        self._allow = parse_allow_list(settings.ai_local_hosts)
        self._sessions = sessions
        self._box = box
        self._transport = transport
        self.breaker = Breaker(settings.ai_breaker_failures, settings.ai_breaker_seconds)

    @property
    def resolver(self) -> Resolver:
        return self._resolver

    @property
    def allow_hosts(self) -> frozenset[str]:
        return self._allow

    # --- adapters ---------------------------------------------------------------------------
    def adapter_for(self, provider: AiProvider) -> Adapter:
        key = ""
        if provider.secret_ciphertext:
            try:
                key = self._box.open(
                    provider.secret_ciphertext, aad_for(provider.id, provider.owner_user_id)
                )
            except SecretsError as exc:
                raise AiError("SECRET_UNAVAILABLE") from exc
        elif provider.privacy_mode == "cloud":
            raise AiError("SECRET_UNAVAILABLE")
        return Adapter(
            provider.kind,
            provider.model,
            provider.endpoint,
            key,
            timeout=self._s.ai_timeout_seconds,
            max_response_bytes=self._s.ai_max_response_bytes,
            transport=self._transport,
            resolver=self._resolver,
            allow_hosts=self._allow,
        )

    # --- the public call --------------------------------------------------------------------
    def generate(
        self,
        caller: AiCaller,
        node_ids: Sequence[uuid.UUID],
        *,
        purpose: str,
        instructions: str,
        text: str,
        output_model: type[T],
        max_output_tokens: int | None = None,
    ) -> Generated[T]:
        """Raises `AiRefused` (not sent: policy, consent, budget, unavailable) or `AiError`.

        `text` must be derived only from the files in `node_ids`: the gate uses the strictest
        label among them, and every one must be readable by the caller (and unlocked, for
        Confidential). Callers that mix in text from elsewhere must list that file too."""
        max_out = min(
            max_output_tokens or self._s.ai_max_output_tokens, self._s.ai_max_output_tokens
        )
        if not node_ids or len(set(node_ids)) > 20:
            raise AiRefused("node_unavailable")
        if len(text) + len(instructions) > self._s.ai_max_input_chars:
            raise AiRefused("input_too_large")
        with self._sessions() as db:
            route, label, cleared = self._authorise(db, caller, list(dict.fromkeys(node_ids)), text)
            return self._run(
                db, caller, route, cleared, purpose, instructions, text, output_model, max_out
            )

    def probe(self, caller: AiCaller, provider: AiProvider) -> bool:
        """The opt-in connection test: a tiny synthetic request (Normal, no document involved).
        Cloud probes are billed to the provider's budget scope like any other request."""
        scope = f"user:{provider.owner_user_id}" if provider.scope == "user" else "instance"
        cleared = clear(
            "ping",
            label="normal",
            provider_mode=provider.privacy_mode,
            local_opt_in=False,
            unlocked_by_person=False,
        )
        with self._sessions() as db:
            row = db.get(AiProvider, provider.id)
            if row is None or (
                row.privacy_mode == "cloud" and row.consent_version != CONSENT_VERSION
            ):
                raise AiRefused("consent_required")
            self._run(
                db,
                caller,
                Route(row, scope, "ok"),
                cleared,
                "probe",
                'Reply with {"ok": true}.',
                "ping",
                Pong,
                32,
            )
        return True

    def _run(
        self,
        db: Session,
        caller: AiCaller,
        route: Route,
        cleared: ClearedInput,
        purpose: str,
        instructions: str,
        text: str,
        output_model: type[T],
        max_out: int,
    ) -> Generated[T]:
        provider = route.provider
        if provider is None:
            raise AiRefused(route.reason)
        if self.breaker.is_open(provider.id):
            raise self._refuse(db, caller, route, provider, purpose, "provider_unavailable")
        reserved = 0
        month = budget.month_key()
        if provider.privacy_mode == "cloud":
            est = budget.cost_micro(
                budget.estimate_tokens(
                    len(text.encode()) + len(instructions.encode()) + WRAPPER_OVERHEAD_BYTES
                ),
                max_out,
                provider.price_in_micro,
                provider.price_out_micro,
            )
            budget.sweep_stale(db, route.scope_key)
            got = budget.reserve(db, route.scope_key, est, self._s.ai_default_cap_micro)
            if got is None:
                raise self._refuse(db, caller, route, provider, purpose, "budget_exceeded")
            month, reserved = got, est
        usage = AiUsage(
            at=clock.utcnow(),
            month=month,
            scope_key=route.scope_key,
            actor_user_id=caller.user_id,
            provider_id=provider.id,
            provider_kind=provider.kind,
            model=provider.model,
            purpose=purpose[:32],
            status="reserved",
            reserved_micro=reserved,
        )
        db.add(usage)
        db.commit()
        adapter = self.adapter_for_safe(db, route, provider, usage, month, reserved)
        try:
            done = adapter.complete(cleared, instructions=instructions, max_output_tokens=max_out)
        except ProviderError as exc:
            self._fail(db, route, usage, month, reserved, provider, exc.code)
            raise AiError(exc.code) from None
        except AiRefused as exc:  # adapter re-check: nothing was sent
            self._fail(db, route, usage, month, reserved, provider, exc.reason, refused=True)
            raise
        finally:
            adapter.close()
        self.breaker.succeeded(provider.id)
        final = (
            budget.cost_micro(
                done.input_tokens,
                done.output_tokens,
                provider.price_in_micro,
                provider.price_out_micro,
            )
            if provider.privacy_mode == "cloud"
            else 0
        )
        if reserved or final:
            # Settle only a reservation we still own; if the stale sweep already gave it back,
            # just record what was spent.
            owned = budget.claim(db, usage.id) if reserved else True
            budget.settle(db, route.scope_key, month, reserved if owned else 0, final)
            usage.reason = "" if owned else "after_expiry"
        usage.status, usage.final_micro = "ok", final
        usage.input_tokens, usage.output_tokens = done.input_tokens, done.output_tokens
        db.commit()
        try:
            value = output_model.model_validate(_json_object(done.text))
        except (ValueError, ValidationError):
            usage.status, usage.reason = "failed", "bad_output"
            db.commit()
            raise AiError("BAD_OUTPUT") from None
        return Generated(
            value, done.input_tokens, done.output_tokens, final, provider.kind, provider.model
        )

    # --- steps ----------------------------------------------------------------------------
    def _authorise(
        self, db: Session, caller: AiCaller, node_ids: list[uuid.UUID], text: str
    ) -> tuple[Route, str, ClearedInput]:
        user = db.get(User, caller.user_id)
        actor = Actor(caller.user_id, caller.kind, caller.session_id)
        nodes = [db.get(Node, i) for i in node_ids]
        if user is None or any(
            n is None or n.kind != "file" or not engine.decide(db, actor, n).allows(Privilege.READ)
            for n in nodes
        ):
            raise AiRefused("node_unavailable")  # one answer for missing and forbidden
        files = [n for n in nodes if n is not None]
        if len({n.root_id for n in files}) != 1:
            raise AiRefused("mixed_roots")  # one request is one tree, one provider, one budget
        root = db.get(StorageRoot, files[0].root_id)
        if root is None:
            raise AiRefused("node_unavailable")
        if root.kind == "personal":
            # Someone else's personal file is never sent on a caller's account or budget.
            if root.owner_user_id != caller.user_id:
                raise AiRefused("owner_only")
            owner: User | None = user
        else:
            owner = None
        route = resolve_route(db, root_kind=root.kind, owner=owner)
        provider = route.provider
        if provider is None:
            raise AiRefused(route.reason)
        if provider.privacy_mode == "cloud" and provider.consent_version != CONSENT_VERSION:
            raise AiRefused("consent_required")
        # Labels are read now, from the database: nothing the caller says can lower them. The
        # request is as protected as the most protected file it was built from.
        label = "normal"
        unlocked = True
        for n in files:
            label = privacy.stricter(label, privacy.effective_label(db, n))
            scope = privacy.confidential_scope(db, n)
            if scope is not None and not (
                caller.kind == "user"
                and privacy.live_unlock(db, caller.user_id, caller.session_id, scope) is not None
            ):
                unlocked = False
        cleared = clear(
            text,
            label=label,
            provider_mode=provider.privacy_mode,
            local_opt_in=user.local_processing_opt_in,
            unlocked_by_person=unlocked,
        )
        return route, label, cleared

    def adapter_for_safe(
        self,
        db: Session,
        route: Route,
        provider: AiProvider,
        usage: AiUsage,
        month: str,
        reserved: int,
    ) -> Adapter:
        try:
            return self.adapter_for(provider)
        except AiError as exc:
            self._fail(db, route, usage, month, reserved, provider, exc.code, count=False)
            raise

    def _refuse(
        self,
        db: Session,
        caller: AiCaller,
        route: Route,
        provider: AiProvider,
        purpose: str,
        reason: str,
    ) -> AiRefused:
        db.add(
            AiUsage(
                at=clock.utcnow(),
                month=budget.month_key(),
                scope_key=route.scope_key,
                actor_user_id=caller.user_id,
                provider_id=provider.id,
                provider_kind=provider.kind,
                model=provider.model,
                purpose=purpose[:32],
                status="refused",
                reason=reason,
            )
        )
        db.commit()
        return AiRefused(reason)

    def _fail(
        self,
        db: Session,
        route: Route,
        usage: AiUsage,
        month: str,
        reserved: int,
        provider: AiProvider,
        code: str,
        *,
        refused: bool = False,
        count: bool = True,
    ) -> None:
        if reserved and budget.claim(db, usage.id):
            budget.release(db, route.scope_key, month, reserved)
        usage.status = "refused" if refused else "failed"
        usage.reason = code.lower()[:40]
        db.commit()
        if count and not refused:
            self.breaker.failed(provider.id)


def _json_object(text: str) -> object:
    """Models sometimes wrap JSON in a code fence; nothing else is tolerated."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.rsplit("```", 1)[0]
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value
