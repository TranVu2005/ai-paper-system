from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# =============================================================================
# CONSTANTS
# =============================================================================

# Convention: mọi str tham chiếu entity khác đều dùng .name (ConceptEntity)
# hoặc doc_id (PaperEntity / EvidenceEntity). Không mix.

SECTION_TYPES = {
    "abstract", "introduction", "related_work", "background",
    "method", "methodology", "experiment", "result",
    "discussion", "conclusion", "limitation",
    "appendix", "acknowledgment", "unknown",
}

CONCEPT_CATEGORIES = {
    # CS / AI
    "method", "model", "algorithm", "task", "framework", "architecture",
    # Bio / Med
    "gene", "protein", "disease", "drug", "pathway",
    "organism",     # vi khuẩn, virus, sinh vật — LLM hay extract trong paper y tế  [v4]
    # Social / Economics
    "theory", "policy", "event", "organization",
    # Pháp lý / Hành chính  [v3]
    "regulation",           # nghị định, thông tư, luật PPP, quyết định
    "project",              # dự án PPP, dự án xử lý CTRSH
    "contract",             # hợp đồng PPP, hợp đồng dịch vụ
    "financial_instrument", # trái phiếu xanh, tín chỉ carbon, quỹ đầu tư
    # Kỹ thuật / Khoa học vật liệu  [v3]
    "technology",           # công nghệ thông tin, 5G, in 3D
    "process",              # quy trình tích hợp, hóa già, nung, pidgeon process
    "material",             # composite, dolomit, permalloy, hợp kim
    "system",               # hệ thống thông tin, hệ thống giám sát
    # Physics
    "phenomenon", "law", "particle",
    # Economics
    "indicator", "market",
    # Lab / Engineering  [v2]
    "equipment",        # máy đo, thiết bị thí nghiệm (VD: GC-2030, UV-Vis spectrometer)
    "tool",             # phần mềm công cụ (VD: Excel, Minitab, SPSS)
    "method_component", # thành phần của phương pháp (VD: cột sắc ký, detector)
    "compound",         # hợp chất hóa học (VD: cinnamaldehyde, ethanol)
    "software",         # phần mềm phân tích (VD: R, SPSS, STATA)
    # Giáo dục / Xã hội  [v4]
    "education",        # giáo dục STEM, phương pháp giảng dạy
    "activity",         # hoạt động trải nghiệm, hoạt động nhóm
    "group",            # nhóm đối tượng, tổ chuyên môn (vd: tổ KHTN)
    "program",          # chương trình đào tạo, chương trình giảm giá
    "plan",             # kế hoạch dạy học, kế hoạch tổ chức
    "document",         # tài liệu văn bản, hồ sơ hành chính
    # Thống kê / Kinh tế lượng  [v4]
    "test",             # hausman test, t-test, f-test
    "metric",           # chỉ số đo lường (khác MetricEntity — đây là concept về metric)
    # Địa lý / Văn hóa  [v4]
    "location",         # tỉnh, huyện, địa danh — LLM hay extract trong paper xã hội
    # NOTE: "person" KHÔNG có trong đây — entity person bị filter/skip ở extractor
    # Fallback
    "concept",
}

EVIDENCE_TYPES = {
    "dataset", "benchmark", "corpus",          # CS / NLP
    "experiment", "clinical_trial",            # Bio / Med
    "survey", "census", "case_study",          # Social
    "observation", "simulation",               # Physics / General
    "report",                                  # Báo cáo ngành, báo cáo tổng kết  [v2]
    "software",                                # Phần mềm phân tích (VD: SPSS, STATA)  [v2]
    "book",                                    # Sách tham khảo, tác phẩm văn học  [v3]
    "standard",                                # Tiêu chuẩn kỹ thuật (VD: ISO 56000, TCVN)  [v3]
    "event",                                   # Sự kiện lịch sử / thực nghiệm  [v3]
    "journal",                                 # Tạp chí — LLM hay dùng cho nguồn tham khảo  [v4]
    "statistic",                               # Số liệu thống kê, dữ liệu công bố  [v4]
    "secondary_data",                          # Dữ liệu thứ cấp từ nguồn khác  [v4]
    "interview",                               # Phỏng vấn chuyên gia, khảo sát định tính  [v4]
    # NOTE: "organization", "material", "equipment", "regulation", "contract" KHÔNG vào đây
    # → chúng thuộc ConceptEntity với category tương ứng
    # NOTE: "table", "figure", "person", "group" KHÔNG vào đây
    # → table/figure là reference trong text, không phải evidence entity
}

FINDING_TYPES = {
    "result", "hypothesis", "conclusion", "limitation", "contribution",
}

# Các evidence_type mà LLM hay nhầm — nên redirect sang ConceptEntity thay vì EvidenceEntity
# Dùng trong entity_extractor._ExtractionAccumulator.build_entities()
EVIDENCE_TYPE_REDIRECT_TO_CONCEPT = {
    "organization", "material", "equipment", "regulation",
    "contract", "technology", "process", "plan",
}


# =============================================================================
# SHARED UTILS
# =============================================================================

def normalize_entity_name(name: str) -> str:
    return re.sub(r"[-_\s]", "", name).lower()


# =============================================================================
# PAPER ENTITY — node trung tâm, bắt buộc phải có trong KG
# =============================================================================

@dataclass
class PaperEntity:
    """
    Đại diện cho bản thân paper trong KG.
    Mọi entity khác (Author, Concept, Metric, Finding …) đều quan hệ vào node này.

    doc_id  : khớp với UnifiedDocument.doc_id — dùng làm MERGE key trong Neo4j.
    venue   : journal hoặc conference (VD: "NeurIPS 2023", "Nature", "EMNLP").
    domain  : lĩnh vực chính — "cs" | "bio" | "social" | "physics" | "economics" | ...
    """
    doc_id:  str
    title:   str
    year:    Optional[int] = None
    doi:     Optional[str] = None
    venue:   Optional[str] = None
    domain:  Optional[str] = None


# =============================================================================
# UNIVERSAL ENTITIES
# =============================================================================

@dataclass
class AuthorEntity:
    """
    Không đổi — Author là universal.
    """
    name:             str
    affiliation:      Optional[str] = None
    email:            Optional[str] = None
    country:          Optional[str] = None
    institution_type: Optional[str] = None  # "university" | "company" | "research_lab" | ...


@dataclass
class ConceptEntity:
    """
    Thay thế MethodEntity + TaskEntity.

    category  : xem CONCEPT_CATEGORIES — bắt buộc khớp với set đó.
    domain    : lĩnh vực — "cs" | "bio" | "social" | "physics" | "economics" | ...
    based_on  : list[str] — tên ConceptEntity mà concept này *sử dụng / phụ thuộc*.
                VD: "Transformer" based_on ["Attention Mechanism", "Positional Encoding"]
    extends   : list[str] — tên ConceptEntity mà concept này *kế thừa / cải tiến*.
                VD: "RoBERTa" extends ["BERT"]
    source_paper   : doc_id của UnifiedDocument chứa entity này.
    source_section : tên section (nên khớp SECTION_TYPES).
    """
    name:           str
    category:       str            = "concept"
    aliases:        list[str]      = field(default_factory=list)
    based_on:       list[str]      = field(default_factory=list)   # dùng / phụ thuộc
    extends:        list[str]      = field(default_factory=list)   # kế thừa / cải tiến
    domain:         Optional[str]  = None
    source_paper:   Optional[str]  = None   # doc_id
    source_section: Optional[str]  = None   # tên section
    confidence:     float          = 0.80
    evidence:       str            = ""

    def __post_init__(self) -> None:
        if self.category not in CONCEPT_CATEGORIES:
            # Không raise — chỉ warn để không block pipeline
            import warnings
            warnings.warn(
                f"ConceptEntity '{self.name}': category '{self.category}' "
                f"không nằm trong CONCEPT_CATEGORIES. Fallback → 'concept'.",
                stacklevel=2,
            )
            self.category = "concept"


@dataclass
class EvidenceEntity:
    """
    Thay thế DatasetEntity — dùng chung cho mọi lĩnh vực.

    evidence_type : xem EVIDENCE_TYPES.
    language      : chỉ relevant với NLP / corpus.
    source_paper  : doc_id.
    """
    name:           str
    evidence_type:  str            = "dataset"
    language:       Optional[str]  = None
    aliases:        list[str]      = field(default_factory=list)
    source_paper:   Optional[str]  = None   # doc_id
    source_section: Optional[str]  = None
    confidence:     float          = 0.80
    evidence:       str            = ""

    def __post_init__(self) -> None:
        if self.evidence_type not in EVIDENCE_TYPES:
            import warnings
            warnings.warn(
                f"EvidenceEntity '{self.name}': evidence_type '{self.evidence_type}' "
                f"không nằm trong EVIDENCE_TYPES. Fallback → 'dataset'.",
                stacklevel=2,
            )
            self.evidence_type = "dataset"   # [v4] thêm fallback, tránh lưu type sai vào Neo4j


@dataclass
class MetricEntity:
    """
    Metric / kết quả đo lường — universal.

    measured_on : tên EvidenceEntity (dataset / benchmark) mà metric được đo trên đó.
                  VD: "SQuAD 2.0"
    measured_by : tên ConceptEntity (method / model) thực hiện phép đo.
                  VD: "RoBERTa-large"

    Không có 2 field này thì KG không thể trả lời:
      "F1=92.3 đo trên dataset nào, bằng model nào?"

    higher_better : True  → cao hơn tốt hơn (Accuracy, F1, BLEU …)
                    False → thấp hơn tốt hơn (Perplexity, FID, MAE …)
                    None  → không xác định
    source_paper  : doc_id.
    """
    name:          str
    value:         Optional[float] = None
    unit:          Optional[str]   = None        # "%", "score", "eV", ...
    higher_better: Optional[bool]  = None
    measured_on:   str             = ""          # tên EvidenceEntity, "" nếu không rõ
    measured_by:   str             = ""          # tên ConceptEntity, "" nếu không rõ
    source_paper:  Optional[str]   = None        # doc_id
    evidence:      str             = ""


@dataclass
class FindingEntity:
    """
    Kết luận / claim chính của paper — universal nhất.

    supports     : list[str] — doc_id của paper mà finding này *ủng hộ / xác nhận*.
    contradicts  : list[str] — doc_id của paper mà finding này *phản bác / mâu thuẫn*.

    2 field trên là nền tảng của citation graph và claim tracking:
      "Paper A ủng hộ kết quả của Paper B"
      "Paper C phản bác hypothesis của Paper D"

    finding_type  : xem FINDING_TYPES.
    source_paper  : doc_id.
    source_section: tên section (nên khớp SECTION_TYPES).
    """
    description:    str
    finding_type:   str           = "result"
    supports:       list[str]     = field(default_factory=list)   # doc_id list
    contradicts:    list[str]     = field(default_factory=list)   # doc_id list
    source_paper:   Optional[str] = None   # doc_id
    source_section: Optional[str] = None
    confidence:     float         = 0.80
    evidence:       str           = ""

    def __post_init__(self) -> None:
        if self.finding_type not in FINDING_TYPES:
            import warnings
            warnings.warn(
                f"FindingEntity: finding_type '{self.finding_type}' "
                f"không nằm trong FINDING_TYPES. Fallback → 'result'.",
                stacklevel=2,
            )
            self.finding_type = "result"


# =============================================================================
# CONTAINER
# =============================================================================

@dataclass
class ExtractedEntities:
    """
    Container kết quả — graph_builder nhận object này.

    paper     : PaperEntity tương ứng với doc đang xử lý.
                graph_builder dùng paper.doc_id làm MERGE key cho node trung tâm.
    authors   : list AuthorEntity
    concepts  : list ConceptEntity  (thay methods + tasks)
    evidences : list EvidenceEntity (thay datasets)
    metrics   : list MetricEntity
    findings  : list FindingEntity
    """
    paper:     Optional[PaperEntity]  = None
    authors:   list[AuthorEntity]     = field(default_factory=list)
    concepts:  list[ConceptEntity]    = field(default_factory=list)
    evidences: list[EvidenceEntity]   = field(default_factory=list)
    metrics:   list[MetricEntity]     = field(default_factory=list)
    findings:  list[FindingEntity]    = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.paper or self.authors or self.concepts
            or self.evidences or self.metrics or self.findings
        )

    def summary(self) -> str:
        paper_id = self.paper.doc_id if self.paper else "None"
        return (
            f"paper={paper_id} "
            f"authors={len(self.authors)} "
            f"concepts={len(self.concepts)} "
            f"evidences={len(self.evidences)} "
            f"metrics={len(self.metrics)} "
            f"findings={len(self.findings)}"
        )
