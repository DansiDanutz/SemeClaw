"""GitHub pull request helpers backed by the gh CLI."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_DIFF_CHARS = 20000


@dataclass
class GitHubPrStatus:
    """Status summary for a GitHub pull request."""

    repo_path: Path
    number: int
    title: str
    url: str
    head_ref: str
    base_ref: str
    state: str
    review_decision: str | None
    merge_state_status: str | None
    changed_files: int
    additions: int
    deletions: int
    author: str | None
    files: list[dict[str, Any]]
    checks: list[dict[str, Any]]


@dataclass
class GitHubPrReviewBundle:
    """Review bundle written for pi-company driven PR review."""

    status: GitHubPrStatus
    output_path: Path
    bundle_written: bool


@dataclass
class LocalReviewBundle:
    """Review bundle for a repo without an open PR."""

    repo_path: Path
    branch_name: str
    output_path: Path
    bundle_written: bool


def get_pr_status(repo_path: Path, pr_ref: str | None = None) -> GitHubPrStatus:
    """Fetch PR metadata and check state using gh."""
    repo = repo_path.expanduser().resolve()
    pr_json = _gh_json(
        [
            "pr",
            "view",
            *([pr_ref] if pr_ref else []),
            "--json",
            ",".join(
                [
                    "number",
                    "title",
                    "url",
                    "headRefName",
                    "baseRefName",
                    "state",
                    "reviewDecision",
                    "mergeStateStatus",
                    "changedFiles",
                    "additions",
                    "deletions",
                    "author",
                    "files",
                ]
            ),
        ],
        cwd=repo,
    )

    checks = _gh_json(
        [
            "pr",
            "checks",
            *([pr_ref] if pr_ref else [str(pr_json["number"])]),
            "--json",
            "name,state,bucket,workflow,link",
        ],
        cwd=repo,
    )

    author = pr_json.get("author") or {}
    return GitHubPrStatus(
        repo_path=repo,
        number=pr_json["number"],
        title=pr_json["title"],
        url=pr_json["url"],
        head_ref=pr_json["headRefName"],
        base_ref=pr_json["baseRefName"],
        state=pr_json["state"],
        review_decision=pr_json.get("reviewDecision"),
        merge_state_status=pr_json.get("mergeStateStatus"),
        changed_files=pr_json["changedFiles"],
        additions=pr_json["additions"],
        deletions=pr_json["deletions"],
        author=author.get("login"),
        files=pr_json.get("files", []),
        checks=checks if isinstance(checks, list) else [],
    )


def write_review_bundle(repo_path: Path, pr_ref: str | None = None) -> GitHubPrReviewBundle:
    """Write a local review bundle for pi-company workflows."""
    status = get_pr_status(repo_path, pr_ref=pr_ref)
    diff_text = _gh_text(["pr", "diff", str(status.number)], cwd=status.repo_path)

    output_path = status.repo_path / ".pi" / "reviews" / f"pr-{status.number}-review.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_render_review_bundle(status, diff_text))

    return GitHubPrReviewBundle(status=status, output_path=output_path, bundle_written=True)


def write_review_bundle_or_local(
    repo_path: Path, pr_ref: str | None = None
) -> GitHubPrReviewBundle | LocalReviewBundle:
    """Write a PR review bundle, or fall back to a local diff bundle if no PR exists."""
    try:
        return write_review_bundle(repo_path=repo_path, pr_ref=pr_ref)
    except RuntimeError as exc:
        message = str(exc).lower()
        if "no pull requests found" not in message:
            raise
        return write_local_review_bundle(repo_path)


def write_local_review_bundle(repo_path: Path) -> LocalReviewBundle:
    """Write a local diff review bundle when there is no PR yet."""
    repo = repo_path.expanduser().resolve()
    branch_name = _git_text(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).strip()
    status_text = _git_text(["status", "--short"], cwd=repo).strip()
    diff_stat = _git_text(["diff", "--stat"], cwd=repo).strip()
    diff_text = _git_text(["diff"], cwd=repo)

    output_path = repo / ".pi" / "reviews" / f"local-{branch_name}-review.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_render_local_review_bundle(repo, branch_name, status_text, diff_stat, diff_text))

    return LocalReviewBundle(
        repo_path=repo,
        branch_name=branch_name,
        output_path=output_path,
        bundle_written=True,
    )


def format_pr_status(status: GitHubPrStatus) -> str:
    """Render a concise human-readable PR summary."""
    lines = [
        f"PR #{status.number}: {status.title}",
        f"Repo: {status.repo_path}",
        f"URL: {status.url}",
        f"Branch: {status.head_ref} -> {status.base_ref}",
        f"State: {status.state}",
        f"Review decision: {status.review_decision or 'none'}",
        f"Merge state: {status.merge_state_status or 'unknown'}",
        f"Diff stats: {status.changed_files} files, +{status.additions} / -{status.deletions}",
    ]

    if status.checks:
        lines.append("Checks:")
        for check in status.checks[:10]:
            lines.append(
                f"  - {check.get('name', 'unknown')}: {check.get('bucket') or check.get('state') or 'unknown'}"
            )
    else:
        lines.append("Checks: none reported")

    if status.files:
        lines.append("Files:")
        for file_info in status.files[:10]:
            path = file_info.get("path") or file_info.get("name") or "(unknown)"
            lines.append(f"  - {path}")

    return "\n".join(lines)


def format_review_bundle_result(bundle: GitHubPrReviewBundle) -> str:
    """Render a concise human-readable review bundle result."""
    return "\n".join(
        [
            f"Wrote review bundle for PR #{bundle.status.number}",
            f"Bundle: {bundle.output_path}",
            f"PR URL: {bundle.status.url}",
            "Next step: run pi in that repo and use the pr-review skill against the bundle and diff.",
        ]
    )


def format_local_review_bundle_result(bundle: LocalReviewBundle) -> str:
    """Render a concise human-readable local review bundle result."""
    return "\n".join(
        [
            f"Wrote local diff review bundle for branch {bundle.branch_name}",
            f"Bundle: {bundle.output_path}",
            "No open PR was found, so this bundle is based on the current git diff.",
            "Next step: run pi in that repo and use the pr-review skill against the local bundle and diff.",
        ]
    )


def _render_review_bundle(status: GitHubPrStatus, diff_text: str) -> str:
    file_lines = [f"- {file_info.get('path') or file_info.get('name') or '(unknown)'}" for file_info in status.files]
    check_lines = [
        f"- {check.get('name', 'unknown')}: {check.get('bucket') or check.get('state') or 'unknown'}"
        for check in status.checks
    ]
    truncated_diff = diff_text[:MAX_DIFF_CHARS]
    if len(diff_text) > MAX_DIFF_CHARS:
        truncated_diff += "\n\n[diff truncated]"

    return "\n".join(
        [
            f"# PR Review Bundle: #{status.number} {status.title}",
            "",
            "Use this bundle with `pi-company` and the `pr-review` skill.",
            "",
            "## PR Summary",
            f"- URL: {status.url}",
            f"- Author: {status.author or 'unknown'}",
            f"- Branch: {status.head_ref} -> {status.base_ref}",
            f"- State: {status.state}",
            f"- Review decision: {status.review_decision or 'none'}",
            f"- Merge state: {status.merge_state_status or 'unknown'}",
            f"- Diff stats: {status.changed_files} files, +{status.additions} / -{status.deletions}",
            "",
            "## Status Checks",
            *(check_lines or ["- No checks reported"]),
            "",
            "## Changed Files",
            *(file_lines or ["- No files reported"]),
            "",
            "## Review Instructions",
            "- Read `.pi/company/context.md` first if present.",
            "- Prioritize correctness, regressions, missing tests, rollout risk, and ownership gaps.",
            "- Findings first. Keep summaries short.",
            "",
            "## Diff",
            "```diff",
            truncated_diff,
            "```",
            "",
        ]
    )


def _render_local_review_bundle(repo: Path, branch_name: str, status_text: str, diff_stat: str, diff_text: str) -> str:
    truncated_diff = diff_text[:MAX_DIFF_CHARS]
    if len(diff_text) > MAX_DIFF_CHARS:
        truncated_diff += "\n\n[diff truncated]"

    return "\n".join(
        [
            f"# Local Review Bundle: {branch_name}",
            "",
            "No open PR was found for this branch. Use this bundle with `pi-company` and the `pr-review` skill.",
            "",
            "## Repo Summary",
            f"- Repo: {repo}",
            f"- Branch: {branch_name}",
            "",
            "## Git Status",
            "```text",
            status_text or "(clean working tree)",
            "```",
            "",
            "## Diff Stat",
            "```text",
            diff_stat or "(no diff)",
            "```",
            "",
            "## Review Instructions",
            "- Read `.pi/company/context.md` first if present.",
            "- Prioritize correctness, regressions, missing tests, rollout risk, and ownership gaps.",
            "- Findings first. Keep summaries short.",
            "",
            "## Diff",
            "```diff",
            truncated_diff,
            "```",
            "",
        ]
    )


def _gh_json(args: list[str], cwd: Path) -> Any:
    raw = _gh_text(args, cwd)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh returned non-JSON output: {raw}") from exc


def _gh_text(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["gh", *args],
        cwd=str(cwd),
        shell=False,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown gh error"
        raise RuntimeError(stderr)
    return result.stdout


def _git_text(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        shell=False,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise RuntimeError(stderr)
    return result.stdout
