"""Structured JSON logging. Messages must never contain secrets or document content."""

import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            # Exception type only: tracebacks and messages can embed DSNs or paths.
            payload["exc_type"] = record.exc_info[0].__name__
        return json.dumps(payload)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    # uvicorn configures its own stderr handler before loading the app factory. Replacing only
    # root handlers leaves its default traceback formatter active, leaking exception messages.
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    # Uvicorn decides whether to emit access lines by hasHandlers(). Propagating this logger to
    # root would silently re-enable URL-bearing logs despite the image's --no-access-log flag.
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
