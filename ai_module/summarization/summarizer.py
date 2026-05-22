from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

from ai_module.inference.inference_config import InferenceConfig
from ai_module.inference.llm_engine import LLMEngine
from ai_module.reasoning.prompt_builder import (
    chunk_summary_prompt,
    collection_summary_prompt,
    document_summary_prompt,
    higen_highlight_prompt,
    higen_summary_prompt_by_style,
)
from ai_module.retrieval.vector_retriever import chunk_document
from ai_module.utils.document_schema import extract_document_text, resolve_document_id


class Summarizer:
    STYLE_ACADEMIC = "academic"
    STYLE_SEMANTIC = "semantic"
    STYLE_EXECUTIVE = "executive"

    def __init__(self, config: InferenceConfig) -> None:
        self.config = config
        self.llm = LLMEngine(config)

    def summarize_text(self, text: str, num_highlights: int = 20, summary_style: str = "academic") -> Dict[str, Any]:
        safe_text = text or ""
        highlights = self.llm.generate(
            higen_highlight_prompt(document=safe_text, num_highlights=num_highlights),
            max_new_tokens=self.config.max_new_tokens_summary,
        )
        summary = self.llm.generate(
            higen_summary_prompt_by_style(highlights=highlights, style=summary_style),
            max_new_tokens=self.config.max_new_tokens_summary,
        )
        return {"highlights": highlights, "summary": summary}

    def summarize_text_academic(self, text: str, num_highlights: int = 20) -> Dict[str, Any]:
        return self.summarize_text(text=text, num_highlights=num_highlights, summary_style=self.STYLE_ACADEMIC)

    def summarize_text_semantic(self, text: str, num_highlights: int = 20) -> Dict[str, Any]:
        return self.summarize_text(text=text, num_highlights=num_highlights, summary_style=self.STYLE_SEMANTIC)

    def summarize_text_executive(self, text: str, num_highlights: int = 20) -> Dict[str, Any]:
        return self.summarize_text(text=text, num_highlights=num_highlights, summary_style=self.STYLE_EXECUTIVE)

    def summarize_document(
        self,
        document: Dict[str, Any],
        num_highlights: int = 20,
        summary_style: str = "academic",
    ) -> Dict[str, Any]:
        result = self.summarize_text(
            extract_document_text(document),
            num_highlights=num_highlights,
            summary_style=summary_style,
        )
        return {"file": resolve_document_id(document), "result": result}

    def summarize_document_academic(
        self,
        document: Dict[str, Any],
        num_highlights: int = 20,
    ) -> Dict[str, Any]:
        return self.summarize_document(
            document=document,
            num_highlights=num_highlights,
            summary_style=self.STYLE_ACADEMIC,
        )

    def summarize_document_semantic(
        self,
        document: Dict[str, Any],
        num_highlights: int = 20,
    ) -> Dict[str, Any]:
        return self.summarize_document(
            document=document,
            num_highlights=num_highlights,
            summary_style=self.STYLE_SEMANTIC,
        )

    def summarize_document_executive(
        self,
        document: Dict[str, Any],
        num_highlights: int = 20,
    ) -> Dict[str, Any]:
        return self.summarize_document(
            document=document,
            num_highlights=num_highlights,
            summary_style=self.STYLE_EXECUTIVE,
        )

    def summarize_document_hybrid(
        self,
        document: Dict[str, Any],
        num_highlights: int = 20,
        chunk_highlights: int = 4,
        summary_style: str = "academic",
    ) -> Dict[str, Any]:
        chunks = chunk_document(document, self.config.chunk_size_chars, self.config.chunk_overlap_chars)
        if not chunks:
            return self.summarize_document(
                document,
                num_highlights=num_highlights,
                summary_style=summary_style,
            )

        per_chunk = max(1, chunk_highlights)
        highlight_max_tokens = min(220, self.config.max_new_tokens_summary)

        def _highlight_one(item: tuple[int, Dict[str, Any]]) -> str:
            i, ch = item
            h = self.llm.generate(
                higen_highlight_prompt(document=ch["text"], num_highlights=per_chunk),
                max_new_tokens=highlight_max_tokens,
            )
            return f"[Chunk {i}]\n{h}"

        can_parallel = (
            self.config.summary_highlight_concurrency > 1
            and len(chunks) > 1
            and (self.config.llm_backend == "vllm" or self.config.summary_highlight_parallel_force)
        )
        if can_parallel:
            workers = min(self.config.summary_highlight_concurrency, len(chunks))
            with ThreadPoolExecutor(max_workers=workers) as ex:
                merged_blocks = list(ex.map(_highlight_one, enumerate(chunks, start=1)))
        else:
            merged_blocks = [_highlight_one((i, ch)) for i, ch in enumerate(chunks, start=1)]

        merged_highlights = "\n\n".join(merged_blocks).strip()
        summary = self.llm.generate(
            higen_summary_prompt_by_style(highlights=merged_highlights, style=summary_style),
            max_new_tokens=self.config.max_new_tokens_summary,
        )
        return {
            "file": resolve_document_id(document),
            "result": {
                "highlights": merged_highlights,
                "summary": summary,
                "chunk_count": len(chunks),
            },
        }

    def summarize_document_hybrid_academic(
        self,
        document: Dict[str, Any],
        num_highlights: int = 20,
        chunk_highlights: int = 4,
    ) -> Dict[str, Any]:
        return self.summarize_document_hybrid(
            document=document,
            num_highlights=num_highlights,
            chunk_highlights=chunk_highlights,
            summary_style=self.STYLE_ACADEMIC,
        )

    def summarize_document_hybrid_semantic(
        self,
        document: Dict[str, Any],
        num_highlights: int = 20,
        chunk_highlights: int = 4,
    ) -> Dict[str, Any]:
        return self.summarize_document_hybrid(
            document=document,
            num_highlights=num_highlights,
            chunk_highlights=chunk_highlights,
            summary_style=self.STYLE_SEMANTIC,
        )

    def summarize_document_hybrid_executive(
        self,
        document: Dict[str, Any],
        num_highlights: int = 20,
        chunk_highlights: int = 4,
    ) -> Dict[str, Any]:
        return self.summarize_document_hybrid(
            document=document,
            num_highlights=num_highlights,
            chunk_highlights=chunk_highlights,
            summary_style=self.STYLE_EXECUTIVE,
        )

    def summarize_multi_documents(self, documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        per_doc = []
        for doc in documents:
            chunks = chunk_document(doc, self.config.chunk_size_chars, self.config.chunk_overlap_chars)
            chunk_summaries = []
            for ch in chunks:
                chunk_summaries.append(
                    self.llm.generate(chunk_summary_prompt(ch["text"]), max_new_tokens=min(260, self.config.max_new_tokens_summary))
                )
            doc_summary = self.llm.generate(
                document_summary_prompt("\n".join(chunk_summaries)),
                max_new_tokens=self.config.max_new_tokens_summary,
            )
            per_doc.append({"file": resolve_document_id(doc), "chunk_summaries": chunk_summaries, "summary": doc_summary})

        final = self.llm.generate(
            collection_summary_prompt("\n\n".join([x["summary"] for x in per_doc])),
            max_new_tokens=self.config.max_new_tokens_summary,
        )
        return {"document_summaries": per_doc, "final_summary": final}
