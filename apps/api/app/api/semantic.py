"""Administration of optional local semantic search (M19).

An administrator saves a local embedding endpoint, verifies it (health and dimension), and only
then can switch semantic search on. Verifying or editing never turns anything on by itself, and
the administrator role gives no access to anyone's files: the numbers here are totals only.
"""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.providers import EndpointError
from app.auth.deps import Principal, client_ip, get_db, http_error, require_admin
from app.auth.service import audit
from app.db.models import SearchChunk, SearchDoc, SearchEmbed
from app.search import embed, semantic

router = APIRouter(prefix="/api/v1/admin/semantic", tags=["admin"])


class ConfigBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["openai_compatible", "ollama"]
    endpoint: str = Field(min_length=1, max_length=300)
    model: str = Field(min_length=1, max_length=120)
    # Some embedding models want a task prefix ("search_query: " ...). Empty is fine.
    doc_prefix: str = Field(default="", max_length=embed.MAX_PREFIX)
    query_prefix: str = Field(default="", max_length=embed.MAX_PREFIX)


class EnabledBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def sem_of(request: Request) -> semantic.Semantic:
    sem: semantic.Semantic = request.app.state.semantic
    return sem


def _view(db: Session, sem: semantic.Semantic) -> dict[str, Any]:
    cfg = semantic.load_config(db)
    gen = cfg.generation
    chunks = db.scalar(select(func.count()).select_from(SearchChunk)) or 0
    done = (
        db.scalar(
            select(func.count())
            .select_from(SearchEmbed)
            .where(SearchEmbed.state == "done", SearchEmbed.generation == gen)
        )
        or 0
    )
    waiting = (
        db.scalar(
            select(func.count())
            .select_from(SearchEmbed)
            .where(SearchEmbed.state.in_(("queued", "running")))
        )
        or 0
    )
    texts = db.scalar(select(func.count()).select_from(SearchDoc)) or 0
    return {
        "state": semantic.inactive_reason(db, sem.settings),
        "active": semantic.is_active(db, sem.settings),
        "pgvector": semantic.pgvector_ready(db),
        "kinds": list(embed.KINDS),
        "kind": cfg.kind,
        "endpoint": cfg.endpoint,
        "model": cfg.model,
        "doc_prefix": cfg.doc_prefix,
        "query_prefix": cfg.query_prefix,
        "dimension": cfg.dim or None,
        "generation": cfg.generation,
        "enabled": cfg.enabled,
        "healthy": cfg.healthy,
        "error": cfg.error,
        "checked_at": cfg.checked_at,
        "edited_since_verify": bool(cfg.spec and cfg.spec.signature != cfg.verified),
        # Totals across the whole instance: file and chunk counts, never names or content.
        "totals": {"texts": texts, "embedded_files": done, "chunks": chunks, "waiting": waiting},
    }


@router.get("")
def get_config(
    request: Request,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _view(db, sem_of(request))


@router.put("")
def put_config(
    body: ConfigBody,
    request: Request,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Saves the endpoint. Any change makes it unverified and switches search off until it is
    verified again and the administrator turns it back on."""
    sem = sem_of(request)
    try:
        spec = embed.validate_spec(
            body.kind, body.endpoint, body.model, sem.resolver, sem.allow_hosts
        )
    except EndpointError:
        raise http_error(422, "ENDPOINT_NOT_LOCAL") from None
    except ValueError:
        raise http_error(422, "INVALID_ENDPOINT") from None
    cfg = semantic.load_config(db)
    changed = (cfg.spec.signature if cfg.spec else "") != spec.signature
    cfg.kind, cfg.endpoint, cfg.model = spec.kind, spec.endpoint, spec.model
    prefixes_changed = (cfg.doc_prefix, cfg.query_prefix) != (body.doc_prefix, body.query_prefix)
    cfg.doc_prefix, cfg.query_prefix = body.doc_prefix, body.query_prefix
    if changed or prefixes_changed:
        cfg.enabled, cfg.healthy = False, False
        cfg.error = None
        if prefixes_changed and not changed:
            cfg.verified = (
                ""  # prefixes change the vectors: verifying again starts a new generation
            )
    semantic.save_config(db, cfg)
    audit(
        db, "search.semantic_config", actor=principal.user.id, ip=client_ip(request), kind=spec.kind
    )
    db.commit()
    return _view(db, sem)


@router.post("/verify")
def verify(
    request: Request,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """The health and dimension check. A new model or dimension starts a new generation."""
    sem = sem_of(request)
    if not semantic.pgvector_ready(db):
        raise http_error(409, "PGVECTOR_MISSING")
    try:
        sem.verify(db)
    except embed.EmbedError as exc:
        audit(
            db, "search.semantic_verify", actor=principal.user.id, ip=client_ip(request), ok=False
        )
        db.commit()
        raise http_error(502, f"EMBEDDING_{exc.code}") from None
    audit(db, "search.semantic_verify", actor=principal.user.id, ip=client_ip(request), ok=True)
    db.commit()
    request.app.state.semantic_indexer.kick()
    return _view(db, sem)


@router.put("/enabled")
def set_enabled(
    body: EnabledBody,
    request: Request,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    sem = sem_of(request)
    cfg = semantic.load_config(db)
    if body.enabled and not (cfg.healthy and cfg.dim and semantic.pgvector_ready(db)):
        raise http_error(409, "NOT_HEALTHY")
    cfg.enabled = body.enabled
    semantic.save_config(db, cfg)
    audit(
        db,
        "search.semantic_enabled",
        actor=principal.user.id,
        ip=client_ip(request),
        value=body.enabled,
    )
    db.commit()
    if body.enabled:
        request.app.state.semantic_indexer.kick()
    return _view(db, sem)
