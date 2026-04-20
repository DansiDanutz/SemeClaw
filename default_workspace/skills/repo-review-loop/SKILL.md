---
name: repo-review-loop
description: Run a recurring repository review loop from a generated PR or local diff bundle, produce findings, save memory, and notify only when risk is meaningful.
---

# Repo Review Loop

## Goal

Turn a generated review bundle into a compact operational review that can run on a schedule without wasting attention.

## Workflow

1. Confirm repo setup.
   - Use `pi_company_status` for the target repo.
   - Note whether `pi-company` is configured.

2. Generate or locate the review bundle.
   - Use `github_pr_review_bundle`.
   - This may produce either:
     - a PR review bundle
     - a local diff review bundle when no PR exists

3. Read the bundle file.
   - Use `read_file` on the bundle path.
   - Ground your findings in the actual bundle contents.

4. Review with this priority:
   - correctness risk
   - regression risk
   - missing verification
   - rollout or operational risk
   - ownership gaps

5. Keep the output short.
   - Findings first.
   - If there are no material findings, say so directly.

6. Save memory.
   - Use `memory_save` under `axis=projects`.
   - Key format: `<repo-name>-review-loop`
   - Include:
     - bundle type
     - bundle path
     - findings or "no major findings"
     - next inspection target

7. Notify only when needed.
   - If the review surfaces real blockers, regressions, or rollout risk, use `telegram_notify`.
   - Keep the alert short enough to read in under 30 seconds.
   - If there are no material findings, skip Telegram noise.

## Output shape

- `Bundle`: type and path
- `Findings`: flat bullets, highest severity first
- `Next check`: one short line

## Rule

Do not invent certainty from incomplete diffs. If the bundle is too thin, say what verification is still missing.
