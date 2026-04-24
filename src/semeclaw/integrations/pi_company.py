"""Helpers for bridging SemeClaw workspaces with pi-company."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


PACKAGE_ENV_VAR = "SEMECLAW_PI_COMPANY_PACKAGE_PATH"


@dataclass
class PiCompanyStatus:
    """Status for pi-company integration within a target repo."""

    target_path: Path
    package_path: Path | None
    settings_path: Path
    context_path: Path
    package_configured: bool
    context_exists: bool


@dataclass
class PiCompanyBootstrapResult:
    """Result from bootstrapping pi-company in a target repo."""

    target_path: Path
    package_path: Path
    settings_path: Path
    context_path: Path
    package_added: bool
    context_written: bool


def candidate_package_paths(workspace: Path) -> list[Path]:
    """Return likely local locations for the pi-company package."""
    candidates: list[Path] = []

    env_path = os.getenv(PACKAGE_ENV_VAR)
    if env_path:
        candidates.append(Path(env_path).expanduser())

    current = workspace.resolve()
    parent = current.parent

    candidates.extend(
        [
            current / "packages" / "pi-company",
            parent / "pi-mono" / "packages" / "pi-company",
            parent / "packages" / "pi-company",
            # (removed developer-specific /Users/davidai/... fallback during prod hardening)
        ]
    )

    # Preserve order while deduplicating
    deduped: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser()
        if resolved in seen:
            continue
        deduped.append(resolved)
        seen.add(resolved)
    return deduped


def resolve_package_path(workspace: Path, package_path: str | None = None) -> Path | None:
    """Resolve the pi-company package path."""
    if package_path:
        candidate = Path(package_path).expanduser()
        return candidate if _is_package_dir(candidate) else None

    for candidate in candidate_package_paths(workspace):
        if _is_package_dir(candidate):
            return candidate
    return None


def get_status(target_path: Path, workspace: Path, package_path: str | None = None) -> PiCompanyStatus:
    """Inspect the pi-company integration status for a repo."""
    target = target_path.expanduser().resolve()
    resolved_package = resolve_package_path(workspace, package_path)
    settings_path = target / ".pi" / "settings.json"
    context_path = target / ".pi" / "company" / "context.md"

    configured = False
    if settings_path.exists() and resolved_package:
        settings = _load_settings(settings_path)
        configured = _settings_has_package(settings, resolved_package)

    return PiCompanyStatus(
        target_path=target,
        package_path=resolved_package,
        settings_path=settings_path,
        context_path=context_path,
        package_configured=configured,
        context_exists=context_path.exists(),
    )


def bootstrap_repo(
    target_path: Path,
    workspace: Path,
    package_path: str | None = None,
    company_name: str | None = None,
    force: bool = False,
) -> PiCompanyBootstrapResult:
    """Bootstrap pi-company into a target repository."""
    target = target_path.expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(f"Target path does not exist: {target}")
    if not target.is_dir():
        raise NotADirectoryError(f"Target path is not a directory: {target}")

    resolved_package = resolve_package_path(workspace, package_path)
    if resolved_package is None:
        raise FileNotFoundError(
            "Could not find pi-company package. "
            f"Set {PACKAGE_ENV_VAR} or pass an explicit package_path."
        )

    settings_path = target / ".pi" / "settings.json"
    context_path = target / ".pi" / "company" / "context.md"

    settings = _load_settings(settings_path)
    package_added = _ensure_package(settings, resolved_package)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")

    context_written = force or not context_path.exists()
    if context_written:
        context_path.parent.mkdir(parents=True, exist_ok=True)
        context_path.write_text(_render_context(target, workspace, company_name))

    return PiCompanyBootstrapResult(
        target_path=target,
        package_path=resolved_package,
        settings_path=settings_path,
        context_path=context_path,
        package_added=package_added,
        context_written=context_written,
    )


def format_status(status: PiCompanyStatus) -> str:
    """Render a human-readable status string."""
    lines = [
        f"Target: {status.target_path}",
        f"Package path: {status.package_path or 'not found'}",
        f"Settings: {'present' if status.settings_path.exists() else 'missing'} ({status.settings_path})",
        f"Context: {'present' if status.context_exists else 'missing'} ({status.context_path})",
        f"Package configured: {'yes' if status.package_configured else 'no'}",
    ]
    if status.package_path is None:
        lines.append(
            f"Hint: set {PACKAGE_ENV_VAR} or place pi-company at ../pi-mono/packages/pi-company"
        )
    return "\n".join(lines)


def format_bootstrap_result(result: PiCompanyBootstrapResult) -> str:
    """Render a human-readable bootstrap result string."""
    return "\n".join(
        [
            f"Bootstrapped pi-company for {result.target_path}",
            f"Package: {result.package_path}",
            f"Updated settings: {result.settings_path}",
            f"Context file: {result.context_path}",
            f"Package entry added: {'yes' if result.package_added else 'already present'}",
            f"Context written: {'yes' if result.context_written else 'kept existing file'}",
        ]
    )


def _is_package_dir(path: Path) -> bool:
    package_json = path / "package.json"
    if not package_json.exists():
        return False
    try:
        data = json.loads(package_json.read_text())
    except Exception:
        return False
    return data.get("name") == "@mariozechner/pi-company"


def _load_settings(settings_path: Path) -> dict:
    if not settings_path.exists():
        return {}
    try:
        return json.loads(settings_path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {settings_path}: {exc}") from exc


def _settings_has_package(settings: dict, package_path: Path) -> bool:
    package_entries = settings.get("packages", [])
    target = str(package_path.resolve())
    for entry in package_entries:
        if isinstance(entry, str) and Path(entry).expanduser().resolve() == package_path.resolve():
            return True
        if isinstance(entry, dict):
            source = entry.get("source")
            if isinstance(source, str) and Path(source).expanduser().resolve() == package_path.resolve():
                return True
    return False


def _ensure_package(settings: dict, package_path: Path) -> bool:
    if _settings_has_package(settings, package_path):
        return False
    packages = settings.setdefault("packages", [])
    packages.append(str(package_path.resolve()))
    return True


def _render_context(target: Path, workspace: Path, company_name: str | None) -> str:
    resolved_company_name = company_name or workspace.name
    return "\n".join(
        [
            f"# {resolved_company_name} Repo Context",
            "",
            "This file was bootstrapped by SemeClaw to give `pi-company` durable repo-local context.",
            "",
            "## Repo",
            f"- Path: {target}",
            "- Mission: Fill in what this repository is responsible for.",
            "- Owners: Fill in the engineering owner and operational owner.",
            "",
            "## Systems Of Record",
            "- Source control: Fill in GitHub or other repo details.",
            "- Tickets: Fill in Linear, Jira, or other planning system.",
            "- Communication: Fill in Slack or other primary channel.",
            "- Runbooks: Link the docs or directories Pi should trust first.",
            "",
            "## Delivery Rules",
            "- Environments: Document local, staging, and production environments.",
            "- Release process: Describe how changes move to users.",
            "- Verification: Describe the minimum checks before merge or deploy.",
            "",
            "## Risk Boundaries",
            "- Protected files and directories:",
            "  - secrets and env files",
            "  - infrastructure state",
            "  - deployment configs",
            "- Commands requiring human confirmation:",
            "  - deploys",
            "  - database migrations",
            "  - destructive shell commands",
            "",
            "## Incident Process",
            "- Severity model:",
            "- Escalation channel:",
            "- First status update expectations:",
            "- Rollback guidance:",
            "",
            "## Notes For Pi",
            "- Prefer reading this file before proposing workflow-specific steps.",
            "- Update this file as the repo operating model becomes clearer.",
            "",
        ]
    )
