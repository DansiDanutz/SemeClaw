"""
Obsidian adapter — bidirectional sync with an Obsidian vault.

Obsidian does not expose a native REST API by default. This adapter uses
direct vault file I/O (read/write markdown files) as the primary mode, with
an optional fallback to the Obsidian Local REST API plugin if available.

Vault path: OBSIDIAN_VAULT_PATH (default ~/Obsidian/Vault/)
War Room folder: War Room/ inside the vault

Each note is a markdown file with YAML frontmatter. Notes tagged as tasks
have frontmatter like:
  ---
  status: todo
  assignee: war_room
  tags: [task, war-room]
  ---

When the vault path is not available, the adapter falls back to a local
mirror directory (~/.semeclaw/obsidian_mirror/).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

try:
    from war_room.adapters.base import Adapter, AdapterHealth
except ImportError:
    from adapters.base import Adapter, AdapterHealth

logger = logging.getLogger("war_room.adapters.obsidian")

DEFAULT_VAULT = Path.home() / "Obsidian" / "Vault"
OBSIDIAN_MIRROR = Path.home() / ".semeclaw" / "obsidian_mirror"
WAR_ROOM_FOLDER = "War Room"

# Frontmatter regex
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _default_vault_path() -> Path:
    env = os.environ.get("OBSIDIAN_VAULT_PATH", "")
    if env:
        return Path(env).expanduser()
    return DEFAULT_VAULT.expanduser()


def _extract_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Extract YAML frontmatter and body from markdown text."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        import yaml
        front = yaml.safe_load(m.group(1)) or {}
    except Exception:
        front = {}
    body = text[m.end():]
    return front, body


def _build_note(title: str, body: str, frontmatter: dict[str, Any] | None = None) -> str:
    """Build a markdown note with YAML frontmatter."""
    fm = frontmatter or {}
    fm.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    try:
        import yaml
        fm_text = yaml.safe_dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
    except Exception:
        fm_text = json.dumps(fm, indent=2)
    return f"---\n{fm_text}---\n\n# {title}\n\n{body}\n"


class ObsidianAdapter(Adapter):
    """Obsidian vault adapter with file-based sync."""

    def __init__(
        self,
        vault_path: str | Path | None = None,
        api_url: str | None = None,
    ) -> None:
        super().__init__("obsidian", "")
        self.vault_path = Path(vault_path) if vault_path else _default_vault_path()
        self.api_url = api_url or os.environ.get("OBSIDIAN_API_URL", "")
        self._mirror = OBSIDIAN_MIRROR
        self._client: httpx.AsyncClient | None = None
        self._load_mirror_state()

    def _war_room_dir(self) -> Path:
        return self.vault_path / WAR_ROOM_FOLDER

    def _mirror_dir(self) -> Path:
        return self._mirror / WAR_ROOM_FOLDER

    def _load_mirror_state(self) -> None:
        self._mirror.mkdir(parents=True, exist_ok=True)

    async def _ensure_client(self) -> httpx.AsyncClient | None:
        if self.api_url and self._client is None:
            self._client = httpx.AsyncClient(base_url=self.api_url.rstrip("/"), timeout=10.0)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    def _vault_available(self) -> bool:
        return self.vault_path.exists() and self.vault_path.is_dir()

    def _is_api_reachable(self) -> bool:
        if not self.api_url:
            return False
        try:
            import socket
            parsed = httpx.URL(self.api_url)
            host = parsed.host
            port = parsed.port or 80
            with socket.create_connection((host, port), timeout=2.0):
                return True
        except OSError:
            return False

    # ------------------------------------------------------------------ #
    #  Health
    # ------------------------------------------------------------------ #

    async def health(self) -> AdapterHealth:
        if self._vault_available():
            return AdapterHealth(
                name="obsidian",
                reachable=True,
                meta={"vault": str(self.vault_path), "mode": "filesystem"},
            )
        if self._is_api_reachable():
            return AdapterHealth(
                name="obsidian",
                reachable=True,
                meta={"api": self.api_url, "mode": "rest"},
            )
        return AdapterHealth(
            name="obsidian",
            reachable=False,
            error=f"Vault not found at {self.vault_path}",
            meta={"mirror": str(self._mirror), "mode": "mirror"},
        )

    # ------------------------------------------------------------------ #
    #  Read (sync TO war room)
    # ------------------------------------------------------------------ #

    async def sync_to_war_room(self) -> list[dict[str, Any]]:
        agents = await self.list_agents()
        tasks = await self.list_tasks()
        return [{"type": "agent", "data": a} for a in agents] + [{"type": "task", "data": t} for t in tasks]

    async def list_agents(self) -> list[dict[str, Any]]:
        # Obsidian doesn't have agents per se; return a single vault agent
        return [
            {
                "id": "obsidian",
                "name": "Obsidian Vault",
                "role": "Knowledge Base",
                "status": "active" if self._vault_available() else "offline",
                "platform": self.name,
                "vault_path": str(self.vault_path),
            }
        ]

    async def list_tasks(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        notes = await self._scan_notes()
        tasks = [self._normalize_task(n) for n in notes if self._is_task_note(n)]
        if agent_id:
            tasks = [t for t in tasks if t.get("assignee") == agent_id]
        return tasks

    async def _scan_notes(self) -> list[dict[str, Any]]:
        """Scan the War Room folder for markdown notes."""
        notes: list[dict] = []
        target = self._war_room_dir() if self._vault_available() else self._mirror_dir()
        if not target.exists():
            return notes
        for path in target.rglob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")
                front, body = _extract_frontmatter(text)
                notes.append({
                    "filename": path.name,
                    "path": str(path.relative_to(target)),
                    "title": front.get("title", path.stem),
                    "frontmatter": front,
                    "body": body.strip(),
                })
            except Exception as e:
                logger.debug("Could not read note %s: %s", path, e)
        return notes

    def _is_task_note(self, note: dict[str, Any]) -> bool:
        front = note.get("frontmatter", {})
        tags = front.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        return "task" in tags or front.get("status") in ("todo", "in_progress", "done", "blocked")

    # ------------------------------------------------------------------ #
    #  Write (sync FROM war room)
    # ------------------------------------------------------------------ #

    async def sync_from_war_room(self, changes: list[dict[str, Any]]) -> bool:
        ok = True
        for change in changes:
            ctype = change.get("type")
            if ctype in ("create_note", "create_task", "create_issue"):
                ok = ok and bool(await self.create_note(**change.get("payload", {})))
            elif ctype in ("update_note", "update_task"):
                ok = ok and await self.update_note(
                    change.get("title", ""),
                    change.get("content", ""),
                    change.get("frontmatter", {}),
                )
        return ok

    async def create_note(
        self,
        title: str,
        content: str = "",
        folder: str | None = None,
        tags: list[str] | None = None,
        frontmatter: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        fm = dict(frontmatter or {})
        if tags:
            existing = fm.get("tags", [])
            if isinstance(existing, str):
                existing = [existing]
            fm["tags"] = list(set(existing + tags))
        fm.setdefault("title", title)

        note_text = _build_note(title, content, fm)
        target_dir = self._war_room_dir() if self._vault_available() else self._mirror_dir()
        if folder:
            target_dir = target_dir / folder
        target_dir.mkdir(parents=True, exist_ok=True)

        safe_name = "".join(c if c.isalnum() or c in "-_ " else "-" for c in title)
        safe_name = safe_name.strip().replace(" ", "-") or "note"
        path = target_dir / f"{safe_name}.md"

        try:
            path.write_text(note_text, encoding="utf-8")
            logger.info("Created Obsidian note: %s", path)
            return {
                "id": str(path),
                "title": title,
                "path": str(path),
                "platform": self.name,
            }
        except Exception as e:
            logger.error("Failed to write Obsidian note: %s", e)
            return None

    async def update_note(
        self,
        title: str,
        content: str = "",
        frontmatter: dict[str, Any] | None = None,
    ) -> bool:
        target_dir = self._war_room_dir() if self._vault_available() else self._mirror_dir()
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "-" for c in title)
        safe_name = safe_name.strip().replace(" ", "-") or "note"
        path = target_dir / f"{safe_name}.md"

        if not path.exists():
            logger.warning("Obsidian note not found for update: %s", path)
            return False

        try:
            old_text = path.read_text(encoding="utf-8")
            old_fm, _ = _extract_frontmatter(old_text)
            if frontmatter:
                old_fm.update(frontmatter)
            old_fm["updated_at"] = datetime.now(timezone.utc).isoformat()
            path.write_text(_build_note(title, content, old_fm), encoding="utf-8")
            logger.info("Updated Obsidian note: %s", path)
            return True
        except Exception as e:
            logger.error("Failed to update Obsidian note: %s", e)
            return False

    async def list_notes(self, folder: str | None = None) -> list[dict[str, Any]]:
        target_dir = self._war_room_dir() if self._vault_available() else self._mirror_dir()
        if folder:
            target_dir = target_dir / folder
        if not target_dir.exists():
            return []
        notes = []
        for path in target_dir.rglob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")
                front, body = _extract_frontmatter(text)
                notes.append({
                    "id": str(path),
                    "title": front.get("title", path.stem),
                    "path": str(path),
                    "frontmatter": front,
                    "body": body.strip(),
                    "platform": self.name,
                })
            except Exception:
                continue
        return notes

    # ------------------------------------------------------------------ #
    #  Normalize
    # ------------------------------------------------------------------ #

    def _normalize_task(self, raw: dict[str, Any]) -> dict[str, Any]:
        front = raw.get("frontmatter", {})
        tags = front.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        return {
            "id": raw.get("filename", raw.get("id", "unknown")),
            "title": raw.get("title", "Untitled"),
            "status": front.get("status", "open"),
            "assignee": front.get("assignee"),
            "priority": front.get("priority", "medium"),
            "description": raw.get("body", "")[:200],
            "tags": tags,
            "platform": self.name,
        }
