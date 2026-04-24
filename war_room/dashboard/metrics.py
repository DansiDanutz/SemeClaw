"""Prometheus counters + a /metrics endpoint for the War Room dashboard.

Every metric is created once at import time. The hot-path callers bump the
counters; the endpoint serialises to Prometheus text format. When the
prometheus_client library isn\'t installed the module still imports but
every op becomes a no-op — so an operator without Prom tooling can still
boot the server.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("war_room.metrics")

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    _ENABLED = True
except ImportError:  # pragma: no cover
    _ENABLED = False
    CONTENT_TYPE_LATEST = "text/plain; charset=utf-8"

    class _Stub:
        def labels(self, **_: Any) -> _Stub:
            return self

        def inc(self, *_: Any, **__: Any) -> None:
            return None

        def set(self, *_: Any, **__: Any) -> None:
            return None

        def observe(self, *_: Any, **__: Any) -> None:
            return None

    def Counter(*a: Any, **kw: Any) -> _Stub:  # noqa: N802
        return _Stub()

    def Gauge(*a: Any, **kw: Any) -> _Stub:  # noqa: N802
        return _Stub()

    def Histogram(*a: Any, **kw: Any) -> _Stub:  # noqa: N802
        return _Stub()

    def generate_latest() -> bytes:
        return b"# prometheus_client not installed - metrics disabled.\n"


# ---------------------------------------------------------------------------
# Counters + gauges
# ---------------------------------------------------------------------------
impressions_total = Counter(
    "semeclaw_impressions_total",
    "Ad impressions served. Labelled by tenant.",
    ["tenant_id"],
)
credits_deducted_total = Counter(
    "semeclaw_credits_deducted_total",
    "Credits deducted from advertiser wallets (Supabase RPC side).",
    ["tenant_id"],
)
tts_requests_total = Counter(
    "semeclaw_tts_requests_total",
    "TTS synthesis requests. Labelled by engine and outcome.",
    ["engine", "outcome"],
)
tts_latency_seconds = Histogram(
    "semeclaw_tts_latency_seconds",
    "End-to-end TTS latency in seconds.",
    ["engine"],
)
dlq_appends_total = Counter(
    "semeclaw_dlq_appends_total",
    "Dead-letter-queue entries written.",
    ["dlq"],
)
ws_connections_active = Gauge(
    "semeclaw_ws_connections_active",
    "Current WebSocket connections attached to ConnectionManager.",
)
meeting_broadcasts_total = Counter(
    "semeclaw_meeting_broadcasts_total",
    "Meeting broadcasts sent to WS clients.",
    ["type"],
)
task_operations_total = Counter(
    "semeclaw_task_operations_total",
    "Task subsystem operations.",
    ["op", "outcome"],
)
http_requests_total = Counter(
    "semeclaw_http_requests_total",
    "HTTP requests handled. Labelled by method and status-class.",
    ["method", "status_class"],
)
http_request_latency_seconds = Histogram(
    "semeclaw_http_request_latency_seconds",
    "HTTP request latency.",
    ["method", "path"],
)


def metrics_response() -> tuple[bytes, str]:
    """Return (body, content_type) for a /metrics endpoint."""
    return generate_latest(), CONTENT_TYPE_LATEST
