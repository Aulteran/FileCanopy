"""AI configuration, consent, caps and usage (M15). No route here reads a document.

Administrators manage the server's models (several, one of them the default) and the server-wide
AI switch. A person manages only their own provider (a
cloud vendor; local endpoints are administrator-only, design D3). No response ever carries an API
key, and an administrator never sees whose provider or budget a person has.
"""

import uuid
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai import budget
from app.ai.gateway import AiCaller, AiError, AiGateway
from app.ai.providers import (
    CLOUD_KINDS,
    KINDS,
    EndpointError,
    mode_of,
    validate_endpoint,
    validate_model,
)
from app.ai.routing import (
    AI_ENABLED_KEY,
    CONSENT_VERSION,
    INSTANCE_PROVIDER_KEY,
    MAX_INSTANCE_MODELS,
    PREFERENCES,
    AiRefused,
    default_instance_model,
    instance_local_provider,
    instance_models,
    instance_provider,
    instance_signature,
    resolve_route,
    server_ai_enabled,
)
from app.ai.secrets import SecretBox, SecretsError, aad_for
from app.auth import clock
from app.auth.deps import (
    Principal,
    client_ip,
    ctx_of,
    current_principal,
    get_db,
    http_error,
    require_admin,
)
from app.auth.schemas import Ok
from app.auth.service import audit
from app.db.models import AiProvider, AiUsage, AppSetting, User

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])

NOTICE = (
    "Cloud AI sends the text of eligible files to the provider you choose, over the internet, "
    "and the provider may keep it under its own terms. Files in Private and Confidential folders "
    "are never sent to a cloud provider. Use an official API key; a chat subscription does not "
    "include API access. Requests are billed by the provider."
)


def gateway_of(request: Request) -> AiGateway:
    gateway: AiGateway = request.app.state.ai
    return gateway


def box_of(request: Request) -> SecretBox:
    box: SecretBox = request.app.state.ai_box
    return box


class ProviderOut(BaseModel):
    id: uuid.UUID
    label: str = ""
    is_default: bool = False
    kind: str
    mode: str
    model: str
    endpoint: str
    enabled: bool
    key_set: bool
    price_in_micro: int
    price_out_micro: int
    consent_ok: bool


class ProviderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["anthropic", "openai", "gemini", "ollama", "openai_compatible"]
    model: str = Field(min_length=1, max_length=120)
    label: str = Field(default="", max_length=80)
    endpoint: str = Field(default="", max_length=300)
    # Omitted = keep the stored key; "" = remove it.
    api_key: str | None = Field(default=None, max_length=512)
    price_in_micro: int = Field(default=0, ge=0, le=10**9)
    price_out_micro: int = Field(default=0, ge=0, le=10**9)
    enabled: bool = False
    acknowledge_cloud: bool = False


class CapBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cap_micro: int = Field(ge=0)


class PreferenceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preference: Literal["off", "instance_default", "instance_default_local_only", "my_provider"]
    # Required when the server's AI is a cloud service and the person picks "instance_default"
    # (or re-confirms after the administrator changed the provider).
    acknowledge_instance: bool = False


class EnabledBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class DefaultBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID


class TestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_billed: bool = False


def _out(p: AiProvider, default_id: uuid.UUID | None = None) -> ProviderOut:
    return ProviderOut(
        id=p.id,
        label=p.label,
        is_default=p.id == default_id,
        kind=p.kind,
        mode=p.privacy_mode,
        model=p.model,
        endpoint=p.endpoint,
        enabled=p.enabled,
        key_set=p.secret_ciphertext is not None,
        price_in_micro=p.price_in_micro,
        price_out_micro=p.price_out_micro,
        consent_ok=p.privacy_mode == "local" or p.consent_version == CONSENT_VERSION,
    )


def _commit_provider(db: Session, *, flush: bool = False) -> None:
    """Two simultaneous saves of the one provider of a scope: the second gets a clean conflict."""
    try:
        db.flush() if flush else db.commit()
    except IntegrityError:
        db.rollback()
        raise http_error(409, "CONFLICT") from None


def _usage(db: Session, scope_key: str, default_cap: int) -> dict[str, Any]:
    summary = budget.ensure(db, scope_key, default_cap)
    status_counts = dict(
        db.execute(
            select(AiUsage.status, func.count())
            .where(AiUsage.scope_key == scope_key, AiUsage.month == summary.month)
            .group_by(AiUsage.status)
        ).all()
    )
    return {
        "month": summary.month,
        "cap_micro": summary.cap_micro,
        "spent_micro": summary.spent_micro,
        "reserved_micro": summary.reserved_micro,
        "requests": status_counts,
    }


def _instance(db: Session) -> AiProvider | None:
    """The server's default model, whether or not it is switched on."""
    return default_instance_model(db)


def _instance_model(db: Session, model_id: uuid.UUID) -> AiProvider:
    p = db.get(AiProvider, model_id)
    if p is None or p.scope != "instance":
        raise http_error(404, "NO_PROVIDER")
    return p


def _set_default(db: Session, provider: AiProvider | None) -> None:
    setting = db.get(AppSetting, INSTANCE_PROVIDER_KEY)
    if provider is None:
        if setting is not None:
            db.delete(setting)
    elif setting is None:
        db.add(AppSetting(key=INSTANCE_PROVIDER_KEY, value=str(provider.id)))
    else:
        setting.value = str(provider.id)


def _save_instance(
    request: Request,
    db: Session,
    principal: Principal,
    existing: AiProvider | None,
    body: ProviderBody,
) -> AiProvider:
    """Create or update one server model; the first one becomes the default."""
    if existing is None and len(instance_models(db)) >= MAX_INSTANCE_MODELS:
        raise http_error(409, "TOO_MANY_MODELS")
    first = existing is None and default_instance_model(db) is None
    provider = _save(request, db, principal, existing, body, scope="instance")
    _commit_provider(db, flush=True)
    if first:  # adding a model when none is marked default makes it the default
        _set_default(db, provider)
    if db.get(AppSetting, AI_ENABLED_KEY) is None:  # the first model switches AI on, once
        db.add(AppSetting(key=AI_ENABLED_KEY, value=True))
    audit(
        db,
        "ai.provider_set",
        actor=principal.user.id,
        ip=client_ip(request),
        scope="instance",
        kind=provider.kind,
        enabled=provider.enabled,
    )
    _commit_provider(db)
    return provider


def _delete_instance(request: Request, db: Session, principal: Principal, p: AiProvider) -> None:
    was_default = default_instance_model(db)
    db.delete(p)
    db.flush()
    if was_default is not None and was_default.id == p.id:
        # Promote only an enabled model of the same kind of provider (local stays local, cloud
        # stays cloud); otherwise leave no default and let the administrator choose.
        heir = next(
            (m for m in instance_models(db) if m.enabled and m.privacy_mode == p.privacy_mode),
            None,
        )
        _set_default(db, heir)
    audit(
        db,
        "ai.provider_removed",
        actor=principal.user.id,
        ip=client_ip(request),
        scope="instance",
    )
    db.commit()


def _mine(db: Session, user: User) -> AiProvider | None:
    return db.scalar(
        select(AiProvider).where(AiProvider.scope == "user", AiProvider.owner_user_id == user.id)
    )


def _save(
    request: Request,
    db: Session,
    principal: Principal,
    existing: AiProvider | None,
    body: ProviderBody,
    *,
    scope: str,
) -> AiProvider:
    """Create or update the one provider of a scope. Validation failures change nothing."""
    box = box_of(request)
    mode = mode_of(body.kind)
    if scope == "user" and mode != "cloud":
        raise http_error(403, "LOCAL_ENDPOINT_ADMIN_ONLY")
    gw = gateway_of(request)
    try:
        endpoint = validate_endpoint(body.kind, body.endpoint, gw.resolver, gw.allow_hosts)
        model = validate_model(body.model)
    except EndpointError:
        raise http_error(422, "ENDPOINT_NOT_LOCAL") from None
    except ValueError:
        raise http_error(422, "INVALID_PROVIDER") from None
    if mode == "cloud" and not (body.price_in_micro or body.price_out_micro):
        raise http_error(422, "PRICES_REQUIRED")
    if existing is not None and existing.kind != body.kind:
        existing.secret_ciphertext = None  # a key never moves to another vendor
        existing.consent_version = existing.consent_at = existing.consent_by = None
    provider = existing or AiProvider(
        id=uuid.uuid4(),
        scope=scope,
        owner_user_id=principal.user.id if scope == "user" else None,
        created_at=clock.utcnow(),
    )
    provider.kind, provider.privacy_mode, provider.model = body.kind, mode, model
    provider.label = body.label.strip()
    provider.endpoint = endpoint
    provider.price_in_micro, provider.price_out_micro = body.price_in_micro, body.price_out_micro
    if body.api_key is not None:
        if body.api_key == "":
            provider.secret_ciphertext = None
        else:
            try:
                provider.secret_ciphertext = box.seal(
                    body.api_key.strip(), aad_for(provider.id, provider.owner_user_id)
                )
            except SecretsError:
                raise http_error(503, "SECRETS_KEY_NOT_CONFIGURED") from None
    if mode == "cloud" and body.enabled:
        if provider.secret_ciphertext is None:
            raise http_error(422, "API_KEY_REQUIRED")
        if provider.consent_version != CONSENT_VERSION:
            if not body.acknowledge_cloud:
                raise http_error(409, "CONSENT_REQUIRED")
            provider.consent_version = CONSENT_VERSION
            provider.consent_at = clock.utcnow()
            provider.consent_by = principal.user.id
    provider.enabled = body.enabled
    db.add(provider)
    return provider


def _model_name(p: AiProvider | None) -> str | None:
    if p is None:
        return None
    return p.label or p.model


@router.get("/status")
def status(
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    cfg = ctx_of(request).settings
    user = principal.user
    inst = instance_provider(db)  # None when the server's AI is off or the default is off
    local = instance_local_provider(db)
    mine = _mine(db, user)
    shared = resolve_route(db, root_kind="shared", owner=None)
    personal = resolve_route(db, root_kind="personal", owner=user)
    return {
        "secrets_key_configured": box_of(request).configured,
        "notice": NOTICE,
        "consent_version": CONSENT_VERSION,
        "preference": user.ai_preference,
        "preferences": list(PREFERENCES),
        "instance_available": shared.provider is not None,
        # What choosing the server's AI would mean: where it runs, so a person can judge it.
        "instance_kind": inst.kind if inst else None,
        "instance_host": (urlparse(inst.endpoint).netloc if inst else None) or None,
        "instance_needs_ack": personal.reason == "instance_consent_required",
        # Only the facts a person needs: not the endpoint or key state of the instance provider.
        "instance_mode": inst.privacy_mode if inst else None,
        "server_ai_enabled": server_ai_enabled(db),
        # The names a person sees when choosing; never an address or key state.
        "default_model": _model_name(inst),
        "local_model": _model_name(local),
        "local_available": local is not None,
        "shared_tree_ai": shared.provider is not None,
        "personal_tree_ai": personal.provider is not None,
        "personal_tree_reason": personal.reason,
        "my_provider": _out(mine) if mine else None,
        "my_usage": _usage(db, f"user:{user.id}", cfg.ai_default_cap_micro) if mine else None,
        "cloud_kinds": sorted(CLOUD_KINDS),
        "kinds": list(KINDS),
    }


@router.put("/me/preference", response_model=Ok)
def set_preference(
    body: PreferenceBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> Ok:
    user = principal.user
    if body.preference != "off" and not server_ai_enabled(db):
        raise http_error(409, "AI_NOT_ENABLED")
    if body.preference == "instance_default":
        shared = instance_provider(db)
        if shared is not None and shared.privacy_mode == "cloud":
            if not body.acknowledge_instance:
                raise http_error(409, "CONSENT_REQUIRED")
            user.ai_ack = instance_signature(shared)
    user.ai_preference = body.preference
    audit(db, "ai.preference", actor=user.id, ip=client_ip(request), value=body.preference)
    db.commit()
    return Ok()


@router.put("/me/provider", response_model=ProviderOut)
def put_my_provider(
    body: ProviderBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> ProviderOut:
    user = principal.user
    if body.enabled and not server_ai_enabled(db):
        raise http_error(409, "AI_NOT_ENABLED")
    provider = _save(request, db, principal, _mine(db, user), body, scope="user")
    audit(
        db,
        "ai.provider_set",
        actor=user.id,
        ip=client_ip(request),
        scope="user",
        kind=provider.kind,
        enabled=provider.enabled,
    )
    _commit_provider(db)
    return _out(provider)


@router.delete("/me/provider", response_model=Ok)
def delete_my_provider(
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> Ok:
    user = principal.user
    if (p := _mine(db, user)) is not None:
        db.delete(p)
        audit(db, "ai.provider_removed", actor=user.id, ip=client_ip(request), scope="user")
        db.commit()
    return Ok()


@router.put("/me/cap")
def set_my_cap(
    body: CapBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    cfg = ctx_of(request).settings
    scope = f"user:{principal.user.id}"
    try:
        budget.set_cap(db, scope, cfg.ai_default_cap_micro, body.cap_micro)
    except ValueError:
        raise http_error(422, "INVALID_CAP") from None
    audit(db, "ai.cap_set", actor=principal.user.id, ip=client_ip(request), scope="user")
    db.commit()
    return _usage(db, scope, cfg.ai_default_cap_micro)


@router.post("/me/provider/test", response_model=Ok)
def test_my_provider(
    body: TestBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> Ok:
    user = principal.user
    return _probe(request, principal, _mine(db, user), body)


@router.get("/instance")
def get_instance(
    request: Request,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    cfg = ctx_of(request).settings
    default = default_instance_model(db)
    default_id = default.id if default else None
    return {
        "secrets_key_configured": box_of(request).configured,
        "notice": NOTICE,
        "consent_version": CONSENT_VERSION,
        "enabled": server_ai_enabled(db),
        "needs_default": default is None and bool(instance_models(db)),
        "models": [_out(m, default_id) for m in instance_models(db)],
        "max_models": MAX_INSTANCE_MODELS,
        # The default model, for single-model callers.
        "provider": _out(default, default_id) if default else None,
        "usage": _usage(db, "instance", cfg.ai_default_cap_micro),
        "kinds": list(KINDS),
    }


@router.put("/instance/enabled", response_model=Ok)
def set_server_enabled(
    body: EnabledBody,
    request: Request,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Ok:
    setting = db.get(AppSetting, AI_ENABLED_KEY)
    if setting is None:
        db.add(AppSetting(key=AI_ENABLED_KEY, value=body.enabled))
    else:
        setting.value = body.enabled
    audit(
        db,
        "ai.server_enabled",
        actor=principal.user.id,
        ip=client_ip(request),
        value=body.enabled,
    )
    db.commit()
    return Ok()


@router.post("/instance/models", response_model=ProviderOut)
def add_instance_model(
    body: ProviderBody,
    request: Request,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ProviderOut:
    p = _save_instance(request, db, principal, None, body)
    d = default_instance_model(db)
    return _out(p, d.id if d else None)


@router.put("/instance/models/{model_id}", response_model=ProviderOut)
def put_instance_model(
    model_id: uuid.UUID,
    body: ProviderBody,
    request: Request,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ProviderOut:
    p = _save_instance(request, db, principal, _instance_model(db, model_id), body)
    d = default_instance_model(db)
    return _out(p, d.id if d else None)


@router.delete("/instance/models/{model_id}", response_model=Ok)
def delete_instance_model(
    model_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Ok:
    _delete_instance(request, db, principal, _instance_model(db, model_id))
    return Ok()


@router.put("/instance/default", response_model=Ok)
def set_default_model(
    body: DefaultBody,
    request: Request,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Ok:
    _set_default(db, _instance_model(db, body.id))
    audit(db, "ai.default_model", actor=principal.user.id, ip=client_ip(request))
    db.commit()
    return Ok()


@router.post("/instance/models/{model_id}/test", response_model=Ok)
def test_instance_model(
    model_id: uuid.UUID,
    body: TestBody,
    request: Request,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Ok:
    return _probe(request, principal, _instance_model(db, model_id), body)


# Deprecated single-model shortcuts (use /instance/models): act on the default model.
@router.put("/instance/provider", response_model=ProviderOut, deprecated=True)
def put_instance_provider(
    body: ProviderBody,
    request: Request,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ProviderOut:
    provider = _save_instance(request, db, principal, _instance(db), body)
    return _out(provider, provider.id)


@router.delete("/instance/provider", response_model=Ok, deprecated=True)
def delete_instance_provider(
    request: Request,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Ok:
    if (p := _instance(db)) is not None:
        _delete_instance(request, db, principal, p)
    return Ok()


@router.put("/instance/cap")
def set_instance_cap(
    body: CapBody,
    request: Request,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    cfg = ctx_of(request).settings
    try:
        budget.set_cap(db, "instance", cfg.ai_default_cap_micro, body.cap_micro)
    except ValueError:
        raise http_error(422, "INVALID_CAP") from None
    audit(db, "ai.cap_set", actor=principal.user.id, ip=client_ip(request), scope="instance")
    db.commit()
    return _usage(db, "instance", cfg.ai_default_cap_micro)


@router.post("/instance/provider/test", response_model=Ok)
def test_instance_provider(
    body: TestBody,
    request: Request,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Ok:
    return _probe(request, principal, _instance(db), body)


def _probe(
    request: Request, principal: Principal, provider: AiProvider | None, body: TestBody
) -> Ok:
    if provider is None:
        raise http_error(404, "NO_PROVIDER")
    if provider.privacy_mode == "cloud" and not body.confirm_billed:
        raise http_error(409, "BILLING_CONFIRMATION_REQUIRED")
    try:
        gateway_of(request).probe(AiCaller(principal.user.id, principal.session.id), provider)
    except AiRefused as exc:
        raise http_error(409, exc.reason.upper()) from None
    except AiError as exc:
        raise http_error(502, f"PROVIDER_{exc.code}") from None
    return Ok()
