"""Structured logging + request-id correlation for the War Room.

Behaviour:
    SEMECLAW_LOG_JSON=1       -> one JSON line per log record
    (anything else)           -> existing human format, no change

The request_id lives in a contextvar that\'s set by a FastAPI middleware
for the duration of each request. ``logger.info("msg")`` auto-picks it up;
background tasks keep working and simply log without a request_id.

The current process logger tree is read once at ``setup_logging()`` time
so existing handlers (uvicorn etc.) are replaced, not duplicated.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from contextvars import ContextVar

_request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def current_request_id() -> str:
    return _request_id_var.get()


def set_request_id(rid: str) -> None:
    _request_id_var.set(rid)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id() or "-"
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(force_json: bool | None = None) -> None:
    """Install a stdout handler with the right formatter + request-id filter.

    Idempotent — safe to call multiple times (removes existing handlers).
    """
    want_json = (
        force_json
        if force_json is not None
        else os.environ.get("SEMECLAW_LOG_JSON", "").strip() in ("1", "true", "yes")
    )
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    if want_json:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(os.environ.get("SEMECLAW_LOG_LEVEL", "INFO").upper())


def new_request_id() -> str:
    """12-char URL-safe identifier — short enough to eyeball in grep."""
    return uuid.uuid4().hex[:12]
