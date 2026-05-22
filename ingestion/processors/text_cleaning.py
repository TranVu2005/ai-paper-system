"""
text_cleaning.py
----------------
Clean text content trong UnifiedDocument sau parser layer.

Input:  UnifiedDocument (sau loaders + parsers + section_splitter)
Output: UnifiedDocument mới với text đã clean (immutable — dùng dataclasses.replace)

Cleaning pipeline (theo thứ tự áp dụng):
  1. Unicode normalization
       - NFC normalization
       - Ligature expansion (ﬁ→fi, ﬀ→ff, ...) — chỉ typography ligatures, không æ/œ
       - Smart quotes / curly apostrophe → ASCII
       - Zero-width / invisible chars removal
       - Non-breaking space → regular space

  2. PDF artifacts
       - Dehyphenation: "meth-\nod" → "method"
       - Broken line rejoining: dòng bị ngắt giữa câu → nối lại
       - Header/footer noise: page numbers, running titles
       - Repeated whitespace từ column layout

  3. Boilerplate removal
       - Page numbers (standalone số / "Page N of M")
       - Copyright lines ("© 2023 ...", "All rights reserved")
       - Running headers/footers (lặp lại across sections)
       - Submission artifacts ("Preprint", "Under review", "arXiv:")
       - Email / URL lines đứng một mình trong body

  4. Whitespace normalization
       - Tabs → spaces
       - Multiple spaces → single space
       - 3+ blank lines → 2 blank lines
       - Strip leading/trailing whitespace per section

Fields được clean:
  - doc.abstract
  - doc.sections[*].content
  - doc.sections[*].name  (nhẹ hơn — chỉ whitespace + unicode)
  - doc.figures[*].caption
  - doc.tables[*].caption

Fields KHÔNG clean:
  - doc.formulas[*].raw  (LaTeX/MathML — không touch)
  - doc.references        (giữ nguyên format)
  - bibliographic fields  (title, authors, journal — minimal clean only)
"""

import logging
import re
import unicodedata
from dataclasses import replace
from typing import Callable

from ingestion.schema.document_schema import Figure, Section, Table, UnifiedDocument

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# OCR diacritics fix: PaddleOCR PP-OCRv5 server model nhầm dấu thanh tiếng Việt
# thành macron (ˉ) và caron (ˇ) — các ký tự này KHÔNG tồn tại trong tiếng Việt.
#
# Mapping: non-Vietnamese diacritic → closest Vietnamese base vowel
#   macron vowels: ē→ê, ō→ô, ā→â, ū→ư (nhầm circumflex/horn thành macron)
#   caron vowels:  ǎ→ă, ǔ→ủ, ǒ→ỏ, ī→ị
#
# Lưu ý: mapping chỉ sửa nguyên âm gốc (base vowel), KHÔNG phục hồi được dấu
# thanh chính xác (vd: "hê" thay vì "hệ"). Tuy nhiên đây là safe replacement
# vì macron/caron vowels không bao giờ hợp lệ trong tiếng Việt.
_OCR_DIACRITIC_MAP: dict[str, str] = {
    # Lowercase macron → Vietnamese circumflex/horn
    "\u0113": "\u00ea",  # ē → ê (macron → circumflex)
    "\u014d": "\u00f4",  # ō → ô (macron → circumflex)
    "\u0101": "\u00e2",  # ā → â (macron → circumflex)
    "\u016b": "\u01b0",  # ū → ư (macron → horn)
    # Lowercase caron → Vietnamese breve/tone marks
    "\u01ce": "\u0103",  # ǎ → ă (caron → breve)
    "\u01d4": "\u1ee7",  # ǔ → ủ (caron u → hỏi tone)
    "\u01d2": "\u1ecf",  # ǒ → ỏ (caron o → hỏi tone)
    "\u012b": "\u1ecb",  # ī → ị (macron i → nặng tone)
    # Uppercase equivalents
    "\u0112": "\u00ca",  # Ē → Ê
    "\u014c": "\u00d4",  # Ō → Ô
    "\u0100": "\u00c2",  # Ā → Â
    "\u016a": "\u01af",  # Ū → Ư
    "\u01cd": "\u0102",  # Ǎ → Ă
    "\u01d3": "\u1ee6",  # Ǔ → Ủ
    "\u01d1": "\u1ece",  # Ǒ → Ỏ
    "\u012a": "\u1eca",  # Ī → Ị
}

# Precompile regex for fast OCR diacritic detection
_OCR_DIACRITIC_CHARS = re.compile(
    "[" + re.escape("".join(_OCR_DIACRITIC_MAP.keys())) + "]"
)

# Unicode ligature map — chỉ typography ligatures từ PDF rendering
# KHÔNG bao gồm æ/œ vì chúng là legitimate Unicode trong tên người, địa danh, journal names
_LIGATURE_MAP: dict[str, str] = {
    "\ufb00": "ff",   # ﬀ
    "\ufb01": "fi",   # ﬁ
    "\ufb02": "fl",   # ﬂ
    "\ufb03": "ffi",  # ﬃ
    "\ufb04": "ffl",  # ﬄ
    "\ufb05": "st",   # ﬅ
    "\ufb06": "st",   # ﬆ
    "\u0132": "IJ",   # Ĳ
    "\u0133": "ij",   # ĳ
    # æ (\u00e6) và œ (\u0153) bị loại bỏ — xuất hiện hợp lệ trong tên người và journal names
}

# Smart quotes / typographic punctuation → ASCII
_QUOTE_MAP: dict[str, str] = {
    "\u2018": "'",   # '  left single
    "\u2019": "'",   # '  right single / apostrophe
    "\u201a": "'",   # ‚  single low
    "\u201b": "'",   # ‛  single high reversed
    "\u201c": '"',   # "  left double
    "\u201d": '"',   # "  right double
    "\u201e": '"',   # „  double low
    "\u201f": '"',   # ‟  double high reversed
    "\u2033": '"',   # ″  double prime
    "\u2032": "'",   # ′  prime
    "\u00ab": '"',   # «  left angle quote
    "\u00bb": '"',   # »  right angle quote
    "\u2039": "'",   # ‹  left single angle
    "\u203a": "'",   # ›  right single angle
    "\u2014": "--",  # —  em dash
    "\u2013": "-",   # –  en dash
    "\u2026": "...", # …  ellipsis
    "\u00ad": "",    # ­  soft hyphen (xoá)
}

# Zero-width / invisible characters cần xoá
# FIX: bỏ \u00a0 khỏi đây vì được handle riêng bởi _NBSP → space (không xoá)
_INVISIBLE_CHARS = re.compile(
    r"[\u200b\u200c\u200d\u200e\u200f"   # zero-width space/joiner/non-joiner/LRM/RLM
    r"\ufeff"                             # BOM
    r"\u00b7"                             # middle dot (bullet trong một số PDF)
    r"\u2060\u2061\u2062\u2063\u2064"    # word joiner, function application
    r"]"
)

# Non-breaking space → regular space (xử lý trước _INVISIBLE_CHARS)
_NBSP = re.compile(r"\u00a0")

# Dehyphenation: "meth-\nod" → "method"
_HYPHEN_NEWLINE = re.compile(r"(\w)-\n\s*(\w)")

# Broken line rejoin: dòng không kết thúc bằng dấu câu + dòng tiếp theo bắt đầu chữ thường
_BROKEN_LINE = re.compile(r"([a-z,])\n([a-z\(])")

# Page number patterns
_PAGE_NUMBER = re.compile(
    r"(?m)^[\s]*"
    r"(?:"
    r"Page\s+\d+\s+of\s+\d+"          # "Page 3 of 10"
    r"|Page\s+\d+"                      # "Page 3"
    r"|\d+\s*/\s*\d+"                  # "3/10"
    r"|[-–]\s*\d+\s*[-–]"             # "- 3 -"
    r"|\[\s*\d+\s*\]"                  # "[3]" standalone
    r")"
    r"[\s]*$"
)

# Copyright / legal boilerplate
_COPYRIGHT = re.compile(
    r"(?m)^[^\n]*"
    r"(?:"
    r"©|Copyright\s+\d{4}"
    r"|All\s+rights\s+reserved"
    r"|Permission\s+to\s+(?:make|copy|reproduce)"
    r"|Licensed\s+under"
    r"|This\s+work\s+is\s+licensed"
    r"|Creative\s+Commons"
    r")"
    r"[^\n]*$",
    re.IGNORECASE,
)

# Preprint / submission artifacts
_PREPRINT = re.compile(
    r"(?m)^[^\n]*"
    r"(?:"
    r"Preprint[.\s]"
    r"|Under\s+review"
    r"|Submitted\s+to"
    r"|arXiv:\d{4}\.\d+"
    r"|DRAFT[:\s]"
    r"|Confidential[:\s]"
    r")"
    r"[^\n]*$",
    re.IGNORECASE,
)

# Standalone URLs trên một dòng riêng (trong body text)
_STANDALONE_URL = re.compile(
    r"(?m)^\s*https?://\S+\s*$"
)

# Standalone email trên một dòng riêng
_STANDALONE_EMAIL = re.compile(
    r"(?m)^\s*[\w.+-]+@[\w-]+\.[\w.]+\s*$"
)

# Repeated whitespace trong cùng một dòng
_MULTI_SPACE = re.compile(r"[ \t]{2,}")

# 3+ blank lines → 2 blank lines
_MULTI_BLANK = re.compile(r"\n{3,}")

# Running header/footer detection threshold
_RUNNING_HEADER_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def clean_document(
    doc: UnifiedDocument,
    aggressive: bool = False,
) -> UnifiedDocument:
    """
    Clean toàn bộ text content trong UnifiedDocument.

    Args:
        doc:        UnifiedDocument sau loaders + parsers + section_splitter
        aggressive: True = bật thêm broken-line rejoin và URL/email removal

    Returns:
        UnifiedDocument mới với text đã clean.
        Nếu không có gì thay đổi → trả về doc gốc (object identity).
    """
    updates: dict = {}

    # --- Abstract ---
    if doc.abstract:
        cleaned = _clean_body_text(doc.abstract, aggressive=aggressive)
        if cleaned != doc.abstract:
            updates["abstract"] = cleaned

    # --- Sections ---
    if doc.sections:
        cleaned_sections, sections_changed = _clean_sections(doc.sections, aggressive=aggressive)
        if sections_changed:
            updates["sections"] = cleaned_sections

    # --- Figure captions ---
    if doc.figures:
        cleaned_figures, figures_changed = _clean_figures(doc.figures)
        if figures_changed:
            updates["figures"] = cleaned_figures

    # --- Table captions ---
    if doc.tables:
        cleaned_tables, tables_changed = _clean_tables(doc.tables)
        if tables_changed:
            updates["tables"] = cleaned_tables

    # --- Title (minimal clean) ---
    if doc.title and doc.title != "Unknown":
        cleaned_title = _clean_metadata_string(doc.title)
        if cleaned_title != doc.title:
            updates["title"] = cleaned_title

    # --- Authors (minimal clean per name) ---
    if doc.authors:
        cleaned_authors = [_clean_metadata_string(a) for a in doc.authors]
        if cleaned_authors != doc.authors:
            updates["authors"] = cleaned_authors

    if not updates:
        logger.debug("No cleaning changes for: '%s'", doc.title[:50])
        return doc

    result = replace(doc, **updates)
    logger.info(
        "Cleaned '%s': fields=%s, sections=%d",
        doc.title[:50], list(updates.keys()), len(result.sections),
    )
    return result


def clean_text(text: str, aggressive: bool = False) -> str:
    """
    Clean một plain string độc lập (không cần UnifiedDocument).

    Args:
        text:       Input string
        aggressive: True = bật broken-line rejoin và URL/email removal

    Returns:
        Cleaned string.
    """
    return _clean_body_text(text, aggressive=aggressive)


def clean_documents_batch(
    docs: list[UnifiedDocument],
    aggressive: bool = False,
) -> list[UnifiedDocument]:
    """Clean một batch UnifiedDocuments."""
    return [clean_document(doc, aggressive=aggressive) for doc in docs]


# ---------------------------------------------------------------------------
# Internal: per-field cleaners
# ---------------------------------------------------------------------------

def _clean_sections(
    sections: list[Section],
    aggressive: bool,
) -> tuple[list[Section], bool]:
    """
    Clean content + name của mỗi Section.

    Returns:
        (list[Section], bool changed)
    """
    # Pass 1: detect running headers/footers across all sections
    boilerplate_lines = _detect_running_headers(sections)

    cleaned = []
    changed = False

    for section in sections:
        new_content = _clean_body_text(
            section.content,
            aggressive        = aggressive,
            boilerplate_lines = boilerplate_lines,
        )
        new_name = _clean_heading_text(section.name)

        if new_content != section.content or new_name != section.name:
            cleaned.append(replace(section, content=new_content, name=new_name))
            changed = True
        else:
            cleaned.append(section)

    return (cleaned if changed else sections), changed


def _clean_figures(figures: list[Figure]) -> tuple[list[Figure], bool]:
    """
    Clean caption của mỗi Figure.

    Returns:
        (list[Figure], bool changed)
    """
    cleaned = []
    changed = False
    for figure in figures:
        new_caption = _clean_caption(figure.caption)
        if new_caption != figure.caption:
            cleaned.append(replace(figure, caption=new_caption))
            changed = True
        else:
            cleaned.append(figure)
    return (cleaned if changed else figures), changed


def _clean_tables(tables: list[Table]) -> tuple[list[Table], bool]:
    """
    Clean caption của mỗi Table.

    Returns:
        (list[Table], bool changed)
    """
    cleaned = []
    changed = False
    for table in tables:
        new_caption = _clean_caption(table.caption)
        if new_caption != table.caption:
            cleaned.append(replace(table, caption=new_caption))
            changed = True
        else:
            cleaned.append(table)
    return (cleaned if changed else tables), changed


# ---------------------------------------------------------------------------
# Core cleaning pipeline
# ---------------------------------------------------------------------------

def _clean_body_text(
    text: str,
    aggressive: bool = False,
    boilerplate_lines: set[str] | None = None,
) -> str:
    """
    Full cleaning pipeline cho body text (abstract, section content).

    Pipeline:
      1. Unicode normalization
      2. PDF artifact removal
      3. Boilerplate removal
      4. Running header/footer removal (nếu có boilerplate_lines)
      5. Whitespace normalization
    """
    if not text:
        return text

    text = _normalize_unicode(text)
    text = _remove_pdf_artifacts(text, aggressive=aggressive)
    text = _remove_boilerplate(text, aggressive=aggressive)

    if boilerplate_lines:
        text = _remove_running_headers(text, boilerplate_lines)

    text = _normalize_whitespace(text)
    return text


def _clean_heading_text(text: str) -> str:
    """
    Light clean cho heading/section name.
    Chỉ: unicode normalization + whitespace normalization.
    """
    if not text:
        return text
    text = _normalize_unicode(text)
    text = _normalize_whitespace(text, multiline=False)
    return text.strip()


def _clean_caption(text: str) -> str:
    """
    Clean cho figure/table captions.
    Unicode + whitespace — giữ nguyên content, không remove URLs.
    """
    if not text:
        return text
    text = _normalize_unicode(text)
    text = _normalize_whitespace(text, multiline=False)
    return text.strip()


def _clean_metadata_string(text: str) -> str:
    """
    Minimal clean cho metadata strings (title, author names).
    Chỉ unicode normalization + whitespace collapse.
    """
    if not text:
        return text
    text = _normalize_unicode(text)
    text = _MULTI_SPACE.sub(" ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Step 1: Unicode normalization
# ---------------------------------------------------------------------------

def _normalize_unicode(text: str) -> str:
    """
    1a. NFC normalization
    1b. OCR diacritics fix (macron/caron → Vietnamese base vowels)
    1c. Ligature expansion (ﬁ→fi, ﬀ→ff, ...) — chỉ typography ligatures
    1d. Smart quotes → ASCII
    1e. Non-breaking space → regular space
    1f. Zero-width / invisible chars removal
    """
    # NFC
    text = unicodedata.normalize("NFC", text)

    # OCR diacritics fix (PaddleOCR PP-OCRv5 nhầm dấu thanh tiếng Việt)
    # macron/caron vowels → Vietnamese base vowels (safe: these chars never valid in VN)
    text = _fix_ocr_diacritics(text)

    # Ligatures (chỉ PDF typography artifacts)
    for ligature, replacement in _LIGATURE_MAP.items():
        if ligature in text:
            text = text.replace(ligature, replacement)

    # Smart quotes / typographic punctuation
    for char, replacement in _QUOTE_MAP.items():
        if char in text:
            text = text.replace(char, replacement)

    # Non-breaking space → space (trước invisible removal)
    text = _NBSP.sub(" ", text)

    # Zero-width / invisible chars (không bao gồm \u00a0 — đã xử lý trên)
    text = _INVISIBLE_CHARS.sub("", text)

    return text


def _fix_ocr_diacritics(text: str) -> str:
    """
    Fix lỗi dấu thanh tiếng Việt từ PaddleOCR PP-OCRv5.

    PaddleOCR server model nhận diện sai dấu thanh tiếng Việt thành
    macron (ˉ) và caron (ˇ) — các ký tự không hợp lệ trong tiếng Việt:
      ē → ê, ō → ô, ā → â, ū → ư  (macron → circumflex/horn)
      ǎ → ă, ǔ → ủ, ǒ → ỏ, ī → ị  (caron → breve/tone)

    Chỉ chạy replacement khi phát hiện ký tự bất thường trong text,
    tránh overhead cho text đã sạch.
    """
    # Quick check: có ký tự macron/caron không? Nếu không → skip
    if not _OCR_DIACRITIC_CHARS.search(text):
        return text

    # Replace từng ký tự bất thường
    for bad_char, good_char in _OCR_DIACRITIC_MAP.items():
        if bad_char in text:
            text = text.replace(bad_char, good_char)

    logger.debug("Fixed OCR diacritics in text")
    return text


# ---------------------------------------------------------------------------
# Step 2: PDF artifact removal
# ---------------------------------------------------------------------------

def _remove_pdf_artifacts(text: str, aggressive: bool = False) -> str:
    """
    2a. Dehyphenation: "meth-\nod" → "method"
    2b. Broken line rejoin (aggressive only)
    2c. Tab → space
    """
    # Dehyphenation (luôn bật — safe)
    text = _HYPHEN_NEWLINE.sub(r"\1\2", text)

    # Broken line rejoin (aggressive mode)
    if aggressive:
        text = _BROKEN_LINE.sub(r"\1 \2", text)

    # Tab → space
    text = text.replace("\t", " ")

    return text


# ---------------------------------------------------------------------------
# Step 3: Boilerplate removal
# ---------------------------------------------------------------------------

def _remove_boilerplate(text: str, aggressive: bool = False) -> str:
    """
    3a. Page numbers
    3b. Copyright / legal lines
    3c. Preprint / submission artifacts
    3d. Standalone URLs (aggressive only)
    3e. Standalone emails (aggressive only)
    """
    text = _PAGE_NUMBER.sub("", text)
    text = _COPYRIGHT.sub("", text)
    text = _PREPRINT.sub("", text)

    if aggressive:
        text = _STANDALONE_URL.sub("", text)
        text = _STANDALONE_EMAIL.sub("", text)

    return text


# ---------------------------------------------------------------------------
# Step 3b: Running header/footer detection & removal
# ---------------------------------------------------------------------------

def _detect_running_headers(sections: list[Section]) -> set[str]:
    """
    Detect running headers/footers: lines xuất hiện >= threshold sections.

    Returns:
        set[str] các lines được coi là boilerplate.
    """
    from collections import Counter

    line_counts: Counter = Counter()

    for section in sections:
        if not section.content:
            continue
        lines = {
            line.strip()
            for line in section.content.split("\n")
            if 3 < len(line.strip()) < 120
        }
        line_counts.update(lines)

    boilerplate = {
        line
        for line, count in line_counts.items()
        if count >= _RUNNING_HEADER_THRESHOLD
    }

    if boilerplate:
        logger.debug("Detected %d running header/footer lines", len(boilerplate))

    return boilerplate


def _remove_running_headers(text: str, boilerplate_lines: set[str]) -> str:
    """Remove các lines được detect là running header/footer."""
    if not boilerplate_lines:
        return text

    result_lines = []
    for line in text.split("\n"):
        if line.strip() not in boilerplate_lines:
            result_lines.append(line)

    return "\n".join(result_lines)


# ---------------------------------------------------------------------------
# Step 4: Whitespace normalization
# ---------------------------------------------------------------------------

def _normalize_whitespace(text: str, multiline: bool = True) -> str:
    """
    4a. Multiple spaces → single space
    4b. 3+ blank lines → 2 blank lines (multiline mode)
    4c. Strip leading/trailing whitespace
    """
    text = _MULTI_SPACE.sub(" ", text)

    if multiline:
        text = _MULTI_BLANK.sub("\n\n", text)

    return text.strip()


# ---------------------------------------------------------------------------
# Quick self-test (run: python text_cleaning.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")

    # ------------------------------------------------------------------
    print("=" * 60)
    print("TEST A: Unicode normalization")
    print("=" * 60)

    unicode_cases = [
        ("\ufb01ne-tuning with \ufb00ow",    "fine-tuning with flow"),   # ligatures
        ("\u201cHello\u201d it\u2019s fine", '"Hello" it\'s fine'),      # smart quotes
        ("hello\u200bworld",                  "helloworld"),              # zero-width space
        ("non\u00a0breaking",                 "non breaking"),            # NBSP → space
        ("em\u2014dash and en\u2013dash",     "em--dash and en-dash"),    # dashes
    ]

    all_pass = True
    for input_text, expected in unicode_cases:
        result = _normalize_unicode(input_text)
        status = "✓" if result == expected else f"✗ got: '{result}'"
        print(f"  '{input_text}' → '{result}' {status}")
        if result != expected:
            all_pass = False
    print(f"  {'All pass ✓' if all_pass else 'Some failed ✗'}")

    # FIX: verify æ/œ KHÔNG bị convert — legitimate Unicode characters
    assert _normalize_unicode("caf\u00e9") == "caf\u00e9",    "é should be preserved"
    assert _normalize_unicode("S\u00f8ren") == "S\u00f8ren",  "ø should be preserved"
    assert _normalize_unicode("\u00e6sop") == "\u00e6sop",     "æ should be preserved"
    assert _normalize_unicode("\u0153uvre") == "\u0153uvre",   "œ should be preserved"
    print("  ✓ æ/œ/accents preserved correctly")

    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("TEST B: PDF artifact removal")
    print("=" * 60)

    hyphen_text = "The trans-\nformer model uses self-at-\ntention mech-\nanism."
    result_b = _remove_pdf_artifacts(hyphen_text)
    print(f"  Input:  {repr(hyphen_text)}")
    print(f"  Output: {repr(result_b)}")
    assert "transformer" in result_b, "Dehyphenation failed"
    assert "self-attention" in result_b
    print("  ✓ Dehyphenation correct")

    broken_text = "This is a long sen-\ntence that was broken\nacross multiple lines."
    result_b2 = _remove_pdf_artifacts(broken_text, aggressive=True)
    print(f"\n  Broken line (aggressive):")
    print(f"  Input:  {repr(broken_text)}")
    print(f"  Output: {repr(result_b2)}")

    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("TEST C: Boilerplate removal")
    print("=" * 60)

    boilerplate_text = """
This is the main content of the paper.

Page 3 of 10

© 2023 Association for Computing Machinery. All rights reserved.

The methodology section describes our approach.

Preprint. Under review at NeurIPS 2024.

We evaluate on three benchmark datasets.

https://github.com/some/repo

More content follows here with detailed analysis.
"""
    result_c = _remove_boilerplate(boilerplate_text, aggressive=True)
    result_c = _normalize_whitespace(result_c)
    print(f"  Input lines:  {len(boilerplate_text.splitlines())}")
    print(f"  Output lines: {len(result_c.splitlines())}")
    print(f"  Output:\n{result_c}")
    assert "Page 3 of 10" not in result_c,       "Page number not removed"
    assert "All rights reserved" not in result_c, "Copyright not removed"
    assert "Under review" not in result_c,         "Preprint not removed"
    print("  ✓ Boilerplate removed correctly")

    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("TEST D: Running header detection")
    print("=" * 60)

    from ingestion.schema.document_schema import Section

    sections_d = [
        Section(name="Introduction", order=0, level=1,
                content="Neural Networks\nThis section introduces the topic.\nNeural Networks"),
        Section(name="Method",       order=1, level=1,
                content="Neural Networks\nWe propose a new method.\nNeural Networks"),
        Section(name="Results",      order=2, level=1,
                content="Neural Networks\nExperiments show improvements.\nNeural Networks"),
        Section(name="Conclusion",   order=3, level=1,
                content="Neural Networks\nWe conclude the paper.\nNeural Networks"),
    ]
    headers = _detect_running_headers(sections_d)
    print(f"  Detected boilerplate: {headers}")
    assert "Neural Networks" in headers, "Should detect 'Neural Networks' as running header"
    print("  ✓ Running header detected correctly")

    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("TEST E: changed flag — _clean_figures / _clean_tables")
    print("=" * 60)

    from ingestion.schema.document_schema import Figure, Table

    # Figures: one needs cleaning, one doesn't
    figs = [
        Figure(caption="\u201cFig 1\u201d: Clean this",  index=0),  # has smart quotes
        Figure(caption="Fig 2: Already clean",            index=1),  # no change needed
    ]
    cleaned_figs, figs_changed = _clean_figures(figs)
    assert figs_changed,                              "Should detect change"
    assert cleaned_figs[0].caption == '"Fig 1": Clean this', "Smart quotes not converted"
    assert cleaned_figs[1].caption == "Fig 2: Already clean", "Should not change clean caption"
    print("  ✓ _clean_figures changed flag correct")

    # Tables: nothing needs cleaning
    tables_clean = [Table(caption="Table 1: Clean", index=0)]
    cleaned_tbls, tbls_changed = _clean_tables(tables_clean)
    assert not tbls_changed, "Should not flag change when nothing to clean"
    assert cleaned_tbls is tables_clean, "Should return same object when unchanged"
    print("  ✓ _clean_tables no-change flag correct")

    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("TEST F: Full clean_document pipeline")
    print("=" * 60)

    from ingestion.schema.document_schema import UnifiedDocument

    doc_f = UnifiedDocument(
        title    = "Attention\u00a0Is\u00a0All You Need",
        authors  = ["Ashish\u200b Vaswani", "Noam Shazeer"],
        abstract = (
            "We propose the Trans-\nformer, a model based solely on attention.\n"
            "Page 1\n"
            "\u00a9 2017 Authors. All rights reserved.\n"
            "The model achieves state-of-the-art results on machine\ntranslation tasks."
        ),
        sections = [
            Section(
                name    = "1. Introduction",
                content = (
                    "Neural machine trans-\nlation has advanced signi\ufb01cantly.\n"
                    "Page 2 of 8\n"
                    "Previous work used recurrent networks.\n"
                ),
                order = 0, level = 1,
            ),
            Section(
                name    = "2. Methodology",
                content = "We use multi-head at-\ntention with  multiple   spaces.\nPage 3 of 8",
                order = 1, level = 1,
            ),
        ],
        figures = [Figure(caption="\u201cFigure\u00a01:\u201d Architecture overview", index=0)],
        tables  = [Table(caption="Table\u00a01: Results on WMT\u00a02014")],
        source_type = "pdf",
    )

    result_f = clean_document(doc_f, aggressive=True)

    print(f"  Title:    '{result_f.title}'")
    print(f"  Authors:  {result_f.authors}")
    print(f"  Abstract:\n{result_f.abstract}")
    print(f"  Section 0:\n{result_f.sections[0].content}")
    print(f"  Section 1:\n{result_f.sections[1].content}")
    print(f"  Figure caption: '{result_f.figures[0].caption}'")
    print(f"  Table caption:  '{result_f.tables[0].caption}'")

    assert "Trans-\n" not in result_f.abstract,            "Dehyphenation failed in abstract"
    assert "All rights reserved" not in result_f.abstract, "Copyright not removed"
    assert "Page 1" not in result_f.abstract,              "Page number not removed"
    assert "fi" in result_f.sections[0].content,           "Ligature not expanded"
    assert "  " not in result_f.sections[1].content,       "Multi-space not collapsed"
    assert "\u00a0" not in result_f.title,                  "NBSP not removed from title"
    assert "\u200b" not in result_f.authors[0],             "ZWS not removed from author"
    print("  ✓ All assertions passed")

    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("TEST G: Idempotency — clean twice = same result")
    print("=" * 60)

    result_g1 = clean_document(doc_f, aggressive=True)
    result_g2 = clean_document(result_g1, aggressive=True)
    assert result_g1.abstract == result_g2.abstract, "Abstract not idempotent"
    assert result_g1.sections == result_g2.sections, "Sections not idempotent"
    print("  ✓ Cleaning is idempotent")

    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("TEST H: No-change returns same object")
    print("=" * 60)

    clean_doc = UnifiedDocument(
        title       = "Clean Title",
        source_type = "pdf",
        abstract    = "This is already clean text with no issues.",
        sections    = [Section(name="Intro", content="Clean content.", order=0, level=1)],
    )
    result_h = clean_document(clean_doc)
    assert result_h is clean_doc, "Should return same object when nothing changed"
    print("  ✓ No-change returns same object (no unnecessary copy)")

    print()
    print("=" * 60)
    print("All tests complete.")
