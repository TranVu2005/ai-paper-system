from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List

import numpy as np
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from ai_module.embedding.embedding_service import EmbeddingService
from ai_module.inference.inference_config import InferenceConfig


class BGEReranker:
    """
    Reranker with two modes:
    - cross_encoder: for models like BAAI/bge-reranker-v2-*
    - embedding_cosine: fallback for embedding models like BAAI/bge-m3
    """

    def __init__(
        self,
        config: InferenceConfig | None = None,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        device: str = "cuda",
        strict_device: bool = True,
    ) -> None:
        base_cfg = config or InferenceConfig()
        self.config = replace(base_cfg, embedding_model_name=model_name, device=device)
        self.mode = "cross_encoder" if "reranker" in model_name.lower() else "embedding_cosine"

        self.embedding = None
        self._ce_tokenizer = None
        self._ce_model = None
        self._torch_device = torch.device("cpu")

        if self.mode == "cross_encoder":
            self._init_cross_encoder(model_name=model_name, device=device, strict_device=strict_device)
        else:
            self._init_embedding_cosine(model_name=model_name, device=device, strict_device=strict_device)

    def _init_embedding_cosine(self, model_name: str, device: str, strict_device: bool) -> None:
        self.config = replace(self.config, embedding_model_name=model_name, device=device)
        self.embedding = EmbeddingService(self.config)
        self.model_name = self.embedding.model.model_name
        self.device = str(getattr(self.embedding.model.model, "device", "unknown"))
        if strict_device and device == "cuda" and not self.device.startswith("cuda"):
            raise RuntimeError(
                f"BGEReranker expected CUDA but got device={self.device}. "
                "Check VRAM / CUDA env, or set strict_device=False."
            )

    def _init_cross_encoder(self, model_name: str, device: str, strict_device: bool) -> None:
        want_cuda = device == "cuda" and torch.cuda.is_available()
        self._torch_device = torch.device("cuda" if want_cuda else "cpu")
        if strict_device and device == "cuda" and self._torch_device.type != "cuda":
            raise RuntimeError("BGEReranker expected CUDA but CUDA is not available.")

        self._ce_tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
        dtype = torch.float16 if self._torch_device.type == "cuda" else torch.float32
        self._ce_model = (
            AutoModelForSequenceClassification.from_pretrained(
                model_name,
                trust_remote_code=True,
                torch_dtype=dtype,
            )
            .to(self._torch_device)
            .eval()
        )
        self.model_name = model_name
        self.device = str(self._torch_device)

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        va = np.array(a, dtype=np.float32)
        vb = np.array(b, dtype=np.float32)
        den = float(np.linalg.norm(va) * np.linalg.norm(vb))
        if den <= 1e-12:
            return 0.0
        return float(np.dot(va, vb) / den)

    def _score_pairs_cross_encoder(self, query: str, texts: List[str], batch_size: int = 8) -> List[float]:
        if self._ce_model is None or self._ce_tokenizer is None:
            raise RuntimeError("Cross-encoder reranker is not initialized.")

        scores: List[float] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            enc = self._ce_tokenizer(
                [query] * len(batch),
                batch,
                padding=True,
                truncation=True,
                max_length=1024,
                return_tensors="pt",
            )
            enc = {k: v.to(self._torch_device) for k, v in enc.items()}
            with torch.no_grad():
                logits = self._ce_model(**enc).logits

            # Most reranker checkpoints output [B,1]. Some output [B,2].
            if logits.ndim == 2 and logits.shape[1] > 1:
                raw = logits[:, -1]
            else:
                raw = logits.squeeze(-1)

            probs = torch.sigmoid(raw.float()).detach().cpu().tolist()
            scores.extend([float(x) for x in probs])
        return scores

    def score(self, query: str, text: str) -> float:
        if self.mode == "cross_encoder":
            return self._score_pairs_cross_encoder(query=query, texts=[text], batch_size=1)[0]
        if self.embedding is None:
            raise RuntimeError("Embedding reranker is not initialized.")
        qv = self.embedding.embed_query(query)
        tv = self.embedding.embed_texts([text])[0]
        # map cosine [-1, 1] -> [0, 1]
        return max(0.0, min(1.0, (self._cosine(qv, tv) + 1.0) / 2.0))

    def rerank(self, query: str, candidates: List[Dict[str, Any]], top_k: int = 5) -> List[Dict[str, Any]]:
        if top_k <= 0:
            return []

        valid_candidates: List[Dict[str, Any]] = []
        valid_texts: List[str] = []
        for cand in candidates:
            text = str(cand.get("text", "")).strip()
            if not text:
                continue
            valid_candidates.append(cand)
            valid_texts.append(text)

        if not valid_candidates:
            return []

        if self.mode == "cross_encoder":
            pair_scores = self._score_pairs_cross_encoder(query=query, texts=valid_texts, batch_size=8)
        else:
            pair_scores = [self.score(query=query, text=t) for t in valid_texts]

        scored: List[Dict[str, Any]] = []
        for cand, s in zip(valid_candidates, pair_scores):
            out = dict(cand)
            out["rerank_score"] = round(float(s), 6)
            out["rerank_model"] = self.model_name
            out["rerank_mode"] = self.mode
            scored.append(out)

        scored.sort(key=lambda x: x["rerank_score"], reverse=True)
        return scored[:top_k]
