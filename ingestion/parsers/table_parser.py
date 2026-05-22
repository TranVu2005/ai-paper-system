"""
table_parser.py
---------------
Enrich Table objects extracted by loaders.

Input:  list[Table] từ pdf_loader / docx_loader / html_loader
Output: list[Table] đã được enrich với:
  - header row detection chính xác hơn
  - cell text đã clean (whitespace, ký tự rác từ PDF)
  - data types normalized (float, int, None thay vì string)
  - index theo thứ tự xuất hiện trong document
  - keywords extract từ caption (dùng cho KG)

Strategy:
  - Heuristic để detect header row (first cell is string, < 80% numeric)
  - Regex-based type normalization: percentage → float, integer, None
  - Strip + collapse whitespace cho mọi cell
"""

import re
import logging
from typing import Any

from ingestion.schema.document_schema import Table

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns cho type normalization
# ---------------------------------------------------------------------------

# Percentage: "73.2%", "73.2 %" → 0.732
RE_PERCENTAGE = re.compile(r"^([+-]?\d+\.?\d*)\s*%$")

# Float với optional scientific: "3.14", "-0.5", "17.6e9", "+1.2e-3"
RE_FLOAT = re.compile(r"^[+-]?\d+\.\d*([eE][+-]?\d+)?$")

# Integer: "42", "-7", "+100"
RE_INTEGER = re.compile(r"^[+-]?\d+$")

# Scientific notation không có dấu chấm: "1e5", "2E-3"
RE_SCIENTIFIC = re.compile(r"^[+-]?\d+[eE][+-]?\d+$")

# FIX: Thêm en-dash (–), ký tự footnote PDF (*, †, ‡)
RE_NULL = re.compile(
    r"^(-+|–+|—+|n/?a|null|none|undefined|\*+|†+|‡+)$",
    re.IGNORECASE,
)

# Ký tự rác từ PDF extraction
RE_WHITESPACE = re.compile(r"\s+")

# Stop words cho keyword extraction
STOP_WORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "and",
    "or", "but", "is", "are", "was", "were", "with", "from", "by",
    "this", "that", "we", "our", "their", "its", "as", "be", "has",
    "have", "using", "used", "show", "shows", "shown", "each", "per",
    "across", "between", "after", "before", "during", "both", "all",
}

# Short ML/NLP terms quan trọng không bị bỏ
IMPORTANT_SHORT_TERMS = {"f1", "auc", "roc", "map", "nlp", "llm", "rag", "kg"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_tables(tables: list[Table]) -> list[Table]:
    """
    Enrich một list Table objects.

    Args:
        tables: list[Table] từ loader (headers/data thô)

    Returns:
        list[Table] đã enrich — index, clean cells, correct headers, typed data
    """
    if not tables:
        return []

    enriched = [_enrich_table(tbl, idx + 1) for idx, tbl in enumerate(tables)]
    logger.debug("Parsed %d tables", len(enriched))
    return enriched


def parse_table(table: Table, index: int = 0) -> Table:
    """Enrich một Table object đơn lẻ."""
    return _enrich_table(table, index)


# ---------------------------------------------------------------------------
# Core enrichment
# ---------------------------------------------------------------------------

def _enrich_table(table: Table, index: int) -> Table:
    """
    Enrich một Table:
      1. Clean tất cả cells
      2. Detect + fix header row nếu loader đoán sai
      3. Normalize data types trong data rows
      4. Extract keywords từ caption cho KG
    """
    # Step 1: clean raw data
    raw_headers = [_clean_cell(h) for h in table.headers]
    raw_data    = [[_clean_cell(str(cell)) for cell in row] for row in table.data]

    # Step 2: detect header
    headers, data = _fix_headers(raw_headers, raw_data)

    # Step 3: normalize types trong data
    typed_data = [[_normalize_cell(cell) for cell in row] for row in data]

    # Step 4: clean caption + extract keywords
    caption  = _clean_cell(table.caption or "")
    keywords = _extract_keywords(caption)

    return Table(
        caption  = caption,
        index    = index,
        headers  = headers,
        data     = typed_data,
        keywords = keywords,
    )


# ---------------------------------------------------------------------------
# Cell cleaning
# ---------------------------------------------------------------------------

def _clean_cell(text: str) -> str:
    """
    Clean một cell text:
      - Strip leading/trailing whitespace
      - Collapse internal whitespace (tab, newline, multiple spaces)
      - Bỏ ký tự null byte

    Example:
        "  73.2%\n"    → "73.2%"
        "BERT\t base"  → "BERT base"
    """
    if not text:
        return ""
    text = text.replace("\x00", "")          # null bytes từ PDF
    text = RE_WHITESPACE.sub(" ", text)      # collapse whitespace
    return text.strip()


# ---------------------------------------------------------------------------
# Header detection
# ---------------------------------------------------------------------------

def _fix_headers(
    headers: list[str],
    data: list[list[str]],
) -> tuple[list[str], list[list[str]]]:
    """
    Detect và fix header row.

    Cases:
      A. headers đã đúng (loader detect đúng) → giữ nguyên
      B. headers rỗng nhưng data row đầu là header → promote row 0
      C. headers là data thực (toàn số) → đẩy vào data, tạo generic headers

    Returns:
        (headers, data) đã được fix
    """
    # Case A: headers có vẻ đúng
    if headers and _looks_like_header(headers):
        return headers, data

    # Case B: không có headers, check row đầu tiên
    if not headers and data:
        if _looks_like_header(data[0]):
            logger.debug("Header detected from first data row")
            return data[0], data[1:]

    # Case C: headers là số (loader nhầm data row làm header)
    if headers and _looks_like_data_row(headers):
        logger.debug("Headers look like data — promoting to data, generating generic headers")
        col_count   = len(headers)
        new_headers = [f"Column {i + 1}" for i in range(col_count)]
        return new_headers, [headers] + data

    # Fallback: mixed headers — giữ nguyên nhưng log để debug
    logger.debug("Mixed headers kept as-is: %s", headers)
    return headers, data


def _looks_like_header(row: list[str]) -> bool:
    """
    Heuristic: row này có phải header không?

    FIX: Ưu tiên check cell đầu tiên — academic tables luôn có label ở cột đầu.
    Threshold numeric tăng lên 80% (từ 60%) để tránh miss header dạng:
        ["Method", "84.2", "86.1", "87.3"]  # 3/4 numeric nhưng vẫn là header

    Returns True nếu row có vẻ là header.
    """
    if not row:
        return False

    non_empty = [c for c in row if c.strip()]
    if not non_empty:
        return False

    # FIX: Nếu cell đầu tiên là string rõ ràng → gần chắc là header
    if non_empty and not _is_purely_numeric(non_empty[0]):
        # Vẫn check thêm: không phải null value
        if not RE_NULL.match(non_empty[0]):
            return True

    # FIX: Threshold 80% (từ 60%) — stricter
    numeric_count = sum(1 for c in non_empty if _is_purely_numeric(c))
    if numeric_count / len(non_empty) > 0.8:
        return False

    # Nếu tất cả cells đều rỗng hoặc "-" → không phải header
    meaningful = [c for c in non_empty if not RE_NULL.match(c)]
    if not meaningful:
        return False

    return True


def _looks_like_data_row(row: list[str]) -> bool:
    """Ngược lại với _looks_like_header."""
    return not _looks_like_header(row)


def _is_purely_numeric(text: str) -> bool:
    """Check nếu text là số thuần túy (int, float, percentage, scientific)."""
    text = text.strip()
    return bool(
        RE_INTEGER.match(text)    or
        RE_FLOAT.match(text)      or
        RE_PERCENTAGE.match(text) or
        RE_SCIENTIFIC.match(text)
    )


# ---------------------------------------------------------------------------
# Type normalization
# ---------------------------------------------------------------------------

def _normalize_cell(text: str) -> Any:
    """
    Normalize một cell string sang Python type phù hợp.

    Conversion rules:
      ""  / "-" / "N/A" / "–"  → None
      "73.2%"                   → 0.732  (float)
      "3.14" / "17.6e9"         → float
      "42"                      → 42     (int)
      "1e5"                     → 100000.0 (float)
      "BERT base"               → "BERT base" (str, giữ nguyên)

    Args:
        text: cell string đã clean

    Returns:
        None | int | float | str
    """
    if not text or RE_NULL.match(text):
        return None

    # Percentage → float (73.2% → 0.732)
    m = RE_PERCENTAGE.match(text)
    if m:
        return round(float(m.group(1)) / 100, 6)

    # Float (bao gồm scientific có dấu chấm: "17.6e9")
    if RE_FLOAT.match(text):
        try:
            return float(text)
        except ValueError:
            pass

    # Scientific notation không có dấu chấm: "1e5", "2E-3"
    if RE_SCIENTIFIC.match(text):
        try:
            return float(text)
        except ValueError:
            pass

    # Integer
    if RE_INTEGER.match(text):
        try:
            return int(text)
        except ValueError:
            pass

    # String — giữ nguyên
    return text


# ---------------------------------------------------------------------------
# Keyword extraction (cho KG)
# ---------------------------------------------------------------------------

def _extract_keywords(caption: str) -> list[str]:
    """
    Extract keywords từ caption để dùng cho Knowledge Graph.

    FIX: Thêm whitelist IMPORTANT_SHORT_TERMS để giữ "f1", "auc", v.v.

    Example:
        "Table 1: BLEU scores comparison on WMT14 dataset"
        → ["bleu", "scores", "comparison", "wmt14", "dataset"]
    """
    if not caption:
        return []

    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]*", caption.lower())

    keywords = []
    seen     = set()
    for token in tokens:
        if token in STOP_WORDS:
            continue
        # FIX: giữ short terms quan trọng, bỏ những từ ngắn khác
        if len(token) <= 2 and token not in IMPORTANT_SHORT_TERMS:
            continue
        if token not in seen:
            seen.add(token)
            keywords.append(token)

    return keywords[:10]


# ---------------------------------------------------------------------------
# Quick self-test (run: python table_parser.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")

    # Test 1: loader đoán đúng header
    t1 = Table(
        caption = "Table 1: BLEU scores on WMT14",
        headers = ["Model", "BLEU", "ROUGE-L", "F1"],
        data    = [
            ["BERT base",  "73.2%", "68.5%", "71.0%"],
            ["GPT-2",      "75.1%", "70.2%", "72.6%"],
            ["Our model",  "78.4%", "74.1%", "76.2%"],
        ],
    )

    # Test 2: loader không có header, row đầu là header
    t2 = Table(
        caption = "Table 2: Parameter counts",
        headers = [],
        data    = [
            ["Model",     "Params",   "FLOPs"],
            ["ResNet-50",  "25.6",    "4.1e9"],
            ["ViT-B/16",   "86.6",    "17.6e9"],
        ],
    )

    # Test 3: loader nhầm data row làm header
    t3 = Table(
        caption = "Table 3: Accuracy results",
        headers = ["89.2", "91.4", "87.6"],
        data    = [
            ["90.1", "92.0", "88.3"],
            ["88.7", "90.5", "86.9"],
        ],
    )

    # Test 4: cells rác từ PDF
    t4 = Table(
        caption = "  Table  4 : Ablation  study  ",
        headers = ["  Method  ", "Acc\t(%)", "F1\n"],
        data    = [
            ["  Baseline  ", "  82.3%  ", "  -  "],
            ["+ Attention",  "85.1%",     "84.2%"],
        ],
    )

    # Test 5: FIX — en-dash và footnote symbols từ PDF
    t5 = Table(
        caption = "Table 5: AUC and ROC comparison",
        headers = ["Method", "AUC", "ROC"],
        data    = [
            ["Model A", "0.92",  "–"],       # en-dash → None
            ["Model B", "†",     "0.88"],    # dagger → None
            ["Model C", "0.95",  "0.94"],
        ],
    )

    # Test 6: FIX — header dạng mixed ["Method", "84.2", "86.1", "87.3"]
    t6 = Table(
        caption = "Table 6: F1 scores per class",
        headers = ["Method", "84.2", "86.1", "87.3"],
        data    = [
            ["Baseline", "82.1", "85.0", "86.2"],
        ],
    )

    print("=" * 60)
    for tbl in parse_tables([t1, t2, t3, t4, t5, t6]):
        print(f"\n[Table {tbl.index}] {tbl.caption}")
        print(f"  Headers  : {tbl.headers}")
        print(f"  Keywords : {tbl.keywords}")
        for row in tbl.data:
            print(f"  Row      : {row}")
    print("\n" + "=" * 60)
