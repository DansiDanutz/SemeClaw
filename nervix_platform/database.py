"""Simple JSON-file database for NERVIX platform."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class JSONEncoder(json.JSONEncoder):
    """Handle datetime serialization."""

    def default(self, o: Any) -> Any:
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


@dataclass
class Database:
    """JSON-file backed database."""

    path: Path
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"members": {}, "projects": {}, "transactions": [], "ad_plays": []}
        with open(self.path) as f:
            return json.load(f)

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(data, f, cls=JSONEncoder, indent=2)

    # Members
    def get_member(self, member_id: str) -> dict | None:
        with self._lock:
            data = self._load()
            return data["members"].get(member_id)

    def get_member_by_email(self, email: str) -> dict | None:
        with self._lock:
            data = self._load()
            for m in data["members"].values():
                if m["email"] == email:
                    return m
            return None

    def create_member(self, member: dict) -> dict:
        with self._lock:
            data = self._load()
            data["members"][member["id"]] = member
            self._save(data)
            return member

    def update_member(self, member_id: str, updates: dict) -> dict | None:
        with self._lock:
            data = self._load()
            m = data["members"].get(member_id)
            if not m:
                return None
            m.update(updates)
            self._save(data)
            return m

    # Projects
    def get_project(self, project_id: str) -> dict | None:
        with self._lock:
            data = self._load()
            return data["projects"].get(project_id)

    def list_projects(self, member_id: str | None = None) -> list[dict]:
        with self._lock:
            data = self._load()
            projects = list(data["projects"].values())
            if member_id:
                projects = [p for p in projects if p["member_id"] == member_id]
            return projects

    def create_project(self, project: dict) -> dict:
        with self._lock:
            data = self._load()
            data["projects"][project["id"]] = project
            self._save(data)
            return project

    def update_project(self, project_id: str, updates: dict) -> dict | None:
        with self._lock:
            data = self._load()
            p = data["projects"].get(project_id)
            if not p:
                return None
            p.update(updates)
            self._save(data)
            return p

    # Transactions
    def list_transactions(self, member_id: str | None = None) -> list[dict]:
        with self._lock:
            data = self._load()
            txs = data["transactions"]
            if member_id:
                txs = [t for t in txs if t["member_id"] == member_id]
            return list(reversed(txs))

    def add_transaction(self, tx: dict, *, update_credits: bool = True) -> dict:
        with self._lock:
            data = self._load()
            data["transactions"].append(tx)
            if update_credits:
                m = data["members"].get(tx["member_id"])
                if m:
                    m["credits"] = m.get("credits", 0) + tx["amount"]
            self._save(data)
            return tx

    # Ad plays
    def add_ad_play(self, event: dict) -> dict:
        with self._lock:
            data = self._load()
            data["ad_plays"].append(event)
            # Update project counters
            p = data["projects"].get(event["project_id"])
            if p:
                p["ad_plays"] = p.get("ad_plays", 0) + 1
                if event.get("completed"):
                    p["clicks"] = p.get("clicks", 0) + 1
            self._save(data)
            return event

    def try_debit_credits(self, member_id: str, amount: int) -> bool:
        """Atomically check balance and debit. Returns True if successful."""
        with self._lock:
            data = self._load()
            m = data["members"].get(member_id)
            if not m or m.get("credits", 0) < amount:
                return False
            m["credits"] = m.get("credits", 0) - amount
            self._save(data)
            return True

    def get_ad_plays(self, project_id: str | None = None) -> list[dict]:
        with self._lock:
            data = self._load()
            plays = data["ad_plays"]
            if project_id:
                plays = [e for e in plays if e["project_id"] == project_id]
            return plays


# Singleton instance
db = Database(Path(__file__).parent / "data" / "nervix.json")
