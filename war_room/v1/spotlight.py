"""Subscriber spotlight + pre-meeting ad rotation.

Pulls the curated subscriber spotlight definition from
``ad-semeclaw/spotlight.json`` (the same JSON the Vercel advertiser
console renders) and surfaces it through SemeClaw's API. Local v1
additions can extend the list via ``data/v1/spotlight_overrides.json``
so operators can promote a tenant without editing the source-of-truth
JSON.

Selection rules (matches the original spotlight.json `note`):
  - ``pinned: true`` items always show.
  - Non-pinned items appear when ``featured_from <= now``
    AND ``now - featured_from <= ttl_days``.
  - Override items merge in by ``id`` (override wins on conflict).
"""

from __future__ import annotations

import logging
import random
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from war_room.v1 import storage as _s

logger = logging.getLogger("semeclaw.v1.spotlight")

# Source-of-truth — the same file the Vercel frontend reads.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = REPO_ROOT / "ad-semeclaw" / "spotlight.json"


def _overrides_path() -> Path:
    return Path(_s._data_dir()) / "spotlight_overrides.json"


def _impressions_path() -> Path:
    return Path(_s._data_dir()) / "spotlight_impressions.jsonl"


# Back-compat aliases for tests that touch the module attribute directly.
OVERRIDES_FILE = _s._PathProxy("spotlight_overrides.json")
IMPRESSIONS_FILE = _s._PathProxy("spotlight_impressions.jsonl")


@dataclass(frozen=True)
class SpotlightItem:
    id: str
    name: str
    tagline: str
    description: str
    url: str
    logo_letter: str
    gradient_from: str
    gradient_to: str
    accent_rgb: str
    pinned: bool
    featured_from: str | None
    hook: str = ""
    hook_highlight: str = ""
    bullets: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, row: dict) -> SpotlightItem:
        return cls(
            id=row.get("id", "") or secrets.token_hex(4),
            name=row.get("name", "(unnamed)"),
            tagline=row.get("tagline", ""),
            description=row.get("description", ""),
            url=row.get("url", ""),
            logo_letter=(row.get("logo_letter") or row.get("name", "?")[:1]).upper(),
            gradient_from=row.get("gradient_from", "#6366f1"),
            gradient_to=row.get("gradient_to", "#8b5cf6"),
            accent_rgb=row.get("accent_rgb", "99,102,241"),
            pinned=bool(row.get("pinned", False)),
            featured_from=row.get("featured_from"),
            hook=row.get("hook", ""),
            hook_highlight=row.get("hook_highlight", ""),
            bullets=tuple(row.get("bullets") or ()),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "tagline": self.tagline,
            "description": self.description,
            "url": self.url,
            "logo_letter": self.logo_letter,
            "gradient_from": self.gradient_from,
            "gradient_to": self.gradient_to,
            "accent_rgb": self.accent_rgb,
            "pinned": self.pinned,
            "featured_from": self.featured_from,
            "hook": self.hook,
            "hook_highlight": self.hook_highlight,
            "bullets": list(self.bullets),
        }


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # Tolerate "Z" suffix used in the JSON file.
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _source_payload() -> dict:
    """Load the spotlight.json source-of-truth, tolerating absence."""
    if not DEFAULT_SOURCE.exists():
        return {"ttl_days": 7, "items": []}
    try:
        import json

        return json.loads(DEFAULT_SOURCE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("spotlight.json unreadable (%s); using empty list", exc)
        return {"ttl_days": 7, "items": []}


async def _overrides() -> list[dict]:
    data = await _s.read_json(OVERRIDES_FILE, default=[])
    return data if isinstance(data, list) else []


def _is_eligible(item: SpotlightItem, *, ttl_days: int, now: datetime) -> bool:
    if item.pinned:
        return True
    start = _parse_dt(item.featured_from)
    if start is None:
        return True  # missing dates fall back to "always visible"
    if start > now:
        return False
    return (now - start) <= timedelta(days=ttl_days)


async def list_items(*, include_ineligible: bool = False) -> list[SpotlightItem]:
    payload = _source_payload()
    ttl_days = int(payload.get("ttl_days", 7) or 7)
    src_items = [SpotlightItem.from_dict(r) for r in payload.get("items") or []]

    over_rows = await _overrides()
    overrides = {row.get("id"): SpotlightItem.from_dict(row) for row in over_rows if row.get("id")}

    # Merge — override wins on id, otherwise append unique overrides at the end.
    merged_ids = {item.id for item in src_items}
    merged: list[SpotlightItem] = [overrides.pop(item.id, item) for item in src_items]
    for item in overrides.values():
        merged.append(item)

    now = datetime.now(timezone.utc)
    if include_ineligible:
        return merged
    return [item for item in merged if _is_eligible(item, ttl_days=ttl_days, now=now)]


async def add_override(item: dict) -> SpotlightItem:
    rows = await _overrides()
    new_id = item.get("id") or f"spot_{secrets.token_hex(4)}"
    item = {**item, "id": new_id}
    rows = [row for row in rows if row.get("id") != new_id]
    rows.append(item)
    await _s.write_json(OVERRIDES_FILE, rows)
    return SpotlightItem.from_dict(item)


async def remove_override(item_id: str) -> bool:
    rows = await _overrides()
    new = [row for row in rows if row.get("id") != item_id]
    if len(new) == len(rows):
        return False
    await _s.write_json(OVERRIDES_FILE, new)
    return True


async def pick_one() -> SpotlightItem | None:
    """Return a single eligible spotlight item, weighted toward pinned anchors."""
    items = await list_items()
    if not items:
        return None
    weights = [3 if item.pinned else 1 for item in items]
    return random.choices(items, weights=weights, k=1)[0]


async def record_impression(item_id: str, *, source: str, tenant_id: str | None) -> None:
    await _s.append_jsonl(
        IMPRESSIONS_FILE,
        {
            "ts": _s.utcnow_iso(),
            "item_id": item_id,
            "source": source,
            "tenant_id": tenant_id or "default",
        },
    )


def impression_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in _s.iter_jsonl(IMPRESSIONS_FILE):
        cid = entry.get("item_id")
        if not cid:
            continue
        counts[cid] = counts.get(cid, 0) + 1
    return counts


def recent_impressions(limit: int = 50) -> list[dict]:
    rows = list(_s.iter_jsonl(IMPRESSIONS_FILE))
    return rows[-limit:][::-1]


def source_path() -> Path:
    return DEFAULT_SOURCE
