from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List

import numpy as np
try:
    import faiss  # type: ignore
except Exception:  # pragma: no cover
    faiss = None  # type: ignore

from ai_module.embedding.embedding_service import EmbeddingService
from ai_module.inference.inference_config import InferenceConfig
from ai_module.utils.document_schema import extract_document_chunks


@dataclass
class FAISSStore:
    index: Any | None = None
    metadata: List[Dict[str, Any]] | None = None
    vectors_np: np.ndarray | None = None

    def build(self, vectors: List[List[float]], metadata: List[Dict[str, Any]]) -> None:
        if not vectors:
            raise ValueError("Empty vectors")
        arr = np.array(vectors, dtype=np.float32)
        self.vectors_np = arr
        if faiss is not None:
            self.index = faiss.IndexFlatIP(arr.shape[1])
            self.index.add(arr)
        else:
            self.index = None
        self.metadata = metadata

    def search(self, qvec: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        if self.metadata is None:
            raise RuntimeError("Index is not initialized")
        q = np.array(qvec, dtype=np.float32)

        if self.index is not None:
            scores, idxs = self.index.search(np.array([q], dtype=np.float32), top_k)
            ranked_pairs = list(zip(scores[0], idxs[0]))
        else:
            if self.vectors_np is None:
                raise RuntimeError("Fallback vectors are missing")
            # Numpy fallback: inner-product ranking when faiss is unavailable.
            raw_scores = self.vectors_np @ q
            top_idxs = np.argsort(-raw_scores)[:top_k]
            ranked_pairs = [(float(raw_scores[i]), int(i)) for i in top_idxs]

        out = []
        for score, idx in ranked_pairs:
            if idx < 0 or idx >= len(self.metadata):
                continue
            item = dict(self.metadata[idx])
            item["score"] = float(score)
            out.append(item)
        return out



def _split_text(text: str, chunk_size: int, chunk_overlap: int) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    step = max(1, chunk_size - chunk_overlap)
    for i in range(0, len(text), step):
        part = text[i : i + chunk_size].strip()
        if part:
            chunks.append(part)
        if i + chunk_size >= len(text):
            break
    return chunks



def chunk_document(doc: Dict[str, Any], chunk_size: int, chunk_overlap: int) -> List[Dict[str, Any]]:
    # Keep signature for compatibility, but use upstream processed chunks as-is.
    _ = (chunk_size, chunk_overlap)
    return extract_document_chunks(doc)


class VectorRetriever:
    def __init__(self, config: InferenceConfig) -> None:
        self.config = config
        self.embedding = EmbeddingService(config)
        self.store = FAISSStore()

    def build(self, documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        all_chunks: List[Dict[str, Any]] = []
        for doc in documents:
            all_chunks.extend(
                chunk_document(
                    doc,
                    chunk_size=self.config.chunk_size_chars,
                    chunk_overlap=self.config.chunk_overlap_chars,
                )
            )
        if not all_chunks:
            raise ValueError("No chunks produced")

        vectors = self.embedding.embed_texts([c["text"] for c in all_chunks])
        self.store.build(vectors, all_chunks)
        return {"num_chunks": len(all_chunks)}

    def retrieve(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        qvec = self.embedding.embed_query(query)
        return self.store.search(qvec, top_k=top_k)
