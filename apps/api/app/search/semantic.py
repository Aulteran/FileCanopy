"""Optional local semantic search (M19): configuration, background embedding, ACL-aware queries.

Everything here is derived from `search_doc` (the M11 text), so what is eligible for embedding is
decided by the same rules: Normal files, Private files only with the local-processing opt-in,
Confidential never. Two more things apply: a person can switch semantic indexing off for their own
files, and nothing runs unless an administrator has verified a local endpoint and switched search
on (`active`). Vectors are sensitive derived data. They are read only by `semantic_hits`, which
narrows candidates in SQL and then lets the policy engine decide every node for this person *now*;
text for a snippet is cut from `search_doc.body` after that decision, and neither a hidden node nor
a count of hidden nodes is ever reported.
"""

import logging
import math
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import Float, bindparam, delete, func, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.ai.providers import Resolver, system_resolver
from app.ai.routing import AiRefused, ClearedInput, clear
from app.auth import clock
from app.config import Settings
from app.db.models import (
    HIDDEN_NODE_STATUSES,
    AppSetting,
    Embedding,
    Node,
    SearchChunk,
    SearchDoc,
    SearchEmbed,
    StorageRoot,
    User,
)
from app.policy import engine, privacy
from app.policy.model import Actor, Privilege
from app.preview.sandbox import PreviewError
from app.preview.service import require_processing_allowed
from app.search import embed
from app.search.indexer import identity_for
from app.search.query import SCAN_CAP, Hit, finish_hits, may_show_text, scope_filter

log = logging.getLogger("filecanopy.semantic")

CONFIG_KEY = "semantic.config"
DEFERRED = timedelta(hours=1)  # not eligible now (no opt-in, locked, opted out): look again later
RRF_K = 60  # reciprocal-rank fusion constant
SNIPPET_CHARS = 300
TRANSIENT = {"OUTAGE", "TIMEOUT", "BAD_RESPONSE", "TOO_LARGE"}


def now() -> datetime:
    return clock.utcnow()


# --------------------------------------------------------------------------- configuration


@dataclass
class SemanticConfig:
    """Stored as one JSON row. `enabled` is the administrator's switch, `healthy` the machine's
    verdict; search is on only when both are true (and pgvector exists)."""

    enabled: bool = False
    kind: str = ""
    endpoint: str = ""
    model: str = ""
    doc_prefix: str = ""
    query_prefix: str = ""
    dim: int = 0
    generation: int = 0
    healthy: bool = False
    error: str | None = None
    checked_at: str | None = None
    verified: str = ""  # signature (kind|endpoint|model) the vectors were made with
    # Normalised probe vectors from verification: the model fingerprint (M19 review M2).
    reference: list[list[float]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def spec(self) -> embed.EndpointSpec | None:
        if not (self.kind and self.endpoint and self.model):
            return None
        return embed.EndpointSpec(self.kind, self.endpoint, self.model)


def load_config(db: Session) -> SemanticConfig:
    row = db.get(AppSetting, CONFIG_KEY)
    if row is None or not isinstance(row.value, dict):
        return SemanticConfig()
    known = {k: v for k, v in row.value.items() if k in SemanticConfig.__dataclass_fields__}
    try:
        return SemanticConfig(**known)
    except TypeError:
        return SemanticConfig()  # unreadable config = off


def save_config(db: Session, cfg: SemanticConfig) -> None:
    value = asdict(cfg)
    row = db.get(AppSetting, CONFIG_KEY)
    if row is None:
        db.add(AppSetting(key=CONFIG_KEY, value=value))
    else:
        row.value = value
    db.flush()


def pgvector_ready(db: Session) -> bool:
    """True when vectors can be stored and searched. SQLite (unit tests, development) always can;
    PostgreSQL needs the `vector` extension and the column created as that type (migration 0022)."""
    if db.get_bind().dialect.name != "postgresql":
        return True
    udt = db.scalar(
        text(
            "SELECT udt_name FROM information_schema.columns "
            "WHERE table_name = 'search_chunk' AND column_name = 'embedding'"
        )
    )
    return bool(udt == "vector")


def is_active(db: Session, settings: Settings) -> bool:
    """The one test every semantic code path starts with."""
    cfg = load_config(db)
    return bool(
        settings.search_enabled and cfg.enabled and cfg.healthy and cfg.dim and pgvector_ready(db)
    )


def inactive_reason(db: Session, settings: Settings) -> str:
    cfg = load_config(db)
    if not settings.search_enabled:
        return "search_disabled"
    if cfg.spec is None:
        return "not_configured"
    if not pgvector_ready(db):
        return "pgvector_missing"
    if not cfg.healthy:
        return "unhealthy" if cfg.verified else "not_verified"
    if not cfg.enabled:
        return "off"
    return "active"


# --------------------------------------------------------------------------- chunking


def chunk_text(body: str, size: int, overlap: int, max_chunks: int) -> list[tuple[int, int]]:
    """Character ranges of overlapping chunks that break at whitespace where possible."""
    n = len(body)
    out: list[tuple[int, int]] = []
    pos = 0
    while pos < n and len(out) < max_chunks:
        end = min(n, pos + size)
        if end < n:
            cut = body.rfind(" ", pos + size // 2, end)
            if cut > pos:
                end = cut
        if body[pos:end].strip():
            out.append((pos, end))
        if end >= n:
            break
        pos = max(end - overlap, pos + 1)
        while pos < n and body[pos] == " ":
            pos += 1
    return out


# --------------------------------------------------------------------------- the service


class Skip(Exception):
    """A file that must not be embedded right now. `code` is stored; nothing else is."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Semantic:
    """Builds clients for the saved endpoint and runs verification. One per application."""

    def __init__(
        self,
        settings: Settings,
        sessions: "sessionmaker[Session]",
        *,
        resolver: Resolver = system_resolver,
        allow_hosts: frozenset[str] = frozenset(),
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.resolver = resolver
        self.allow_hosts = allow_hosts
        self.transport = transport
        self._lock = threading.Lock()
        self._recent: dict[uuid.UUID, list[float]] = {}
        self._slots = threading.BoundedSemaphore(settings.semantic_query_concurrency)

    def client(
        self, spec: embed.EndpointSpec, timeout: float | None = None
    ) -> embed.EmbeddingClient:
        return embed.EmbeddingClient(
            spec,
            timeout=timeout or self.settings.semantic_timeout_seconds,
            max_response_bytes=self.settings.semantic_max_response_bytes,
            transport=self.transport,
            resolver=self.resolver,
            allow_hosts=self.allow_hosts,
        )

    @staticmethod
    def probe_input(text_: str) -> ClearedInput:
        """Synthetic, label Normal, local mode: the same gate as any other AI input."""
        return clear(
            text_,
            label="normal",
            provider_mode="local",
            local_opt_in=False,
            unlocked_by_person=False,
        )

    def verify(self, db: Session) -> SemanticConfig:
        """Runs the health and dimension check against the saved endpoint and records the result.

        A change of endpoint, model or dimension starts a new generation: the previous vectors are
        deleted here (a different model's vectors are not comparable) and the indexer rebuilds
        them in the background. The administrator's switch is never turned on by this call."""
        cfg = load_config(db)
        spec = cfg.spec
        if spec is None:
            raise embed.EmbedError("NOT_CONFIGURED")
        client = self.client(spec)
        try:
            dim, probes = embed.verify(client, self.probe_input)
        except embed.EmbedError as exc:
            cfg.healthy, cfg.error, cfg.checked_at = False, exc.code, now().isoformat()
            save_config(db, cfg)
            db.commit()
            raise
        finally:
            client.close()
        changed_model = bool(cfg.reference) and not embed.matches_reference(probes, cfg.reference)
        if cfg.verified != spec.signature or cfg.dim != dim or changed_model:
            cfg.generation += 1
            db.execute(delete(SearchChunk))
            db.execute(delete(SearchEmbed))
            cfg.verified, cfg.dim = spec.signature, dim
            cfg.reference = probes
        if not cfg.reference:  # a config verified before the fingerprint existed
            cfg.reference = probes
        cfg.healthy, cfg.error, cfg.checked_at = True, None, now().isoformat()
        save_config(db, cfg)
        db.commit()
        return cfg

    def recheck(self, db: Session) -> None:
        """Periodic health. An endpoint that fails, or answers with another dimension, turns the
        feature off (full-text search is unaffected). It recovers by itself only if it is again
        the very model and dimension the vectors were made with."""
        cfg = load_config(db)
        spec = cfg.spec
        if spec is None or not cfg.verified:
            return
        if spec.signature != cfg.verified:
            return  # edited and not yet verified: stays off until the administrator verifies
        client = self.client(spec)
        try:
            dim, probes = embed.verify(client, self.probe_input)
            if dim != cfg.dim:
                ok, error = False, "DIMENSION_CHANGED"
            elif cfg.reference and not embed.matches_reference(probes, cfg.reference):
                ok, error = False, "MODEL_CHANGED"  # same address, name and size, other model
            else:
                ok, error = True, None
        except embed.EmbedError as exc:
            ok, error = False, exc.code
        finally:
            client.close()
        if cfg.healthy != ok or cfg.error != error:
            cfg.healthy, cfg.error = ok, error
        cfg.checked_at = now().isoformat()
        save_config(db, cfg)
        db.commit()

    def mark_unhealthy(self, db: Session, code: str) -> None:
        cfg = load_config(db)
        cfg.healthy, cfg.error, cfg.checked_at = False, code, now().isoformat()
        save_config(db, cfg)
        db.commit()

    def allow_query(self, user_id: uuid.UUID) -> bool:
        """Per-person sliding-window limit on queries that call the embedding server."""
        limit = self.settings.semantic_query_per_minute
        t = time.monotonic()
        with self._lock:
            recent = [x for x in self._recent.get(user_id, []) if t - x < 60]
            if len(recent) >= limit:
                self._recent[user_id] = recent
                return False
            recent.append(t)
            self._recent[user_id] = recent
            if len(self._recent) > 10_000:  # forget people who have been quiet
                self._recent = {k: v for k, v in self._recent.items() if v and t - v[-1] < 60}
        return True

    def query_vector(self, q: str) -> list[float] | None:
        """Embeds a query. Uses its own short-lived session (the caller's is not held during the
        network call), a short timeout and a global cap on concurrent calls."""
        with self.sessions() as db:
            cfg = load_config(db)
        spec = cfg.spec
        if spec is None or not self._slots.acquire(blocking=False):
            return None
        client = self.client(spec, self.settings.semantic_query_timeout_seconds)
        try:
            vec = client.embed([self.probe_input(cfg.query_prefix + q)])[0]
        except (embed.EmbedError, AiRefused):
            return None
        finally:
            client.close()
            self._slots.release()
        return vec if len(vec) == cfg.dim else None


# --------------------------------------------------------------------------- eligibility


def eligible(db: Session, node: Node) -> tuple[str, uuid.UUID]:
    """(label, indexing identity) or `Skip`. Mirrors the M11 rules and adds the person's own
    "no semantic indexing" switch. Confidential content is never embedded."""
    label = privacy.effective_label(db, node)
    if label == "confidential":
        raise Skip("CONFIDENTIAL")
    root = db.get(StorageRoot, node.root_id)
    if root is None:
        raise Skip("NO_IDENTITY")
    if root.kind == "personal":
        owner = db.get(User, root.owner_user_id) if root.owner_user_id else None
        if owner is None or owner.semantic_opt_out:
            raise Skip("OPTED_OUT")
    actor = identity_for(db, root)
    if actor is None:
        raise Skip("NO_IDENTITY")
    if label != "normal":
        try:
            require_processing_allowed(db, node, actor)
        except PreviewError:
            raise Skip("LOCAL_PROCESSING_OPT_IN_REQUIRED") from None
    return label, actor


def _version(node: Node) -> tuple[int, int, int]:
    return node.revision, node.mtime_ns or 0, node.size or 0


# --------------------------------------------------------------------------- background indexer


class SemanticIndexer:
    def __init__(self, semantic: Semantic) -> None:
        self.semantic = semantic
        self.settings = semantic.settings
        self.sessions = semantic.sessions
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- scanning
    def scan_once(self, db: Session) -> int:
        cfg = load_config(db)
        if not is_active(db, self.settings):
            return 0
        t = now()
        stale = or_(
            SearchEmbed.node_id.is_(None),
            SearchEmbed.generation != cfg.generation,
            SearchEmbed.revision != Node.revision,
            SearchEmbed.mtime_ns != func.coalesce(Node.mtime_ns, 0),
            SearchEmbed.size != func.coalesce(Node.size, 0),
            (SearchEmbed.state == "failed") & (SearchEmbed.run_after <= t),
        )
        rows = db.execute(
            select(Node, SearchEmbed)
            .join(SearchDoc, SearchDoc.node_id == Node.id)
            .outerjoin(SearchEmbed, SearchEmbed.node_id == Node.id)
            .where(
                Node.kind == "file",
                Node.status == "active",
                SearchDoc.revision == Node.revision,
                SearchDoc.mtime_ns == func.coalesce(Node.mtime_ns, 0),
                SearchDoc.size == func.coalesce(Node.size, 0),
                stale,
            )
            .order_by(Node.updated_at.desc())
            .limit(self.settings.semantic_batch)
        ).all()
        for node, job in rows:
            rev, mtime, size = _version(node)
            if job is None:
                db.add(
                    SearchEmbed(
                        node_id=node.id,
                        generation=cfg.generation,
                        revision=rev,
                        mtime_ns=mtime,
                        size=size,
                        state="queued",
                        attempts=0,
                        run_after=t,
                    )
                )
            else:
                job.generation, job.revision, job.mtime_ns, job.size = (
                    cfg.generation,
                    rev,
                    mtime,
                    size,
                )
                job.state, job.attempts, job.run_after = "queued", 0, t
                job.lease_until, job.error = None, None
        db.commit()
        return len(rows)

    # ---- claiming
    def claim(self, db: Session) -> uuid.UUID | None:
        cfg = load_config(db)
        t = now()
        while True:
            job = db.scalar(
                select(SearchEmbed)
                .where(
                    SearchEmbed.generation == cfg.generation,
                    or_(
                        (SearchEmbed.state == "queued") & (SearchEmbed.run_after <= t),
                        (SearchEmbed.state == "running") & (SearchEmbed.lease_until < t),
                    ),
                )
                .order_by(SearchEmbed.run_after)
                .limit(1)
            )
            if job is None:
                return None
            if job.attempts >= self.settings.semantic_max_attempts:
                job.state, job.error, job.run_after = "failed", "GAVE_UP", t + timedelta(days=1)
                job.lease_until = None
                db.commit()
                continue
            job.state = "running"
            job.attempts += 1
            job.lease_until = t + timedelta(seconds=self.settings.semantic_lease_seconds)
            db.commit()
            return job.node_id

    # ---- one job
    def run_job(self, node_id: uuid.UUID) -> str:
        """Embeds one file. Returns a short outcome word (tests and logs; never content)."""
        with self.sessions() as db:
            cfg = load_config(db)
            job = db.get(SearchEmbed, node_id)
            node = db.get(Node, node_id)
            doc = db.get(SearchDoc, node_id)
            spec = cfg.spec
            if job is None:
                return "gone"
            if not is_active(db, self.settings) or spec is None:
                job.state, job.lease_until = "queued", None  # picked up again once active
                job.attempts = max(0, job.attempts - 1)
                db.commit()
                return "inactive"
            if (
                node is None
                or node.kind != "file"
                or node.status != "active"
                or job.generation != cfg.generation
                or doc is None
                or _version(node) != (job.revision, job.mtime_ns, job.size)
                or (doc.revision, doc.mtime_ns, doc.size) != _version(node)
            ):
                db.execute(delete(SearchEmbed).where(SearchEmbed.node_id == node_id))
                db.commit()  # the scanner queues the new version if there is one
                return "stale"
            try:
                label, actor = eligible(db, node)
            except Skip as skip:
                return self._defer(db, job, node, skip.code)
            body = doc.body
            spans = chunk_text(
                body,
                self.settings.semantic_chunk_chars,
                self.settings.semantic_chunk_overlap,
                self.settings.semantic_max_chunks,
            )
            version = _version(node)
            root_id = node.root_id
            opt_in = bool(db.scalar(select(User.local_processing_opt_in).where(User.id == actor)))
            client = self.semantic.client(spec)
            vectors: list[list[float]] = []
            try:
                batch = self.settings.semantic_embed_batch
                for i in range(0, len(spans), batch):
                    group = [
                        clear(
                            cfg.doc_prefix + body[s:e],
                            label=label,
                            provider_mode="local",
                            local_opt_in=opt_in,
                            unlocked_by_person=False,  # a job never counts as unlocked
                        )
                        for s, e in spans[i : i + batch]
                    ]
                    vectors.extend(client.embed(group))
                    if self.settings.semantic_pause_ms:
                        time.sleep(self.settings.semantic_pause_ms / 1000)
            except AiRefused as refused:
                return self._defer(db, job, node, refused.reason.upper()[:32])
            except embed.EmbedError as exc:
                if exc.code == "DIMENSION_MISMATCH":
                    self.semantic.mark_unhealthy(db, "DIMENSION_CHANGED")
                return self._transient(db, job, exc.code)
            finally:
                client.close()
            if any(len(v) != cfg.dim for v in vectors):
                self.semantic.mark_unhealthy(db, "DIMENSION_CHANGED")
                return self._transient(db, job, "DIMENSION_MISMATCH")
            # The world may have moved while the endpoint was working: re-check before storing.
            db.expire_all()
            node, doc, job_now = db.get(Node, node_id), db.get(SearchDoc, node_id), None
            job_now = db.get(SearchEmbed, node_id)
            cfg_now = load_config(db)
            if (
                node is None
                or doc is None
                or job_now is None
                or node.status != "active"
                or _version(node) != version
                or (doc.revision, doc.mtime_ns, doc.size) != version
                or cfg_now.generation != cfg.generation
                or not is_active(db, self.settings)
            ):
                return "stale"
            try:
                eligible(db, node)
            except Skip as skip:
                return self._defer(db, job_now, node, skip.code)
            db.execute(delete(SearchChunk).where(SearchChunk.node_id == node_id))
            for ordinal, ((s, e), vec) in enumerate(zip(spans, vectors, strict=True)):
                db.add(
                    SearchChunk(
                        node_id=node_id,
                        root_id=root_id,
                        generation=cfg.generation,
                        ordinal=ordinal,
                        start=s,
                        end=e,
                        revision=version[0],
                        mtime_ns=version[1],
                        size=version[2],
                        embedding=vec,
                    )
                )
            job_now.state, job_now.chunks = "done", len(spans)
            job_now.lease_until, job_now.error = None, None
            db.commit()
            return "embedded"

    def _defer(self, db: Session, job: SearchEmbed, node: Node, code: str) -> str:
        db.execute(delete(SearchChunk).where(SearchChunk.node_id == node.id))
        job.state, job.chunks, job.error = "failed", 0, code[:32]
        job.lease_until, job.run_after = None, now() + DEFERRED
        db.commit()
        return f"skipped:{code}"

    def _transient(self, db: Session, job: SearchEmbed, code: str) -> str:
        job.lease_until, job.error = None, code[:32]
        if job.attempts >= self.settings.semantic_max_attempts:
            job.state, job.error, job.run_after = "failed", "GAVE_UP", now() + timedelta(days=1)
        else:
            job.state = "queued"
            job.run_after = now() + timedelta(seconds=min(30 * 2**job.attempts, 3600))
        db.commit()
        return f"failed:{code}"

    # ---- housekeeping
    def sweep(self, db: Session) -> int:
        """Deletes vectors that must not outlive their file, their text row, their model
        generation or the owner's consent."""
        cfg = load_config(db)
        removed = 0
        ids = list(
            db.execute(
                select(SearchChunk.node_id)
                .outerjoin(Node, Node.id == SearchChunk.node_id)
                .outerjoin(SearchDoc, SearchDoc.node_id == SearchChunk.node_id)
                .where(
                    or_(
                        Node.id.is_(None),
                        Node.status.in_(HIDDEN_NODE_STATUSES),
                        Node.kind != "file",
                        SearchDoc.node_id.is_(None),
                        SearchChunk.generation != cfg.generation,
                    )
                )
                .distinct()
            ).scalars()
        )
        if ids:
            db.execute(delete(SearchChunk).where(SearchChunk.node_id.in_(ids)))
            removed += len(ids)
        db.execute(
            delete(SearchEmbed).where(
                SearchEmbed.node_id.in_(
                    select(SearchEmbed.node_id)
                    .outerjoin(Node, Node.id == SearchEmbed.node_id)
                    .where(or_(Node.id.is_(None), Node.status.in_(HIDDEN_NODE_STATUSES)))
                )
            )
        )
        # Labels, opt-ins and the person's switch can change without the file changing.
        offset = 0
        while True:
            batch = list(
                db.execute(
                    select(SearchChunk.node_id)
                    .distinct()
                    .order_by(SearchChunk.node_id)
                    .offset(offset)
                    .limit(500)
                ).scalars()
            )
            if not batch:
                break
            offset += len(batch)
            drop: list[uuid.UUID] = []
            for nid in batch:
                node = db.get(Node, nid)
                if node is None:
                    continue
                try:
                    eligible(db, node)
                except Skip:
                    drop.append(nid)
            if drop:
                db.execute(delete(SearchChunk).where(SearchChunk.node_id.in_(drop)))
                db.execute(delete(SearchEmbed).where(SearchEmbed.node_id.in_(drop)))
                removed += len(drop)
                offset -= len(drop)
        db.commit()
        return removed

    # ---- thread
    def kick(self) -> None:
        self._wake.set()

    def start(self) -> None:
        if self._thread is not None or not self.settings.search_enabled:
            return
        self._thread = threading.Thread(target=self._loop, name="fc-semantic", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def tick(self) -> int:
        """One pass: scan, then work through due jobs. Returns the number of jobs run."""
        ran = 0
        with self.sessions() as db:
            if not is_active(db, self.settings):
                return 0
            self.scan_once(db)
        while not self._stop.is_set():
            with self.sessions() as db:
                node_id = self.claim(db)
            if node_id is None:
                break
            try:
                self.run_job(node_id)
            except Exception as exc:  # the lease runs out and the job is taken again
                log.warning("semantic job failed unexpectedly: %s", type(exc).__name__)
            ran += 1
        return ran

    def _loop(self) -> None:
        last_sweep = last_health = 0.0
        while not self._stop.is_set():
            try:
                if time.monotonic() - last_health > self.settings.semantic_health_seconds:
                    last_health = time.monotonic()
                    with self.sessions() as db:
                        self.semantic.recheck(db)
                self.tick()
                if time.monotonic() - last_sweep > 3600:
                    last_sweep = time.monotonic()
                    with self.sessions() as db:
                        self.sweep(db)
            except Exception as exc:  # never takes the API down
                log.warning("semantic pass failed: %s", type(exc).__name__)
            self._wake.wait(self.settings.semantic_scan_seconds)
            self._wake.clear()


# --------------------------------------------------------------------------- querying


def _owner_opted_out(db: Session, node: Node, cache: dict[uuid.UUID, bool]) -> bool:
    if node.root_id not in cache:
        root = db.get(StorageRoot, node.root_id)
        owner = db.get(User, root.owner_user_id) if root and root.owner_user_id else None
        cache[node.root_id] = bool(
            root and root.kind == "personal" and (owner is None or owner.semantic_opt_out)
        )
    return cache[node.root_id]


def semantic_hits(
    db: Session,
    actor: Actor,
    semantic: Semantic,
    q: str,
    *,
    qvec: list[float] | None = None,
    folder_id: uuid.UUID | None = None,
    limit: int = 25,
    snippet_chars: int = SNIPPET_CHARS,
    root_id: uuid.UUID | None = None,
) -> list[Hit] | None:
    """Nearest chunks the person may read, best chunk per file. `None` means semantic search is
    not available right now (callers fall back to full-text); an empty list means nothing matched.
    Candidates are narrowed in SQL, but nothing is returned before the policy engine has decided
    the node for this person (read access, label, consent) and the snippet is cut afterwards."""
    settings = semantic.settings
    if not is_active(db, settings):
        return None
    cfg = load_config(db)
    q = q.strip()
    if not q:
        return []
    folder: Node | None = None
    if folder_id is not None:
        folder = db.get(Node, folder_id)
        if folder is None or Privilege.TRAVERSE not in engine.effective(db, actor, folder):
            return []
    if qvec is None:  # routes embed first, with the database session released
        qvec = semantic.query_vector(q)
    if qvec is None or len(qvec) != cfg.dim:
        return None
    scope = scope_filter(db, actor, folder, root_id)
    dialect = db.get_bind().dialect.name
    snippet = func.substr(
        SearchDoc.body, SearchChunk.start + 1, SearchChunk.end - SearchChunk.start
    )
    conditions = [
        Node.status.not_in(HIDDEN_NODE_STATUSES),
        SearchChunk.generation == cfg.generation,
        SearchChunk.revision == Node.revision,
        SearchChunk.mtime_ns == func.coalesce(Node.mtime_ns, 0),
        SearchChunk.size == func.coalesce(Node.size, 0),
        SearchDoc.revision == Node.revision,
        SearchDoc.mtime_ns == func.coalesce(Node.mtime_ns, 0),
        SearchDoc.size == func.coalesce(Node.size, 0),
        *scope,
    ]
    joined = (
        select(Node, snippet)
        .join(SearchChunk, SearchChunk.node_id == Node.id)
        .join(SearchDoc, SearchDoc.node_id == Node.id)
        .where(*conditions)
    )
    ranked: list[tuple[Node, str, float]] = []
    if dialect == "postgresql":
        dist = SearchChunk.embedding.op("<=>", return_type=Float)(
            bindparam("qv", qvec, type_=Embedding())
        )
        stmt = joined.add_columns(dist.label("dist")).order_by(dist, SearchChunk.id)
        for node, snip, d in db.execute(stmt.limit(SCAN_CAP)).all():  # one fetch, not paged
            ranked.append((node, snip or "", 1.0 - float(d)))
    else:  # SQLite: development and unit tests; exact cosine in Python over the narrowed rows
        stmt = joined.add_columns(SearchChunk.embedding).limit(20_000)
        for node, snip, vec in db.execute(stmt).all():
            if vec is None or len(vec) != len(qvec):
                continue
            ranked.append((node, snip or "", embed.cosine(vec, qvec)))
        ranked.sort(key=lambda r: (-r[2], str(r[0].id)))
    hits: list[Hit] = []
    seen: set[uuid.UUID] = set()
    opted: dict[uuid.UUID, bool] = {}
    for node, snip, score in ranked[:SCAN_CAP]:
        if not math.isfinite(score) or score < settings.semantic_min_score:
            continue
        if node.id in seen:
            continue
        seen.add(node.id)
        if not may_show_text(db, actor, node) or _owner_opted_out(db, node, opted):
            continue
        hits.append(
            Hit(
                node.id,
                node.name,
                node.kind,
                node.parent_id,
                "semantic",
                " ".join(snip.split())[:snippet_chars] or None,
            )
        )
        if len(hits) >= limit:
            break
    return finish_hits(db, actor, hits)


def fuse(fulltext: list[Hit], semantic_hits_: list[Hit], limit: int) -> list[Hit]:
    """Reciprocal-rank fusion of the two ranked lists. A file in both keeps its full-text hit
    (its snippet shows the matching words) unless that hit has no snippet, and then takes the
    semantic one. Only ranks are used, so no similarity numbers leave this function."""
    scores: dict[uuid.UUID, float] = {}
    by_id: dict[uuid.UUID, Hit] = {}
    for hits in (fulltext, semantic_hits_):
        for rank, h in enumerate(hits):
            scores[h.node_id] = scores.get(h.node_id, 0.0) + 1.0 / (RRF_K + rank + 1)
            cur = by_id.get(h.node_id)
            if cur is None:
                by_id[h.node_id] = h
            elif cur.snippet is None and h.snippet is not None:
                cur.snippet = h.snippet
    order = sorted(scores, key=lambda i: (-scores[i], str(i)))
    return [by_id[i] for i in order[:limit]]
