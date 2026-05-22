"""
docx_loader.py
--------------
Load academic DOCX papers into UnifiedDocument format.

Strategy:
  - python-docx  → paragraphs, headings, tables, metadata
  - Heading styles → sections với level và section_type
  - Tables → Table dataclass
  - Images → Figure dataclass (caption từ paragraph gần nhất)
"""
from datetime import datetime
import logging
import re
from pathlib import Path
from typing import Optional

from ingestion.schema.document_schema import (
    Figure,
    Reference,
    Section,
    Table,
    UnifiedDocument,
)

logger = logging.getLogger(__name__)

# Heading style names trong DOCX (Word mặc định)
HEADING_STYLES = {
    "heading 1": 1,
    "heading 2": 2,
    "heading 3": 3,
    "heading 4": 4,
    "title": 1,
}

# Section type mapping — giống pdf_loader
SECTION_TYPE_MAP = {
    "abstract": "abstract",
    "introduction": "introduction",
    "related work": "related_work",
    "background": "background",
    "method": "method",
    "methodology": "method",
    "approach": "method",
    "experiment": "experiment",
    "experiments": "experiment",
    "evaluation": "experiment",
    "result": "result",
    "results": "result",
    "discussion": "discussion",
    "conclusion": "conclusion",
    "conclusions": "conclusion",
    "future work": "future_work",
    "acknowledgment": "acknowledgment",
    "acknowledgements": "acknowledgment",
    "references": "references",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_docx(file_path: str) -> UnifiedDocument:
    """
    Load an academic DOCX paper into UnifiedDocument format.

    Args:
        file_path: Path to the DOCX file.

    Returns:
        UnifiedDocument with all extracted fields populated.

    Raises:
        FileNotFoundError: If the DOCX file does not exist.
        ImportError:       If python-docx is not installed.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"DOCX not found: {file_path}")

    try:
        from docx import Document
    except ImportError:
        raise ImportError("python-docx not installed. Run: pip install python-docx")

    logger.info("Loading DOCX: %s", file_path)

    doc = Document(str(path))

    # Extract từng phần
    metadata    = _extract_metadata(doc)
    sections    = _extract_sections(doc)
    tables      = _extract_tables(doc)
    figures     = _extract_figures(doc)
    abstract    = _extract_abstract(sections)

    unified = UnifiedDocument(
        # Metadata từ core properties
        title    = metadata["title"] or path.stem,
        authors  = metadata["authors"],
        year     = metadata["year"],
        journal  = None,       # DOCX thường không có journal metadata
        doi      = None,       # DOCX thường không có DOI metadata
        keywords = metadata["keywords"],

        # Content
        abstract = abstract,
        sections = sections,
        tables   = tables,
        figures  = figures,
        references = _extract_references(sections),

        # Source
        source_file = file_path,
        source_type = "docx",
        page_count  = None,    # python-docx không đếm page trực tiếp

        processing_status="parsed",
        created_at=datetime.now(),
    )

    logger.info("Loaded: '%s' — %d sections, %d tables, %d figures",
            unified.title, len(unified.sections), len(unified.tables), len(unified.figures))
    return unified


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def _extract_metadata(doc) -> dict:
    """Extract metadata từ DOCX core properties."""
    props = doc.core_properties
    metadata = {
        "title":    "",
        "authors":  [],
        "year":     None,
        "keywords": [],
    }

    # Title
    if props.title:
        metadata["title"] = props.title.strip()

    # Authors — core_properties.author là string, có thể chứa nhiều tên
    if props.author:
        # Tách theo dấu phẩy, chấm phẩy, hoặc "and"
        raw = props.author
        authors = re.split(r",|;| and ", raw, flags=re.IGNORECASE)
        metadata["authors"] = [a.strip() for a in authors if a.strip()]

    # Year từ created date
    if props.created:
        # Năm tạo file, không phải năm publication — có thể không chính xác
        metadata["year"] = props.created.year

    # Keywords
    if props.keywords:
        kws = re.split(r",|;", props.keywords)
        metadata["keywords"] = [k.strip() for k in kws if k.strip()]

    return metadata


# ---------------------------------------------------------------------------
# Reference
# ---------------------------------------------------------------------------

def _extract_references(sections: list[Section]) -> list[Reference]:
    for section in sections:
        if section.section_type == "references":
            lines = section.content.split("\n")
            refs = []
            for line in lines:
                line = line.strip()
                if re.match(r"^\[\d+\]|\d+\.", line):
                    refs.append(Reference(raw_text=line))
            return refs if refs else [Reference(raw_text=section.content)]
    return []

# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _extract_sections(doc) -> list[Section]:
    """
    Extract sections bằng cách scan headings.

    Logic:
      - Gặp Heading paragraph → bắt đầu section mới
      - Các paragraph bình thường → gom vào section hiện tại
      - Paragraph trước heading đầu tiên → section "Preamble"
    """
    sections = []
    current_heading = None
    current_level   = 1
    current_parts   = []
    order           = 0

    for para in doc.paragraphs:
        style_name = para.style.name.lower()
        text = para.text.strip()

        if not text:
            continue

        if style_name in HEADING_STYLES:
            # Lưu section trước đó
            if current_parts:
                content = "\n".join(current_parts).strip()
                if content:
                    parent_name = None
                    if current_level > 1 and sections:
                        for prev in reversed(sections):
                            if prev.level < current_level:
                                parent_name = prev.name
                                break
                    sections.append(Section(
                        name           = current_heading or "Preamble",
                        content        = content,
                        order          = order,
                        level          = current_level,
                        section_type   = _infer_section_type(current_heading or ""),
                        parent_section = parent_name,
                    ))
                    order += 1

            # Bắt đầu section mới
            current_heading = text
            current_level   = HEADING_STYLES[style_name]
            current_parts   = []

        else:
            current_parts.append(text)

    # Lưu section cuối cùng
    if current_parts:
        parent_name = None
        if current_level > 1 and sections:
            for prev in reversed(sections):
                if prev.level < current_level:
                    parent_name = prev.name
                    break
        sections.append(Section(
            name           = current_heading or "Body",
            content        = "\n".join(current_parts).strip(),  # dùng trực tiếp
            order          = order,
            level          = current_level,
            section_type   = _infer_section_type(current_heading or ""),
            parent_section = parent_name,
        ))

    return sections


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

def _extract_tables(doc) -> list[Table]:
    """Extract tất cả tables từ DOCX."""
    tables = []

    for idx, table in enumerate(doc.tables):
        try:
            rows = []
            for row in table.rows:
                # Fix: dùng cell._tc làm unique key để tránh duplicate merged cells
                seen  = set()
                cells = []
                for cell in row.cells:
                    cell_id = cell._tc
                    if cell_id not in seen:
                        seen.add(cell_id)
                        cells.append(cell.text.strip())
                rows.append(cells)

            if not rows:
                continue

            # Row đầu tiên làm header nếu có text
            headers = rows[0] if rows else []
            data    = rows[1:] if len(rows) > 1 else []

            tables.append(Table(
                caption=f"Table {idx + 1}",
                index=idx + 1,        # thêm vào
                headers=headers,
                data=data,
            ))

        except Exception as e:
            logger.debug("Table %d extraction failed: %s", idx + 1, e)

    return tables


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def _extract_figures(doc) -> list[Figure]:
    """
    Extract figures từ DOCX.

    python-docx không expose inline images trực tiếp qua paragraphs,
    nên detect bằng cách tìm runs có embedded images (r:drawing / r:picture).
    Caption lấy từ paragraph style 'Caption' gần nhất.
    """
    figures  = []
    captions = _collect_captions(doc)
    fig_idx  = 0

    for para_idx, para in enumerate(doc.paragraphs):
        # Check nếu paragraph chứa drawing/picture XML
        if _paragraph_has_image(para):
            # Lấy caption gần nhất phía sau
            caption = captions.get(para_idx + 1) or captions.get(para_idx + 2) or f"Figure {fig_idx + 1}"
            figures.append(Figure(
                caption=caption,
                index=fig_idx + 1,    # thêm vào
                path=None,
                figure_type="image",
            ))
            fig_idx += 1

    return figures


def _collect_captions(doc) -> dict[int, str]:
    """Map paragraph index → caption text cho các paragraph có style 'Caption'."""
    captions = {}
    for idx, para in enumerate(doc.paragraphs):
        if "caption" in para.style.name.lower() and para.text.strip():
            captions[idx] = para.text.strip()
    return captions


def _paragraph_has_image(para) -> bool:
    """Check nếu paragraph XML chứa drawing hoặc picture element."""
    try:
        xml = para._element.xml
        return "<w:drawing" in xml or "<w:pict" in xml
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Abstract & References helpers
# ---------------------------------------------------------------------------

def _extract_abstract(sections: list[Section]) -> str:
    """Tìm abstract từ sections đã extract."""
    for section in sections:
        if section.section_type == "abstract":
            return section.content
    return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _infer_section_type(section_name: str) -> Optional[str]:
    """Map section heading to known academic section type."""
    normalized = section_name.lower().strip()
    
    for key, section_type in sorted(SECTION_TYPE_MAP.items(), key=lambda x: -len(x[0])):
        if key in normalized:
            return section_type
    return "other"


# ---------------------------------------------------------------------------
# Quick self-test (run: python docx_loader.py <path_to_docx>)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s | %(name)s | %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python docx_loader.py <path_to_docx>")
        sys.exit(1)

    docx_path = sys.argv[1]

    try:
        doc = load_docx(docx_path)
        print(f"\n{'='*50}")
        print(f"Title    : {doc.title}")
        print(f"Authors  : {', '.join(doc.authors)}")
        print(f"Year     : {doc.year}")
        print(f"Keywords : {', '.join(doc.keywords)}")
        print(f"Abstract : {doc.abstract[:200]}..." if doc.abstract else "Abstract : (none)")
        print(f"Sections : {len(doc.sections)}")
        for s in doc.sections[:5]:
            print(f"  [{s.order}] L{s.level} [{s.section_type}] {s.name} — {len(s.content)} chars")
        print(f"Tables   : {len(doc.tables)}")
        print(f"Figures  : {len(doc.figures)}")
        print(f"Refs     : {len(doc.references)}")
        print(f"{'='*50}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
