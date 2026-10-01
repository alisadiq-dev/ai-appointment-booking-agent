"""Structured JSON logging. One JSON object per line, ready for Cloud Logging.

Only fields this module names are ever emitted: request headers, bodies and chat text are never
passed to a logger, and the formatter ignores any other attribute on a record.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, TextIO

# The id of the request being handled; set by the request-context middleware.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Extra record attributes that are copied into the JSON line (the access log fields).
_EXTRA_FIELDS = ("method", "route", "status", "duration_ms")

_HANDLER_MARK = "_booking_agent_json_handler"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or request_id_var.get()
        if request_id:
            data["request_id"] = request_id
        for field in _EXTRA_FIELDS:
            if hasattr(record, field):
                data[field] = getattr(record, field)
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        # json.dumps escapes newlines, so a message cannot forge a second log line.
        return json.dumps(data, default=str)


def configure_logging(level: str, stream: TextIO | None = None) -> None:
    """Send all logs (ours, libraries, uvicorn) to one JSON handler on the root logger.

    Idempotent: calling it again replaces the handler instead of adding another.
    """
    level_number = logging.getLevelNamesMapping().get(level.upper())
    if level_number is None:
        raise ValueError(f"LOG_LEVEL is not a valid level: {level}")

    root = logging.getLogger()
    for existing in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(existing)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    root.setLevel(level_number)

    # uvicorn installs its own plain-text handlers; route its logs through ours instead. Its
    # access log is replaced by the request-context middleware's line (which has no query string).
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
