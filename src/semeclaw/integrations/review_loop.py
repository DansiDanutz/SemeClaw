"""Helpers for installing recurring repo review loops."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_REVIEW_LOOP_SCHEDULE = "0 9 * * 1-5"


@dataclass
class ReviewLoopInstallResult:
    """Result of installing a recurring review loop."""

    repo_path: Path
    cron_path: Path
    cron_id: str
    schedule: str
    prompt: str


def install_review_loop(
    workspace: Path,
    repo_path: Path,
    schedule: str = DEFAULT_REVIEW_LOOP_SCHEDULE,
    agent: str = "seme",
    force: bool = False,
) -> ReviewLoopInstallResult:
    """Install a recurring review loop cron for a target repo."""
    workspace = workspace.expanduser().resolve()
    repo = repo_path.expanduser().resolve()

    if not repo.exists():
        raise FileNotFoundError(f"Repo path does not exist: {repo}")
    if not repo.is_dir():
        raise NotADirectoryError(f"Repo path is not a directory: {repo}")

    crons_dir = workspace / "crons"
    crons_dir.mkdir(parents=True, exist_ok=True)

    slug = _slugify(repo.name)
    cron_id = f"review-loop-{slug}"
    cron_path = crons_dir / f"{cron_id}.yaml"

    if cron_path.exists() and not force:
        raise FileExistsError(f"Review loop already exists: {cron_path}")

    prompt = _build_prompt(repo)
    data = {
        "id": cron_id,
        "schedule": schedule,
        "agent": agent,
        "description": f"Recurring repo review loop for {repo}",
        "enabled": True,
        "prompt": prompt,
    }

    cron_path.write_text(yaml.dump(data, sort_keys=False))
    return ReviewLoopInstallResult(
        repo_path=repo,
        cron_path=cron_path,
        cron_id=cron_id,
        schedule=schedule,
        prompt=prompt,
    )


def format_review_loop_install(result: ReviewLoopInstallResult) -> str:
    """Render a concise human-readable install result."""
    return "\n".join(
        [
            f"Installed review loop for {result.repo_path}",
            f"Cron: {result.cron_id}",
            f"Schedule: {result.schedule}",
            f"File: {result.cron_path}",
        ]
    )


def _slugify(name: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")


def _build_prompt(repo: Path) -> str:
    return "\n".join(
        [
            f"Run the repository review loop for {repo}.",
            "",
            "0. Call get_skill with skill_id=repo-review-loop and follow it.",
            "1. Call pi_company_status for this repo path to confirm pi-company is configured.",
            "2. Call github_pr_review_bundle for this repo path.",
            "   - If there is an open PR, use the PR bundle.",
            "   - If there is no open PR, the tool will fall back to a local diff review bundle.",
            "3. Read the bundle file that was created.",
            "4. Produce a short review with findings first, focused on correctness, regressions, missing tests, rollout risk, and ownership gaps.",
            "5. Save a short operational summary with memory_save under axis=projects and key matching the repo name plus '-review-loop'.",
            "6. In that summary include:",
            "   - whether this was a PR review bundle or a local diff bundle",
            "   - the bundle file path",
            "   - the concrete findings, or say 'no major findings'",
            "   - the highest-risk areas to inspect next",
            "7. If there are meaningful risks or blockers, send a concise telegram_notify message with the repo, bundle path, and top findings.",
            "8. Return a concise summary of what bundle was created, where it lives, and the top findings.",
        ]
    )
