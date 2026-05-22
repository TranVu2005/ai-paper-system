from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

import torch
from torch.utils.data import Dataset

from .features import RerankExample, apply_min_max, extract_features, min_max_scale


class RerankDataset(Dataset):
    def __init__(self, features: List[List[float]], labels: List[float], groups: List[str]) -> None:
        self.x = torch.tensor(features, dtype=torch.float32)
        self.y = torch.tensor(labels, dtype=torch.float32)
        self.groups = groups

    def __len__(self) -> int:
        return self.x.size(0)

    def __getitem__(self, idx: int):
        return {"x": self.x[idx], "y": self.y[idx]}


def load_examples(path: str) -> List[RerankExample]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")

    examples: List[RerankExample] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            query = str(row.get("query", "")).strip()
            text = str(row.get("text", "")).strip()
            if not query or not text:
                continue
            examples.append(
                RerankExample(
                    query=query,
                    text=text,
                    label=float(row.get("label", 0.0)),
                    base_score=float(row.get("base_score", 0.0)),
                    group_id=str(row.get("group_id", query)),
                )
            )

    if not examples:
        raise ValueError(f"No valid records in dataset: {path}")
    return examples


def build_feature_matrix(examples: List[RerankExample]) -> Tuple[List[List[float]], List[float], List[str]]:
    features: List[List[float]] = []
    labels: List[float] = []
    groups: List[str] = []

    for ex in examples:
        features.append(extract_features(ex.query, ex.text, ex.base_score))
        labels.append(ex.label)
        groups.append(ex.group_id)

    return features, labels, groups


def make_dataset(path: str, feature_min: List[float] | None = None, feature_max: List[float] | None = None):
    examples = load_examples(path)
    features, labels, groups = build_feature_matrix(examples)

    if feature_min is None or feature_max is None:
        scaled = min_max_scale(features)
        x = scaled["scaled"]
        stats = {"min": scaled["min"], "max": scaled["max"]}
    else:
        x = apply_min_max(features, feature_min, feature_max)
        stats = {"min": feature_min, "max": feature_max}

    return RerankDataset(x, labels, groups), stats
