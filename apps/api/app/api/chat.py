"""Document chat (M20): one question over the files the person may read, answered with citations.
Stateless: no conversation is stored. See `app.ai.chat` for the order of privacy checks."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.ai import chat
from app.ai.gateway import AiGateway
from app.auth.deps import Principal, client_ip, current_principal, get_db, http_error

router = APIRouter(prefix="/api/v1/chat", tags=["ai"])


class AskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=2, max_length=chat.MAX_QUESTION)
    scope: Literal["mine", "shared"] = "mine"
    folder_id: uuid.UUID | None = None


class CitationOut(BaseModel):
    n: int
    node_id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None
    excerpt: str


class AskOut(BaseModel):
    status: Literal["answered", "passages_only", "no_sources"]
    reason: str | None
    answer: str | None
    grounded: bool
    citations: list[CitationOut]
    withheld_protected: int
    provider_mode: str | None


@router.post("/ask", response_model=AskOut)
def ask(
    body: AskBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> AskOut:
    if not request.app.state.chat_limiter.allow(principal.user.id):
        raise http_error(429, "CHAT_TOO_FAST")
    gateway: AiGateway = request.app.state.ai
    result = chat.ask(
        db,
        request.app.state.auth.settings,
        gateway,
        request.app.state.semantic,
        principal.user,
        principal.session.id,
        client_ip(request),
        body.question,
        scope=body.scope,
        folder_id=body.folder_id,
    )
    return AskOut(
        status=result.status,
        reason=result.reason,
        answer=result.answer,
        grounded=result.grounded,
        citations=[CitationOut(**vars(c)) for c in result.citations],
        withheld_protected=result.withheld_protected,
        provider_mode=result.provider_mode,
    )
