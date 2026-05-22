from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

import torch

from ai_module.rerank.dataset import load_examples
from ai_module.rerank.inference import RerankInference


def _dcg(labels: List[int], k: int) -> float:
    score = 0.0
    for i, rel in enumerate(labels[:k], start=1):
        score += rel / math.log2(i + 1)
    return score


def _ndcg(labels: List[int], k: int) -> float:
    ideal = sorted(labels, reverse=True)
    idcg = _dcg(ideal, k)
    if idcg <= 0:
        return 0.0
    return _dcg(labels, k) / idcg


def evaluate(path: str, model_path: str, k: int) -> Dict[str, float]:
    examples = load_examples(path)
    infer = RerankInference(model_path, device="cuda" if torch.cuda.is_available() else "cpu")

    groups: Dict[str, List[Tuple[float, int]]] = defaultdict(list)
    for ex in examples:
        s = infer.score(ex.query, ex.text, ex.base_score)
        groups[ex.group_id].append((s, int(ex.label)))

    mrr = 0.0
    hit = 0.0
    ndcg = 0.0
    n = len(groups)

    for items in groups.values():
        ranked = sorted(items, key=lambda x: x[0], reverse=True)
        labels = [label for _, label in ranked]

        rr = 0.0
        for idx, rel in enumerate(labels, start=1):
            if rel > 0:
                rr = 1.0 / idx
                break
        mrr += rr

        hit += 1.0 if any(rel > 0 for rel in labels[:k]) else 0.0
        ndcg += _ndcg(labels, k)

    return {
        f"mrr@{k}": mrr / max(1, n),
        f"hit@{k}": hit / max(1, n),
        f"ndcg@{k}": ndcg / max(1, n),
        "num_queries": float(n),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate rerank model")
    parser.add_argument("--data", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    metrics = evaluate(args.data, args.checkpoint, args.k)
    payload = {"status": "ok", "metrics": metrics}
    print(json.dumps(payload, ensure_ascii=False))

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
