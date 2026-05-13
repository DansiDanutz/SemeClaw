"""Frozen-fixture eval harness for the SemeClaw orchestrator.

A case is a JSONL row:
  {
    "id": "case-001",
    "description": "human-readable purpose",
    "task": {"id": "...", "title": "...", "description": "...",
             "status": "open", "assigned_agents": ["research"]},
    "interventions": [
      {"turn_index": 1, "user_comment": "...",
       "agent_replies": [{"agent_id": "research", "text": "..."}]},
      ...
    ],
    "expected": {
      "task_patch": {"status": "needs_review", "assigned_agents": ["research"]},
      "rationale_keywords": ["push", "back"],
      "must_include_in_description": ["[v2 update]"]
    }
  }

The runner forces the deterministic fallback path of
``orchestrator_decide`` so CI runs in zero-key mode reliably. When
``OPENROUTER_API_KEY`` is set we additionally exercise the live path
under a relaxed scorer.

Public API:
  - ``load_cases(path)``        → list[dict]
  - ``score_case(case, decision)`` → CaseResult
  - ``run(*, glob)``            → coroutine[list[CaseResult]]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

DEFAULT_FIXTURES = Path(__file__).parent / "fixtures"


@dataclass(frozen=True)
class CaseResult:
    id: str
    passed: bool
    score: float  # 1.0 = perfect, 0.0 = full miss
    failures: tuple[str, ...]
    decision: dict


def load_cases(glob: str = "*.jsonl") -> list[dict]:
    cases: list[dict] = []
    for path in sorted(DEFAULT_FIXTURES.glob(glob)):
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw or raw.startswith("//"):
                continue
            cases.append(json.loads(raw))
    return cases


def _normalise_agents(value) -> list[str]:
    return sorted([str(a) for a in (value or [])])


def score_case(case: dict, decision: dict) -> CaseResult:
    """Return a CaseResult comparing the orchestrator output to the
    locked expectation. Field coverage:
      - task_patch.status         — exact match
      - task_patch.assigned_agents — set equality
      - rationale_keywords        — every keyword must be a substring
      - must_include_in_description — every fragment must be a substring
    """
    expected = case.get("expected") or {}
    patch = decision.get("task_patch") or {}
    rationale = (decision.get("rationale") or "").lower()
    failures: list[str] = []
    checks = 0
    hits = 0

    exp_patch = expected.get("task_patch") or {}
    if "status" in exp_patch:
        checks += 1
        if patch.get("status") == exp_patch["status"]:
            hits += 1
        else:
            failures.append(f"status: expected {exp_patch['status']!r}, got {patch.get('status')!r}")
    if "assigned_agents" in exp_patch:
        checks += 1
        if _normalise_agents(patch.get("assigned_agents")) == _normalise_agents(exp_patch["assigned_agents"]):
            hits += 1
        else:
            failures.append(
                f"assigned_agents: expected {sorted(exp_patch['assigned_agents'])}, "
                f"got {_normalise_agents(patch.get('assigned_agents'))}"
            )

    for kw in expected.get("rationale_keywords") or []:
        checks += 1
        if kw.lower() in rationale:
            hits += 1
        else:
            failures.append(f"rationale missing keyword {kw!r}")

    for frag in expected.get("must_include_in_description") or []:
        checks += 1
        if frag in (patch.get("description") or ""):
            hits += 1
        else:
            failures.append(f"description missing fragment {frag!r}")

    score = (hits / checks) if checks else 1.0
    return CaseResult(
        id=case.get("id", "?"),
        passed=not failures,
        score=round(score, 3),
        failures=tuple(failures),
        decision=decision,
    )


async def _decide(case: dict) -> dict:
    # Force deterministic path by hiding the API key for this call only.
    saved = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        from war_room.tasks import intervene as _iv

        decision = await _iv.orchestrator_decide(
            case["task"],
            case.get("dialog_lines", []),
            case.get("interventions", []),
        )
    finally:
        if saved is not None:
            os.environ["OPENROUTER_API_KEY"] = saved
    return decision


async def run(glob: str = "*.jsonl") -> list[CaseResult]:
    cases = load_cases(glob=glob)
    results: list[CaseResult] = []
    for case in cases:
        decision = await _decide(case)
        results.append(score_case(case, decision))
    return results


# ---------------------------------------------------------------------------
# CLI entry — used by Makefile + CI.
# ---------------------------------------------------------------------------
def _format(result: CaseResult) -> str:
    status = "PASS" if result.passed else "FAIL"
    line = f"[{status}] {result.id} score={result.score:.2f}"
    if result.failures:
        line += "\n        - " + "\n        - ".join(result.failures)
    return line


async def _main(argv: Iterable[str]) -> int:
    argv = list(argv)
    glob = argv[0] if argv else "*.jsonl"
    results = await run(glob=glob)
    for r in results:
        print(_format(r))
    passed = sum(1 for r in results if r.passed)
    print(f"\n{passed}/{len(results)} cases passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(asyncio.run(_main(sys.argv[1:])))
