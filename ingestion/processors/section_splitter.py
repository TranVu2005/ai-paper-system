"""
section_splitter.py
-------------------
Split UnifiedDocument thành list[Section] theo source_type.

Input:  UnifiedDocument sau loaders + parsers (có doc.abstract, doc.sections sơ bộ
        hoặc doc.sections = [] nếu loader chưa split)
Output: UnifiedDocument với doc.sections đã được populate đầy đủ

Strategy theo source_type:
  - pdf  → Grobid XML (nếu có grobid_sections) → fallback regex heading → fallback paragraph
  - docx → python-docx paragraph styles (Heading 1/2/3) → fallback paragraph
  - html → BeautifulSoup h1-h6 tags → fallback paragraph

Fallback chung (khi không detect được headings):
  Split theo paragraph/blank lines → 1 Section per paragraph block

Section types được nhận diện:
  abstract, introduction, related_work, methodology, experiments,
  results, discussion, conclusion, references, appendix, unknown

Section.level:
  1 = top-level heading (H1, Grobid <div level="1">)
  2 = sub-heading (H2, <div level="2">)
  3 = sub-sub-heading (H3+)
"""

import logging
import re
from dataclasses import replace
from typing import Optional

from ingestion.schema.document_schema import Section, UnifiedDocument

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Map section name → section_type
# SYNC: đồng bộ values với pdf_loader.SECTION_TYPE_MAP
#   method (không phải methodology), experiment (không phải experiments),
#   result (không phải results), acknowledgment (không phải acknowledgements),
#   other (không phải unknown)
_SECTION_TYPE_MAP: dict[str, str] = {
    "abstract":          "abstract",
    "introduction":      "introduction",
    "related work":      "related_work",
    "background":        "background",
    "literature":        "related_work",
    "methodology":       "method",
    "method":            "method",
    "methods":           "method",
    "approach":          "method",
    "proposed":          "method",
    "experiment":        "experiment",
    "experiments":       "experiment",
    "experimental":      "experiment",
    "evaluation":        "experiment",
    "result":            "result",
    "results":           "result",
    "finding":           "result",
    "findings":          "result",
    "discussion":        "discussion",
    "analysis":          "discussion",
    "conclusion":        "conclusion",
    "conclusions":       "conclusion",
    "concluding":        "conclusion",
    "summary":           "conclusion",
    "reference":         "references",
    "references":        "references",
    "bibliography":      "references",
    "appendix":          "appendix",
    "supplementary":     "appendix",
    "acknowledgement":   "acknowledgment",
    "acknowledgements":  "acknowledgment",
    "acknowledgment":    "acknowledgment",
    # Tiếng Việt
    "đặt vấn đề":         "introduction",
    "giới thiệu":         "introduction",
    "tổng quan":           "background",
    "cơ sở lý thuyết":    "background",
    "phương pháp":         "method",
    "thiết kế":            "method",
    "mô hình":             "method",
    "thuật toán":          "method",
    "thực nghiệm":         "experiment",
    "chạy thử":            "experiment",
    "kết quả":             "result",
    "kết luận":            "conclusion",
    "tài liệu tham khảo": "references",
    "tham khảo":           "references",
    "thảo luận":           "discussion",
    "nghiên cứu":          "background",
    "đánh giá":            "experiment",
}

# Minimum content length để tính là section hợp lệ
MIN_SECTION_CONTENT_LEN = 20

# Minimum paragraph length để tính là paragraph block (fallback)
MIN_PARAGRAPH_LEN = 50


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def split_sections(
    doc: UnifiedDocument,
    force: bool = False,
) -> UnifiedDocument:
    """
    Split UnifiedDocument thành list[Section].

    Args:
        doc:   UnifiedDocument sau loaders + parsers
        force: True = re-split kể cả khi doc.sections đã có

    Returns:
        UnifiedDocument với doc.sections populated.
        Nếu không split được gì → trả về doc gốc.
    """
    # Skip nếu đã có sections (và không force)
    if doc.sections and not force:
        logger.debug(f"Sections already present ({len(doc.sections)}) — skipping split")
        return doc

    source_type = doc.source_type.lower() if doc.source_type else ""

    sections: list[Section] = []

    if source_type == "pdf":
        sections = _split_pdf(doc, force=force)
    elif source_type == "docx":
        sections = _split_docx(doc)
    elif source_type == "html":
        sections = _split_html(doc)
    else:
        logger.warning(f"Unknown source_type='{source_type}' — using paragraph fallback")
        sections = _split_by_paragraphs(_get_full_text(doc))

    if not sections:
        logger.warning(f"No sections detected for: '{doc.title[:60]}' — keeping empty")
        return doc

    logger.info(
        f"Split '{doc.title[:60]}': {len(sections)} sections "
        f"[{source_type}] "
        f"types={_summarize_types(sections)}"
    )
    return replace(doc, sections=sections)


def split_sections_batch(
    docs: list[UnifiedDocument],
    force: bool = False,
) -> list[UnifiedDocument]:
    """
    Split sections cho một batch UnifiedDocuments.

    Args:
        docs:  list[UnifiedDocument]
        force: True = re-split kể cả khi đã có sections

    Returns:
        list[UnifiedDocument] đã split
    """
    return [split_sections(doc, force=force) for doc in docs]


# ---------------------------------------------------------------------------
# PDF splitting
# ---------------------------------------------------------------------------

def _split_pdf(doc: UnifiedDocument, force: bool = False) -> list[Section]:
    """
    Split PDF document theo thứ tự ưu tiên:
      1. Grobid sections (nếu doc.sections đã có từ grobid_parser và force=False)
      2. Regex heading detection trên full text
      3. Paragraph fallback

    Args:
        doc:   UnifiedDocument
        force: True = bỏ qua Grobid sections, re-split từ text
    """
    # Strategy 1: Grobid đã parse sẵn sections — chỉ dùng khi không force
    if doc.sections and not force:
        logger.debug("Using pre-parsed Grobid sections")
        return _normalize_sections(doc.sections)

    full_text = _get_full_text(doc)
    if not full_text:
        logger.warning("PDF has no text content")
        return []

    # Strategy 2: Regex heading detection
    sections = _split_by_headings_regex(full_text)
    if sections:
        logger.debug(f"PDF regex heading split: {len(sections)} sections")
        return sections

    # Strategy 3: Paragraph fallback
    logger.debug("PDF: no headings detected — using paragraph fallback")
    return _split_by_paragraphs(full_text)


# ---------------------------------------------------------------------------
# DOCX splitting
# ---------------------------------------------------------------------------

def _split_docx(doc: UnifiedDocument) -> list[Section]:
    """
    Split DOCX dùng python-docx paragraph styles.
    Heading 1/2/3 → Section boundaries.

    Fallback: paragraph split nếu không có heading styles.
    """
    if not doc.source_file:
        logger.warning("DOCX: no source_file — using text fallback")
        return _split_by_headings_regex(_get_full_text(doc)) or _split_by_paragraphs(_get_full_text(doc))

    try:
        from docx import Document as DocxDocument

        docx_doc        = DocxDocument(doc.source_file)
        sections        = []
        current_heading: Optional[str] = None
        current_level   = 1
        current_lines:  list[str] = []
        order           = 0

        # Prepend abstract nếu có
        if doc.abstract:
            sections.append(Section(
                name         = "Abstract",
                content      = doc.abstract.strip(),
                order        = order,
                level        = 1,
                section_type = "abstract",
            ))
            order += 1

        for para in docx_doc.paragraphs:
            style_name = para.style.name if para.style else ""
            text       = para.text.strip()

            if not text:
                continue

            # Detect heading level từ style name
            heading_level = _docx_heading_level(style_name)

            if heading_level is not None:
                # Flush accumulated content vào section hiện tại
                if current_heading is not None and current_lines:
                    content = "\n\n".join(current_lines).strip()
                    if len(content) >= MIN_SECTION_CONTENT_LEN:
                        sections.append(Section(
                            name         = current_heading,
                            content      = content,
                            order        = order,
                            level        = current_level,
                            section_type = _infer_section_type(current_heading),
                        ))
                        order += 1

                current_heading = text
                current_level   = heading_level
                current_lines   = []

            else:
                # Content paragraph
                current_lines.append(text)

        # Flush section cuối
        if current_heading is not None and current_lines:
            content = "\n\n".join(current_lines).strip()
            if len(content) >= MIN_SECTION_CONTENT_LEN:
                sections.append(Section(
                    name         = current_heading,
                    content      = content,
                    order        = order,
                    level        = current_level,
                    section_type = _infer_section_type(current_heading),
                ))

        if sections:
            logger.debug(f"DOCX heading split: {len(sections)} sections")
            return sections

    except ImportError:
        logger.warning("python-docx not installed — using text fallback")
    except Exception as e:
        logger.warning(f"DOCX split failed: {e} — using text fallback")

    # Fallback
    full_text = _get_full_text(doc)
    return _split_by_headings_regex(full_text) or _split_by_paragraphs(full_text)


def _docx_heading_level(style_name: str) -> Optional[int]:
    """
    Parse DOCX style name → heading level.

    Examples:
        "Heading 1" → 1
        "Heading 2" → 2
        "heading3"  → 3
        "Normal"    → None
    """
    if not style_name:
        return None
    match = re.match(r"[Hh]eading\s*(\d)", style_name)
    if match:
        return int(match.group(1))
    return None


# ---------------------------------------------------------------------------
# HTML splitting
# ---------------------------------------------------------------------------

def _split_html(doc: UnifiedDocument) -> list[Section]:
    """
    Split HTML document dùng BeautifulSoup h1-h6 tags.

    Fallback: paragraph split.
    """
    if not doc.source_file:
        return _split_by_headings_regex(_get_full_text(doc)) or _split_by_paragraphs(_get_full_text(doc))

    try:
        from bs4 import BeautifulSoup

        with open(doc.source_file, encoding="utf-8", errors="replace") as f:
            html = f.read()

        soup     = BeautifulSoup(html, "lxml")
        sections = []
        order    = 0

        # Prepend abstract
        if doc.abstract:
            sections.append(Section(
                name         = "Abstract",
                content      = doc.abstract.strip(),
                order        = order,
                level        = 1,
                section_type = "abstract",
            ))
            order += 1

        heading_tags = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])

        for heading in heading_tags:
            heading_text = heading.get_text(strip=True)
            if not heading_text:
                continue

            level = int(heading.name[1])  # h1→1, h2→2, ...

            # Collect text nodes đến heading tiếp theo
            content_parts: list[str] = []
            for sibling in heading.next_siblings:
                if sibling.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                    break
                text = (
                    sibling.get_text(separator=" ", strip=True)
                    if hasattr(sibling, "get_text")
                    else str(sibling).strip()
                )
                if text:
                    content_parts.append(text)

            content = "\n\n".join(content_parts).strip()
            if len(content) < MIN_SECTION_CONTENT_LEN:
                continue

            sections.append(Section(
                name         = heading_text,
                content      = content,
                order        = order,
                level        = level,
                section_type = _infer_section_type(heading_text),
            ))
            order += 1

        if sections:
            logger.debug(f"HTML heading split: {len(sections)} sections")
            return sections

    except ImportError:
        logger.warning("BeautifulSoup not installed — using text fallback. Run: pip install beautifulsoup4")
    except Exception as e:
        logger.warning(f"HTML split failed: {e} — using text fallback")

    full_text = _get_full_text(doc)
    return _split_by_headings_regex(full_text) or _split_by_paragraphs(full_text)


# ---------------------------------------------------------------------------
# Fallback: regex heading detection
# ---------------------------------------------------------------------------

def _split_by_headings_regex(text: str) -> list[Section]:
    """
    Detect headings bằng regex trên plain text.
    Dùng làm fallback cho PDF khi không có Grobid.

    Returns:
        list[Section] hoặc [] nếu không detect được gì.
    """
    if not text:
        return []

    lines    = text.split("\n")
    sections: list[Section] = []
    order    = 0

    current_heading: Optional[str] = None
    current_level   = 1
    current_lines:  list[str] = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            if current_lines:
                current_lines.append("")  # preserve paragraph breaks
            continue

        next_line = lines[i+1] if i + 1 < len(lines) else ""

        if _is_heading_line(stripped, next_line):
            # Nếu 2 heading nằm sát nhau (không có nội dung ở giữa), gộp chúng lại
            content_so_far = "\n".join(current_lines).strip()
            if current_heading is not None and len(content_so_far) < 15:
                current_heading = current_heading + " " + stripped
                # Xóa current_lines để không chứa khoảng trắng thừa
                current_lines = []
                continue

            # Flush
            if current_heading is not None:
                content = "\n".join(current_lines).strip()
                if len(content) >= MIN_SECTION_CONTENT_LEN:
                    sections.append(Section(
                        name         = current_heading,
                        content      = content,
                        order        = order,
                        level        = current_level,
                        section_type = _infer_section_type(current_heading),
                    ))
                    order += 1

            current_heading = stripped
            current_level   = _infer_heading_level(stripped)
            current_lines   = []
        else:
            current_lines.append(stripped)

    # Flush cuối
    if current_heading is not None:
        content = "\n".join(current_lines).strip()
        if len(content) >= MIN_SECTION_CONTENT_LEN:
            sections.append(Section(
                name         = current_heading,
                content      = content,
                order        = order,
                level        = current_level,
                section_type = _infer_section_type(current_heading),
            ))

    return sections


def _is_heading_line(line: str, next_line: str = "") -> bool:
    """
    Sử dụng heuristic mạnh mẽ để xác định 1 dòng có phải là tiêu đề (heading) không.
    """
    line = line.strip()
    
    # Quá dài hoặc quá ngắn → không phải heading
    if len(line) < 3 or len(line) > 120:
        return False
        
    # Heading phải chứa ít nhất 1 chữ cái (loại bỏ các dòng chỉ chứa số/ký hiệu như "0.81 0.35")
    if not any(c.isalpha() for c in line):
        return False
        
    # Tiêu đề không bao giờ kết thúc bằng các từ nối/giới từ/mạo từ (do bị cắt dòng giữa chừng)
    trailing_words = (' bộ', ' và', ' của', ' được', ' là', ' thì', ' mà', ' nhưng', ' cho', ' với', ' này', ' kia', ' các', ' những', ' do', ' ở', ' tại', ' trong', ' trên', ' dưới', ' về', ' việc', ' sự', ' the', ' a', ' an', ' of', ' in', ' on', ' at', ' to', ' for', ' with')
    if line.lower().endswith(trailing_words):
        return False

    lower_line = line.lower()
    
    # Bỏ qua các Artifact Header/Footer của bài báo
    blacklist_prefixes = (
        "tạp chí", "journal of", "khoa học - công nghệ", "khoa học & công nghệ",
        "khoa học và công nghệ", "nghiên cứu - trao đổi", "nghiên cứu và phát triển", 
        "thông tin", "issn", "vol.", "số ", "trang ", "page ", "bản quyền", "copyright",
        "nghiên cứu khoa học", "research journal"
    )
    if lower_line.startswith(blacklist_prefixes):
        return False
    if "khoa học - công nghệ" in lower_line or "khoa học & công nghệ" in lower_line:
        return False

    # Không bao giờ là heading nếu chứa các ký tự toán học đặc trưng
    if any(c in line for c in ['=', '+', '*', '<', '>']):
        return False

    # Heading của bài báo hiếm khi kết thúc bằng dấu chấm câu (trừ khi là danh sách liệt kê)
    if line.endswith((".", ",", ";", ":")):
        # Trừ ngoại lệ mục "References." hoặc tương tự
        if not line.lower().strip(".") in ["references", "tài liệu tham khảo"]:
            return False

    # 1. Numbered heading (Arabic: 1. , 1.1 , 1.1.2)
    # Bắt buộc phải có dấu chấm (vd: 1. hoặc 1.1) hoặc bắt đầu theo chuẩn "1 Introduction" (Viết hoa chữ đầu)
    if re.match(r"^(?:\d+\.(?:\d+\.?)*)\s+[^\n]{2,}", line):
        return True
    if re.match(r"^\d+\s+[A-ZĐ][^\n]{2,}", line): # Kiểu "1 Introduction" hoặc "1 TỔNG QUAN"
        return True

    # 2. Roman numbered heading (I. , II. , III.)
    if re.match(r"^(?:[IVXLCDM]+\.)\s+[^\n]{2,}", line):
        return True
        
    # 3. Alphabetical heading (A. , B. , C. ) - Thường dùng trong chuẩn IEEE
    if re.match(r"^[A-Z]\.\s+[A-ZĐ][^\n]{2,}", line):
        return True

    # 3. Known exact headings (Case-insensitive)
    known_headings = {
        "abstract", "introduction", "conclusion", "references", "appendix",
        "related work", "methodology", "experiments", "results", "discussion",
        "background", "acknowledgement", "bibliography",
        "tóm tắt", "lời cảm ơn", "tài liệu tham khảo",
        "đặt vấn đề", "giới thiệu", "tổng quan", "cơ sở lý thuyết", 
        "phương pháp", "thiết kế", "mô hình", "thực nghiệm", "kết quả", "kết luận"
    }
    if lower_line in known_headings:
        return True

    # 4. ALL CAPS (Toàn bộ là chữ in hoa)
    # Đếm số chữ cái, đảm bảo có chữ và 100% chữ cái là in hoa.
    # Phải có ít nhất 1 khoảng trắng (2 từ trở lên) để tránh các biến số/công thức.
    letters = [c for c in line if c.isalpha()]
    if letters and all(c.isupper() for c in letters) and " " in line:
        return True

    return False


def _infer_heading_level(heading: str) -> int:
    """
    Infer heading level từ text:
      - "1. Title"   → 1
      - "1.2 Title"  → 2
      - "1.2.3 ..."  → 3
      - "I. Title"   → 1
      - ALL CAPS / Named → 1
    """
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", heading)
    if match:
        level = sum(1 for g in match.groups() if g is not None)
        return min(level, 3)
    return 1


# ---------------------------------------------------------------------------
# Fallback: paragraph split
# ---------------------------------------------------------------------------

def _split_by_paragraphs(text: str) -> list[Section]:
    """
    Split text theo blank lines → mỗi paragraph block = 1 Section.
    Dùng khi không detect được bất kỳ heading nào.

    Returns:
        list[Section] — mỗi item là 1 paragraph block đủ dài.
    """
    if not text:
        return []

    # Split theo 2+ blank lines (paragraph separator)
    blocks  = re.split(r"\n\s*\n", text.strip())
    sections: list[Section] = []
    order   = 0

    for block in blocks:
        content = block.strip()
        if len(content) < MIN_PARAGRAPH_LEN:
            continue

        # Dùng dòng đầu tiên làm "name" (truncate nếu cần)
        first_line = content.split("\n")[0].strip()[:80]

        sections.append(Section(
            name         = first_line,
            content      = content,
            order        = order,
            level        = 1,
            section_type = _infer_section_type(first_line),
        ))
        order += 1

    logger.debug(f"Paragraph fallback: {len(sections)} blocks")
    return sections


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_full_text(doc: UnifiedDocument) -> str:
    """
    Lấy full text từ document.
    Ưu tiên doc.full_text (raw OCR text từ pdf_loader).
    Fallback: assemble từ abstract + sections content.
    """
    # Ưu tiên full_text đã lưu (từ OCR pipeline)
    if doc.full_text:
        return doc.full_text

    parts: list[str] = []
    if doc.abstract:
        parts.append(doc.abstract)

    for section in doc.sections:
        if section.name:
            parts.append(section.name)
        if section.content:
            parts.append(section.content)

    return "\n\n".join(parts)


def _normalize_sections(sections: list[Section]) -> list[Section]:
    """
    Normalize sections đã có (từ Grobid):
      - Ensure section_type được infer nếu còn None
      - Re-index order theo thứ tự list
    """
    normalized = []
    for i, section in enumerate(sections):
        updates: dict = {"order": i}
        if section.section_type is None:
            updates["section_type"] = _infer_section_type(section.name)
        normalized.append(replace(section, **updates))
    return normalized


def _infer_section_type(name: str) -> str:
    """
    Infer section_type từ section name.

    Examples:
        "1. Introduction"            → "introduction"
        "2.3 RELATED WORK"           → "related_work"
        "Conclusion and Future Work" → "conclusion"
        "Random Title"               → "other"
    """
    if not name:
        return "other"

    # Normalize: lowercase, strip số đầu, strip punctuation
    normalized = name.lower()
    normalized = re.sub(r"^\d+[\.\d]*\s*", "", normalized)  # strip "1.2 "
    normalized = re.sub(r"[^\w\s]", " ", normalized).strip()

    for key, section_type in sorted(_SECTION_TYPE_MAP.items(), key=lambda x: -len(x[0])):
        if key in normalized:
            return section_type

    return "other"


def _summarize_types(sections: list[Section]) -> str:
    """Tóm tắt distribution section_types để logging."""
    from collections import Counter
    counts = Counter(s.section_type for s in sections)
    return ", ".join(f"{t}:{n}" for t, n in counts.most_common(5))


# ---------------------------------------------------------------------------
# Quick self-test (run: python section_splitter.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")

    # ------------------------------------------------------------------
    print("=" * 60)
    print("TEST A: PDF — regex heading fallback")
    print("=" * 60)

    pdf_text = """Abstract
This paper proposes a new approach to neural machine translation.

1. Introduction
Neural machine translation has gained significant attention in recent years.
The attention mechanism allows models to focus on relevant parts of the input.

2. Related Work
Previous work on sequence-to-sequence models laid the foundation.
Early approaches used RNNs with fixed-length context vectors.

2.1 Attention Mechanisms
Bahdanau et al. introduced soft attention for alignment.

3. Methodology
We propose a transformer-based architecture with multi-head attention.
The model consists of encoder and decoder stacks.

4. Experiments
We evaluate on WMT 2014 English-German and English-French tasks.
Training takes 3.5 days on 8 NVIDIA P100 GPUs.

5. Results
Our model achieves 28.4 BLEU on EN-DE, outperforming prior work.

6. Conclusion
We have presented the Transformer, a novel architecture based solely on attention.
Future work will extend this to other modalities.

References
Bahdanau et al., 2015. Neural Machine Translation by Jointly Learning to Align and Translate.
Vaswani et al., 2017. Attention Is All You Need.
"""

    doc_a = UnifiedDocument(
        title       = "Attention Is All You Need",
        source_type = "pdf",
        abstract    = pdf_text,   # inject via abstract để _get_full_text có text
    )

    result_a = split_sections(doc_a)
    print(f"Total sections: {len(result_a.sections)}")
    for s in result_a.sections:
        print(f"  [{s.order}] level={s.level} type={s.section_type:15s} name='{s.name[:50]}'")
    assert len(result_a.sections) > 0, "Should detect sections from PDF text"
    print("✓ PDF regex heading split OK")

    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("TEST B: Paragraph fallback (no headings)")
    print("=" * 60)

    no_heading_text = """
This is the first paragraph discussing the background of the problem.
It contains multiple sentences and provides context for the reader.

The second paragraph introduces the main contribution of the paper.
We propose a novel method that achieves state-of-the-art performance
on multiple benchmark datasets across different domains.

The third paragraph describes the experimental setup in detail.
We use standard evaluation metrics and compare against strong baselines
to demonstrate the effectiveness of our proposed approach.

Finally, we conclude by summarizing the key findings and discussing
future research directions that could further improve the results.
"""
    sections_b = _split_by_paragraphs(no_heading_text)
    print(f"Paragraph blocks: {len(sections_b)}")
    for s in sections_b:
        print(f"  [{s.order}] '{s.name[:50]}'")
    assert len(sections_b) == 4, f"Expected 4 paragraph blocks, got {len(sections_b)}"
    print("✓ Paragraph fallback OK")

    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("TEST C: _infer_section_type")
    print("=" * 60)

    type_cases = [
        ("1. Introduction",              "introduction"),
        ("2.3 RELATED WORK",             "related_work"),
        ("3. Proposed Methodology",      "method"),
        ("4.1 Experimental Results",     "result"),
        ("5. Conclusion and Future Work","conclusion"),
        ("References",                   "references"),
        ("Appendix A: Proofs",           "appendix"),
        ("Random Unrelated Title",       "other"),
    ]
    all_pass = True
    for name, expected in type_cases:
        got    = _infer_section_type(name)
        status = "✓" if got == expected else f"✗ (expected '{expected}', got '{got}')"
        print(f"  '{name}' → '{got}' {status}")
        if got != expected:
            all_pass = False
    print(f"  {'All pass ✓' if all_pass else 'Some failed ✗'}")

    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("TEST D: _infer_heading_level")
    print("=" * 60)

    heading_tests = [
        ("Introduction",    1),
        ("1. Introduction", 1),
        ("2.3 Related Work",2),
        ("1.2.3 Sub-sub",   3),
        ("CONCLUSION",      1),
    ]
    all_pass = True
    for heading, expected in heading_tests:
        got    = _infer_heading_level(heading)
        status = "✓" if got == expected else f"✗ (expected {expected}, got {got})"
        print(f"  '{heading}' → level={got} {status}")
        if got != expected:
            all_pass = False
    print(f"  {'All pass ✓' if all_pass else 'Some failed ✗'}")

    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("TEST E: force flag behavior")
    print("=" * 60)

    # E1: force=False → skip re-split
    existing_section = Section(
        name    = "Pre-existing",
        content = "Already split content here.",
        order   = 0,
    )
    doc_e = UnifiedDocument(
        title       = "Already Split",
        source_type = "pdf",
        sections    = [existing_section],
    )
    result_e = split_sections(doc_e, force=False)
    assert len(result_e.sections) == 1,                  "force=False should skip re-split"
    assert result_e.sections[0].name == "Pre-existing",  "force=False should keep original section"
    assert result_e is doc_e,                            "force=False should return same object"
    print("✓ force=False: skipped re-split, returned same object")

    # E2: force=True → _split_pdf bypass Grobid sections, re-split từ text
    # Prepare doc với Grobid sections đã có VÀ abstract có headings
    grobid_section = Section(name="Grobid Section", content="Grobid content.", order=0)
    doc_e2 = UnifiedDocument(
        title       = "Force Re-split",
        source_type = "pdf",
        sections    = [grobid_section],
        abstract    = pdf_text,   # có headings → regex sẽ detect
    )
    result_e2 = split_sections(doc_e2, force=True)
    assert result_e2 is not doc_e2,                    "force=True should return new object"
    assert len(result_e2.sections) > 1,                "force=True should re-split into multiple sections"
    assert result_e2.sections[0].name != "Grobid Section", \
        "force=True should not reuse old Grobid section name"
    print(f"✓ force=True: re-split bypassed Grobid → {len(result_e2.sections)} sections")

    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("TEST F: _normalize_sections (Grobid section_type backfill)")
    print("=" * 60)

    grobid_sections = [
        Section(name="Introduction",  content="Intro content here.",  order=5, section_type=None),
        Section(name="Related Work",  content="Related content here.", order=3, section_type=None),
        Section(name="Random Title",  content="Random content here.",  order=1, section_type="other"),
    ]
    normalized = _normalize_sections(grobid_sections)
    assert normalized[0].order        == 0,              "Should re-index order"
    assert normalized[0].section_type == "introduction", "Should infer introduction"
    assert normalized[1].section_type == "related_work", "Should infer related_work"
    assert normalized[2].section_type == "other",        "Should keep existing type"
    for s in normalized:
        print(f"  [{s.order}] type={s.section_type:15s} name='{s.name}'")
    print("✓ _normalize_sections OK")

    print()
    print("=" * 60)
    print("All tests complete.")
