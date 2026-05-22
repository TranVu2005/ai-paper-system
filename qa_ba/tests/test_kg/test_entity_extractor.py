"""
test_entity_extractor.py

Test suite toàn diện cho EntityExtractor — 200 file JSON, 5 domain tiếng Việt.

Domain:
    - khoa học y dược          (y_duoc)
    - khoa học xã hội nhân văn (xa_hoi_nhan_van)
    - khoa học tự nhiên        (tu_nhien)
    - khoa học nông nghiệp     (nong_nghiep)
    - khoa học kỹ thuật công nghệ (ky_thuat_cong_nghe)

Entity types được test:
    AuthorEntity, ConceptEntity, EvidenceEntity, MetricEntity,
    FindingEntity, PaperEntity, ExtractedEntities (container)

Output:
    - Bảng 3.4  Precision / Recall / F1 theo entity type
    - Bảng 3.5  F1 theo domain
    - Biểu đồ 3.3  Bar chart F1 theo entity type
    + Các bảng/biểu đồ bổ sung

Cách chạy:
    python test_entity_extractor.py                        # toàn bộ 200 file
    python test_entity_extractor.py --domain y_duoc        # 1 domain
    python test_entity_extractor.py --limit 20             # 20 file đầu
    python test_entity_extractor.py --no-llm               # mock LLM (nhanh)
    python test_entity_extractor.py --output results/      # thư mục lưu biểu đồ
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import traceback
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import concurrent.futures

# ── thêm project root vào sys.path ─────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── visualization ───────────────────────────────────────────────────────
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    import numpy as np
    HAS_PLOT = True
except ImportError:
    HAS_PLOT = False
    warnings.warn("matplotlib/numpy không có — bỏ qua biểu đồ. pip install matplotlib numpy")

# ── project imports ─────────────────────────────────────────────────────
try:
    from ai_module.kg.entity_extractor import (
        EntityExtractor,
        AnthropicBackend,
        OllamaBackend,
        LLMBackend,
        parse_authors,
    )
    from ai_module.kg.entities import (
        AuthorEntity, ConceptEntity, EvidenceEntity,
        MetricEntity, FindingEntity, ExtractedEntities,
        CONCEPT_CATEGORIES, EVIDENCE_TYPES,
    )
    from ingestion.schema.document_schema import UnifiedDocument, Section
except ImportError as e:
    print(f"[FATAL] Import lỗi: {e}")
    print("Chạy từ thư mục gốc project hoặc kiểm tra PYTHONPATH.")
    sys.exit(1)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_entity_extractor")

# ═══════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

DATA_DIR = Path(r"D:\Study\Study_Class\Semester_6\Data_mining\Project\data\processed")

DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "y_duoc": [
        "thuốc", "bệnh", "điều trị", "lâm sàng", "dược", "y tế",
        "bệnh nhân", "virus", "vi khuẩn", "gen", "protein",
        "clinical", "drug", "disease", "treatment", "medical",
        "vaccine", "kháng sinh", "ung thư", "đái tháo đường",
    ],
    "xa_hoi_nhan_van": [
        "xã hội", "văn hóa", "lịch sử", "giáo dục", "kinh tế",
        "chính sách", "pháp luật", "nhân văn", "ngôn ngữ",
        "văn học", "triết học", "tâm lý", "sociology", "history",
        "education", "policy", "law", "culture",
    ],
    "tu_nhien": [
        "vật lý", "hóa học", "toán học", "sinh học", "địa chất",
        "thiên văn", "physics", "chemistry", "mathematics",
        "biology", "geology", "quantum", "phân tử", "nguyên tử",
        "phản ứng", "hợp chất",
    ],
    "nong_nghiep": [
        "nông nghiệp", "cây trồng", "vật nuôi", "đất", "phân bón",
        "thuốc trừ sâu", "giống", "lúa", "hoa màu", "thủy sản",
        "agriculture", "crop", "soil", "fertilizer", "livestock",
        "pesticide", "irrigation", "harvest",
    ],
    "ky_thuat_cong_nghe": [
        "kỹ thuật", "công nghệ", "phần mềm", "máy tính", "mạng",
        "trí tuệ nhân tạo", "robot", "tự động hóa", "điện tử",
        "viễn thông", "engineering", "software", "network", "AI",
        "machine learning", "deep learning", "IoT", "algorithm",
        "hardware", "circuit",
    ],
}

ENTITY_TYPES = [
    "author", "concept", "evidence", "metric", "finding", "paper",
]

DOMAIN_LABELS = {
    "y_duoc":             "Y Dược",
    "xa_hoi_nhan_van":    "XH & Nhân Văn",
    "tu_nhien":           "Tự Nhiên",
    "nong_nghiep":        "Nông Nghiệp",
    "ky_thuat_cong_nghe": "KT & CN",
}

ENTITY_TYPE_LABELS = {
    "author":   "Author",
    "concept":  "Concept",
    "evidence": "Evidence",
    "metric":   "Metric",
    "finding":  "Finding",
    "paper":    "Paper",
}

# Vietnamese-specific challenge patterns
VI_CHALLENGE_PATTERNS = {
    "tone_marks":     re.compile(r"[àáảãạăắặằẳẵâấậầẩẫèéẻẽẹêếệềểễìíỉĩịòóỏõọôốộồổỗơớợờởỡùúủũụưứựừửữỳýỷỹỵđ]"),
    "compound_words": re.compile(r"\b\w+\s+\w+\b"),
    "vi_stopwords":   {"và", "hoặc", "trong", "của", "các", "những", "được", "này", "đó", "với"},
    "abbreviations":  re.compile(r"\b[A-ZĐÁẮẮẴẮẶẮẮ]{2,}\b"),
}

# ═══════════════════════════════════════════════════════════════════════
# MOCK LLM (no-llm mode)
# ═══════════════════════════════════════════════════════════════════════

class MockLLMBackend(LLMBackend):
    """Mock LLM trả về JSON hợp lệ cho testing không cần API."""

    @property
    def model(self) -> str:
        return "mock-model"

    def extract(self, system: str, user: str) -> str:
        section_hint = ""
        if "section:" in user:
            m = re.search(r"section:\s*(\w+)", user)
            if m:
                section_hint = m.group(1)

        # Extract content keywords to generate realistic mock entities
        content = user[:500].lower()
        concepts = self._infer_concepts(content, section_hint)
        evidences = self._infer_evidences(content)
        metrics = self._infer_metrics(content)
        findings = self._infer_findings(content, section_hint)

        return json.dumps({
            "concepts":  concepts,
            "evidences": evidences,
            "metrics":   metrics,
            "findings":  findings,
        }, ensure_ascii=False)

    def _infer_concepts(self, content: str, section_hint: str) -> list[dict]:
        results = []
        method_keywords = [
            ("học máy", "model"), ("deep learning", "model"),
            ("mạng nơ-ron", "architecture"), ("transformer", "architecture"),
            ("phương pháp", "method"), ("thuật toán", "algorithm"),
            ("hồi quy", "method"), ("phân loại", "task"),
            ("xử lý ngôn ngữ", "task"), ("thống kê", "method"),
        ]
        for kw, cat in method_keywords:
            if kw in content:
                results.append({
                    "name": kw, "category": cat,
                    "aliases": [], "based_on": [], "extends": [],
                    "domain": None,
                    "evidence": f"Tìm thấy '{kw}' trong section {section_hint}",
                })
        return results[:3]

    def _infer_evidences(self, content: str) -> list[dict]:
        results = []
        ev_keywords = [
            ("khảo sát", "survey"), ("bộ dữ liệu", "dataset"),
            ("thực nghiệm", "experiment"), ("báo cáo", "report"),
        ]
        for kw, etype in ev_keywords:
            if kw in content:
                results.append({
                    "name": f"dữ liệu {kw}", "evidence_type": etype,
                    "language": "vi", "aliases": [],
                    "evidence": f"Tìm thấy '{kw}' trong đoạn văn",
                })
        return results[:2]

    def _infer_metrics(self, content: str) -> list[dict]:
        results = []
        metric_keywords = [
            "độ chính xác", "recall", "f1", "accuracy",
            "precision", "auc", "rmse", "mae",
        ]
        for kw in metric_keywords:
            if kw in content:
                results.append({
                    "name": kw, "value": None, "unit": "%",
                    "higher_better": True,
                    "measured_on": None, "measured_by": None,
                    "evidence": f"Đề cập '{kw}'",
                })
        return results[:2]

    def _infer_findings(self, content: str, section_hint: str) -> list[dict]:
        if section_hint in ("conclusion", "result", "discussion"):
            return [{
                "description": "Phương pháp đề xuất cho kết quả tốt hơn baseline",
                "finding_type": "result",
                "supports": [], "contradicts": [],
                "evidence": "Kết quả thực nghiệm cho thấy...",
            }]
        return []


# ═══════════════════════════════════════════════════════════════════════
# DOCUMENT LOADER
# ═══════════════════════════════════════════════════════════════════════

def _detect_domain(data: dict) -> str:
    """Phát hiện domain từ JSON document."""
    # Ưu tiên field domain/category/field nếu có
    for field_name in ("domain", "category", "field", "subject", "lĩnh vực"):
        val = data.get(field_name, "")
        if isinstance(val, str) and val:
            val_lower = val.lower()
            for domain, keywords in DOMAIN_KEYWORDS.items():
                if any(kw.lower() in val_lower for kw in keywords[:5]):
                    return domain

    # Fallback: scan title + abstract + keywords
    text_to_scan = " ".join(filter(None, [
        str(data.get("title", "")),
        str(data.get("abstract", ""))[:300],
        " ".join(data.get("keywords", [])),
    ])).lower()

    scores: dict[str, int] = {}
    for domain, keywords in DOMAIN_KEYWORDS.items():
        scores[domain] = sum(1 for kw in keywords if kw.lower() in text_to_scan)

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "ky_thuat_cong_nghe"  # default


def _json_to_unified_doc(data: dict, file_path: Path) -> Optional[UnifiedDocument]:
    """Convert JSON dict → UnifiedDocument."""
    try:
        # Build sections
        sections = []
        raw_sections = data.get("sections") or data.get("content_sections") or []
        if isinstance(raw_sections, list):
            for i, s in enumerate(raw_sections):
                if isinstance(s, dict):
                    name    = s.get("name") or s.get("title") or s.get("section_type") or f"section_{i}"
                    content = s.get("content") or s.get("text") or ""
                    stype   = s.get("section_type") or s.get("type") or name.lower()
                elif isinstance(s, str):
                    name, content, stype = f"section_{i}", s, "unknown"
                else:
                    continue
                if content and len(content.strip()) > 20:
                    sections.append(Section(
                        name=str(name), content=str(content),
                        order=i, section_type=str(stype),
                    ))

        # Nếu không có sections, dùng abstract/full_text
        if not sections:
            full_text = (
                data.get("full_text") or
                data.get("body") or
                data.get("text") or ""
            )
            abstract = data.get("abstract") or ""
            if abstract:
                sections.append(Section(name="abstract", content=str(abstract), order=0, section_type="abstract"))
            if full_text and len(str(full_text)) > 100:
                sections.append(Section(name="body", content=str(full_text)[:8000], order=1, section_type="introduction"))

        # Authors
        raw_authors = data.get("authors") or []
        if isinstance(raw_authors, list):
            authors = [str(a) for a in raw_authors if a]
        elif isinstance(raw_authors, str):
            authors = [a.strip() for a in raw_authors.split(";") if a.strip()]
        else:
            authors = []

        doc = UnifiedDocument(
            title=str(data.get("title") or file_path.stem),
            abstract=str(data.get("abstract") or "")[:2000],
            authors=authors,
            sections=sections,
            year=int(data.get("year") or data.get("publication_year") or 0) or None,
            doi=data.get("doi") or data.get("DOI") or None,
            keywords=data.get("keywords") or [],
            language=data.get("language") or "vi",
        )
        # Gắn thêm metadata
        doc.doc_id      = data.get("doc_id") or data.get("id") or file_path.stem
        doc.journal     = data.get("journal") or data.get("venue") or None
        doc.source_file = str(file_path)
        return doc
    except Exception as e:
        logger.warning("_json_to_unified_doc: lỗi file=%s — %s", file_path.name, e)
        return None


def load_documents(
    data_dir: Path,
    domain_filter: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[tuple[UnifiedDocument, str, Path]]:
    """
    Load tất cả JSON từ data_dir.
    Returns: list of (UnifiedDocument, domain, file_path)
    """
    if not data_dir.exists():
        raise FileNotFoundError(f"DATA_DIR không tồn tại: {data_dir}")

    json_files = sorted(data_dir.glob("**/*.json"))
    if not json_files:
        json_files = sorted(data_dir.glob("*.json"))

    print(f"[LOAD] Tìm thấy {len(json_files)} file JSON trong {data_dir}")

    results = []
    errors  = 0
    domain_counts: dict[str, int] = defaultdict(int)

    for fp in json_files:
        try:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.debug("Không đọc được %s: %s", fp.name, e)
            errors += 1
            continue

        if isinstance(data, list):
            data = data[0] if data else {}

        domain = _detect_domain(data)
        if domain_filter and domain != domain_filter:
            continue

        doc = _json_to_unified_doc(data, fp)
        if doc and doc.sections:
            results.append((doc, domain, fp))
            domain_counts[domain] += 1

        if limit and len(results) >= limit:
            break

    print(f"[LOAD] Loaded {len(results)} documents (errors={errors})")
    for d, cnt in sorted(domain_counts.items()):
        print(f"       {DOMAIN_LABELS.get(d, d)}: {cnt}")

    return results


# ═══════════════════════════════════════════════════════════════════════
# EVALUATION METRICS
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class EntityMetrics:
    """Precision / Recall / F1 cho 1 entity type."""
    entity_type:    str
    total_docs:     int   = 0
    extracted_count: int  = 0   # tổng entity extracted (≥ 1 per doc)
    non_empty_docs:  int  = 0   # số doc có entity loại này
    avg_per_doc:     float = 0.0
    quality_score:   float = 0.0  # dựa trên validity checks
    precision:       float = 0.0  # ratio valid entities
    recall:          float = 0.0  # ratio docs có ít nhất 1 entity
    f1:              float = 0.0
    errors:          int   = 0
    warnings:        int   = 0
    latency_ms:      float = 0.0


@dataclass
class DomainMetrics:
    domain:          str
    label:           str
    total_docs:      int   = 0
    entity_counts:   dict  = field(default_factory=dict)
    f1_by_type:      dict  = field(default_factory=dict)
    avg_f1:          float = 0.0
    total_concepts:  int   = 0
    total_evidences: int   = 0
    total_metrics:   int   = 0
    total_findings:  int   = 0
    errors:          int   = 0
    latency_ms:      float = 0.0


@dataclass
class VietnameseChallenge:
    """Metrics đặc thù tiếng Việt."""
    total_docs:            int   = 0
    docs_with_tone_marks:  int   = 0
    docs_with_abbreviations: int = 0
    avg_entity_name_length: float = 0.0
    # Entities với tên tiếng Việt thuần (có dấu)
    vi_entity_count:       int   = 0
    mixed_entity_count:    int   = 0  # mix Việt + Anh
    en_entity_count:       int   = 0
    # Category distribution
    category_dist:         dict  = field(default_factory=dict)


@dataclass
class TestResult:
    """Container kết quả toàn bộ test run."""
    total_docs:       int = 0
    total_files:      int = 0
    errors:           int = 0
    total_time_s:     float = 0.0
    entity_metrics:   dict[str, EntityMetrics]  = field(default_factory=dict)
    domain_metrics:   dict[str, DomainMetrics]  = field(default_factory=dict)
    vi_challenges:    VietnameseChallenge        = field(default_factory=VietnameseChallenge)
    raw_results:      list[dict]                 = field(default_factory=list)
    # Per-file latency
    latencies_ms:     list[float]               = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# ENTITY VALIDATORS
# ═══════════════════════════════════════════════════════════════════════

def validate_author(e: AuthorEntity) -> tuple[bool, list[str]]:
    issues = []
    if not e.name or len(e.name.strip()) < 2:
        issues.append("name quá ngắn")
    if e.name and re.match(r"^\d+$", e.name.strip()):
        issues.append("name là số")
    return len(issues) == 0, issues


def validate_concept(e: ConceptEntity) -> tuple[bool, list[str]]:
    issues = []
    if not e.name or len(e.name.strip()) < 2:
        issues.append("name quá ngắn")
    if e.category not in CONCEPT_CATEGORIES:
        issues.append(f"category không hợp lệ: {e.category}")
    if e.confidence < 0 or e.confidence > 1:
        issues.append(f"confidence ngoài [0,1]: {e.confidence}")
    return len(issues) == 0, issues


def validate_evidence(e: EvidenceEntity) -> tuple[bool, list[str]]:
    issues = []
    if not e.name or len(e.name.strip()) < 4:
        issues.append("name quá ngắn (<4 ký tự)")
    if e.evidence_type not in EVIDENCE_TYPES:
        issues.append(f"evidence_type không hợp lệ: {e.evidence_type}")
    return len(issues) == 0, issues


def validate_metric(e: MetricEntity) -> tuple[bool, list[str]]:
    issues = []
    if not e.name or len(e.name.strip()) < 1:
        issues.append("name rỗng")
    if e.value is not None and (e.value < -1e6 or e.value > 1e6):
        issues.append(f"value bất thường: {e.value}")
    return len(issues) == 0, issues


def validate_finding(e: FindingEntity) -> tuple[bool, list[str]]:
    issues = []
    if not e.description or len(e.description.strip()) < 10:
        issues.append("description quá ngắn")
    if e.finding_type not in ("result", "hypothesis", "conclusion", "limitation", "contribution"):
        issues.append(f"finding_type không hợp lệ: {e.finding_type}")
    return len(issues) == 0, issues


def validate_paper(e) -> tuple[bool, list[str]]:
    issues = []
    if not e:
        return False, ["paper entity là None"]
    if not e.doc_id:
        issues.append("doc_id rỗng")
    if not e.title or e.title == "Unknown":
        issues.append("title rỗng hoặc Unknown")
    return len(issues) == 0, issues


VALIDATORS = {
    "author":   validate_author,
    "concept":  validate_concept,
    "evidence": validate_evidence,
    "metric":   validate_metric,
    "finding":  validate_finding,
    "paper":    validate_paper,
}


def count_valid(entities: list, validator) -> tuple[int, int]:
    """Returns (valid_count, total_count)."""
    if not entities:
        return 0, 0
    valid = sum(1 for e in entities if validator(e)[0])
    return valid, len(entities)


# ═══════════════════════════════════════════════════════════════════════
# VIETNAMESE CHALLENGE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════

def analyze_vietnamese_challenges(
    entities: ExtractedEntities,
    vi_stats: VietnameseChallenge,
) -> None:
    """Phân tích đặc thù tiếng Việt trong entity."""
    vi_stats.total_docs += 1

    all_names = (
        [e.name for e in entities.concepts]
        + [e.name for e in entities.evidences]
        + [e.name for e in (entities.authors or [])]
    )
    if not all_names:
        return

    # Tone marks
    if any(VI_CHALLENGE_PATTERNS["tone_marks"].search(n) for n in all_names):
        vi_stats.docs_with_tone_marks += 1

    # Abbreviations
    all_text = " ".join(all_names)
    if VI_CHALLENGE_PATTERNS["abbreviations"].search(all_text):
        vi_stats.docs_with_abbreviations += 1

    # Avg name length
    if all_names:
        avg_len = sum(len(n) for n in all_names) / len(all_names)
        vi_stats.avg_entity_name_length = (
            vi_stats.avg_entity_name_length * (vi_stats.total_docs - 1) + avg_len
        ) / vi_stats.total_docs

    # Language classification
    for name in all_names:
        has_vi = bool(VI_CHALLENGE_PATTERNS["tone_marks"].search(name))
        has_en = bool(re.search(r"[a-zA-Z]", name))
        if has_vi and has_en:
            vi_stats.mixed_entity_count += 1
        elif has_vi:
            vi_stats.vi_entity_count += 1
        elif has_en:
            vi_stats.en_entity_count += 1

    # Category distribution
    for e in entities.concepts:
        vi_stats.category_dist[e.category] = vi_stats.category_dist.get(e.category, 0) + 1


# ═══════════════════════════════════════════════════════════════════════
# CORE TEST RUNNER
# ═══════════════════════════════════════════════════════════════════════

def run_extraction(
    extractor: EntityExtractor,
    doc: UnifiedDocument,
) -> tuple[Optional[ExtractedEntities], float, Optional[str]]:
    """
    Chạy extraction cho 1 document.
    Returns: (entities, latency_ms, error_msg)
    """
    start = time.perf_counter()
    try:
        entities = extractor.extract(doc)
        latency  = (time.perf_counter() - start) * 1000
        return entities, latency, None
    except Exception as e:
        latency = (time.perf_counter() - start) * 1000
        return None, latency, str(e)


def compute_f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def run_tests(
    extractor: EntityExtractor,
    documents: list[tuple[UnifiedDocument, str, Path]],
    max_workers: int = 1,
) -> TestResult:
    """
    Chạy test trên toàn bộ documents.
    """
    result = TestResult(
        total_files=len(documents),
        vi_challenges=VietnameseChallenge(),
    )

    # Init domain metrics
    for domain in DOMAIN_KEYWORDS:
        result.domain_metrics[domain] = DomainMetrics(
            domain=domain,
            label=DOMAIN_LABELS.get(domain, domain),
        )

    # Accumulators per entity type
    type_acc: dict[str, dict] = {
        t: {
            "total_docs": 0, "non_empty": 0,
            "valid": 0, "total_extracted": 0,
            "errors": 0, "latencies": [],
        }
        for t in ENTITY_TYPES
    }

    raw_per_domain: dict[str, list[dict]] = defaultdict(list)

    print(f"\n[RUN] Extracting {len(documents)} documents...")
    start_all = time.time()

    for idx, (doc, domain, fp) in enumerate(documents, 1):
        if idx % 10 == 0 or idx == 1:
            pct = idx / len(documents) * 100
            print(f"  [{idx:3d}/{len(documents)}] {pct:5.1f}% — {fp.name[:50]}")

        entities, latency, err = run_extraction(extractor, doc)
        result.latencies_ms.append(latency)
        result.total_docs += 1

        if err or entities is None:
            result.errors += 1
            for t in ENTITY_TYPES:
                type_acc[t]["errors"] += 1
                type_acc[t]["total_docs"] += 1
            dm = result.domain_metrics[domain]
            dm.total_docs += 1
            dm.errors += 1
            continue

        # ── per-doc stats ─────────────────────────────────────────────
        doc_stats: dict[str, int] = {}

        # Authors
        n_auth = len(entities.authors or [])
        v_auth, _ = count_valid(entities.authors or [], validate_author)
        type_acc["author"]["total_docs"]      += 1
        type_acc["author"]["total_extracted"] += n_auth
        type_acc["author"]["valid"]           += v_auth
        type_acc["author"]["latencies"].append(latency)
        if n_auth > 0:
            type_acc["author"]["non_empty"] += 1
        doc_stats["author"] = n_auth

        # Concepts
        n_conc = len(entities.concepts)
        v_conc, _ = count_valid(entities.concepts, validate_concept)
        type_acc["concept"]["total_docs"]      += 1
        type_acc["concept"]["total_extracted"] += n_conc
        type_acc["concept"]["valid"]           += v_conc
        type_acc["concept"]["latencies"].append(latency)
        if n_conc > 0:
            type_acc["concept"]["non_empty"] += 1
        doc_stats["concept"] = n_conc

        # Evidences
        n_ev = len(entities.evidences)
        v_ev, _ = count_valid(entities.evidences, validate_evidence)
        type_acc["evidence"]["total_docs"]      += 1
        type_acc["evidence"]["total_extracted"] += n_ev
        type_acc["evidence"]["valid"]           += v_ev
        type_acc["evidence"]["latencies"].append(latency)
        if n_ev > 0:
            type_acc["evidence"]["non_empty"] += 1
        doc_stats["evidence"] = n_ev

        # Metrics
        n_met = len(entities.metrics)
        v_met, _ = count_valid(entities.metrics, validate_metric)
        type_acc["metric"]["total_docs"]      += 1
        type_acc["metric"]["total_extracted"] += n_met
        type_acc["metric"]["valid"]           += v_met
        type_acc["metric"]["latencies"].append(latency)
        if n_met > 0:
            type_acc["metric"]["non_empty"] += 1
        doc_stats["metric"] = n_met

        # Findings
        n_fin = len(entities.findings)
        v_fin, _ = count_valid(entities.findings, validate_finding)
        type_acc["finding"]["total_docs"]      += 1
        type_acc["finding"]["total_extracted"] += n_fin
        type_acc["finding"]["valid"]           += v_fin
        type_acc["finding"]["latencies"].append(latency)
        if n_fin > 0:
            type_acc["finding"]["non_empty"] += 1
        doc_stats["finding"] = n_fin

        # Paper
        paper_valid, _ = validate_paper(entities.paper)
        type_acc["paper"]["total_docs"]      += 1
        type_acc["paper"]["total_extracted"] += 1
        type_acc["paper"]["valid"]           += int(paper_valid)
        type_acc["paper"]["latencies"].append(latency)
        if paper_valid:
            type_acc["paper"]["non_empty"] += 1
        doc_stats["paper"] = int(paper_valid)

        # ── domain stats ──────────────────────────────────────────────
        dm = result.domain_metrics[domain]
        dm.total_docs      += 1
        dm.latency_ms      += latency
        dm.total_concepts  += n_conc
        dm.total_evidences += n_ev
        dm.total_metrics   += n_met
        dm.total_findings  += n_fin

        # ── Vietnamese challenge analysis ─────────────────────────────
        analyze_vietnamese_challenges(entities, result.vi_challenges)

        # ── raw result log ────────────────────────────────────────────
        raw_per_domain[domain].append({
            "file":     fp.name,
            "latency":  latency,
            "authors":  n_auth,
            "concepts": n_conc,
            "evidences": n_ev,
            "metrics":  n_met,
            "findings": n_fin,
            "paper_ok": paper_valid,
        })
        result.raw_results.append({
            "domain": domain,
            **raw_per_domain[domain][-1],
        })

    result.total_time_s = time.time() - start_all

    # ── Compute EntityMetrics ─────────────────────────────────────────
    for etype, acc in type_acc.items():
        total   = acc["total_docs"]
        ne      = acc["non_empty"]
        n_valid = acc["valid"]
        n_total = acc["total_extracted"]

        precision = n_valid / n_total if n_total > 0 else 0.0
        recall    = ne / total if total > 0 else 0.0
        f1        = compute_f1(precision, recall)
        avg_lat   = sum(acc["latencies"]) / len(acc["latencies"]) if acc["latencies"] else 0.0

        result.entity_metrics[etype] = EntityMetrics(
            entity_type=etype,
            total_docs=total,
            extracted_count=n_total,
            non_empty_docs=ne,
            avg_per_doc=n_total / total if total > 0 else 0.0,
            quality_score=precision,
            precision=precision,
            recall=recall,
            f1=f1,
            errors=acc["errors"],
            latency_ms=avg_lat,
        )

    # ── Compute DomainMetrics F1 ──────────────────────────────────────
    for domain, dm in result.domain_metrics.items():
        if dm.total_docs == 0:
            continue
        doc_rows = raw_per_domain[domain]
        # F1 per entity type in this domain
        for etype in ENTITY_TYPES:
            ne  = sum(1 for r in doc_rows if r.get(etype, 0) > 0)
            prec_hint = result.entity_metrics[etype].precision if etype in result.entity_metrics else 0.5
            recall_d  = ne / dm.total_docs if dm.total_docs > 0 else 0.0
            dm.f1_by_type[etype] = compute_f1(prec_hint, recall_d)
        dm.avg_f1 = sum(dm.f1_by_type.values()) / len(dm.f1_by_type) if dm.f1_by_type else 0.0

    return result


# ═══════════════════════════════════════════════════════════════════════
# REPORT TABLES
# ═══════════════════════════════════════════════════════════════════════

LINE = "─" * 88

def print_table_34(result: TestResult) -> None:
    """Bảng 3.4 — Precision / Recall / F1 theo entity type."""
    print(f"\n{LINE}")
    print("  BẢNG 3.4 — Precision / Recall / F1 theo Entity Type")
    print(LINE)
    header = f"{'Entity Type':<16} {'Total Docs':>10} {'Extracted':>10} {'Non-Empty':>10} {'Avg/Doc':>8} {'Precision':>10} {'Recall':>8} {'F1':>8} {'Latency(ms)':>12}"
    print(header)
    print(LINE)

    for etype in ENTITY_TYPES:
        m = result.entity_metrics.get(etype)
        if not m:
            continue
        label = ENTITY_TYPE_LABELS.get(etype, etype)
        print(
            f"  {label:<14} {m.total_docs:>10} {m.extracted_count:>10} "
            f"{m.non_empty_docs:>10} {m.avg_per_doc:>8.2f} "
            f"{m.precision:>10.4f} {m.recall:>8.4f} {m.f1:>8.4f} "
            f"{m.latency_ms:>12.1f}"
        )

    print(LINE)
    # Summary row
    all_f1 = [m.f1 for m in result.entity_metrics.values()]
    all_p  = [m.precision for m in result.entity_metrics.values()]
    all_r  = [m.recall for m in result.entity_metrics.values()]
    if all_f1:
        print(
            f"  {'MACRO AVG':<14} {'':>10} {'':>10} {'':>10} {'':>8} "
            f"{sum(all_p)/len(all_p):>10.4f} {sum(all_r)/len(all_r):>8.4f} "
            f"{sum(all_f1)/len(all_f1):>8.4f}"
        )
    print(LINE)


def print_table_35(result: TestResult) -> None:
    """Bảng 3.5 — F1 theo domain."""
    print(f"\n{LINE}")
    print("  BẢNG 3.5 — F1 Score theo Domain và Entity Type")
    print(LINE)

    header_parts = ["Domain".ljust(22)]
    for etype in ENTITY_TYPES:
        header_parts.append(f"{ENTITY_TYPE_LABELS.get(etype, etype):>10}")
    header_parts.append(f"{'Avg F1':>10}")
    header_parts.append(f"{'Docs':>6}")
    print("  " + "".join(header_parts))
    print(LINE)

    for domain in DOMAIN_KEYWORDS:
        dm = result.domain_metrics.get(domain)
        if not dm or dm.total_docs == 0:
            continue
        row = [DOMAIN_LABELS.get(domain, domain).ljust(22)]
        for etype in ENTITY_TYPES:
            f1 = dm.f1_by_type.get(etype, 0.0)
            row.append(f"{f1:>10.4f}")
        row.append(f"{dm.avg_f1:>10.4f}")
        row.append(f"{dm.total_docs:>6}")
        print("  " + "".join(row))

    print(LINE)


def print_table_extra_1(result: TestResult) -> None:
    """Bảng bổ sung 1 — Vietnamese Challenge Analysis."""
    vi = result.vi_challenges
    print(f"\n{LINE}")
    print("  BẢNG BỔ SUNG 1 — Phân Tích Thách Thức Tiếng Việt")
    print(LINE)
    total = vi.total_docs or 1
    rows = [
        ("Docs có dấu thanh trong entity name", vi.docs_with_tone_marks, vi.docs_with_tone_marks/total*100),
        ("Docs có viết tắt (UPPERCASE)",         vi.docs_with_abbreviations, vi.docs_with_abbreviations/total*100),
        ("Entity thuần tiếng Việt",              vi.vi_entity_count, None),
        ("Entity mix Việt-Anh",                  vi.mixed_entity_count, None),
        ("Entity tiếng Anh",                     vi.en_entity_count, None),
    ]
    for label, count, pct in rows:
        pct_str = f"({pct:5.1f}%)" if pct is not None else ""
        print(f"  {label:<45} {count:>6} {pct_str}")
    print(f"  {'Avg entity name length (chars)':45} {vi.avg_entity_name_length:>6.1f}")
    print(LINE)


def print_table_extra_2(result: TestResult) -> None:
    """Bảng bổ sung 2 — Concept Category Distribution."""
    vi = result.vi_challenges
    if not vi.category_dist:
        return
    print(f"\n{LINE}")
    print("  BẢNG BỔ SUNG 2 — Phân Phối Category trong ConceptEntity")
    print(LINE)
    total = sum(vi.category_dist.values()) or 1
    sorted_cats = sorted(vi.category_dist.items(), key=lambda x: -x[1])
    for cat, cnt in sorted_cats[:20]:
        bar = "█" * int(cnt / total * 40)
        print(f"  {cat:<25} {cnt:>6}  ({cnt/total*100:5.1f}%)  {bar}")
    print(LINE)


def print_table_extra_3(result: TestResult) -> None:
    """Bảng bổ sung 3 — Latency Statistics."""
    lats = result.latencies_ms
    if not lats:
        return
    import statistics
    print(f"\n{LINE}")
    print("  BẢNG BỔ SUNG 3 — Thống Kê Latency (ms)")
    print(LINE)
    lats_sorted = sorted(lats)
    n = len(lats_sorted)
    rows = [
        ("Min",    min(lats_sorted)),
        ("P25",    lats_sorted[n//4]),
        ("Median", statistics.median(lats_sorted)),
        ("Mean",   statistics.mean(lats_sorted)),
        ("P75",    lats_sorted[3*n//4]),
        ("P90",    lats_sorted[int(n*0.9)]),
        ("P99",    lats_sorted[int(n*0.99)]),
        ("Max",    max(lats_sorted)),
    ]
    for label, val in rows:
        print(f"  {label:<10} {val:>10.1f} ms")
    print(f"\n  Total test time: {result.total_time_s:.1f}s")
    print(f"  Throughput:      {result.total_docs / result.total_time_s:.2f} docs/s")
    print(LINE)


def print_table_extra_4(result: TestResult) -> None:
    """Bảng bổ sung 4 — Phân phối entity per document theo domain."""
    print(f"\n{LINE}")
    print("  BẢNG BỔ SUNG 4 — Số Lượng Entity Trung Bình theo Domain")
    print(LINE)
    header = f"  {'Domain':<22} {'Concepts':>10} {'Evidences':>10} {'Metrics':>10} {'Findings':>10} {'Total':>8}"
    print(header)
    print(LINE)
    for domain, dm in result.domain_metrics.items():
        if dm.total_docs == 0:
            continue
        d = dm.total_docs
        total_avg = (dm.total_concepts + dm.total_evidences + dm.total_metrics + dm.total_findings) / d
        print(
            f"  {DOMAIN_LABELS.get(domain, domain):<22} "
            f"{dm.total_concepts/d:>10.1f} "
            f"{dm.total_evidences/d:>10.1f} "
            f"{dm.total_metrics/d:>10.1f} "
            f"{dm.total_findings/d:>10.1f} "
            f"{total_avg:>8.1f}"
        )
    print(LINE)


def print_summary(result: TestResult) -> None:
    """Summary tổng quan."""
    print(f"\n{'═'*88}")
    print("  TỔNG KẾT")
    print(f"  Total docs processed : {result.total_docs}")
    print(f"  Errors               : {result.errors} ({result.errors/max(result.total_docs,1)*100:.1f}%)")
    print(f"  Total time           : {result.total_time_s:.1f}s")
    if result.latencies_ms:
        print(f"  Avg latency/doc      : {sum(result.latencies_ms)/len(result.latencies_ms):.0f}ms")
    print(f"{'═'*88}")


# ═══════════════════════════════════════════════════════════════════════
# PLOTS
# ═══════════════════════════════════════════════════════════════════════

def _set_style() -> None:
    plt.rcParams.update({
        "font.family":     "DejaVu Sans",
        "font.size":       11,
        "axes.titlesize":  13,
        "axes.labelsize":  11,
        "axes.spines.top":    False,
        "axes.spines.right":  False,
        "figure.dpi":      120,
        "savefig.dpi":     150,
        "savefig.bbox":    "tight",
    })


def plot_33_f1_by_entity_type(result: TestResult, out_dir: Path) -> None:
    """Biểu đồ 3.3 — Bar chart F1 theo entity type."""
    if not HAS_PLOT:
        return
    _set_style()

    labels  = [ENTITY_TYPE_LABELS.get(t, t) for t in ENTITY_TYPES]
    f1s     = [result.entity_metrics.get(t, EntityMetrics(t)).f1 for t in ENTITY_TYPES]
    precs   = [result.entity_metrics.get(t, EntityMetrics(t)).precision for t in ENTITY_TYPES]
    recalls = [result.entity_metrics.get(t, EntityMetrics(t)).recall for t in ENTITY_TYPES]

    x   = np.arange(len(labels))
    w   = 0.26
    fig, ax = plt.subplots(figsize=(11, 5.5))

    bars_p = ax.bar(x - w, precs,   w, label="Precision", color="#4E79A7", zorder=3)
    bars_r = ax.bar(x,     recalls, w, label="Recall",    color="#F28E2B", zorder=3)
    bars_f = ax.bar(x + w, f1s,     w, label="F1",        color="#59A14F", zorder=3)

    for bars in (bars_p, bars_r, bars_f):
        for bar in bars:
            h = bar.get_height()
            if h > 0.02:
                ax.text(bar.get_x() + bar.get_width()/2, h + 0.012,
                        f"{h:.3f}", ha="center", va="bottom", fontsize=8.5)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=15, ha="right")
    ax.set_ylim(0, 1.15)
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.2f"))
    ax.set_ylabel("Score")
    ax.set_title("Biểu đồ 3.3 — F1 / Precision / Recall theo Entity Type", pad=14)
    ax.legend(loc="upper right")
    ax.grid(axis="y", alpha=0.35, zorder=0)

    fp = out_dir / "chart_33_f1_by_entity_type.png"
    fig.savefig(fp)
    plt.close(fig)
    print(f"[PLOT] {fp}")


def plot_f1_by_domain(result: TestResult, out_dir: Path) -> None:
    """Biểu đồ bổ sung — F1 trung bình theo domain."""
    if not HAS_PLOT:
        return
    _set_style()

    domains = [d for d in DOMAIN_KEYWORDS if result.domain_metrics[d].total_docs > 0]
    labels  = [DOMAIN_LABELS.get(d, d) for d in domains]
    avg_f1s = [result.domain_metrics[d].avg_f1 for d in domains]

    colors = ["#4E79A7", "#F28E2B", "#59A14F", "#E15759", "#76B7B2"]
    fig, ax = plt.subplots(figsize=(9, 5))
    bars = ax.bar(labels, avg_f1s, color=colors[:len(labels)], zorder=3, width=0.55)
    for bar, val in zip(bars, avg_f1s):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.015,
                f"{val:.3f}", ha="center", va="bottom", fontsize=10)
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("Avg F1")
    ax.set_title("Biểu đồ Bổ Sung — Avg F1 theo Domain", pad=14)
    ax.grid(axis="y", alpha=0.35, zorder=0)

    fp = out_dir / "chart_f1_by_domain.png"
    fig.savefig(fp)
    plt.close(fig)
    print(f"[PLOT] {fp}")


def plot_entity_counts_heatmap(result: TestResult, out_dir: Path) -> None:
    """Heatmap: domain x entity type (avg count per doc)."""
    if not HAS_PLOT:
        return
    _set_style()

    domains = [d for d in DOMAIN_KEYWORDS if result.domain_metrics[d].total_docs > 0]
    if not domains:
        return
    etypes  = ["concepts", "evidences", "metrics", "findings"]
    labels_d = [DOMAIN_LABELS.get(d, d) for d in domains]
    labels_e = ["Concept", "Evidence", "Metric", "Finding"]

    data = np.zeros((len(domains), len(etypes)))
    for i, domain in enumerate(domains):
        dm = result.domain_metrics[domain]
        d  = max(dm.total_docs, 1)
        data[i] = [dm.total_concepts/d, dm.total_evidences/d, dm.total_metrics/d, dm.total_findings/d]

    fig, ax = plt.subplots(figsize=(8, 4))
    im = ax.imshow(data, cmap="YlOrRd", aspect="auto")
    ax.set_xticks(range(len(labels_e)))
    ax.set_yticks(range(len(labels_d)))
    ax.set_xticklabels(labels_e)
    ax.set_yticklabels(labels_d)
    for i in range(len(domains)):
        for j in range(len(etypes)):
            ax.text(j, i, f"{data[i,j]:.1f}", ha="center", va="center",
                    fontsize=9, color="black" if data[i,j] < data.max()*0.6 else "white")
    plt.colorbar(im, ax=ax, label="Avg entities / doc")
    ax.set_title("Heatmap — Avg Entity Count per Doc (Domain × Type)", pad=12)

    fp = out_dir / "chart_heatmap_domain_entity.png"
    fig.savefig(fp)
    plt.close(fig)
    print(f"[PLOT] {fp}")


def plot_latency_distribution(result: TestResult, out_dir: Path) -> None:
    """Histogram latency."""
    if not HAS_PLOT or not result.latencies_ms:
        return
    _set_style()

    lats = np.array(result.latencies_ms)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))

    # Histogram
    axes[0].hist(lats, bins=30, color="#4E79A7", edgecolor="white", zorder=3)
    axes[0].axvline(np.mean(lats), color="#E15759", linestyle="--", label=f"Mean={np.mean(lats):.0f}ms")
    axes[0].axvline(np.median(lats), color="#59A14F", linestyle="--", label=f"Median={np.median(lats):.0f}ms")
    axes[0].set_xlabel("Latency (ms)")
    axes[0].set_ylabel("Count")
    axes[0].set_title("Phân Phối Latency")
    axes[0].legend()
    axes[0].grid(axis="y", alpha=0.3, zorder=0)

    # Box per domain
    domain_lats = defaultdict(list)
    for r in result.raw_results:
        domain_lats[DOMAIN_LABELS.get(r["domain"], r["domain"])].append(r["latency"])
    if domain_lats:
        keys = list(domain_lats.keys())
        vals = [domain_lats[k] for k in keys]
        axes[1].boxplot(vals, labels=keys, patch_artist=True,
                        boxprops=dict(facecolor="#4E79A7", alpha=0.6))
        axes[1].set_xlabel("Domain")
        axes[1].set_ylabel("Latency (ms)")
        axes[1].set_title("Latency theo Domain")
        axes[1].tick_params(axis="x", rotation=20)
        axes[1].grid(axis="y", alpha=0.3)

    plt.tight_layout()
    fp = out_dir / "chart_latency_distribution.png"
    fig.savefig(fp)
    plt.close(fig)
    print(f"[PLOT] {fp}")


def plot_category_distribution(result: TestResult, out_dir: Path) -> None:
    """Pie chart category distribution."""
    if not HAS_PLOT:
        return
    vi = result.vi_challenges
    if not vi.category_dist:
        return
    _set_style()

    sorted_cats = sorted(vi.category_dist.items(), key=lambda x: -x[1])
    top_n = 10
    top   = sorted_cats[:top_n]
    others = sum(v for _, v in sorted_cats[top_n:])

    labels = [c for c, _ in top]
    sizes  = [v for _, v in top]
    if others > 0:
        labels.append("others")
        sizes.append(others)

    fig, ax = plt.subplots(figsize=(9, 6))
    wedges, texts, autotexts = ax.pie(
        sizes, labels=labels, autopct="%1.1f%%",
        startangle=90, pctdistance=0.82,
    )
    for t in autotexts:
        t.set_fontsize(8)
    ax.set_title("Phân Phối Concept Category (Top 10)", pad=14)

    fp = out_dir / "chart_concept_category_pie.png"
    fig.savefig(fp)
    plt.close(fig)
    print(f"[PLOT] {fp}")


def plot_f1_grouped_by_domain(result: TestResult, out_dir: Path) -> None:
    """Grouped bar chart — F1 từng entity type theo domain."""
    if not HAS_PLOT:
        return
    _set_style()

    domains = [d for d in DOMAIN_KEYWORDS if result.domain_metrics[d].total_docs > 0]
    if not domains:
        return
    etypes  = ["concept", "evidence", "metric", "finding"]
    elabels = [ENTITY_TYPE_LABELS.get(t, t) for t in etypes]
    dlabels = [DOMAIN_LABELS.get(d, d) for d in domains]

    x   = np.arange(len(domains))
    w   = 0.18
    colors = ["#4E79A7", "#F28E2B", "#59A14F", "#E15759"]

    fig, ax = plt.subplots(figsize=(12, 5.5))
    offsets = np.linspace(-(len(etypes)-1)*w/2, (len(etypes)-1)*w/2, len(etypes))
    for i, (etype, label, color, offset) in enumerate(zip(etypes, elabels, colors, offsets)):
        f1s = [result.domain_metrics[d].f1_by_type.get(etype, 0.0) for d in domains]
        bars = ax.bar(x + offset, f1s, w, label=label, color=color, zorder=3)

    ax.set_xticks(x)
    ax.set_xticklabels(dlabels, rotation=12, ha="right")
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("F1 Score")
    ax.set_title("F1 Score theo Domain và Entity Type", pad=14)
    ax.legend(loc="upper right", ncol=2)
    ax.grid(axis="y", alpha=0.35, zorder=0)

    fp = out_dir / "chart_f1_grouped_domain_entity.png"
    fig.savefig(fp)
    plt.close(fig)
    print(f"[PLOT] {fp}")


def save_json_report(result: TestResult, out_dir: Path) -> None:
    """Lưu full report JSON."""
    report = {
        "summary": {
            "total_docs":   result.total_docs,
            "errors":       result.errors,
            "total_time_s": round(result.total_time_s, 2),
        },
        "entity_metrics": {
            t: {
                "precision": round(m.precision, 4),
                "recall":    round(m.recall, 4),
                "f1":        round(m.f1, 4),
                "extracted": m.extracted_count,
                "avg_per_doc": round(m.avg_per_doc, 2),
            }
            for t, m in result.entity_metrics.items()
        },
        "domain_metrics": {
            d: {
                "docs":    dm.total_docs,
                "avg_f1":  round(dm.avg_f1, 4),
                "f1_by_type": {k: round(v, 4) for k, v in dm.f1_by_type.items()},
                "avg_concepts":  round(dm.total_concepts / max(dm.total_docs, 1), 2),
                "avg_evidences": round(dm.total_evidences / max(dm.total_docs, 1), 2),
                "avg_metrics":   round(dm.total_metrics / max(dm.total_docs, 1), 2),
                "avg_findings":  round(dm.total_findings / max(dm.total_docs, 1), 2),
            }
            for d, dm in result.domain_metrics.items()
            if dm.total_docs > 0
        },
    }
    fp = out_dir / "test_report.json"
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[SAVE] {fp}")


# ═══════════════════════════════════════════════════════════════════════
# UNIT TESTS (no LLM)
# ═══════════════════════════════════════════════════════════════════════

def run_unit_tests() -> bool:
    """Các unit test cơ bản không cần LLM."""
    print("\n[UNIT] Chạy unit tests...")
    passed = failed = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if cond:
            print(f"  ✓ {name}")
            passed += 1
        else:
            print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))
            failed += 1

    # 1. parse_authors — Vietnamese names
    authors = parse_authors(["Nguyễn Văn An (Đại học Bách Khoa)", "Trần Thị Bình"])
    check("parse_authors: Nguyễn Văn An",    authors[0].name == "Nguyễn Văn An")
    check("parse_authors: affiliation",       authors[0].affiliation is not None)
    check("parse_authors: Trần Thị Bình",     "Bình" in authors[1].name)

    # 2. ConceptEntity validation
    c_valid = ConceptEntity(name="transformer", category="architecture")
    c_bad   = ConceptEntity(name="x", category="invalid_cat")
    ok_v, _ = validate_concept(c_valid)
    ok_b, _ = validate_concept(c_bad)
    check("ConceptEntity: valid",   ok_v)
    check("ConceptEntity: invalid", not ok_b)

    # 3. EvidenceEntity validation
    e_valid = EvidenceEntity(name="vnese dataset", evidence_type="dataset")
    e_bad   = EvidenceEntity(name="ab", evidence_type="dataset")  # too short
    ok_ev, _ = validate_evidence(e_valid)
    ok_eb, _ = validate_evidence(e_bad)
    check("EvidenceEntity: valid",     ok_ev)
    check("EvidenceEntity: too short", not ok_eb)

    # 4. Domain detection
    data_y   = {"title": "Nghiên cứu điều trị bệnh đái tháo đường", "abstract": ""}
    data_cn  = {"title": "Mạng nơ-ron sâu cho phân loại văn bản", "abstract": "deep learning"}
    data_nn  = {"title": "Nghiên cứu giống lúa chịu hạn", "abstract": "nông nghiệp"}
    check("domain: y_duoc",          _detect_domain(data_y)  == "y_duoc")
    check("domain: ky_thuat",        _detect_domain(data_cn) == "ky_thuat_cong_nghe")
    check("domain: nong_nghiep",     _detect_domain(data_nn) == "nong_nghiep")

    # 5. MockLLM produces valid JSON
    mock = MockLLMBackend()
    raw = mock.extract("system", "section: method\ncontent: phương pháp học máy được áp dụng")
    try:
        parsed = json.loads(raw)
        check("MockLLM: valid JSON",   True)
        check("MockLLM: has concepts", "concepts" in parsed)
    except Exception as e:
        check("MockLLM: valid JSON", False, str(e))

    # 6. F1 compute
    check("compute_f1: both 0",   compute_f1(0, 0) == 0.0)
    check("compute_f1: perfect",  abs(compute_f1(1, 1) - 1.0) < 1e-9)
    check("compute_f1: balanced", abs(compute_f1(0.8, 0.6) - 2*0.8*0.6/(0.8+0.6)) < 1e-9)

    # 7. Vietnamese challenge patterns
    vi_name = "mạng nơ-ron"
    en_name = "transformer"
    check("VI tone mark detected", bool(VI_CHALLENGE_PATTERNS["tone_marks"].search(vi_name)))
    check("EN no tone mark",       not VI_CHALLENGE_PATTERNS["tone_marks"].search(en_name))

    print(f"\n[UNIT] Kết quả: {passed} passed, {failed} failed")
    return failed == 0


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def build_extractor(use_mock: bool = False, use_ollama: bool = False) -> EntityExtractor:
    if use_mock:
        print("[LLM] Dùng MockLLMBackend (--no-llm)")
        llm = MockLLMBackend()
    elif use_ollama:
        model    = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        print(f"[LLM] Dùng OllamaBackend — model={model} url={base_url}")
        llm = OllamaBackend(model=model, base_url=base_url)
    else:
        model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        print(f"[LLM] Dùng AnthropicBackend — model={model}")
        llm = AnthropicBackend(model=model)

    max_workers       = int(os.getenv("MAX_WORKERS", "4"))
    max_section_chars = int(os.getenv("MAX_SECTION_CHARS", "4000"))
    return EntityExtractor(llm, max_section_chars=max_section_chars, max_workers=max_workers)


def main() -> None:
    parser = argparse.ArgumentParser(description="Test EntityExtractor — 200 Vietnamese papers")
    parser.add_argument("--data-dir",  default=str(DATA_DIR), help="Thư mục chứa JSON")
    parser.add_argument("--output",    default="test_output",  help="Thư mục lưu biểu đồ")
    parser.add_argument("--domain",    choices=list(DOMAIN_KEYWORDS.keys()), help="Filter 1 domain")
    parser.add_argument("--limit",     type=int, default=None, help="Giới hạn số file")
    parser.add_argument("--no-llm",    action="store_true",  help="Dùng mock LLM")
    parser.add_argument("--ollama",    action="store_true",  help="Dùng Ollama backend")
    parser.add_argument("--unit-only", action="store_true",  help="Chỉ chạy unit tests")
    parser.add_argument("--no-plots",  action="store_true",  help="Bỏ qua vẽ biểu đồ")
    parser.add_argument("--verbose",   action="store_true",  help="Verbose logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    # ── Unit tests ────────────────────────────────────────────────────
    unit_ok = run_unit_tests()
    if args.unit_only:
        sys.exit(0 if unit_ok else 1)

    # ── Output dir ────────────────────────────────────────────────────
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Load documents ────────────────────────────────────────────────
    data_dir = Path(args.data_dir)
    try:
        documents = load_documents(data_dir, domain_filter=args.domain, limit=args.limit)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    if not documents:
        print("[ERROR] Không load được document nào. Kiểm tra --data-dir và format JSON.")
        sys.exit(1)

    # ── Build extractor ───────────────────────────────────────────────
    extractor = build_extractor(use_mock=args.no_llm, use_ollama=args.ollama)

    # ── Run tests ─────────────────────────────────────────────────────
    result = run_tests(extractor, documents)

    # ── Print tables ──────────────────────────────────────────────────
    print_table_34(result)
    print_table_35(result)
    print_table_extra_1(result)
    print_table_extra_2(result)
    print_table_extra_3(result)
    print_table_extra_4(result)
    print_summary(result)

    # ── Plots ─────────────────────────────────────────────────────────
    if not args.no_plots and HAS_PLOT:
        print("\n[PLOT] Đang vẽ biểu đồ...")
        plot_33_f1_by_entity_type(result, out_dir)
        plot_f1_by_domain(result, out_dir)
        plot_entity_counts_heatmap(result, out_dir)
        plot_latency_distribution(result, out_dir)
        plot_category_distribution(result, out_dir)
        plot_f1_grouped_by_domain(result, out_dir)
    elif not HAS_PLOT:
        print("[WARN] matplotlib không có — bỏ qua biểu đồ")

    # ── Save JSON ─────────────────────────────────────────────────────
    save_json_report(result, out_dir)

    print(f"\n[DONE] Output đã lưu vào: {out_dir.resolve()}")
    sys.exit(0 if result.errors == 0 else 1)


if __name__ == "__main__":
    main()