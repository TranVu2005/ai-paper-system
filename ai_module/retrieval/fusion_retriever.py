from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .graph_retriever import GraphRetriever
from .vector_retriever import VectorRetriever


class FusionRetriever:
    def __init__(
        self,
        vector_retriever: VectorRetriever,
        graph_retriever: Optional[GraphRetriever] = None,
    ) -> None:
        self.vector_retriever = vector_retriever
        self.graph_retriever = graph_retriever

    def retrieve(self, question: str, top_k: int = 5) -> List[Dict]:
        vec = self.vector_retriever.retrieve(question, top_k=max(10, top_k))
        if self.graph_retriever is None:
            return vec[:top_k]

        graph_result = self.graph_retriever.retrieve(question, top_k=max(10, top_k))
        terms = self._extract_terms(graph_result)

        fused = []
        for r in vec:
            txt = str(r.get("text", "")).lower()
            boost = 0.08 if any(t and t in txt for t in terms) else 0.0
            item = dict(r)
            item["fusion_score"] = float(r.get("score", 0.0)) + boost
            fused.append(item)
        fused.sort(key=lambda x: x.get("fusion_score", 0.0), reverse=True)
        return fused[:top_k]

    def _extract_terms(self, graph_result: Any) -> set[str]:
        """
        Backward-compatible extractor:
        - Old contract: list[dict] with node.label
        - New contract: GraphRetrievalResult with .chunks
        """
        terms: set[str] = set()

        # New graph retriever contract
        chunks = getattr(graph_result, "chunks", None)
        if chunks is not None:
            for ch in chunks:
                metadata = getattr(ch, "metadata", {}) or {}
                source = str(getattr(ch, "source", "")).lower()
                text = str(getattr(ch, "text", "")).lower()

                for key in ("concept_name", "evidence_name", "metric_name"):
                    v = str(metadata.get(key, "")).strip().lower()
                    if v:
                        terms.add(v)

                if source in ("graph_concept", "graph_evidence", "graph_metric"):
                    first_line = text.splitlines()[0] if text else ""
                    if ":" in first_line:
                        candidate = first_line.split(":", 1)[1].strip().lower()
                        if candidate:
                            terms.add(candidate)
            return {t for t in terms if t}

        # Old graph retriever contract
        if isinstance(graph_result, Iterable):
            for row in graph_result:
                if not isinstance(row, dict):
                    continue
                node = row.get("node", {}) or {}
                label = str(node.get("label", "")).strip().lower()
                if label:
                    terms.add(label)
        return terms

