from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ai_module.inference.inference_config import InferenceConfig

from .embedding_model import EmbeddingModel


@dataclass
class EmbeddingService:
    config: InferenceConfig

    def __post_init__(self) -> None:
        self.remote_backend = self.config.embedding_backend == "remote"
        self.model = None if self.remote_backend else EmbeddingModel(self.config)
        self.remote_url = (self.config.embedding_base_url or "").rstrip("/")
        self.remote_model_name = self.config.embedding_remote_model_name or self.config.embedding_model_name

    def _remote_embed(self, texts: List[str]) -> List[List[float]]:
        if not self.remote_url:
            raise RuntimeError("EMBEDDING_BASE_URL is required when EMBEDDING_BACKEND=remote.")
        endpoint = f"{self.remote_url}/v1/embeddings"
        payload = {"model": self.remote_model_name, "input": texts}
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.config.embedding_api_key:
            headers["Authorization"] = f"Bearer {self.config.embedding_api_key}"
        req = Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urlopen(req, timeout=self.config.embedding_timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except HTTPError as ex:
            detail = ex.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Remote embedding HTTP {ex.code}: {detail}") from ex
        except URLError as ex:
            raise RuntimeError(f"Remote embedding connection failed: {ex}") from ex

        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError("Remote embedding response missing 'data' list.")
        vectors: List[List[float]] = []
        for row in rows:
            emb = row.get("embedding") if isinstance(row, dict) else None
            if not isinstance(emb, list):
                raise RuntimeError("Remote embedding response has invalid 'embedding'.")
            vectors.append([float(x) for x in emb])
        return vectors

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if self.remote_backend:
            return self._remote_embed(texts)
        return self.model.encode_texts(texts)

    def embed_query(self, query: str) -> List[float]:
        if self.remote_backend:
            vecs = self._remote_embed([query])
            return vecs[0] if vecs else []
        return self.model.encode_query(query)

    def embed_documents(self, docs: List[Dict[str, Any]], key: str = "text") -> List[List[float]]:
        return self.embed_texts([str(d.get(key, "")) for d in docs])
