"""End-to-end test for the orchestrator eval harness.

Runs every fixture through the deterministic fallback path and demands
the baseline holds. This is the same harness CI runs (zero-key).
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_eval_baseline_holds():
    from evals.orchestrator import runner

    results = await runner.run()
    assert results, "no eval fixtures loaded"
    failures = [r for r in results if not r.passed]
    if failures:
        details = "\n".join(f"{r.id} (score={r.score:.2f}):\n  - " + "\n  - ".join(r.failures) for r in failures)
        raise AssertionError(f"{len(failures)}/{len(results)} eval cases regressed:\n{details}")
    # Overall average score must stay perfect on the deterministic path.
    assert all(r.score == 1.0 for r in results)


def test_score_case_catches_status_drift():
    from evals.orchestrator.runner import score_case

    case = {
        "id": "drift",
        "expected": {"task_patch": {"status": "done"}, "rationale_keywords": ["ship"]},
    }
    decision = {"task_patch": {"status": "needs_review"}, "rationale": "ship it"}
    result = score_case(case, decision)
    assert not result.passed
    assert any("status:" in f for f in result.failures)
    # Rationale keyword still matched, so the score should be partial — not zero.
    assert 0 < result.score < 1
