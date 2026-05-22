from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
from typing import Any

@dataclass
class Section:
    name: str
    content: str
    order: int
    level: int = 1
    section_type: Optional[str] = None
    parent_section: Optional[str] = None  # tên section cha
    subsections: list["Section"] = field(default_factory=list)

@dataclass
class Table:
    caption: str = ""
    index: Optional[int] = None
    headers: list[str] = field(default_factory=list)
    data: list[list[Any]] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)  # thêm vào
    

@dataclass
class Figure:
    caption: str = ""
    index: Optional[int] = None
    path: Optional[str] = None
    base64_data: Optional[str] = None    # thêm vào
    figure_type: str = "image"  # image | chart | diagram
    alt_text: Optional[str] = None
    keywords: list[str] = field(default_factory=list)  # cho KG

@dataclass
class Formula:
    raw: str
    formula_type: str = "inline"      # inline | block | equation
    index: Optional[int] = None
    display_text: Optional[str] = None   # ← THÊM VÀO
    source_format: str = "unknown"   # thêm vào

@dataclass
class Reference:
    raw_text: str
    doi: Optional[str] = None
    title: Optional[str] = None
    authors: list[str] = field(default_factory=list)
    year: Optional[int] = None
    # THÊM — để link sang UnifiedDocument nếu paper đó đã có trong hệ thống:
    internal_doc_id: Optional[str] = None  # None = chưa có trong DB

@dataclass
class ChunkMeta:
    """
    Metadata của 1 chunk sau khi Qdrant upsert.
    Dùng làm value trong chunk_map truyền vào graph_builder.build_phase3().
    
    qdrant_id: ID đã upsert vào Qdrant — dùng làm MERGE key trong Neo4j
    section:   tên section gốc, VD: "abstract", "method"
    page:      số trang trong PDF gốc (None nếu không parse được)
    text:      nội dung chunk — lưu preview 1000 chars trong Neo4j để debug
               full text nằm ở Qdrant
    """
    qdrant_id: str
    section:   str = ""
    page:      Optional[int] = None
    text:      str = ""

@dataclass
class UnifiedDocument:
    # Bibliographic
    title: str = "Unknown"
    authors: list[str] = field(default_factory=list)
    year: Optional[int] = None
    journal: Optional[str] = None
    doi: Optional[str] = None
    keywords: list[str] = field(default_factory=list)
    doc_id: Optional[str] = None

    # Content
    abstract: str = ""
    full_text: str = ""  # raw OCR text — dùng cho LM metadata extraction
    sections: list[Section] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)
    formulas: list[Formula] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)

    # Source info
    source_file: str = ""
    source_type: str = ""  # pdf | docx | html
    language: Optional[str] = None
    page_count: Optional[int] = None

    processing_status: str = "raw"        # raw | parsed | chunked
    errors: list[str] = field(default_factory=list)  # log lỗi từng stage
    created_at: Optional[datetime] = None
    chunk_ids: list[str] = field(default_factory=list)

    publisher: Optional[str] = None
    volume:    Optional[str] = None
    issue:     Optional[str] = None
    pages:     Optional[str] = None
    citation_count: Optional[int] = None