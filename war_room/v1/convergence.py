"""Convergence detection for the orchestrator's intervention loop.

Today the orchestrator commits a writeback unconditionally on turn 3.
The convergence detector estimates how aligned the latest intervention is
with the prior interventions + agent replies, returning a score in [0, 1]
that the dialog endpoint can surface (``meta.convergence_score``) and
that ``intervene()`` can use to commit early when ``early_commit=True``.

The scorer is intentionally simple — Jaccard similarity over a normalised
token set plus an "agreement keyword" boost. It runs in pure Python with
zero dependencies so the deterministic CI path stays viable. Replace
with sentence-transformers when production traffic justifies the install.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Convergence is signalled by phrases that explicitly endorse the current
# direction. Carefully scoped to avoid false positives ("ok but…").
AGREEMENT_TOKENS = {
    "agree",
    "agreed",
    "ship",
    "approved",
    "lgtm",
    "go",
    "yes",
    "confirmed",
    "proceed",
    "good",
    "looks",
    "right",
    "correct",
    "perfect",
}

DISAGREEMENT_TOKENS = {
    "no",
    "stop",
    "wait",
    "rethink",
    "wrong",
    "block",
    "blocked",
    "but",
    "however",
    "instead",
    "actually",
    "disagree",
}

EARLY_COMMIT_THRESHOLD = 0.62
EARLY_COMMIT_MIN_TURN = 2  # Never commit on turn 1 — we need at least one prior signal.


_TOKEN = re.compile(r"[a-z0-9']+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall((text or "").lower()))


@dataclass(frozen=True)
class ConvergenceSignal:
    score: float  # 0.0 = orthogonal, 1.0 = identical sentiment
    agreement: float  # weight of agreement tokens in the new comment
    overlap: float  # Jaccard overlap with prior text
    early_commit: bool  # whether the orchestrator should commit now
    reason: str


def score_convergence(
    *,
    turn: int,
    new_comment: str,
    prior_comments: list[str],
    prior_replies: list[str],
) -> ConvergenceSignal:
    """Return the convergence signal for the latest intervention.

    The score combines:
    - Jaccard overlap of the new comment with concatenated prior context
    - Net-agreement weight (agreement_tokens − disagreement_tokens)
    """
    prior_text = " ".join(prior_comments + prior_replies)
    new_set = _tokens(new_comment)
    prior_set = _tokens(prior_text)

    if not new_set:
        return ConvergenceSignal(0.0, 0.0, 0.0, False, "empty_comment")

    intersection = new_set & prior_set
    union = new_set | prior_set
    overlap = (len(intersection) / len(union)) if union else 0.0

    agree_hits = len(new_set & AGREEMENT_TOKENS)
    disagree_hits = len(new_set & DISAGREEMENT_TOKENS)
    # Normalise by the size of the new comment so short "lgtm" doesn't
    # crowd out longer agreements; clamp to [-1, 1].
    raw_agreement = (agree_hits - disagree_hits) / max(len(new_set), 4)
    agreement = max(-1.0, min(1.0, raw_agreement))

    # Combine: 60% overlap, 40% positive-agreement (negative pulls score down).
    score = 0.6 * overlap + 0.4 * max(0.0, agreement)
    # Strong, unambiguous agreement should trigger commit even when the
    # topic vocabulary differs from prior turns ("lgtm, ship it" rarely
    # repeats every prior word). Floor the score at the agreement weight
    # when agreement crosses a high-confidence threshold.
    if agreement >= 0.6 and disagree_hits == 0:
        score = max(score, agreement * 0.85)
    if disagree_hits and not agree_hits:
        score *= 0.5  # an unmitigated objection halves the score

    early = (
        turn >= EARLY_COMMIT_MIN_TURN
        and score >= EARLY_COMMIT_THRESHOLD
        and disagree_hits == 0
    )
    if early:
        reason = "agreement_threshold_met"
    elif disagree_hits:
        reason = "objection_present"
    elif turn < EARLY_COMMIT_MIN_TURN:
        reason = "first_turn_holds"
    else:
        reason = "more_signal_needed"

    return ConvergenceSignal(
        score=round(score, 3),
        agreement=round(agreement, 3),
        overlap=round(overlap, 3),
        early_commit=early,
        reason=reason,
    )
