"""Previews and safe extraction (M10, ADR-019): converted Office documents, extracted text,
thumbnails and ZIP listings, plus the local-processing opt-in.

Every node route uses the same read guard as `/view` and download (ACL, uniform 403, Confidential
lock, no administrator access to personal folders). Parsing a Private or Confidential file on the
server also needs the local-processing opt-in (`preview.service.require_processing_allowed`).
Answers carry codes only; derivatives are inert data (JSON, a JPEG, a sandboxed PDF).
"""

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.files import _ctx, inline_response, record_once
from app.auth.deps import Principal, client_ip, current_principal, get_db, http_error
from app.auth.service import audit
from app.db.models import User
from app.domain import fileops
from app.domain.fileops import Download, OpError
from app.policy.deps import NodeContext, read_access
from app.preview import kinds
from app.preview.sandbox import PreviewError
from app.preview.service import PreviewService, opted_in, require_processing_allowed

router = APIRouter(prefix="/api/v1", tags=["previews"])

TEXT_RETURN_CHARS = 200_000
SNIFF = 8192


def _svc(request: Request) -> PreviewService:
    svc: PreviewService = request.app.state.previews
    return svc


def _fail(err: PreviewError | OpError) -> HTTPException:
    return http_error(err.status, err.code)


def _open_source(
    request: Request,
    ctx: NodeContext,
    db: Session,
    allowed: set[str],
    content_ok: Any,
) -> tuple[Download, str]:
    """Gate, then open the file the same way download does, and check its first bytes."""
    ext = kinds.ext_of(ctx.node.name)
    if ctx.node.kind != "file" or ext is None or ext not in allowed:
        raise http_error(415, "NOT_PREVIEWABLE")
    try:
        require_processing_allowed(db, ctx.node, ctx.principal.user.id)
        dl = fileops.open_download(_ctx(request, ctx.principal, db), ctx.node.id)
    except (PreviewError, OpError) as err:
        raise _fail(err) from err
    try:
        head = dl.file.read(SNIFF) or b""
        dl.file.seek(0)
        if not content_ok(ext, head):
            raise http_error(415, "TYPE_MISMATCH")
    except BaseException:
        dl.file.close()
        raise
    return dl, ext


@router.get("/nodes/{node_id}/preview")
def office_preview(
    request: Request,
    ctx: NodeContext = Depends(read_access),
    db: Session = Depends(get_db),
    range_header: str | None = Header(default=None, alias="Range"),
    if_range: str | None = Header(default=None, alias="If-Range"),
) -> Any:
    """An Office document as PDF for the viewer, made by the optional sealed converter and cached
    outside the library. Without the converter: 503 CONVERTER_UNAVAILABLE (download instead)."""
    svc = _svc(request)
    dl, ext = _open_source(request, ctx, db, kinds.OFFICE, kinds.office_content_ok)
    try:
        try:
            pdf = svc.office_pdf(ctx.node.id, dl.file, dl.size, dl.revision, dl.mtime_ns, ext)
        finally:
            dl.file.close()
        out = pdf.open("rb")
        size = pdf.stat().st_size
    except PreviewError as err:
        raise _fail(err) from err
    except OSError as exc:
        raise http_error(500, "PREVIEW_FAILED") from exc
    return inline_response(
        request,
        user_id=ctx.principal.user.id,
        node_id=ctx.node.id,
        name=ctx.node.name,
        file=out,
        size=size,
        etag=f'"{dl.revision}-{dl.mtime_ns}-pdf"',
        mime="application/pdf",
        range_header=range_header,
        if_range=if_range,
    )


class ExtractOut(BaseModel):
    kind: str
    pages: int | None
    text: str
    chars: int
    truncated: bool


@router.get("/nodes/{node_id}/extract", response_model=ExtractOut)
def extract_text(
    request: Request,
    ctx: NodeContext = Depends(read_access),
    db: Session = Depends(get_db),
) -> ExtractOut:
    """Plain text of a PDF or DOCX (first 200,000 characters). Stored outside the library."""
    svc = _svc(request)
    dl, ext = _open_source(request, ctx, db, kinds.EXTRACT, kinds.extract_content_ok)
    try:
        try:
            result = svc.extract(ctx.node.id, dl.file, dl.size, dl.revision, dl.mtime_ns, ext)
        finally:
            dl.file.close()
    except PreviewError as err:
        raise _fail(err) from err
    record_once(request, ctx.principal.user.id, ctx.node.id, ctx.node.name, "text_extracted")
    text = str(result.get("text", ""))
    cut = len(text) > TEXT_RETURN_CHARS
    return ExtractOut(
        kind=str(result.get("kind", ext)),
        pages=result.get("pages") if isinstance(result.get("pages"), int) else None,
        text=text[:TEXT_RETURN_CHARS],
        chars=min(len(text), TEXT_RETURN_CHARS),
        truncated=bool(result.get("truncated")) or cut,
    )


@router.get("/nodes/{node_id}/thumbnail")
def thumbnail(
    request: Request,
    ctx: NodeContext = Depends(read_access),
    db: Session = Depends(get_db),
) -> Response:
    """A small JPEG of a PNG, JPEG, GIF or WebP picture: re-encoded, no metadata. SVG has none."""
    svc = _svc(request)
    dl, _ = _open_source(request, ctx, db, set(kinds.THUMB_MIMES), kinds.thumb_content_ok)
    try:
        try:
            path = svc.thumbnail(ctx.node.id, dl.file, dl.size, dl.revision, dl.mtime_ns)
        finally:
            dl.file.close()
        data = path.read_bytes()
    except PreviewError as err:
        raise _fail(err) from err
    except OSError as exc:
        raise http_error(500, "PREVIEW_FAILED") from exc
    return Response(
        data,
        media_type="image/jpeg",
        headers={
            "Content-Disposition": "inline",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


class ArchiveEntryOut(BaseModel):
    name: str
    size: int
    packed: int
    folder: bool
    unsafe_name: bool
    encrypted: bool


class ArchiveOut(BaseModel):
    count: int
    entries: list[ArchiveEntryOut]
    truncated: bool
    total_size: int
    total_packed: int
    encrypted: bool
    suspicious_ratio: bool


@router.get("/nodes/{node_id}/archive", response_model=ArchiveOut)
def archive_listing(
    request: Request,
    ctx: NodeContext = Depends(read_access),
    db: Session = Depends(get_db),
) -> ArchiveOut:
    """What a ZIP file contains (names and sizes from its directory). Nothing is extracted, opened
    or written, and nested archives are not looked into."""
    svc = _svc(request)
    dl, _ = _open_source(
        request, ctx, db, kinds.ZIP_EXT, lambda _e, head: kinds.zip_content_ok(head)
    )
    try:
        try:
            result = svc.archive(dl.file)
        finally:
            dl.file.close()
    except PreviewError as err:
        raise _fail(err) from err
    return ArchiveOut(**result)


# ---------------------------------------------------------------- the opt-in


class ProcessingOut(BaseModel):
    enabled: bool
    converter: bool


class ProcessingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


@router.get("/preferences/local-processing", response_model=ProcessingOut)
def get_processing(
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> ProcessingOut:
    return ProcessingOut(
        enabled=opted_in(db, principal.user.id), converter=_svc(request).converter_configured
    )


@router.put("/preferences/local-processing", response_model=ProcessingOut)
def set_processing(
    body: ProcessingBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> ProcessingOut:
    """Own account only. Lets this server read Private and Confidential files to make previews;
    nothing is ever sent to a cloud service. Turning it off makes cached previews unreachable."""
    user = db.get(User, principal.user.id)
    if user is None:
        raise http_error(403, "FORBIDDEN")
    user.local_processing_opt_in = body.enabled
    audit(
        db,
        "privacy.local_processing",
        actor=user.id,
        ip=client_ip(request),
        enabled=body.enabled,
    )
    db.commit()
    return ProcessingOut(enabled=body.enabled, converter=_svc(request).converter_configured)
