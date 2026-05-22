"""
html_loader.py
--------------
Load academic HTML papers into UnifiedDocument format.

Supports:
  - arXiv HTML papers  (ar5iv.org / arxiv.org/html/...)
  - PubMed Central     (pmc.ncbi.nlm.nih.gov)
  - Generic HTML       (fallback cho các nguồn khác)

Strategy:
  - BeautifulSoup → parse HTML structure
  - Detect source type → dùng extractor phù hợp
  - Fallback generic extractor nếu không nhận ra source
  - Normalize → UnifiedDocument
"""

import logging
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from datetime import datetime
from ingestion.schema.document_schema import (
    Figure,
    Section,
    Table,
    Reference,
    UnifiedDocument,
)

logger = logging.getLogger(__name__)

# Section type mapping
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

# HTML heading tags
HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_html(source: str) -> UnifiedDocument:
    """
    Load an academic HTML paper into UnifiedDocument format.

    Args:
        source: File path (.html/.htm) hoặc URL (https://...).

    Returns:
        UnifiedDocument with all extracted fields populated.

    Raises:
        FileNotFoundError: Nếu file không tồn tại.
        ImportError:       Nếu beautifulsoup4 chưa được cài.
        RuntimeError:      Nếu fetch URL thất bại.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise ImportError(
            "beautifulsoup4 not installed. Run: pip install beautifulsoup4 lxml"
        )

    # --- Load HTML content ---
    html_content, source_url = _load_html_content(source)

    # --- Parse ---
    soup = BeautifulSoup(html_content, "lxml")

    # --- Detect source type → chọn extractor ---
    source_type = _detect_html_source(source_url or source, soup)
    logger.info("Detected HTML source type: %s — %s", source_type, source)

    if source_type == "arxiv":
        extractor = _ArxivExtractor(soup)
    elif source_type == "pubmed":
        extractor = _PubMedExtractor(soup)
    else:
        extractor = _GenericExtractor(soup)

    # --- Extract ---
    metadata   = extractor.extract_metadata()
    sections   = extractor.extract_sections()
    tables     = extractor.extract_tables()
    figures    = extractor.extract_figures()
    references = extractor.extract_references()
    abstract   = _find_abstract(sections)

    unified = UnifiedDocument(
        title      = metadata.get("title", "") or Path(source).stem,
        authors    = metadata.get("authors", []),
        year       = metadata.get("year"),
        journal    = metadata.get("journal"),
        doi        = metadata.get("doi"),
        keywords   = metadata.get("keywords", []),
        abstract   = abstract,
        sections   = sections,
        tables     = tables,
        figures    = figures,
        references = references,
        source_file = source,
        source_type = "html",
        language   = metadata.get("language"),
        processing_status="parsed",
        created_at=datetime.now(),
    )

    logger.info("Loaded: '%s' — %d sections, %d tables, %d figures",
            unified.title, len(unified.sections), len(unified.tables), len(unified.figures))
    return unified


# ---------------------------------------------------------------------------
# HTML content loader
# ---------------------------------------------------------------------------

def _load_html_content(source: str) -> tuple[str, Optional[str]]:
    """
    Load HTML từ file path hoặc URL.
    Returns: (html_string, source_url_or_None)
    """
    # URL
    if source.startswith("http://") or source.startswith("https://"):
        return _fetch_url(source), source

    # File path
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"HTML file not found: {source}")
    return path.read_text(encoding="utf-8", errors="replace"), None


def _fetch_url(url: str) -> str:
    """Fetch HTML content từ URL."""
    try:
        import requests
        response = requests.get(url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (academic-paper-pipeline/1.0)"
        })
        response.raise_for_status()
        return response.text
    except ImportError:
        raise ImportError("requests not installed. Run: pip install requests")
    except Exception as e:
        raise RuntimeError(f"Failed to fetch URL {url}: {e}")


# ---------------------------------------------------------------------------
# Source type detection
# ---------------------------------------------------------------------------

def _detect_html_source(source: str, soup) -> str:
    """Detect xem HTML từ arXiv, PubMed, hay generic."""
    # Check URL pattern
    if "arxiv.org" in source or "ar5iv.org" in source:
        return "arxiv"
    if "pubmed" in source or "pmc.ncbi" in source:
        return "pubmed"

    # Check meta tags
    generator = soup.find("meta", attrs={"name": "generator"})
    if generator:
        content = generator.get("content", "").lower()
        if "arxiv" in content or "ar5iv" in content:
            return "arxiv"
        if "pubmed" in content or "nlm" in content:
            return "pubmed"

    # Check specific CSS classes / IDs
    if soup.find(class_=re.compile(r"arxiv|ar5iv", re.I)):
        return "arxiv"
    if soup.find(class_=re.compile(r"pmc|pubmed", re.I)):
        return "pubmed"

    return "generic"


# ---------------------------------------------------------------------------
# Base Extractor
# ---------------------------------------------------------------------------

class _BaseExtractor:
    def __init__(self, soup):
        self.soup = soup

    def extract_metadata(self) -> dict:
        raise NotImplementedError

    def extract_sections(self) -> list[Section]:
        raise NotImplementedError

    def extract_tables(self) -> list[Table]:
        """Generic table extraction — dùng chung cho mọi source."""
        tables = []
        for idx, table_el in enumerate(self.soup.find_all("table")):
            try:
                headers = []
                data    = []

                # Headers từ <thead> hoặc row đầu tiên có <th>
                thead = table_el.find("thead")
                if thead:
                    headers = [th.get_text(strip=True) for th in thead.find_all(["th", "td"])]

                # Data rows từ <tbody> hoặc tất cả <tr>
                tbody = table_el.find("tbody") or table_el
                for tr in tbody.find_all("tr"):
                    cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                    if cells and cells != headers:
                        data.append(cells)

                # Caption
                caption_el = table_el.find("caption")
                caption = caption_el.get_text(strip=True) if caption_el else f"Table {idx + 1}"

                if headers or data:
                    tables.append(Table(
                        caption = caption,
                        index=idx + 1,
                        headers = headers,
                        data    = data,
                    ))
            except Exception as e:
                logger.debug("Table %d extraction failed: %s", idx + 1, e)

        return tables

    def extract_figures(self) -> list[Figure]:
        """Generic figure extraction."""
        figures = []
        for idx, fig_el in enumerate(self.soup.find_all("figure")):
            caption_el = fig_el.find("figcaption")
            caption    = caption_el.get_text(strip=True) if caption_el else f"Figure {idx + 1}"

            img = fig_el.find("img")
            path = img.get("src") if img else None

            # Infer figure type từ caption
            figure_type = _infer_figure_type(caption)

            figures.append(Figure(
                caption     = caption,
                index=idx + 1,
                path        = path,
                figure_type = figure_type,
                alt_text    = img.get("alt") if img else None,
            ))
        return figures

    def extract_references(self) -> list[Reference]:
        """
        Generic reference extraction.

        Fix: dùng heading text search thay vì string= trên <section>
        vì string= chỉ match khi toàn bộ text của tag khớp regex
        — <section> chứa nhiều children sẽ không bao giờ match.
        """
        refs = []

        # Strategy 1: tìm element có id/class chứa "ref"
        ref_section = (
            self.soup.find(id=re.compile(r"\bref", re.I)) or
            self.soup.find(class_=re.compile(r"\breferences?\b", re.I))
        )

        # Strategy 2: tìm heading "References" rồi lấy parent container
        if ref_section is None:
            ref_heading = self.soup.find(
                re.compile(r"h[1-6]"),
                string=re.compile(r"^\s*references?\s*$", re.I)
            )
            if ref_heading:
                ref_section = ref_heading.find_parent(["section", "div", "article"])

        if ref_section:
            # Ưu tiên lấy <li> items (numbered references)
            items = ref_section.find_all("li")
            if items:
                for li in items:
                    text = li.get_text(strip=True)
                    if text:
                        refs.append(Reference(raw_text=text))
            else:
                # Fallback: mỗi <p> là 1 reference
                for p in ref_section.find_all("p"):
                    text = p.get_text(strip=True)
                    if text:
                        refs.append(Reference(raw_text=text))

        return refs

    def _extract_sections_from_headings(self, container) -> list[Section]:
        """
        Generic section extraction bằng cách scan headings trong container.
        Dùng chung cho arXiv và generic HTML.
        """
        sections       = []
        current_name   = None
        current_level  = 1
        current_parts  = []
        order          = 0

        if container is None:
            return sections

        for el in container.children:
            if not hasattr(el, "name") or el.name is None:
                continue

            tag = el.name.lower()

            if tag in HEADING_TAGS:
                # Lưu section trước
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
                            name         = current_name or "Preamble",
                            content      = content,
                            order        = order,
                            level        = current_level,
                            section_type = _infer_section_type(current_name or ""),
                            parent_section=parent_name,
                        ))
                        order += 1

                current_name  = el.get_text(strip=True)
                current_level = HEADING_TAGS[tag]
                current_parts = []

            else:
                text = el.get_text(separator=" ", strip=True)
                if text:
                    current_parts.append(text)

        # Lưu section cuối
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
                    name         = current_name or "Body",
                    content      = content,
                    order        = order,
                    level        = current_level,
                    section_type = _infer_section_type(current_name or ""),
                    parent_section=parent_name,
                ))

        return sections


# ---------------------------------------------------------------------------
# arXiv Extractor (ar5iv format)
# ---------------------------------------------------------------------------

class _ArxivExtractor(_BaseExtractor):

    def extract_metadata(self) -> dict:
        metadata = {"title": "", "authors": [], "year": None,
                    "journal": None, "doi": None, "keywords": [], "language": None}

        # Title
        title_el = (
            self.soup.find("h1", class_=re.compile(r"title", re.I)) or
            self.soup.find("meta", attrs={"name": "citation_title"})
        )
        if title_el:
            metadata["title"] = (
                title_el.get("content") or title_el.get_text(strip=True)
            ).replace("Title:", "").strip()

        # Authors
        for author_el in self.soup.find_all("meta", attrs={"name": "citation_author"}):
            name = author_el.get("content", "").strip()
            if name:
                metadata["authors"].append(name)

        # Year
        date_el = self.soup.find("meta", attrs={"name": "citation_date"})
        if date_el:
            match = re.search(r"\d{4}", date_el.get("content", ""))
            if match:
                metadata["year"] = int(match.group())

        # DOI
        doi_el = self.soup.find("meta", attrs={"name": "citation_doi"})
        if doi_el:
            metadata["doi"] = doi_el.get("content", "").strip()

        # Keywords
        kw_el = self.soup.find("meta", attrs={"name": "keywords"})
        if kw_el:
            metadata["keywords"] = [
                k.strip() for k in re.split(r",|;", kw_el.get("content", ""))
                if k.strip()
            ]

        # Language
        html_el = self.soup.find("html")
        if html_el:
            metadata["language"] = html_el.get("lang")

        return metadata

    def extract_sections(self) -> list[Section]:
        # arXiv HTML body content thường trong <div class="ltx_document"> hoặc <article>
        container = (
            self.soup.find("div", class_=re.compile(r"ltx_document|ltx_page_main", re.I)) or
            self.soup.find("article") or
            self.soup.find("body")
        )
        return self._extract_sections_from_headings(container)


# ---------------------------------------------------------------------------
# PubMed Extractor
# ---------------------------------------------------------------------------

class _PubMedExtractor(_BaseExtractor):

    def extract_metadata(self) -> dict:
        metadata = {"title": "", "authors": [], "year": None,
                    "journal": None, "doi": None, "keywords": [], "language": None}

        # Title
        title_el = self.soup.find("h1", class_=re.compile(r"content-title|article-title", re.I))
        if title_el:
            metadata["title"] = title_el.get_text(strip=True)

        # Fix: dùng class PubMed cụ thể thay vì class "author" chung chung
        # để tránh lấy nhầm affiliations và labels
        for author_el in self.soup.find_all(
            class_=re.compile(r"authors-list-item|contrib-group__author", re.I)
        ):
            name = author_el.get_text(strip=True)
            # Tên người: 2-4 từ, chỉ chứa chữ cái, dấu gạch ngang, dấu chấm
            if name and re.match(r"^[A-Za-z\s\-\.]{3,60}$", name):
                metadata["authors"].append(name)

        # Fallback nếu không tìm được author theo class cụ thể
        if not metadata["authors"]:
            for author_el in self.soup.find_all(
                ["span", "a"], class_=re.compile(r"\bauthor\b", re.I)
            ):
                name = author_el.get_text(strip=True)
                if name and re.match(r"^[A-Za-z\s\-\.]{3,60}$", name):
                    metadata["authors"].append(name)

        # Year từ citation
        pub_date = self.soup.find(class_=re.compile(r"pub.?date|published", re.I))
        if pub_date:
            match = re.search(r"\d{4}", pub_date.get_text())
            if match:
                metadata["year"] = int(match.group())

        # Journal
        journal_el = self.soup.find(class_=re.compile(r"journal.?title|source", re.I))
        if journal_el:
            metadata["journal"] = journal_el.get_text(strip=True)

        # DOI
        doi_el = self.soup.find("meta", attrs={"name": "citation_doi"})
        if doi_el:
            metadata["doi"] = doi_el.get("content", "").strip()

        # Keywords
        for kw_el in self.soup.find_all(class_=re.compile(r"keyword", re.I)):
            text = kw_el.get_text(strip=True)
            if text:
                metadata["keywords"].append(text)

        return metadata

    def extract_sections(self) -> list[Section]:
        sections = []
        order    = 0

        for sec_el in self.soup.find_all("sec"):
            title_el = sec_el.find("title")
            name     = title_el.get_text(strip=True) if title_el else f"Section {order + 1}"

            parts = []
            for child in sec_el.children:
                if not hasattr(child, "name") or child.name is None:
                    continue
                if child.name == "title":
                    continue
                if child.name == "sec":
                    continue
                text = child.get_text(separator=" ", strip=True)
                if text:
                    parts.append(text)

            content = "\n".join(parts).strip()
            if not content:
                continue

            # ← SỬA Ở ĐÂY
            level = len(sec_el.find_parents("sec")) + 1
            parent_name = None
            if level > 1 and sections:
                for prev in reversed(sections):
                    if prev.level < level:
                        parent_name = prev.name
                        break

            sections.append(Section(
                name           = name,
                content        = content,
                order          = order,
                level          = level,          # ← thay vì hardcode 1
                section_type   = _infer_section_type(name),
                parent_section = parent_name,    # ← thêm vào
            ))
            order += 1

        if not sections:
            container = self.soup.find("article") or self.soup.find("body")
            sections  = self._extract_sections_from_headings(container)

        return sections


# ---------------------------------------------------------------------------
# Generic Extractor (fallback)
# ---------------------------------------------------------------------------

class _GenericExtractor(_BaseExtractor):

    def extract_metadata(self) -> dict:
        metadata = {"title": "", "authors": [], "year": None,
                    "journal": None, "doi": None, "keywords": [], "language": None}

        # Title từ <title> tag hoặc <h1>
        title_el = self.soup.find("title")
        if title_el:
            metadata["title"] = title_el.get_text(strip=True)
        else:
            h1 = self.soup.find("h1")
            if h1:
                metadata["title"] = h1.get_text(strip=True)

        # Meta tags chuẩn
        for meta in self.soup.find_all("meta"):
            name    = meta.get("name", "").lower()
            content = meta.get("content", "").strip()
            if not content:
                continue
            if name == "author":
                metadata["authors"] = [a.strip() for a in re.split(r",|;", content) if a.strip()]
            elif name == "keywords":
                metadata["keywords"] = [k.strip() for k in re.split(r",|;", content) if k.strip()]
            elif name == "citation_doi":
                metadata["doi"] = content

        # Language
        html_el = self.soup.find("html")
        if html_el:
            metadata["language"] = html_el.get("lang")

        return metadata

    def extract_sections(self) -> list[Section]:
        container = (
            self.soup.find("main") or
            self.soup.find("article") or
            self.soup.find("div", id=re.compile(r"content|main|body", re.I)) or
            self.soup.find("body")
        )
        return self._extract_sections_from_headings(container)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_abstract(sections: list[Section]) -> str:
    for section in sections:
        if section.section_type == "abstract":
            return section.content
    return ""


def _infer_section_type(section_name: str) -> Optional[str]:
    normalized = section_name.lower().strip()
    for key, section_type in sorted(SECTION_TYPE_MAP.items(), key=lambda x: -len(x[0])):
        if key in normalized:
            return section_type
    return "other"


def _infer_figure_type(caption: str) -> str:
    caption_lower = caption.lower()
    if any(w in caption_lower for w in ["chart", "graph", "plot"]):
        return "chart"
    if any(w in caption_lower for w in ["diagram", "architecture", "flow"]):
        return "diagram"
    return "image"


# ---------------------------------------------------------------------------
# Quick self-test (run: python html_loader.py <path_or_url>)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s | %(name)s | %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python html_loader.py <path_to_html_or_url>")
        print("Example: python html_loader.py https://ar5iv.org/abs/2310.01234")
        sys.exit(1)

    source = sys.argv[1]

    try:
        doc = load_html(source)
        print(f"\n{'='*50}")
        print(f"Title    : {doc.title}")
        print(f"Authors  : {', '.join(doc.authors)}")
        print(f"Year     : {doc.year}")
        print(f"Journal  : {doc.journal}")
        print(f"DOI      : {doc.doi}")
        print(f"Keywords : {', '.join(doc.keywords)}")
        print(f"Language : {doc.language}")
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
