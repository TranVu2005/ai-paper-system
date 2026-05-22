from __future__ import annotations

from dataclasses import dataclass
from math import log2
from statistics import mean
from typing import Iterable, Sequence


@dataclass(frozen=True)
class EvalCase:
    """One evaluation sample for recommendation metrics."""

    recommended_ids: list[str]
    relevant_ids: set[str]
    history_len: int = 0


def hit_rate_at_k(recommended_ids: Sequence[str], relevant_ids: set[str], k: int) -> float:
    """
    HitRate@k for one sample.
    Returns 1.0 if any relevant item appears in top-k, else 0.0.
    """
    if k <= 0:
        return 0.0
    if not relevant_ids:
        return 0.0
    top_k = recommended_ids[:k]
    return 1.0 if any(item in relevant_ids for item in top_k) else 0.0


def dcg_at_k(recommended_ids: Sequence[str], relevant_ids: set[str], k: int) -> float:
    """Discounted cumulative gain @k (binary relevance)."""
    if k <= 0:
        return 0.0
    score = 0.0
    for idx, item in enumerate(recommended_ids[:k], start=1):
        if item in relevant_ids:
            score += 1.0 / log2(idx + 1.0)
    return score


def ndcg_at_k(recommended_ids: Sequence[str], relevant_ids: set[str], k: int) -> float:
    """
    NDCG@k for one sample (binary relevance).
    Normalized by ideal DCG with min(k, |relevant|) relevant items.
    """
    if k <= 0:
        return 0.0
    if not relevant_ids:
        return 0.0
    dcg = dcg_at_k(recommended_ids, relevant_ids, k)
    ideal_hits = min(k, len(relevant_ids))
    ideal_dcg = sum(1.0 / log2(rank + 1.0) for rank in range(1, ideal_hits + 1))
    if ideal_dcg <= 0.0:
        return 0.0
    return dcg / ideal_dcg


def _avg(values: Iterable[float]) -> float:
    values = list(values)
    return mean(values) if values else 0.0


def evaluate_cases(cases: Sequence[EvalCase]) -> dict[str, float]:
    """
    Aggregate metrics for a batch of recommendation samples.
    Returns values in [0, 1].
    """
    return {
        "num_cases": float(len(cases)),
        "hit_rate@1": _avg(hit_rate_at_k(c.recommended_ids, c.relevant_ids, 1) for c in cases),
        "hit_rate@5": _avg(hit_rate_at_k(c.recommended_ids, c.relevant_ids, 5) for c in cases),
        "hit_rate@10": _avg(hit_rate_at_k(c.recommended_ids, c.relevant_ids, 10) for c in cases),
        "ndcg@5": _avg(ndcg_at_k(c.recommended_ids, c.relevant_ids, 5) for c in cases),
        "ndcg@10": _avg(ndcg_at_k(c.recommended_ids, c.relevant_ids, 10) for c in cases),
    }


def evaluate_cold_start(
    cases: Sequence[EvalCase],
    buckets: Sequence[int] = (0, 1, 3, 5),
) -> dict[int, dict[str, float]]:
    """
    Evaluate metrics by cold-start history bucket.
    Bucket b means samples with history_len >= b.
    """
    out: dict[int, dict[str, float]] = {}
    for b in buckets:
        subset = [c for c in cases if c.history_len >= b]
        out[b] = evaluate_cases(subset)
    return out
