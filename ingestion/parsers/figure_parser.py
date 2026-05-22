"""
figure_parser.py
----------------
Enrich Figure objects extracted by loaders.

Input:  list[Figure] từ pdf_loader / docx_loader / html_loader
Output: list[Figure] đã được enrich với:
  - figure_type chính xác hơn (image | chart | diagram)
  - caption đã clean (bỏ "Figure 3:", "Fig.", số thứ tự)
  - index theo thứ tự xuất hiện trong document
  - keywords extract từ caption (dùng cho KG)

Strategy:
  - Keyword matching trên caption để classify figure_type
  - Regex để clean caption prefix
  - Không dùng OCR hay image analysis (caption đủ cho RAG/KG)
"""

import re
import logging
from typing import Optional
from dataclasses import dataclass, field

from ingestion.schema.document_schema import Figure

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keywords để classify figure_type từ caption
# ---------------------------------------------------------------------------

CHART_KEYWORDS = [
    "accuracy", "loss", "curve", "plot", "graph", "performance",
    "training", "validation", "score", "metric", "comparison",
    "bar chart", "line chart", "pie chart", "histogram", "scatter",
    "f1", "precision", "recall", "auc", "roc", "bleu", "rouge",
    "throughput", "latency", "speedup", "benchmark",
]

DIAGRAM_KEYWORDS = [
    "architecture", "framework", "overview", "pipeline", "workflow",
    "diagram", "structure", "design", "system", "network", "model",
    "flowchart", "flow", "process", "module", "component", "layer",
    "encoder", "decoder", "attention", "block", "schema", "hierarchy",
]

TABLE_LIKE_KEYWORDS = [
    "table", "matrix", "confusion matrix", "grid",
]

# Regex để bỏ prefix kiểu "Figure 3:", "Fig. 2 —", "Fig 1."
CAPTION_PREFIX_PATTERN = re.compile(
    r"^(fig(ure)?\.?\s*\d+[\.\:\-\—\s]+)",
    flags=re.IGNORECASE,
)

# Regex lấy keywords từ caption (chỉ giữ từ có nghĩa, bỏ stop words)
STOP_WORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "and",
    "or", "but", "is", "are", "was", "were", "with", "from", "by",
    "this", "that", "we", "our", "their", "its", "as", "be", "has",
    "have", "using", "used", "show", "shows", "shown", "each", "per",
    "across", "between", "after", "before", "during", "both", "all",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_figures(figures: list[Figure]) -> list[Figure]:
    """
    Enrich một list Figure objects.

    Args:
        figures: list[Figure] từ loader (caption thô, figure_type thô)

    Returns:
        list[Figure] đã enrich — index, clean caption, figure_type, keywords
    """
    if not figures:
        return []

    enriched = []
    for idx, figure in enumerate(figures):
        enriched.append(_enrich_figure(figure, index=idx + 1))

    logger.debug("Parsed %d figures", len(enriched))
    return enriched


def parse_figure(figure: Figure, index: int = 0) -> Figure:
    """
    Enrich một Figure object đơn lẻ.

    Args:
        figure: Figure object từ loader
        index:  Thứ tự xuất hiện trong document (0-based)

    Returns:
        Figure đã enrich
    """
    return _enrich_figure(figure, index=index)


# ---------------------------------------------------------------------------
# Core enrichment
# ---------------------------------------------------------------------------

def _enrich_figure(figure: Figure, index: int) -> Figure:
    """Enrich một Figure: clean caption, classify type, extract keywords."""
    raw_caption   = figure.caption or ""
    clean_caption = _clean_caption(raw_caption)
    figure_type   = _classify_figure_type(clean_caption, figure.figure_type)

    return Figure(
        caption     = clean_caption,
        path        = figure.path,
        base64_data = figure.base64_data,   # giữ lại
        figure_type = figure_type,
        alt_text    = figure.alt_text or _generate_alt_text(clean_caption, figure_type),
        index       = index,
        keywords    = _extract_keywords(clean_caption),
    )


# ---------------------------------------------------------------------------
# Caption cleaning
# ---------------------------------------------------------------------------

def _clean_caption(caption: str) -> str:
    """
    Xóa prefix "Figure 3:", "Fig. 2 —" khỏi caption.

    Examples:
        "Figure 3: Overview of the proposed architecture"
        → "Overview of the proposed architecture"

        "Fig. 2 — Training loss curve on CIFAR-10"
        → "Training loss curve on CIFAR-10"
    """
    caption = caption.strip()
    caption = CAPTION_PREFIX_PATTERN.sub("", caption).strip()

    # Bỏ dấu ":" hoặc "—" còn sót ở đầu
    caption = re.sub(r"^[\:\-\—\s]+", "", caption).strip()

    return caption


# ---------------------------------------------------------------------------
# Figure type classification
# ---------------------------------------------------------------------------

def _classify_figure_type(caption: str, current_type: str) -> str:
    """
    Classify figure_type dựa trên caption.

    Priority:
      1. Nếu caption chứa table-like keywords → "table"
      2. Nếu caption chứa diagram keywords    → "diagram"
      3. Nếu caption chứa chart keywords      → "chart"
      4. Giữ nguyên current_type nếu không match

    Args:
        caption:      Caption đã clean
        current_type: figure_type hiện tại từ loader

    Returns:
        "image" | "chart" | "diagram" | "table"
    """
    caption_lower = caption.lower()

    if any(kw in caption_lower for kw in TABLE_LIKE_KEYWORDS):
        return "table"

    if any(kw in caption_lower for kw in DIAGRAM_KEYWORDS):
        return "diagram"

    if any(kw in caption_lower for kw in CHART_KEYWORDS):
        return "chart"

    # Giữ nguyên nếu loader đã classify không phải default "image"
    if current_type and current_type != "image":
        return current_type

    return "image"


# ---------------------------------------------------------------------------
# Alt text generation
# ---------------------------------------------------------------------------

def _generate_alt_text(caption: str, figure_type: str) -> str:
    """
    Tạo alt_text từ caption nếu chưa có.
    Dùng cho accessibility và multimodal pipeline.

    Example:
        caption="Overview of the proposed architecture", type="diagram"
        → "Diagram: Overview of the proposed architecture"
    """
    if not caption:
        return f"{figure_type.capitalize()} from paper"

    # Truncate nếu quá dài
    truncated = caption if len(caption) <= 120 else caption[:117] + "..."
    return f"{figure_type.capitalize()}: {truncated}"


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------

def _extract_keywords(caption: str) -> list[str]:
    """
    Extract keywords từ caption để dùng cho Knowledge Graph.

    Strategy:
      - Tokenize caption thành words
      - Bỏ stop words và từ ngắn (<= 2 ký tự)
      - Lowercase và deduplicate
      - Giữ tối đa 10 keywords

    Example:
        "Comparison of BLEU scores across translation models"
        → ["comparison", "bleu", "scores", "translation", "models"]
    """
    if not caption:
        return []

    # Tokenize: chỉ giữ chữ cái và số
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]*", caption.lower())

    keywords = []
    seen     = set()
    for token in tokens:
        if token in STOP_WORDS:
            continue
        if len(token) <= 2:
            continue
        if token not in seen:
            seen.add(token)
            keywords.append(token)

    return keywords[:10]


# ---------------------------------------------------------------------------
# Quick self-test (run: python figure_parser.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.DEBUG,
                        format="%(levelname)s | %(message)s")

    test_cases = [
        Figure(caption="Figure 1: Overview of the proposed architecture",     figure_type="image"),
        Figure(caption="Fig. 2 — Training loss curve on CIFAR-10",            figure_type="image"),
        Figure(caption="Fig 3. Comparison of BLEU scores across models",      figure_type="image"),
        Figure(caption="Figure 4: Confusion matrix on test set",               figure_type="image"),
        Figure(caption="An illustration of the attention mechanism",           figure_type="image"),
        Figure(caption="",                                                     figure_type="image"),
        Figure(caption="Figure 5: Pipeline workflow overview",                 figure_type="diagram"),
    ]

    print("=" * 60)
    results = parse_figures(test_cases)
    for fig in results:
        print(f"[{fig.index}] type={fig.figure_type:<8} | caption='{fig.caption}'")
        print(f"      alt  ='{fig.alt_text}'")
        print(f"      kws  ={fig.keywords}")
        print()
    print("=" * 60)
