from __future__ import annotations

from typing import Dict



def weighted_score(parts: Dict[str, float], weights: Dict[str, float]) -> float:
    return sum(float(parts.get(k, 0.0)) * float(w) for k, w in weights.items())



def normalize_cosine_score(raw: float) -> float:
    v = (float(raw) + 1.0) / 2.0
    return max(0.0, min(1.0, v))



def recency_score(year: int | None, min_year: int, max_year: int) -> float:
    if year is None:
        return 0.0
    if min_year == max_year:
        return 1.0
    v = (int(year) - min_year) / float(max_year - min_year)
    return max(0.0, min(1.0, v))
