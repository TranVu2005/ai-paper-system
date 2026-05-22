from __future__ import annotations

from typing import Any, Callable, Literal

SummaryStyle = Literal["academic", "semantic", "executive"]
SUPPORTED_SUMMARY_STYLES = {"academic", "semantic", "executive"}


def normalize_summary_style(style: str | None) -> SummaryStyle:
    raw = (style or "academic").strip().lower()
    if raw not in SUPPORTED_SUMMARY_STYLES:
        return "academic"
    return raw  # type: ignore[return-value]


def generate_summary_from_doc_json(
    doc_json: dict,
    summary_style: str = "academic",
    num_highlights: int = 16,
    chunk_highlights: int = 4,
) -> str:
    from ai_module.inference.inference_config import InferenceConfig
    from ai_module.summarization.summarizer import Summarizer

    style = normalize_summary_style(summary_style)
    cfg = InferenceConfig()
    summarizer = Summarizer(cfg)
    style_method_map: dict[str, Callable[..., dict[str, Any]]] = {
        "academic": getattr(summarizer, "summarize_document_hybrid_academic", None),
        "semantic": getattr(summarizer, "summarize_document_hybrid_semantic", None),
        "executive": getattr(summarizer, "summarize_document_hybrid_executive", None),
    }
    summarize_fn = style_method_map[style]
    if summarize_fn is None:
        result = summarizer.summarize_document_hybrid(
            doc_json,
            num_highlights=num_highlights,
            chunk_highlights=chunk_highlights,
            summary_style=style,
        )
    else:
        result = summarize_fn(
            doc_json,
            num_highlights=num_highlights,
            chunk_highlights=chunk_highlights,
        )
    text = (
        result.get("result", {}).get("summary")
        or result.get("result", {}).get("highlights")
        or ""
    )
    out = str(text).strip()
    if not out:
        raise RuntimeError("AI module returned empty summary.")
    return out
