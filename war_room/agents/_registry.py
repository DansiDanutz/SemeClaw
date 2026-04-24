"""
war_room.agents._registry — Single source of truth for agent definitions.

Loads every .md file in war_room/agents/ (and adapters/) into a registry by
parsing the YAML frontmatter. The dashboard, the CLI, and the orchestrator
all read from here — there is no hardcoded list anywhere else.

Schema (frontmatter fields):
    id                  required, kebab-case unique key
    name                required, display name
    role                required, one-line role description
    core                bool — ships in the OSS demo set
    keywords            list[str] — auto-routing hints
    model_preference    list[str] — "<provider>:<model>" ordered fallback
    tools               list[str] — capability hooks the agent can call
    adapter             dict — present on adapter agents only

System prompt = everything after the closing `---` of the frontmatter.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_AGENTS_DIR = Path(__file__).parent
_ADAPTERS_DIR = _AGENTS_DIR / "adapters"


@dataclass
class Agent:
    id: str
    name: str
    role: str
    core: bool = False
    keywords: list[str] = field(default_factory=list)
    model_preference: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    adapter: dict[str, Any] | None = None
    system_prompt: str = ""
    source_path: str = ""

    @property
    def is_adapter(self) -> bool:
        return self.adapter is not None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "core": self.core,
            "keywords": self.keywords,
            "model_preference": self.model_preference,
            "tools": self.tools,
            "is_adapter": self.is_adapter,
            "source_path": self.source_path,
        }
        if self.adapter:
            d["adapter"] = self.adapter
        return d


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter. Returns (meta_dict, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    fm_text = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")
    try:
        import yaml  # type: ignore
        meta = yaml.safe_load(fm_text) or {}
    except Exception:
        # Tiny fallback parser — handles flat key:value, lists, nested one level
        meta = _tiny_yaml(fm_text)
    return (meta if isinstance(meta, dict) else {}), body


def _tiny_yaml(s: str) -> dict[str, Any]:
    """Minimal YAML for environments without PyYAML installed."""
    out: dict[str, Any] = {}
    cur_key: str | None = None
    cur_list: list[Any] | None = None
    cur_dict: dict[str, Any] | None = None
    for raw in s.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("  - "):
            (cur_list if cur_list is not None else []).append(raw[4:].strip())
            continue
        if raw.startswith("  ") and ":" in raw and cur_dict is not None:
            k, v = raw.strip().split(":", 1)
            cur_dict[k.strip()] = v.strip().strip('"').strip("'")
            continue
        if ":" in raw:
            k, v = raw.split(":", 1)
            k, v = k.strip(), v.strip()
            if not v:
                # next lines are list or dict
                cur_list, cur_dict = [], {}
                out[k] = cur_list  # default to list; swap to dict on first nested k:v
                cur_key = k
            elif v.startswith("[") and v.endswith("]"):
                out[k] = [x.strip().strip('"').strip("'") for x in v[1:-1].split(",") if x.strip()]
            elif v.lower() in ("true", "false"):
                out[k] = v.lower() == "true"
            else:
                out[k] = v.strip('"').strip("'")
        # If we collected dict-style nested, swap
        if cur_key and cur_dict and cur_list == []:
            out[cur_key] = cur_dict
            cur_list = None
    return out


def _load_one(path: Path) -> Agent | None:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("agents: cannot read %s: %s", path.name, e)
        return None
    meta, body = _parse_frontmatter(text)
    if not meta.get("id") or not meta.get("name"):
        logger.warning("agents: %s missing id/name in frontmatter — skipping", path.name)
        return None
    return Agent(
        id=str(meta["id"]),
        name=str(meta["name"]),
        role=str(meta.get("role", "")),
        core=bool(meta.get("core", False)),
        keywords=list(meta.get("keywords", []) or []),
        model_preference=list(meta.get("model_preference", []) or []),
        tools=list(meta.get("tools", []) or []),
        adapter=meta.get("adapter") if isinstance(meta.get("adapter"), dict) else None,
        system_prompt=body.strip(),
        source_path=str(path.relative_to(_AGENTS_DIR.parent.parent)) if _AGENTS_DIR.parent.parent in path.parents else str(path),
    )


_cache: dict[str, Agent] | None = None


def load_all(force: bool = False) -> dict[str, Agent]:
    """Load every agent .md file. Cached after first call."""
    global _cache
    if _cache is not None and not force:
        return _cache
    out: dict[str, Agent] = {}
    paths = sorted(_AGENTS_DIR.glob("*.md"))
    if _ADAPTERS_DIR.exists():
        paths += sorted(_ADAPTERS_DIR.glob("*.md"))
    for p in paths:
        if p.name.startswith("_") or p.name.upper() == "README.MD":
            continue
        a = _load_one(p)
        if a is None:
            continue
        if a.id in out:
            logger.warning("agents: duplicate id %r in %s — keeping first", a.id, p.name)
            continue
        out[a.id] = a
    _cache = out
    return out


def get(agent_id: str) -> Agent | None:
    return load_all().get(agent_id)


def core_agents() -> list[Agent]:
    """The agents that ship in the OSS demo (Research, Writer, Scraping, Coder, Browser)."""
    return [a for a in load_all().values() if a.core]


def adapters() -> list[Agent]:
    return [a for a in load_all().values() if a.is_adapter]


def all_ids() -> set[str]:
    return set(load_all().keys())
