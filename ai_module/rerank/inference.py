from __future__ import annotations

from typing import Any, Dict, List

import torch

from .features import FEATURE_NAMES, apply_min_max, extract_features, sigmoid
from .model import MlpReranker, load_checkpoint


class RerankInference:
    def __init__(self, checkpoint_path: str, device: str = "cpu") -> None:
        self.device = torch.device(device)
        ckpt = load_checkpoint(checkpoint_path, map_location=device)

        self.feature_names = ckpt.get("feature_names", FEATURE_NAMES)
        self.min_vals = ckpt["feature_min"]
        self.max_vals = ckpt["feature_max"]

        self.model = MlpReranker(
            input_dim=int(ckpt["input_dim"]),
            hidden_dim=int(ckpt["hidden_dim"]),
            dropout=float(ckpt["dropout"]),
        ).to(self.device)
        self.model.load_state_dict(ckpt["state_dict"])
        self.model.eval()

    def score(self, query: str, text: str, base_score: float = 0.0) -> float:
        feats = extract_features(query=query, text=text, base_score=base_score)
        feats_scaled = apply_min_max([feats], self.min_vals, self.max_vals)[0]
        x = torch.tensor([feats_scaled], dtype=torch.float32, device=self.device)
        with torch.no_grad():
            logit = self.model(x).item()
        return float(sigmoid(logit))

    def rerank(self, query: str, candidates: List[Dict[str, Any]], top_k: int = 5) -> List[Dict[str, Any]]:
        scored: List[Dict[str, Any]] = []
        for cand in candidates:
            text = str(cand.get("text", ""))
            if not text:
                continue
            base_score = float(cand.get("base_score", cand.get("score", 0.0)))
            score = self.score(query=query, text=text, base_score=base_score)
            out = dict(cand)
            out["rerank_score"] = round(score, 6)
            scored.append(out)

        scored.sort(key=lambda x: x["rerank_score"], reverse=True)
        return scored[:top_k]
