"""Orchestrates previews: privacy gate, cache, limited worker, converter. The routes call this
after the node guard has passed; nothing here decides who may read a node."""

import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any, BinaryIO

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Node, StorageRoot, User
from app.policy import privacy
from app.preview import convert, kinds
from app.preview.cache import PreviewCache, make_key
from app.preview.sandbox import Gate, Limits, PreviewError, run_worker

log = logging.getLogger("filecanopy.preview")

# A request waits this long for the single converter slot, then gets 503 PREVIEW_BUSY, so a queue
# of Office previews can never hold all the API's worker threads.
CONVERTER_QUEUE_WAIT = 5.0
MAX_DOCX_BYTES = 50 * 1024 * 1024
THUMB_EDGE = 320
ZIP_FOR_OFFICE_MAX_ENTRIES = 20_000
OFFICE_MAX_UNPACKED = 500 * 1024**2


def opted_in(db: Session, user_id: uuid.UUID | None) -> bool:
    if user_id is None:
        return False
    user = db.get(User, user_id)
    return bool(user is not None and user.local_processing_opt_in)


def require_processing_allowed(db: Session, node: Node, user_id: uuid.UUID) -> None:
    """Server-side parsing of a Private or Confidential file is local processing: the requester
    must have opted in and, in a personal folder, so must its owner (a viewer grant must not make
    the owner's file get processed against their wish). Checked on every read of a derivative, so
    withdrawing the opt-in makes cached derivatives unreachable at once. The node guard has
    already ensured the Confidential vault is unlocked for this person."""
    label = privacy.effective_label(db, node)
    if label == "normal":
        return
    allowed = opted_in(db, user_id)
    root = db.get(StorageRoot, node.root_id)
    owner = root.owner_user_id if root is not None else None
    if owner is not None and owner != user_id:
        allowed = allowed and opted_in(db, owner)
    route = privacy.ai_route_allowed(label, "local", local_opt_in=allowed, unlocked_by_person=True)
    if not route.allowed:
        raise PreviewError("LOCAL_PROCESSING_OPT_IN_REQUIRED", 403)


class PreviewService:
    def __init__(self, settings: Settings, sessions: "sessionmaker[Session]") -> None:
        self.settings = settings
        self.sessions = sessions
        self.cache = PreviewCache(settings.app_data_path, settings.preview_cache_max_bytes)
        self.gate = Gate(settings.preview_max_workers)
        self.converter_gate = Gate(1, wait_seconds=CONVERTER_QUEUE_WAIT)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ limits

    def limits(self, max_output: int) -> Limits:
        return Limits(
            self.settings.preview_worker_timeout_seconds,
            self.settings.preview_worker_memory_bytes,
            max_output,
            self.settings.preview_require_seccomp,
        )

    @property
    def converter_configured(self) -> bool:
        return convert.configured(self.settings.converter_url)

    # ------------------------------------------------------------------ derivatives

    def extract(
        self, node_id: uuid.UUID, file: BinaryIO, size: int, revision: int, mtime_ns: int, ext: str
    ) -> dict[str, Any]:
        limit = self.settings.preview_pdf_extract_max_bytes if ext == "pdf" else MAX_DOCX_BYTES
        if size > limit:
            raise PreviewError("TOO_LARGE", 413)
        key = make_key(revision, mtime_ns, size)
        hit = self.cache.get(node_id, key, "json")
        if hit is not None:
            try:
                return json.loads(hit.read_bytes())  # type: ignore[no-any-return]
            except (OSError, ValueError):
                pass
        with self.gate:
            out = run_worker(
                "extract",
                file,
                self.limits(self.settings.preview_text_max_chars * 4 + 4096),
                {"ext": ext, "max_chars": self.settings.preview_text_max_chars},
            )
        try:
            result: dict[str, Any] = json.loads(out)
        except ValueError:
            raise PreviewError("PREVIEW_FAILED") from None
        self.cache.put_bytes(node_id, key, "json", json.dumps(result).encode())
        return result

    def extract_text(self, file: BinaryIO, size: int, ext: str) -> str:
        """Text of a PDF or DOCX for the search index: same limited worker, nothing cached."""
        limit = self.settings.preview_pdf_extract_max_bytes if ext == "pdf" else MAX_DOCX_BYTES
        if size > limit:
            raise PreviewError("TOO_LARGE", 413)
        with self.gate:
            out = run_worker(
                "extract",
                file,
                self.limits(self.settings.preview_text_max_chars * 4 + 4096),
                {"ext": ext, "max_chars": self.settings.search_body_max_chars},
            )
        try:
            text = json.loads(out).get("text")
        except (ValueError, AttributeError):
            raise PreviewError("PREVIEW_FAILED") from None
        if not isinstance(text, str):
            raise PreviewError("PREVIEW_FAILED")
        return text

    def pdf_pages(self, file: BinaryIO, size: int) -> dict[str, Any]:
        """Per-page descriptors of a PDF for split proposals (M17): same limited worker."""
        if size > self.settings.preview_pdf_extract_max_bytes:
            raise PreviewError("TOO_LARGE", 413)
        with self.gate:
            out = run_worker(
                "pdfpages",
                file,
                self.limits(4 * 1024 * 1024),
                {"max_pages": self.settings.preview_max_pages, "head_chars": 300},
            )
        try:
            result: dict[str, Any] = json.loads(out)
        except ValueError:
            raise PreviewError("PREVIEW_FAILED") from None
        if not isinstance(result.get("descriptors"), list):
            raise PreviewError("PREVIEW_FAILED")
        return result

    def pdf_part(self, file: BinaryIO, size: int, pages: list[int]) -> bytes:
        """A new PDF of the given 1-based pages, checked against the source inside the worker."""
        if size > self.settings.preview_pdf_extract_max_bytes:
            raise PreviewError("TOO_LARGE", 413)
        with self.gate:
            data = run_worker(
                "pdfpart",
                file,
                self.limits(size + 4 * 1024 * 1024),
                {"pages": pages},
            )
        if not data.startswith(b"%PDF-"):
            raise PreviewError("PREVIEW_FAILED")
        return data

    def thumbnail(
        self, node_id: uuid.UUID, file: BinaryIO, size: int, revision: int, mtime_ns: int
    ) -> Path:
        if size > self.settings.preview_thumb_max_bytes:
            raise PreviewError("TOO_LARGE", 413)
        key = make_key(revision, mtime_ns, size)
        hit = self.cache.get(node_id, key, "jpg")
        if hit is not None:
            return hit
        with self.gate:
            data = run_worker(
                "thumb",
                file,
                self.limits(8 * 1024 * 1024),
                {"edge": THUMB_EDGE, "max_pixels": self.settings.preview_thumb_max_pixels},
            )
        if not data.startswith(b"\xff\xd8\xff"):
            raise PreviewError("PREVIEW_FAILED")
        return self.cache.put_bytes(node_id, key, "jpg", data)

    def archive(self, file: BinaryIO) -> dict[str, Any]:
        with self.gate:
            out = run_worker(
                "zipdir",
                file,
                self.limits(4 * 1024 * 1024),
                {"max_entries": self.settings.preview_zip_max_entries},
            )
        try:
            return json.loads(out)  # type: ignore[no-any-return]
        except ValueError:
            raise PreviewError("PREVIEW_FAILED") from None

    def office_pdf(
        self, node_id: uuid.UUID, file: BinaryIO, size: int, revision: int, mtime_ns: int, ext: str
    ) -> Path:
        """The converted PDF of an Office file (cached). Never touches the original."""
        key = make_key(revision, mtime_ns, size)
        hit = self.cache.get(node_id, key, "pdf")
        if hit is not None:
            return hit
        if not self.converter_configured:
            raise PreviewError("CONVERTER_UNAVAILABLE", 503)
        if size > self.settings.preview_office_max_bytes:
            raise PreviewError("TOO_LARGE", 413)
        if ext not in kinds.LEGACY:
            self._check_container(file)
        with self.converter_gate:
            hit = self.cache.get(node_id, key, "pdf")  # another request may have just made it
            if hit is not None:
                return hit
            tmp = self.cache.tmp_path()
            try:
                convert.convert_to_pdf(
                    self.settings.converter_url,
                    file,
                    size,
                    ext,
                    tmp,
                    timeout=self.settings.converter_timeout_seconds,
                    max_pages=self.settings.preview_max_pages,
                )
                with open(tmp, "rb") as produced, self.gate:
                    run_worker(
                        "pdfinfo",
                        produced,
                        self.limits(4096),
                        {"max_pages": self.settings.preview_max_pages},
                    )
                return self.cache.put_file(node_id, key, "pdf", tmp)
            finally:
                tmp.unlink(missing_ok=True)

    def _check_container(self, file: BinaryIO) -> None:
        """Zip-bomb and encryption check of an OOXML or OpenDocument package before it is handed
        to the converter (central directory only)."""
        info = self.archive_for_office(file)
        if info["encrypted"]:
            raise PreviewError("ENCRYPTED")
        if info.get("macros"):  # never run either way; not previewing them is defence in depth
            raise PreviewError("HAS_MACROS")
        if (
            info["count"] > ZIP_FOR_OFFICE_MAX_ENTRIES
            or info["suspicious_ratio"]
            or info["total_size"] > OFFICE_MAX_UNPACKED
        ):
            raise PreviewError("TOO_LARGE", 413)

    def archive_for_office(self, file: BinaryIO) -> dict[str, Any]:
        with self.gate:
            out = run_worker("zipdir", file, self.limits(4 * 1024 * 1024), {"max_entries": 1})
        try:
            return json.loads(out)  # type: ignore[no-any-return]
        except ValueError:
            raise PreviewError("PREVIEW_FAILED") from None

    # ------------------------------------------------------------------ housekeeping

    def sweep(self) -> dict[str, int]:
        return self.cache.sweep(self.sessions)

    def start(self) -> None:
        """Sweeps at startup and then hourly, so derivatives never outlive their file for long."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="fc-previews", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def sandbox_status(self) -> dict[str, object]:
        """What the parser child can enforce on this kernel (logged at startup)."""
        try:
            out = run_worker("probe", None, self.limits(4096))
            return json.loads(out)  # type: ignore[no-any-return]
        except (PreviewError, ValueError, OSError):
            return {"seccomp": False, "landlock": "unknown"}

    def _loop(self) -> None:
        status = self.sandbox_status()
        log.info("preview sandbox: %s", status)
        if not status.get("seccomp"):
            log.warning("preview parsers refused: seccomp is not available on this kernel")
        elif status.get("landlock") != "enforced":
            log.warning("preview parsers can read all files the API can (no Landlock)")
        while True:
            try:
                self.sweep()
            except Exception as exc:  # never takes the API down
                log.warning("preview sweep failed: %s", type(exc).__name__)
            if self._stop.wait(3600):
                return

    def discard_nodes(self, node_ids: list[uuid.UUID]) -> None:
        try:
            self.cache.discard(node_ids)
        except OSError as exc:  # the sweeper catches whatever this misses
            log.warning("preview discard failed: %s", type(exc).__name__)
