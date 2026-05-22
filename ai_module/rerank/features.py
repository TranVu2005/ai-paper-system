from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List

TOKEN_PATTERN = re.compile(r"\w+", flags=re.UNICODE)
FEATURE_NAMES = [
    "base_score",
    "token_overlap_ratio",
    "jaccard",
    "query_len_norm",
    "doc_len_norm",
    "length_ratio",
    "contains_query_bigram",
]


@dataclass
class RerankExample:
    query: str
    text: str
    label: float
    base_score: float = 0.0
    group_id: str = ""


def tokenize(text: str) -> List[str]:
    return [t.lower() for t in TOKEN_PATTERN.findall(text or "")]


def _safe_div(num: float, den: float) -> float:
    return num / den if den > 0 else 0.0


def _contains_query_bigram(query_tokens: List[str], doc_tokens: List[str]) -> float:
    if len(query_tokens) < 2:
        return 0.0
    doc_text = " ".join(doc_tokens)
    for i in range(len(query_tokens) - 1):
        bg = f"{query_tokens[i]} {query_tokens[i + 1]}"
        if bg in doc_text:
            return 1.0
    return 0.0


def extract_features(query: str, text: str, base_score: float = 0.0) -> List[float]:
    q_tokens = tokenize(query)
    d_tokens = tokenize(text)

    q_set = set(q_tokens)
    d_set = set(d_tokens)
    overlap = len(q_set.intersection(d_set))
    union = len(q_set.union(d_set))

    token_overlap_ratio = _safe_div(overlap, len(q_set))
    jaccard = _safe_div(overlap, union)

    q_len = len(q_tokens)
    d_len = len(d_tokens)

    query_len_norm = min(1.0, q_len / 32.0)
    doc_len_norm = min(1.0, d_len / 512.0)
    length_ratio = _safe_div(min(q_len, d_len), max(q_len, d_len)) if q_len and d_len else 0.0
    contains_query_bigram = _contains_query_bigram(q_tokens, d_tokens)

    return [
        float(base_score),
        float(token_overlap_ratio),
        float(jaccard),
        float(query_len_norm),
        float(doc_len_norm),
        float(length_ratio),
        float(contains_query_bigram),
    ]


def min_max_scale(matrix: List[List[float]]) -> Dict[str, Any]:
    if not matrix:
        raise ValueError("Cannot scale empty feature matrix")

    cols = len(matrix[0])
    min_vals = [float("inf")] * cols
    max_vals = [float("-inf")] * cols

    for row in matrix:
        if len(row) != cols:
            raise ValueError("Inconsistent feature vector length")
        for i, v in enumerate(row):
            if v < min_vals[i]:
                min_vals[i] = v
            if v > max_vals[i]:
                max_vals[i] = v

    scaled = []
    for row in matrix:
        out = []
        for i, v in enumerate(row):
            den = max_vals[i] - min_vals[i]
            out.append((v - min_vals[i]) / den if den > 1e-12 else 0.0)
        scaled.append(out)

    return {"scaled": scaled, "min": min_vals, "max": max_vals}


def apply_min_max(matrix: List[List[float]], min_vals: List[float], max_vals: List[float]) -> List[List[float]]:
    if not matrix:
        return []

    scaled = []
    for row in matrix:
        out = []
        for i, v in enumerate(row):
            den = max_vals[i] - min_vals[i]
            out.append((v - min_vals[i]) / den if den > 1e-12 else 0.0)
        scaled.append(out)
    return scaled


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)
