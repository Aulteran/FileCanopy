import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.config import Settings, load_settings
from app.db.engine import make_engine
from app.db.migrate import head_revision
from app.health import build_report
from app.logging_config import configure_logging

log = logging.getLogger("filecanopy.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    cfg = settings or load_settings()
    engine = make_engine(cfg)
    expected_head = head_revision()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        log.info("api started")
        yield
        engine.dispose()

    app = FastAPI(title="FileCanopy API", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Always generated server-side: a client-supplied value could inject log content.
        response = await call_next(request)
        response.headers["X-Request-ID"] = uuid.uuid4().hex
        return response

    @app.get("/api/v1/health/live")
    def live() -> dict[str, str]:
        """Process liveness only; touches no dependency."""
        return {"status": "ok"}

    @app.get("/api/v1/health")
    def health() -> JSONResponse:
        """200 whenever core is usable (optional workers may be down); 503 when core is not."""
        report = build_report(cfg, engine, expected_head)
        return JSONResponse(report, status_code=503 if report["status"] == "unavailable" else 200)

    return app
