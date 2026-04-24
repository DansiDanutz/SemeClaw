"""
SemeClaw Onboarding — Discover agents and tools installed on the device.

Scans for:
  - OpenClaw (source, logger, dot-dir)
  - Paperclip (running service, config files)
  - Hermes (agent binaries, config, dot-dir)
  - Multica (binaries, config, dot-dir)
  - Generic agents via manifest files in well-known locations

Returns a normalized list of discovered capabilities so the dashboard
can sync them into the War Room roster.
"""

from __future__ import annotations

import json
import logging
import platform
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("semeclaw.onboarding")

SYSTEM = platform.system().lower()
HOME = Path.home()

# ---------------------------------------------------------------------------
# Dataclass for a discovered agent / tool
# ---------------------------------------------------------------------------


@dataclass
class DiscoveredAgent:
    id: str
    name: str
    kind: str  # "agent" | "tool" | "platform"
    version: str | None = None
    path: str | None = None
    url: str | None = None
    status: str = "unknown"  # "running" | "installed" | "config_only" | "unknown"
    meta: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_tcp_port(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _run_cmd(cmd: list[str], cwd: Path | None = None) -> str | None:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
            shell=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _find_dirs(parent: Path, pattern: str, max_depth: int = 2) -> list[Path]:
    """Find directories matching `pattern` under `parent`."""
    found: list[Path] = []
    if not parent.exists():
        return found
    try:
        for p in parent.rglob(pattern):
            if p.is_dir():
                found.append(p)
                if len(found) >= 10:
                    break
    except PermissionError:
        pass
    return found


# ---------------------------------------------------------------------------
# OpenClaw
# ---------------------------------------------------------------------------


def discover_openclaw() -> list[DiscoveredAgent]:
    found: list[DiscoveredAgent] = []

    # 1. Dot-dir ~/.openclaw
    dot_dir = HOME / ".openclaw"
    if dot_dir.exists():
        found.append(
            DiscoveredAgent(
                id="openclaw",
                name="OpenClaw",
                kind="agent",
                path=str(dot_dir),
                status="installed",
                meta={"source": "dot-dir"},
            )
        )

    # 2. Logger package
    logger_dir = HOME / "openclaw-logger"
    if logger_dir.exists():
        version: str | None = None
        pkg_json = logger_dir / "package.json"
        if pkg_json.exists():
            data = _read_json(pkg_json)
            version = data.get("version") if data else None
        found.append(
            DiscoveredAgent(
                id="openclaw-logger",
                name="OpenClaw Logger",
                kind="tool",
                path=str(logger_dir),
                version=version,
                status="installed",
                meta={"source": "logger-package"},
            )
        )

    # 3. Source directory (common dev layout)
    source_candidates = [
        HOME / "Desktop" / "coding" / "danslab" / "openclaw",
        HOME / "dev" / "openclaw",
        HOME / "projects" / "openclaw",
    ]
    for src in source_candidates:
        if src.exists() and src.is_dir() and any(src.iterdir()):
            # Try to detect version from package.json or git
            ver = None
            pkg = src / "package.json"
            if pkg.exists():
                data = _read_json(pkg)
                ver = data.get("version") if data else None
            found.append(
                DiscoveredAgent(
                    id="openclaw-source",
                    name="OpenClaw (source)",
                    kind="agent",
                    path=str(src),
                    version=ver,
                    status="installed",
                    meta={"source": "source-dir"},
                )
            )
            break

    return found


# ---------------------------------------------------------------------------
# Paperclip
# ---------------------------------------------------------------------------


def discover_paperclip() -> list[DiscoveredAgent]:
    found: list[DiscoveredAgent] = []

    # 1. Running service on default port
    running = _check_tcp_port("127.0.0.1", 3100)
    url = "http://127.0.0.1:3100" if running else None

    # 2. Config file in war_room
    cfg = HOME / "SemeClaw" / "war_room" / "config.json"
    if not cfg.exists():
        cfg = Path("war_room/config.json")

    has_config = cfg.exists()

    # 3. Mock state file (means Paperclip adapter has been used)
    mock_state = Path("war_room/.paperclip_mock.json")
    has_mock = mock_state.exists()

    if running or has_config or has_mock:
        meta: dict[str, Any] = {}
        if has_config:
            data = _read_json(cfg)
            if data:
                meta["config"] = data
        if has_mock:
            meta["mock_state"] = str(mock_state)
        found.append(
            DiscoveredAgent(
                id="paperclip",
                name="Paperclip",
                kind="platform",
                path=str(cfg) if has_config else (str(mock_state) if has_mock else None),
                url=url,
                status="running" if running else ("mock" if has_mock else "config_only"),
                meta=meta,
            )
        )

    return found


# ---------------------------------------------------------------------------
# Hermes
# ---------------------------------------------------------------------------


def discover_hermes() -> list[DiscoveredAgent]:
    found: list[DiscoveredAgent] = []

    candidates = [
        HOME / ".hermes",
        HOME / "hermes",
        HOME / "Hermes",
        HOME / "Desktop" / "coding" / "danslab" / "hermes",
    ]

    for cand in candidates:
        if cand.exists():
            version = None
            pkg = cand / "package.json"
            if pkg.exists():
                data = _read_json(pkg)
                version = data.get("version") if data else None
            found.append(
                DiscoveredAgent(
                    id="hermes",
                    name="Hermes",
                    kind="agent",
                    path=str(cand),
                    version=version,
                    status="installed",
                    meta={"source": "directory"},
                )
            )
            break

    # Also check for a `hermes` executable on PATH
    if SYSTEM == "windows":
        out = _run_cmd(["where", "hermes"])
    else:
        out = _run_cmd(["which", "hermes"])
    if out:
        # If we didn't already find a directory entry, add one
        if not any(a.id == "hermes" for a in found):
            found.append(
                DiscoveredAgent(
                    id="hermes",
                    name="Hermes",
                    kind="agent",
                    path=out.splitlines()[0],
                    status="installed",
                    meta={"source": "PATH"},
                )
            )

    return found


# ---------------------------------------------------------------------------
# Multica
# ---------------------------------------------------------------------------


def discover_multica() -> list[DiscoveredAgent]:
    found: list[DiscoveredAgent] = []

    # 1. Running service
    running = _check_tcp_port("127.0.0.1", 3200)
    url = "http://127.0.0.1:3200" if running else None

    # 2. Local state directory
    multica_dir = HOME / ".multica"
    has_dir = multica_dir.exists()

    # 3. Source / project directories
    candidates = [
        HOME / "multica",
        HOME / "Multica",
        HOME / "Desktop" / "coding" / "danslab" / "multica",
    ]
    src_dir = None
    for cand in candidates:
        if cand.exists():
            src_dir = cand
            break

    if running or has_dir or src_dir:
        version = None
        if src_dir:
            pkg = src_dir / "package.json"
            pyproject = src_dir / "pyproject.toml"
            if pkg.exists():
                data = _read_json(pkg)
                version = data.get("version") if data else None
            elif pyproject.exists():
                for line in pyproject.read_text(encoding="utf-8").splitlines():
                    if line.startswith("version"):
                        version = line.split("=")[-1].strip().strip('"').strip("'")
                        break
        found.append(
            DiscoveredAgent(
                id="multica",
                name="Multica",
                kind="platform",
                path=str(src_dir) if src_dir else (str(multica_dir) if has_dir else None),
                url=url,
                version=version,
                status="running" if running else "installed",
                meta={"source": "directory" if src_dir else "local-state"},
            )
        )

    return found


# ---------------------------------------------------------------------------
# Generic manifest scanner
# ---------------------------------------------------------------------------

_MANIFEST_NAMES = {"agent.manifest.json", "manifest.json", "agent.yaml", "agent.yml"}
_SCAN_ROOTS: list[Path] = [
    HOME / "dev",
    HOME / "projects",
    HOME / "Desktop" / "coding",
    HOME / "agents",
    HOME / "workspace",
]


def discover_generic_agents() -> list[DiscoveredAgent]:
    """Scan well-known directories for agent manifest files."""
    found: list[DiscoveredAgent] = []
    seen_paths: set[Path] = set()

    for root in _SCAN_ROOTS:
        if not root.exists():
            continue
        try:
            for manifest_name in _MANIFEST_NAMES:
                for p in root.rglob(manifest_name):
                    if p in seen_paths:
                        continue
                    seen_paths.add(p)
                    agent_dir = p.parent
                    # Skip if inside node_modules or .git
                    if "node_modules" in str(agent_dir) or ".git" in str(agent_dir):
                        continue

                    data = _read_json(p) if p.suffix == ".json" else None
                    if data is None:
                        # Minimal YAML-like parsing for agent.yaml
                        text = p.read_text(encoding="utf-8")
                        data = {}
                        for line in text.splitlines():
                            if ":" in line and not line.strip().startswith("#"):
                                k, v = line.split(":", 1)
                                data[k.strip()] = v.strip().strip('"').strip("'")

                    name = data.get("name") or data.get("agent_name") or agent_dir.name
                    agent_id = data.get("id") or data.get("agent_id") or agent_dir.name.lower().replace(" ", "-")

                    found.append(
                        DiscoveredAgent(
                            id=agent_id,
                            name=name,
                            kind="agent",
                            path=str(agent_dir),
                            version=data.get("version"),
                            status="installed",
                            meta={"manifest": str(p), "source": "generic-scan"},
                        )
                    )
        except PermissionError:
            pass

    return found


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def discover_all() -> list[DiscoveredAgent]:
    """Run every discovery scanner and return a unified list."""
    results: list[DiscoveredAgent] = []
    results.extend(discover_openclaw())
    results.extend(discover_paperclip())
    results.extend(discover_hermes())
    results.extend(discover_multica())
    results.extend(discover_generic_agents())
    logger.info("Onboarding discovery found %d agents/tools", len(results))
    return results


def to_json(agents: list[DiscoveredAgent]) -> list[dict[str, Any]]:
    """Serialize discovered agents to plain JSON-friendly dicts."""
    return [
        {
            "id": a.id,
            "name": a.name,
            "kind": a.kind,
            "version": a.version,
            "path": a.path,
            "url": a.url,
            "status": a.status,
            "meta": a.meta,
        }
        for a in agents
    ]
