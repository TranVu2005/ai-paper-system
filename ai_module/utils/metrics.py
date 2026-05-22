from __future__ import annotations

from typing import Dict, Iterable, List



def precision_at_k(binary_relevance: List[int], k: int) -> float:
    if not binary_relevance:
        return 0.0
    kk = max(1, min(k, len(binary_relevance)))
    return sum(binary_relevance[:kk]) / float(kk)



def recall_at_k(binary_relevance: List[int], total_relevant: int, k: int) -> float:
    if total_relevant <= 0:
        return 0.0
    kk = max(1, min(k, len(binary_relevance)))
    return sum(binary_relevance[:kk]) / float(total_relevant)



def hit_at_k(binary_relevance: List[int], k: int) -> float:
    if not binary_relevance:
        return 0.0
    kk = max(1, min(k, len(binary_relevance)))
    return 1.0 if any(x > 0 for x in binary_relevance[:kk]) else 0.0



def aggregate_metrics(rows: Iterable[Dict[str, float]]) -> Dict[str, float]:
    rows = list(rows)
    if not rows:
        return {}
    keys = sorted({k for r in rows for k in r.keys()})
    return {k: sum(float(r.get(k, 0.0)) for r in rows) / len(rows) for k in keys}
