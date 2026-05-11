"""CSV exporters for v1 admin surfaces."""

from __future__ import annotations

import csv
import io
from typing import Iterable

from war_room.v1 import storage as _s


AUDIT_COLUMNS = (
    "ts",
    "request_id",
    "tenant_id",
    "actor",
    "method",
    "route",
    "status",
    "latency_ms",
    "client_ip",
    "request_hash",
)


def audit_csv(rows: Iterable[dict]) -> str:
    """Serialise audit rows to a CSV string with stable column order."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=AUDIT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({col: row.get(col, "") for col in AUDIT_COLUMNS})
    return buf.getvalue()


def usage_csv(rows: Iterable[dict]) -> str:
    cols = ("tenant_id", "period", "meetings", "tts_chars", "llm_tokens", "interventions", "updated_at")
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({col: row.get(col, "") for col in cols})
    return buf.getvalue()


def stream_audit_query(**filters) -> str:
    rows = _s.query_audit(**filters)
    return audit_csv(rows)
