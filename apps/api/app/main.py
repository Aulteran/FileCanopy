import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker

from app.api import admin_storage
from app.api import files as file_routes
from app.api import nodes as storage_nodes
from app.api import reconcile as reconcile_routes
from app.auth import admin as auth_admin
from app.auth import bootstrap
from app.auth import router as auth_router
from app.auth.deps import AuthContext, scrub_validation_errors
from app.auth.security import Passwords
from app.auth.throttle import Throttle
from app.config import Settings, load_settings
from app.db.engine import make_engine
from app.db.migrate import head_revision
from app.domain.fileops import recover_operations
from app.health import build_report
from app.logging_config import configure_logging
from app.storage.roots import ensure_roots
from app.storage.service import ReconcilerService

log = logging.getLogger("filecanopy.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    cfg = settings or load_settings()
    engine = make_engine(cfg)
    expected_head = head_revision()
    sessions = sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("api started")
        try:
            bootstrap.ensure_token(sessions, cfg)
            app.state.setup_token_issued = True
        except Exception as exc:  # database not ready: retried lazily by /setup/status
            log.warning("setup token not prepared at startup: %s", type(exc).__name__)
        try:
            ensure_roots(sessions, cfg)
        except Exception as exc:  # roots are retried on the next start; the API still serves
            log.warning("storage roots not reconciled at startup: %s", type(exc).__name__)
        try:
            recover_operations(sessions, cfg)
        except Exception as exc:  # flagged on the next start; never blocks serving
            log.warning("file operation recovery failed: %s", type(exc).__name__)
        if cfg.reconciler_enabled:  # after recovery; never blocks serving
            reconciler.start()
        yield
        reconciler.stop()
        engine.dispose()

    reconciler = ReconcilerService(sessions, cfg)
    app = FastAPI(title="FileCanopy API", lifespan=lifespan, docs_url=None, redoc_url=None)

    app.state.auth = AuthContext(cfg, sessions, Passwords(cfg), Throttle(cfg))
    app.state.setup_token_issued = False
    app.state.reconciler = reconciler

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Default 422 bodies echo submitted values (passwords, tokens); send locations only.
        return JSONResponse(
            {
                "detail": {
                    "code": "VALIDATION_ERROR",
                    "errors": scrub_validation_errors(exc.errors()),
                }
            },
            status_code=422,
        )

    @app.middleware("http")
    async def request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Always generated server-side: a client-supplied value could inject log content.
        response = await call_next(request)
        response.headers["X-Request-ID"] = uuid.uuid4().hex
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/v1/") and "/health" not in request.url.path:
            response.headers["Cache-Control"] = "no-store"  # auth state must never be cached
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

    app.include_router(auth_router.router)
    app.include_router(auth_admin.router)
    app.include_router(storage_nodes.router)
    app.include_router(file_routes.router)
    app.include_router(reconcile_routes.router)
    app.include_router(admin_storage.router)

    return app
