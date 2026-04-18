"""
sentinel/tripwire.py — File integrity daemon.

Watches critical config files for unexpected changes.
On drift: Telegram alert with diff → vault backup.
On first run: builds baseline.
"""
import asyncio
import gzip
import hashlib
import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sentinel.alerts import send_alert
from sentinel.thresholds import (
    TRIPWIRE_INTERVAL_SEC,
    TRIPWIRE_BASELINE_PATH,
    TRIPWIRE_VAULT_DIR,
    TRIPWIRE_VAULT_DAYS,
    WATCHED_PATHS_MAC,
)

logger = logging.getLogger("sentinel.tripwire")

BASELINE_PATH = Path(TRIPWIRE_BASELINE_PATH)
VAULT_DIR     = Path(TRIPWIRE_VAULT_DIR)

# Paths approved by Dan — drift is expected
_approved_shas: set[str] = set()


def _expand_paths(paths: list[str]) -> list[Path]:
    result = []
    for p in paths:
        expanded = Path(os.path.expanduser(p))
        if "*" in str(expanded):
            result.extend(Path(expanded.parent).glob(expanded.name))
        elif expanded.exists():
            result.append(expanded)
    return result


def _sha256(path: Path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _snapshot(paths: list[Path]) -> dict[str, str]:
    """Return {path_str: sha256} for all watchable files."""
    snap = {}
    for p in paths:
        sha = _sha256(p)
        if sha:
            snap[str(p)] = sha
    return snap


def _diff_head(path: Path, lines: int = 20) -> str:
    """Return first N lines of a file for the alert message."""
    try:
        text = path.read_text(errors="replace")
        head = "\n".join(text.splitlines()[:lines])
        return head
    except Exception as e:
        return f"<could not read: {e}>"


def _vault_store(path: Path, sha: str) -> Path:
    """Copy file to vault as {sha[:12]}.gz, return vault path."""
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    dest = VAULT_DIR / f"{sha[:12]}.gz"
    if not dest.exists():
        with open(path, "rb") as src, gzip.open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst)
    return dest


def _vault_purge():
    """Remove vault entries older than TRIPWIRE_VAULT_DAYS."""
    cutoff = time.time() - TRIPWIRE_VAULT_DAYS * 86400
    for f in VAULT_DIR.glob("*.gz"):
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
            logger.info("Vault purged: %s", f.name)


def load_baseline() -> dict[str, str]:
    if BASELINE_PATH.exists():
        try:
            return json.loads(BASELINE_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_baseline(snap: dict[str, str]):
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(snap, indent=2))


def approve(sha_prefix: str) -> bool:
    """Mark a SHA as approved (suppresses further alerts for that sha)."""
    _approved_shas.add(sha_prefix[:12])
    logger.info("Approved sha prefix: %s", sha_prefix[:12])
    return True


def update_baseline_for(path_str: str, new_sha: str):
    """After approval: update baseline so it's no longer flagged."""
    baseline = load_baseline()
    baseline[path_str] = new_sha
    save_baseline(baseline)
    logger.info("Baseline updated: %s → %s", path_str, new_sha[:12])


async def run_check(paths: list[Path]) -> list[dict]:
    """
    Compare current snapshot against baseline.
    Returns list of drift events (empty = clean).
    """
    baseline = load_baseline()
    current  = _snapshot(paths)

    if not baseline:
        # First run — establish baseline
        save_baseline(current)
        logger.info("Tripwire baseline established (%d files)", len(current))
        await send_alert(
            f"Tripwire baseline established: monitoring *{len(current)} files*",
            level="✅",
            dedup_key="tripwire-baseline",
            dedup_sec=3600,
        )
        return []

    drifts = []

    # Check for changed or deleted files
    for path_str, old_sha in baseline.items():
        new_sha = current.get(path_str)
        if new_sha is None:
            drifts.append({
                "type": "deleted",
                "path": path_str,
                "old_sha": old_sha,
                "new_sha": None,
            })
        elif new_sha != old_sha:
            if new_sha[:12] in _approved_shas:
                logger.debug("Drift approved: %s", path_str)
                update_baseline_for(path_str, new_sha)
                continue
            drifts.append({
                "type": "modified",
                "path": path_str,
                "old_sha": old_sha,
                "new_sha": new_sha,
            })

    # Check for new files
    for path_str, new_sha in current.items():
        if path_str not in baseline:
            drifts.append({
                "type": "new",
                "path": path_str,
                "old_sha": None,
                "new_sha": new_sha,
            })

    return drifts


async def handle_drifts(drifts: list[dict]):
    """Send Telegram alerts and vault backups for each drift."""
    for drift in drifts:
        path  = Path(drift["path"])
        dtype = drift["type"]
        new_sha = drift.get("new_sha")
        old_sha = drift.get("old_sha")

        # Vault the new version
        vault_path = None
        if new_sha and path.exists():
            vault_path = _vault_store(path, new_sha)

        # Build alert
        label = path.name
        sha_info = f"`{old_sha[:10] if old_sha else 'N/A'}` → `{new_sha[:10] if new_sha else 'DELETED'}`"
        head    = _diff_head(path) if path.exists() else "(file deleted)"

        msg = (
            f"*{dtype.upper()}*: `{label}`\n"
            f"SHA: {sha_info}\n"
            f"Full path: `{drift['path']}`\n"
            f"Reply `/tripwire approve {new_sha[:12] if new_sha else ''}` to accept this change."
        )
        dedup_key = f"tw-{drift['path']}-{new_sha[:12] if new_sha else 'del'}"
        await send_alert(msg, level="🔴" if dtype != "new" else "🟡", dedup_key=dedup_key, dedup_sec=300)
        logger.warning("Tripwire drift [%s]: %s", dtype, drift["path"])


async def tripwire_loop():
    """Background loop: check every TRIPWIRE_INTERVAL_SEC seconds."""
    paths = _expand_paths(WATCHED_PATHS_MAC)
    logger.info("Tripwire watching %d files", len(paths))

    while True:
        try:
            drifts = await run_check(paths)
            if drifts:
                await handle_drifts(drifts)
            _vault_purge()
        except Exception as e:
            logger.error("Tripwire loop error: %s", e)

        await asyncio.sleep(TRIPWIRE_INTERVAL_SEC)
