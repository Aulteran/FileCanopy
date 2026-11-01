"""Background indexing of file text for full-text search (M11).

A scanner queues files that are new or changed; one worker thread claims jobs under a lease, reads
each file through `fileops.open_download` as the root's owner (or an administrator in the shared
tree) with no login session, so ordinary ACL and lock rules apply and the Confidential unlock is
never refreshed or borrowed. Parsing uses the M10 parser sandbox; OCR uses the sealed container.
Nothing here decides who may *see* a result: `app.search.query` does that for every request.
"""

import logging
import re
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth import clock
from app.config import Settings
from app.db.models import Node, SearchDoc, SearchJob, StorageRoot, User
from app.domain.fileops import OpCtx, OpError, open_download
from app.policy import privacy
from app.preview import kinds, ocr
from app.preview.sandbox import PreviewError
from app.preview.service import PreviewService, require_processing_allowed

log = logging.getLogger("filecanopy.search")

TEXT_EXT = {"txt", "md", "csv"}
EXTRACT_EXT = {"pdf", "docx"}
# A PDF with less text than this is treated as a scan and sent to OCR (when OCR is configured).
SCAN_THRESHOLD_CHARS = 20
# Failures that will not fix themselves until the file changes.
PERMANENT = {
    "ENCRYPTED",
    "UNREADABLE",
    "TOO_LARGE",
    "TYPE_MISMATCH",
    "UNSUPPORTED",
    "OCR_FAILED",
    "NOT_PREVIEWABLE",
}
LONG = timedelta(days=3650)
DEFERRED = timedelta(hours=1)  # no opt-in, no access, locked: look again later
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def now() -> datetime:
    return clock.utcnow()


def indexable_ext(settings: Settings) -> set[str]:
    out = TEXT_EXT | EXTRACT_EXT
    if ocr.configured(settings.ocr_url):
        out = out | ocr.IMAGE_EXT
    return out


def _version(node: Node) -> tuple[int, int, int]:
    return node.revision, node.mtime_ns or 0, node.size or 0


def identity_for(db: Session, root: StorageRoot) -> uuid.UUID | None:
    """The account whose authority a background job borrows: the owner of a personal root, or an
    active administrator for the shared tree. Never a login session, so never unlocked."""
    if root.kind == "personal":
        user = db.get(User, root.owner_user_id) if root.owner_user_id else None
        return user.id if user is not None and user.status == "active" else None
    admin = db.scalar(
        select(User.id)
        .where(User.role == "admin", User.status == "active")
        .order_by(User.created_at)
        .limit(1)
    )
    return admin


class Indexer:
    def __init__(
        self, settings: Settings, sessions: "sessionmaker[Session]", previews: PreviewService
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.previews = previews
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ scanning

    def scan_once(self, db: Session) -> int:
        """Queues files that have no current index row and no current job. Returns how many."""
        exts = sorted(indexable_ext(self.settings))
        t = now()
        stale_doc = or_(
            SearchDoc.node_id.is_(None),
            SearchDoc.revision != Node.revision,
            SearchDoc.mtime_ns != func.coalesce(Node.mtime_ns, 0),
            SearchDoc.size != func.coalesce(Node.size, 0),
        )
        stale_job = or_(
            SearchJob.node_id.is_(None),
            SearchJob.revision != Node.revision,
            SearchJob.mtime_ns != func.coalesce(Node.mtime_ns, 0),
            SearchJob.size != func.coalesce(Node.size, 0),
            and_(SearchJob.state == "failed", SearchJob.run_after <= t),
        )
        rows = db.execute(
            select(Node, SearchJob)
            .outerjoin(SearchDoc, SearchDoc.node_id == Node.id)
            .outerjoin(SearchJob, SearchJob.node_id == Node.id)
            .where(
                Node.kind == "file",
                Node.status == "active",
                Node.parent_id.is_not(None),
                or_(*[Node.name_key.like(f"%.{e}") for e in exts]),
                stale_doc,
                stale_job,
            )
            .order_by(Node.updated_at.desc())
            .limit(self.settings.search_batch)
        ).all()
        for node, job in rows:
            rev, mtime, size = _version(node)
            if job is None:
                db.add(
                    SearchJob(
                        node_id=node.id,
                        revision=rev,
                        mtime_ns=mtime,
                        size=size,
                        state="queued",
                        attempts=0,
                        run_after=t,
                    )
                )
            else:
                job.revision, job.mtime_ns, job.size = rev, mtime, size
                job.state, job.attempts, job.run_after = "queued", 0, t
                job.lease_until, job.error = None, None
        db.commit()
        return len(rows)

    # ------------------------------------------------------------------ claiming

    def claim(self, db: Session) -> uuid.UUID | None:
        """Takes the next due job (or one whose worker died: its lease ran out)."""
        t = now()
        while True:
            job = db.scalar(
                select(SearchJob)
                .where(
                    or_(
                        and_(SearchJob.state == "queued", SearchJob.run_after <= t),
                        and_(SearchJob.state == "running", SearchJob.lease_until < t),
                    )
                )
                .order_by(SearchJob.run_after)
                .limit(1)
            )
            if job is None:
                return None
            if job.attempts >= self.settings.search_max_attempts:
                job.state, job.error, job.run_after = "failed", "GAVE_UP", t + timedelta(days=1)
                job.lease_until = None
                db.commit()
                continue
            job.state = "running"
            job.attempts += 1
            job.lease_until = t + timedelta(seconds=self.settings.search_lease_seconds)
            db.commit()
            return job.node_id

    # ------------------------------------------------------------------ running one job

    def run_job(self, node_id: uuid.UUID) -> str:
        """Indexes one file. Returns a short outcome word (for tests and logs; never content)."""
        with self.sessions() as db:
            job = db.get(SearchJob, node_id)
            node = db.get(Node, node_id)
            if job is None:
                return "gone"
            if (
                node is None
                or node.kind != "file"
                or node.status != "active"
                or _version(node) != (job.revision, job.mtime_ns, job.size)
            ):
                db.execute(delete(SearchJob).where(SearchJob.node_id == node_id))
                db.commit()  # the scanner queues the new version if there is one
                return "stale"
            try:
                text, source = self._read_text(db, node)
            except PreviewError as err:
                return self._fail(db, job, node, err.code)
            except OpError as err:
                code = "NO_ACCESS" if err.status == 403 else "UNAVAILABLE"
                return self._fail(db, job, node, code)
            self._store(db, node, text, source)
            db.execute(delete(SearchJob).where(SearchJob.node_id == node_id))
            db.commit()
            return "indexed"

    def _read_text(self, db: Session, node: Node) -> tuple[str, str]:
        ext = kinds.ext_of(node.name)
        if ext is None or ext not in indexable_ext(self.settings):
            raise PreviewError("UNSUPPORTED", 415)
        root = db.get(StorageRoot, node.root_id)
        actor = identity_for(db, root) if root is not None else None
        if actor is None:
            raise PreviewError("NO_IDENTITY", 503)
        # Private needs the local-processing opt-in; Confidential is refused below because a
        # background job has no unlock (open_download answers 403).
        require_processing_allowed(db, node, actor)
        ctx = OpCtx(db, self.settings, actor)
        dl = open_download(ctx, node.id)
        try:
            head = dl.file.read(8192) or b""
            dl.file.seek(0)
            if ext in TEXT_EXT:
                return self._plain(dl.file), "text"
            if ext in ocr.IMAGE_EXT:
                if not ocr.image_content_ok(ext, head):
                    raise PreviewError("TYPE_MISMATCH", 415)
                return self._ocr(dl.file, dl.size, ext), "ocr"
            if not kinds.extract_content_ok(ext, head):
                raise PreviewError("TYPE_MISMATCH", 415)
            text = self.previews.extract_text(dl.file, dl.size, ext)
            if (
                ext == "pdf"
                and len(text.strip()) < SCAN_THRESHOLD_CHARS
                and ocr.configured(self.settings.ocr_url)
            ):
                dl.file.seek(0)
                return self._ocr(dl.file, dl.size, "pdf"), "ocr"
            return text, "text"
        finally:
            dl.file.close()

    def _plain(self, f: Any) -> str:
        data = f.read(self.settings.search_text_max_bytes) or b""
        return data.decode("utf-8", "replace")

    def _ocr(self, f: Any, size: int, ext: str) -> str:
        if size > self.settings.ocr_max_bytes:
            raise PreviewError("TOO_LARGE", 413)
        return ocr.ocr_text(
            self.settings.ocr_url,
            f,
            size,
            ext,
            timeout=self.settings.ocr_timeout_seconds,
            max_pages=self.settings.ocr_max_pages,
            max_chars=self.settings.search_body_max_chars,
        )

    def _store(self, db: Session, node: Node, text: str, source: str) -> None:
        body = _CONTROL.sub(" ", text)[: self.settings.search_body_max_chars]
        rev, mtime, size = _version(node)
        doc = db.get(SearchDoc, node.id)
        if doc is None:
            doc = SearchDoc(node_id=node.id, root_id=node.root_id)
            db.add(doc)
        doc.root_id = node.root_id
        doc.revision, doc.mtime_ns, doc.size = rev, mtime, size
        doc.source, doc.body, doc.indexed_at = source, body, now()

    def _fail(self, db: Session, job: SearchJob, node: Node, code: str) -> str:
        t = now()
        job.lease_until = None
        job.error = code[:32]
        if code in PERMANENT:
            job.state, job.run_after = "failed", t + LONG
            db.execute(delete(SearchDoc).where(SearchDoc.node_id == node.id))
        elif code in {"LOCAL_PROCESSING_OPT_IN_REQUIRED", "NO_ACCESS", "NO_IDENTITY"}:
            job.state, job.run_after = "failed", t + DEFERRED  # the scanner looks again later
            db.execute(delete(SearchDoc).where(SearchDoc.node_id == node.id))
        elif job.attempts >= self.settings.search_max_attempts:
            job.state, job.error, job.run_after = "failed", "GAVE_UP", t + timedelta(days=1)
        else:
            delay = min(30 * 2**job.attempts, 3600)
            job.state, job.run_after = "queued", t + timedelta(seconds=delay)
        db.commit()
        return f"failed:{code}"

    # ------------------------------------------------------------------ housekeeping

    def sweep(self, db: Session) -> int:
        """Deletes index rows that must not outlive their file or the owner's consent."""
        removed = 0
        gone = db.execute(
            select(SearchDoc.node_id)
            .outerjoin(Node, Node.id == SearchDoc.node_id)
            .where(or_(Node.id.is_(None), Node.status != "active", Node.kind != "file"))
        ).scalars()
        ids = list(gone)
        if ids:
            db.execute(delete(SearchDoc).where(SearchDoc.node_id.in_(ids)))
            removed += len(ids)
        db.execute(
            delete(SearchJob).where(
                SearchJob.node_id.in_(
                    select(SearchJob.node_id)
                    .outerjoin(Node, Node.id == SearchJob.node_id)
                    .where(or_(Node.id.is_(None), Node.status != "active"))
                )
            )
        )
        # Labels and consent can change without the file changing.
        offset = 0
        while True:
            batch = list(
                db.execute(
                    select(SearchDoc.node_id).order_by(SearchDoc.node_id).offset(offset).limit(500)
                ).scalars()
            )
            if not batch:
                break
            offset += len(batch)
            drop: list[uuid.UUID] = []
            for nid in batch:
                node = db.get(Node, nid)
                root = db.get(StorageRoot, node.root_id) if node is not None else None
                if node is None or root is None:
                    continue
                if privacy.effective_label(db, node) == "normal":
                    continue
                actor = identity_for(db, root)
                try:
                    if actor is None:
                        raise PreviewError("NO_IDENTITY")
                    require_processing_allowed(db, node, actor)
                    if privacy.effective_label(db, node) == "confidential":
                        raise PreviewError("CONFIDENTIAL")  # never indexed in the background
                except PreviewError:
                    drop.append(nid)
            if drop:
                db.execute(delete(SearchDoc).where(SearchDoc.node_id.in_(drop)))
                removed += len(drop)
            offset -= len(drop)
        db.commit()
        return removed

    # ------------------------------------------------------------------ thread

    def kick(self) -> None:
        self._wake.set()

    def start(self) -> None:
        if self._thread is not None or not self.settings.search_enabled:
            return
        self._thread = threading.Thread(target=self._loop, name="fc-search", daemon=True)
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
            self.scan_once(db)
        while not self._stop.is_set():
            with self.sessions() as db:
                node_id = self.claim(db)
            if node_id is None:
                break
            try:
                self.run_job(node_id)
            except Exception as exc:  # the lease runs out and the job is taken again
                log.warning("search job failed unexpectedly: %s", type(exc).__name__)
            ran += 1
        return ran

    def _loop(self) -> None:
        last_sweep = 0.0
        import time

        while not self._stop.is_set():
            try:
                self.tick()
                if time.monotonic() - last_sweep > 3600:
                    last_sweep = time.monotonic()
                    with self.sessions() as db:
                        self.sweep(db)
            except Exception as exc:  # never takes the API down
                log.warning("search pass failed: %s", type(exc).__name__)
            self._wake.wait(self.settings.search_scan_seconds)
            self._wake.clear()
