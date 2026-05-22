from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import List

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

from ai_module.inference.inference_config import InferenceConfig

logger = logging.getLogger(__name__)
_MODEL_CACHE: dict[tuple[str, str], SentenceTransformer] = {}
_MODEL_CACHE_LOCK = threading.Lock()


class _HashingEmbedder:
    def __init__(self, dim: int = 384) -> None:
        self.dim = max(64, int(dim))
        self._token_pattern = re.compile(r"\w+", re.UNICODE)

    def _encode_one(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        tokens = self._token_pattern.findall((text or "").lower())
        if not tokens:
            return vec
        for tok in tokens:
            idx = hash(tok) % self.dim
            vec[idx] += 1.0
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec /= norm
        return vec

    def encode(self, texts, normalize_embeddings: bool = True, batch_size: int = 16, show_progress_bar: bool = False):
        _ = (normalize_embeddings, batch_size, show_progress_bar)
        vectors = [self._encode_one(t) for t in texts]
        if not vectors:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.vstack(vectors)


@dataclass
class EmbeddingModel:
    config: InferenceConfig

    def __post_init__(self) -> None:
        self.model_name = self.config.embedding_model_name
        self.model = self._load_with_fallback(self.model_name)

    def _load_with_fallback(self, model_name: str):
        candidates: list[str] = []
        if (model_name or "").strip():
            candidates.append(model_name.strip())
        fallback = (self.config.embedding_fallback_model_name or "").strip()
        if fallback and fallback not in candidates:
            candidates.append(fallback)
        if not candidates:
            raise RuntimeError("No embedding model candidates configured")

        last_error = None
        for cand in candidates:
            embedding_device = (self.config.embedding_device or "cpu").lower()
            devices: list[str]
            if embedding_device == "auto":
                devices = ["cuda", "cpu"] if torch.cuda.is_available() else ["cpu"]
            elif embedding_device == "cuda":
                devices = ["cuda", "cpu"] if torch.cuda.is_available() else ["cpu"]
            else:
                devices = ["cpu"]
            for dev in devices:
                try:
                    cache_key = (cand, dev)
                    with _MODEL_CACHE_LOCK:
                        cached = _MODEL_CACHE.get(cache_key)
                    if cached is not None:
                        logger.info("Using cached embedding %s on %s", cand, dev)
                        self.model_name = cand
                        return cached

                    logger.info("Loading embedding %s on %s", cand, dev)
                    self.model_name = cand
                    # Avoid forcing low_cpu_mem_usage here; some transformers combinations
                    # can leave parameters on meta tensors and fail on first real move/encode.
                    model = SentenceTransformer(cand, device=dev, trust_remote_code=True)
                    # Warmup to ensure model is actually usable on this device.
                    _ = model.encode(["warmup"], normalize_embeddings=True, show_progress_bar=False)
                    with _MODEL_CACHE_LOCK:
                        _MODEL_CACHE[cache_key] = model
                    return model
                except Exception as ex:
                    last_error = ex
                    logger.warning("Embedding load failed %s on %s: %s", cand, dev, ex)

        logger.error(
            "All transformer embedding models failed; using hashing fallback. candidates=%s error=%s",
            candidates,
            last_error,
        )
        self.model_name = "hashing-fallback-384d"
        return _HashingEmbedder(dim=384)

    def encode_texts(self, texts: List[str]) -> List[List[float]]:
        vectors = self.model.encode(texts, normalize_embeddings=True, batch_size=16, show_progress_bar=False)
        return vectors.tolist()

    def encode_query(self, query: str) -> List[float]:
        vectors = self.model.encode([query], normalize_embeddings=True, show_progress_bar=False)
        return vectors[0].tolist()
