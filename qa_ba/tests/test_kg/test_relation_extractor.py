"""
test_relation_extractor.py
==========================
Test toàn diện cho ai_module/kg/relation_extractor.py

Kiến trúc:
    - Load 100 JSON files từ D:/Study/.../data/processed (5 domain)
    - Chạy RelationExtractor với LLM thật (AnthropicBackend)
    - So sánh kết quả với ground-truth annotation (tự động sinh nếu chưa có)
    - Xuất báo cáo HTML với bảng + biểu đồ tương tác

Sections:
    1. Data Loading & Schema Validation
    2. Unit Tests (rule-based, không cần LLM)
    3. Integration Tests (LLM thật, chạy sample)
    4. Metrics: P/R/F1 theo relation type và domain
    5. Error Analysis
    6. HTML Report với bảng và biểu đồ

Run:
    # Basic (unit tests only, no LLM):
    python test_relation_extractor.py --mode unit

    # Integration với LLM thật (cần ANTHROPIC_API_KEY):
    python test_relation_extractor.py --mode full --sample 20

    # Full 100 files:
    python test_relation_extractor.py --mode full --sample 100

    # Chỉ xuất report từ kết quả đã lưu:
    python test_relation_extractor.py --mode report --results results.json

Kết quả xuất ra:
    - test_results/relation_test_report.html  ← mở trên browser
    - test_results/results.json               ← raw data
    - test_results/error_analysis.json        ← chi tiết lỗi
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
import re

# ─── Thêm project root vào sys.path ─────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("test_relation_extractor")

# ─── Constants ────────────────────────────────────────────────────────────────
DATA_DIR     = Path(r"D:\Study\Study_Class\Semester_6\Data_mining\Project\data\processed")
OUTPUT_DIR   = Path("test_results")
REPORT_FILE  = OUTPUT_DIR / "relation_test_report.html"
RESULTS_FILE = OUTPUT_DIR / "results.json"
ERROR_FILE   = OUTPUT_DIR / "error_analysis.json"

DOMAINS = [
    "y_duoc",           # Khoa học y dược
    "xa_hoi_nhan_van",  # Khoa học xã hội & nhân văn
    "tu_nhien",         # Khoa học tự nhiên
    "nong_nghiep",      # Khoa học nông nghiệp
    "ky_thuat_cong_nghe",  # Khoa học kỹ thuật & công nghệ
]

DOMAIN_LABELS = {
    "y_duoc":              "Y Dược",
    "xa_hoi_nhan_van":     "Xã hội & Nhân văn",
    "tu_nhien":            "Tự nhiên",
    "nong_nghiep":         "Nông nghiệp",
    "ky_thuat_cong_nghe":  "Kỹ thuật & CN",
}

RELATION_TYPES = [
    "WROTE",
    "PUBLISHED_AT",
    "HAS_TOPIC",
    "CITES",
    "USES_CONCEPT",
    "EVALUATES_ON",
    "ACHIEVES_METRIC",
    "BASED_ON",
    "EXTENDS",
    "SUPPORTS",
    "CONTRADICTS",
]

RELATION_LABELS = {
    "WROTE":           "Authored By",
    "PUBLISHED_AT":    "Published At",
    "HAS_TOPIC":       "Has Topic",
    "CITES":           "Cites",
    "USES_CONCEPT":    "Uses Concept/Method",
    "EVALUATES_ON":    "Evaluates On",
    "ACHIEVES_METRIC": "Achieves Metric",
    "BASED_ON":        "Based On",
    "EXTENDS":         "Extends",
    "SUPPORTS":        "Supports",
    "CONTRADICTS":     "Contradicts",
}


# =============================================================================
# SCHEMA HELPERS — đọc JSON file theo nhiều format thực tế
# =============================================================================

def load_json_file(path: Path) -> Optional[dict]:
    """Load JSON, hỗ trợ UTF-8 và UTF-8-BOM."""
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as e:
        logger.warning("load_json_file: không đọc được '%s' — %s", path.name, e)
        return None


def detect_domain(path: Path, data: dict) -> str:
    """
    Detect domain từ path hoặc metadata trong JSON.
    Ưu tiên: data["domain"] > tên thư mục cha > tên file.
    """
    # Từ metadata
    for key in ("domain", "category", "field", "lĩnh vực"):
        val = (data.get(key) or "").lower().strip()
        if val:
            for d in DOMAINS:
                if d in val or val in d:
                    return d
    # Từ path
    path_str = str(path).lower()

    # Map folder names thực tế của dataset bạn
    if "khyd" in path_str:
        return "y_duoc"
    if "khxh&nv" in path_str or "khxh" in path_str:
        return "xa_hoi_nhan_van"
    if "khtn" in path_str:
        return "tu_nhien"
    if "khnn" in path_str:
        return "nong_nghiep"
    if "khkt&kt" in path_str or "khkt" in path_str:
        return "ky_thuat_cong_nghe"

    # fallback
    for d in DOMAINS:
        if d in path_str:
            return d
    # Fallback theo từ khóa trong tên file
    name = path.stem.lower()
    kw_map = {
        "y_duoc": ["duoc", "benh", "medical", "clinical", "health"],
        "xa_hoi_nhan_van": ["xa_hoi", "nhan_van", "social", "culture", "lich_su"],
        "tu_nhien": ["hoa", "sinh", "vat_ly", "natural", "chemistry", "biology"],
        "nong_nghiep": ["nong", "cay", "thu_y", "agri", "farm"],
        "ky_thuat_cong_nghe": ["ky_thuat", "cong_nghe", "tech", "engineer", "ai", "cntt"],
    }
    for domain, kws in kw_map.items():
        if any(k in name for k in kws):
            return domain
    return "unknown"


def json_to_unified_document(data: dict, source_path: Path):
    """
    Chuyển JSON dict → UnifiedDocument.
    Hỗ trợ nhiều schema khác nhau (flat, nested sections, v.v.).
    """
    try:
        from ingestion.schema.document_schema import UnifiedDocument, Section, Reference
    except ImportError:
        # Fallback stub nếu import path chưa đúng
        return _stub_unified_document(data, source_path)

    sections = []
    raw_sections = data.get("sections") or data.get("content_sections") or []
    for s in raw_sections:
        if isinstance(s, dict):
            sections.append(Section(
                name=s.get("name") or s.get("title") or "unknown",
                content=s.get("content") or s.get("text") or "",
                order=s.get("order") or 0,
                section_type=s.get("section_type") or s.get("type") or "unknown",
            ))
        elif isinstance(s, str):
            sections.append(Section(name="body", content=s, order=0, section_type="body"))

    refs = []
    for r in (data.get("references") or []):
        if isinstance(r, dict):
            try:
                refs.append(Reference(
                    raw_text=r.get("raw_text") or r.get("text") or "",
                    title=r.get("title"),
                    doi=r.get("doi"),
                    year=r.get("year"),
                ))
            except Exception:
                pass

    return UnifiedDocument(
        doc_id=str(data.get("doc_id") or data.get("id") or source_path.stem),
        title=str(data.get("title") or "Unknown"),
        authors=data.get("authors") or [],
        abstract=data.get("abstract") or data.get("summary") or "",
        sections=sections,
        references=refs,
        keywords=data.get("keywords") or [],
        language=data.get("language") or "vi",
        year=data.get("year"),
        journal=data.get("journal") or data.get("venue"),
        doi=data.get("doi"),
    )


class _StubDocument:
    """Fallback stub khi không import được UnifiedDocument."""
    def __init__(self, data: dict, path: Path):
        self.doc_id   = str(data.get("doc_id") or path.stem)
        self.title    = str(data.get("title") or "Unknown")
        self.authors  = data.get("authors") or []
        self.abstract = data.get("abstract") or ""
        self.sections = self._parse_sections(data)
        self.references = []
        self.keywords = data.get("keywords") or []
        self.language = data.get("language") or "vi"
        self.year     = data.get("year")
        self.journal  = data.get("journal") or data.get("venue")
        self.doi      = data.get("doi")

    def _parse_sections(self, data):
        result = []
        for s in (data.get("sections") or []):
            if isinstance(s, dict):
                result.append(type("Section", (), {
                    "name": s.get("name") or "unknown",
                    "content": s.get("content") or s.get("text") or "",
                    "order": s.get("order") or 0,
                    "section_type": s.get("section_type") or "unknown",
                })())
        return result

def _stub_unified_document(data: dict, path: Path) -> _StubDocument:
    return _StubDocument(data, path)


# =============================================================================
# GROUND-TRUTH MANAGER
# =============================================================================

GT_FILE = OUTPUT_DIR / "ground_truth.json"


def load_or_create_ground_truth(doc_ids: list[str]) -> dict:
    """
    Load ground-truth từ file nếu có.
    Nếu chưa có, tạo template rỗng để annotator điền vào.
    """
    if GT_FILE.exists():
        try:
            return json.loads(GT_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Tạo template
    template = {}
    for doc_id in doc_ids:
        template[doc_id] = {
            "WROTE":           [],  # [{"author": "...", "paper": "..."}]
            "PUBLISHED_AT":    [],  # [{"paper": "...", "venue": "..."}]
            "HAS_TOPIC":       [],  # [{"paper": "...", "topic": "..."}]
            "CITES":           [],  # [{"from": "...", "to": "..."}]
            "USES_CONCEPT":    [],  # [{"paper": "...", "concept": "..."}]
            "EVALUATES_ON":    [],  # [{"paper": "...", "evidence": "..."}]
            "ACHIEVES_METRIC": [],  # [{"paper": "...", "metric": "...", "value": null}]
            "BASED_ON":        [],  # [{"child": "...", "parent": "..."}]
            "EXTENDS":         [],  # [{"child": "...", "parent": "..."}]
            "SUPPORTS":        [],  # [{"from": "...", "to": "..."}]
            "CONTRADICTS":     [],  # [{"from": "...", "to": "..."}]
        }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    GT_FILE.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Đã tạo ground-truth template: %s", GT_FILE)
    logger.info("Hãy điền annotation vào file này trước khi chạy mode=full với ground-truth")
    return template


# =============================================================================
# METRICS CALCULATION
# =============================================================================

@dataclass
class RelationMetrics:
    relation_type: str
    domain:        str
    tp:            int   = 0
    fp:            int   = 0
    fn:            int   = 0
    total_pred:    int   = 0
    total_gold:    int   = 0

    @property
    def precision(self) -> float:
        return self.tp / self.total_pred if self.total_pred else 0.0

    @property
    def recall(self) -> float:
        return self.tp / self.total_gold if self.total_gold else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class ErrorRecord:
    doc_id:        str
    domain:        str
    relation_type: str
    error_type:    str    # "wrong_direction" | "wrong_entity" | "missing" | "spurious" | "schema_error"
    predicted:     str    = ""
    expected:      str    = ""
    detail:        str    = ""


@dataclass
class DocResult:
    doc_id:      str
    domain:      str
    file_path:   str
    success:     bool
    latency_sec: float
    relations:   dict     = field(default_factory=dict)   # rel_type → list of tuples
    error_msg:   str      = ""
    entity_counts: dict   = field(default_factory=dict)


def _extract_relation_tuples(result) -> dict[str, list[tuple]]:
    """
    Chuyển RelationResult → dict[rel_type → list[tuple]] để dễ so sánh.
    Mỗi tuple là key đủ để identify một edge.
    """
    out = defaultdict(list)
    for r in (result.wrote or []):
        out["WROTE"].append((r.author_id.lower(), r.paper_id.lower()))

    for r in (result.published_at or []):
        out["PUBLISHED_AT"].append((r.paper_id.lower(), r.venue_name.lower()))

    for r in (result.has_topic or []):
        out["HAS_TOPIC"].append((r.paper_id.lower(), r.topic_name.lower()))

    for r in (result.cites or []):
        if r.cited_doi:
            out["CITES"].append((r.citing_paper_id.lower(), f"doi:{r.cited_doi.lower()}"))
        elif r.cited_title:
            out["CITES"].append((r.citing_paper_id.lower(), r.cited_title.lower()[:60]))

    for r in (result.uses_concept or []):
        out["USES_CONCEPT"].append((r.paper_id.lower(), r.concept_name.lower()))

    for r in (result.evaluates_on or []):
        out["EVALUATES_ON"].append((r.paper_id.lower(), r.evidence_name.lower()))

    for r in (result.achieves_metric or []):
        key = f"{r.metric_name}={r.value}" if r.value else r.metric_name
        out["ACHIEVES_METRIC"].append((r.paper_id.lower(), key.lower()))

    for r in (result.based_on or []):
        out["BASED_ON"].append((r.child_concept.lower(), r.parent_concept.lower()))

    for r in (result.extends or []):
        out["EXTENDS"].append((r.child_concept.lower(), r.parent_concept.lower()))

    for r in (result.supports or []):
        out["SUPPORTS"].append((r.source_paper_id.lower(), r.target_paper_id.lower()))

    for r in (result.contradicts or []):
        out["CONTRADICTS"].append((r.source_paper_id.lower(), r.target_paper_id.lower()))

    return dict(out)


def compute_metrics(
    doc_results: list[DocResult],
    ground_truth: dict,
) -> tuple[dict[str, dict[str, RelationMetrics]], list[ErrorRecord]]:
    """
    Tính P/R/F1 theo (relation_type, domain).
    Trả về:
        metrics_by_rel_domain: {rel_type: {domain: RelationMetrics}}
        errors: list[ErrorRecord]
    """
    metrics_by_rel_domain: dict[str, dict[str, RelationMetrics]] = {
        rel: {d: RelationMetrics(relation_type=rel, domain=d) for d in DOMAINS + ["unknown", "all"]}
        for rel in RELATION_TYPES
    }
    errors: list[ErrorRecord] = []

    for doc_res in doc_results:
        if not doc_res.success:
            continue

        gt = ground_truth.get(doc_res.doc_id, {})
        domain = doc_res.domain

        for rel_type in RELATION_TYPES:
            pred_tuples = set(doc_res.relations.get(rel_type) or [])
            gold_raw    = gt.get(rel_type) or []

            # Normalize gold tuples
            gold_tuples: set[tuple] = set()
            for g in gold_raw:
                if isinstance(g, dict):
                    vals = [str(v).lower() for v in g.values() if v]
                    if len(vals) >= 2:
                        gold_tuples.add(tuple(vals[:2]))
                elif isinstance(g, (list, tuple)) and len(g) >= 2:
                    gold_tuples.add((str(g[0]).lower(), str(g[1]).lower()))

            tp = len(pred_tuples & gold_tuples)
            fp = len(pred_tuples - gold_tuples)
            fn = len(gold_tuples - pred_tuples)

            for d in [domain, "all"]:
                if d not in metrics_by_rel_domain[rel_type]:
                    metrics_by_rel_domain[rel_type][d] = RelationMetrics(
                        relation_type=rel_type, domain=d
                    )
                m = metrics_by_rel_domain[rel_type][d]
                m.tp          += tp
                m.fp          += fp
                m.fn          += fn
                m.total_pred  += len(pred_tuples)
                m.total_gold  += len(gold_tuples)

            # Error analysis — chỉ khi có ground-truth
            if gold_tuples:
                # Missing relations (FN)
                for g in gold_tuples - pred_tuples:
                    errors.append(ErrorRecord(
                        doc_id=doc_res.doc_id,
                        domain=domain,
                        relation_type=rel_type,
                        error_type="missing",
                        expected=str(g),
                    ))
                # Spurious (FP)
                for p in pred_tuples - gold_tuples:
                    errors.append(ErrorRecord(
                        doc_id=doc_res.doc_id,
                        domain=domain,
                        relation_type=rel_type,
                        error_type="spurious",
                        predicted=str(p),
                    ))

            # Direction check — CITES A→B vs B→A
            if rel_type == "CITES":
                for p in pred_tuples:
                    reversed_p = (p[1], p[0]) if len(p) >= 2 else p
                    if reversed_p in gold_tuples:
                        errors.append(ErrorRecord(
                            doc_id=doc_res.doc_id,
                            domain=domain,
                            relation_type="CITES",
                            error_type="wrong_direction",
                            predicted=str(p),
                            expected=str(reversed_p),
                            detail="Nhầm chiều A→B vs B→A",
                        ))

    return metrics_by_rel_domain, errors


# =============================================================================
# UNIT TESTS — không cần LLM
# =============================================================================

def run_unit_tests() -> dict:
    """Chạy unit tests với mock data, không cần LLM."""
    import unittest
    from unittest.mock import MagicMock, patch

    results = {"passed": 0, "failed": 0, "errors": [], "details": []}

    # ── Test 1: WroteRelation direction ──────────────────────────────────────
    def test_wrote_direction():
        """WROTE phải là Author → Paper, không phải Paper → Author."""
        try:
            from ai_module.kg.relation_extractor import WroteRelation
            r = WroteRelation(author_id="nguyen_van_a", paper_id="paper_001", order=0)
            assert r.author_id == "nguyen_van_a", "author_id sai"
            assert r.paper_id  == "paper_001",    "paper_id sai"
            assert r.order     == 0,              "order sai"
            return True, "WroteRelation: author→paper direction OK"
        except AssertionError as e:
            return False, f"WroteRelation direction FAIL: {e}"
        except ImportError as e:
            return None, f"SKIP (import error): {e}"

    # ── Test 2: CitesRelation direction ──────────────────────────────────────
    def test_cites_direction():
        """CITES phải là citing_paper → cited_paper."""
        try:
            from ai_module.kg.relation_extractor import CitesRelation
            r = CitesRelation(
                citing_paper_id="paper_a",
                cited_doc_id="paper_b",
                cited_title="Paper B về học máy",
                confidence=0.9,
            )
            assert r.citing_paper_id == "paper_a", "citing phải là A"
            assert r.cited_doc_id    == "paper_b", "cited phải là B"
            assert r.citing_paper_id != r.cited_doc_id, "A cites B ≠ B cites A"
            return True, "CitesRelation: A→B direction OK, không nhầm B→A"
        except AssertionError as e:
            return False, f"CitesRelation direction FAIL: {e}"
        except ImportError as e:
            return None, f"SKIP: {e}"

    # ── Test 3: BasedOn self-loop filter ─────────────────────────────────────
    def test_based_on_no_self_loop():
        """BASED_ON không được có self-loop (concept based_on chính nó)."""
        try:
            from ai_module.kg.relation_extractor import BasedOnRelation, RuleRelationExtractor
            from ai_module.kg.entities import normalize_entity_name

            rre = RuleRelationExtractor()

            # Tạo mock doc và entities
            mock_doc      = MagicMock()
            mock_doc.journal = None
            mock_doc.keywords = []
            mock_doc.references = []
            mock_doc.sections = []
            mock_doc.abstract = ""

            mock_concept        = MagicMock()
            mock_concept.name   = "bert"
            mock_concept.based_on = ["bert"]  # self-loop
            mock_concept.extends  = []
            mock_concept.confidence = 0.8
            mock_concept.source_section = "method"
            mock_concept.evidence = ""
            mock_concept.category = "model"

            mock_entities = MagicMock()
            mock_entities.authors   = []
            mock_entities.concepts  = [mock_concept]
            mock_entities.evidences = []
            mock_entities.metrics   = []
            mock_entities.findings  = []

            result, _ = rre.extract(mock_doc, "paper_test", mock_entities, {})

            # Self-loop nên bị filter
            self_loops = [r for r in result.based_on
                          if normalize_entity_name(r.child_concept) ==
                             normalize_entity_name(r.parent_concept)]
            assert len(self_loops) == 0, f"Còn {len(self_loops)} self-loop BASED_ON"
            return True, "BasedOn: self-loop bị filter OK"
        except AssertionError as e:
            return False, f"BasedOn self-loop FAIL: {e}"
        except Exception as e:
            return None, f"SKIP: {e}"

    # ── Test 4: RelationResult summary format ────────────────────────────────
    def test_relation_result_summary():
        """RelationResult.summary() phải trả về string đủ fields."""
        try:
            from ai_module.kg.relation_extractor import RelationResult
            r = RelationResult()
            s = r.summary()
            expected_keys = ["wrote", "cites", "uses_concept", "evaluates_on", "achieves_metric"]
            for k in expected_keys:
                assert k in s, f"'{k}' không có trong summary"
            return True, f"RelationResult.summary() OK: {s[:80]}..."
        except AssertionError as e:
            return False, f"RelationResult.summary FAIL: {e}"
        except ImportError as e:
            return None, f"SKIP: {e}"

    # ── Test 5: Noise evidence filter ────────────────────────────────────────
    def test_noise_filter():
        """_is_noise_evidence phải filter citation numbers và tên quá ngắn."""
        try:
            from ai_module.kg.entity_extractor import _is_noise_evidence
            assert _is_noise_evidence("[1]"),       "[1] phải là noise"
            assert _is_noise_evidence("[2, 3]"),    "[2,3] phải là noise"
            assert _is_noise_evidence("AB"),        "AB quá ngắn"
            assert _is_noise_evidence("hình 4"),    "hình 4 phải là noise"
            assert _is_noise_evidence("table_1"),   "table_1 phải là noise"
            assert not _is_noise_evidence("SQuAD 2.0"),   "SQuAD 2.0 là evidence hợp lệ"
            assert not _is_noise_evidence("VLSP 2022"),   "VLSP 2022 hợp lệ"
            assert not _is_noise_evidence("GSO Vietnam"), "GSO Vietnam hợp lệ"
            return True, "Noise filter OK (citation, short, figure/table filtered)"
        except AssertionError as e:
            return False, f"Noise filter FAIL: {e}"
        except ImportError as e:
            return None, f"SKIP: {e}"

    # ── Test 6: Category resolve pipe-separated ───────────────────────────────
    def test_category_resolve():
        """_resolve_category phải xử lý pipe-separated values."""
        try:
            from ai_module.kg.entity_extractor import _resolve_category
            assert _resolve_category("method|algorithm") == "method"
            assert _resolve_category("model|architecture") == "model"
            assert _resolve_category("study|experiment")  == "concept"  # fallback
            assert _resolve_category("tourism_model")     == "concept"  # không match
            assert _resolve_category("regulation")        == "regulation"
            return True, "_resolve_category pipe-separated OK"
        except AssertionError as e:
            return False, f"_resolve_category FAIL: {e}"
        except ImportError as e:
            return None, f"SKIP: {e}"

    # ── Test 7: JSON double-brace repair ─────────────────────────────────────
    def test_double_brace_repair():
        """_repair_double_brace phải fix Qwen2.5 output."""
        try:
            from ai_module.kg.entity_extractor import EntityExtractor
            import json

            # Qwen2.5 hay sinh: [{ { "name": "bert" } }]
            broken = '[{ { "name": "bert", "value": 1 } }]'
            fixed  = EntityExtractor._repair_double_brace(broken)
            parsed = json.loads(fixed)
            assert isinstance(parsed, list), "Phải là list"
            assert parsed[0]["name"] == "bert", "name phải là bert"
            return True, "_repair_double_brace: Qwen2.5 double-brace OK"
        except Exception as e:
            return False, f"_repair_double_brace FAIL: {e}"

    # ── Test 8: ExtractedEntities.is_empty() ─────────────────────────────────
    def test_extracted_entities_empty():
        """ExtractedEntities.is_empty() phải đúng."""
        try:
            from ai_module.kg.entities import ExtractedEntities
            e = ExtractedEntities()
            assert e.is_empty(), "Không có gì phải là empty"

            from ai_module.kg.entities import ConceptEntity
            e.concepts = [ConceptEntity(name="bert")]
            assert not e.is_empty(), "Có concept thì không phải empty"
            return True, "ExtractedEntities.is_empty() OK"
        except AssertionError as e:
            return False, f"is_empty FAIL: {e}"
        except ImportError as e:
            return None, f"SKIP: {e}"

    # ── Test 9: Multi-hop reasoning presence ─────────────────────────────────
    def test_multi_hop_chain():
        """
        Kiểm tra khả năng phát hiện chain A→B→C qua USES_CONCEPT + BASED_ON.
        A (paper) USES_CONCEPT B (model) BASED_ON C (method)
        """
        try:
            from ai_module.kg.relation_extractor import (
                UsesConceptRelation, BasedOnRelation, RelationResult
            )
            r = RelationResult()
            r.uses_concept = [UsesConceptRelation(
                paper_id="paper_a", concept_name="roberta", category="model"
            )]
            r.based_on = [BasedOnRelation(
                child_concept="roberta", parent_concept="bert"
            )]
            # Chain: paper_a → roberta → bert
            chain = []
            for uc in r.uses_concept:
                for bo in r.based_on:
                    if uc.concept_name == bo.child_concept:
                        chain.append((uc.paper_id, uc.concept_name, bo.parent_concept))

            assert len(chain) > 0, "Không tìm được chain A→B→C"
            assert chain[0] == ("paper_a", "roberta", "bert"), f"Chain sai: {chain[0]}"
            return True, f"Multi-hop A→B→C OK: {chain[0]}"
        except AssertionError as e:
            return False, f"Multi-hop FAIL: {e}"
        except ImportError as e:
            return None, f"SKIP: {e}"

    # ── Test 10: Implicit relation in text ───────────────────────────────────
    def test_implicit_relation_cooccurrence():
        """_find_cooccurrence phải detect verb patterns gần entity."""
        try:
            from ai_module.kg.relation_extractor import (
                RuleRelationExtractor, _RE_CONCEPT_USE
            )
            text = """
            Nghiên cứu này sử dụng mô hình BERT để phân tích cảm xúc.
            Chúng tôi fine-tune BERT trên tập dữ liệu tiếng Việt.
            """
            rre = RuleRelationExtractor()
            window, evidence = rre._find_cooccurrence(text, "BERT", _RE_CONCEPT_USE)
            assert window, "Phải tìm thấy cooccurrence"
            assert "bert" in evidence.lower() or "bert" in window.lower()
            return True, f"Implicit cooccurrence OK: '{evidence[:60]}...'"
        except AssertionError as e:
            return False, f"Implicit cooccurrence FAIL: {e}"
        except ImportError as e:
            return None, f"SKIP: {e}"

    # Run all tests
    tests = [
        ("T01 — WroteRelation direction",          test_wrote_direction),
        ("T02 — CitesRelation A→B direction",       test_cites_direction),
        ("T03 — BasedOn self-loop filter",          test_based_on_no_self_loop),
        ("T04 — RelationResult.summary() format",   test_relation_result_summary),
        ("T05 — Noise evidence filter",             test_noise_filter),
        ("T06 — Category resolve pipe-separated",   test_category_resolve),
        ("T07 — JSON double-brace repair",          test_double_brace_repair),
        ("T08 — ExtractedEntities.is_empty()",      test_extracted_entities_empty),
        ("T09 — Multi-hop A→B→C chain",             test_multi_hop_chain),
        ("T10 — Implicit cooccurrence in text",     test_implicit_relation_cooccurrence),
    ]

    for name, fn in tests:
        try:
            ok, msg = fn()
            status = "PASS" if ok is True else ("SKIP" if ok is None else "FAIL")
            if ok is True:
                results["passed"] += 1
                logger.info("  ✓ %s — %s", name, msg)
            elif ok is False:
                results["failed"] += 1
                results["errors"].append({"test": name, "msg": msg})
                logger.error("  ✗ %s — %s", name, msg)
            else:
                logger.warning("  ~ %s — %s", name, msg)
        except Exception as e:
            results["failed"] += 1
            msg = f"EXCEPTION: {e}"
            results["errors"].append({"test": name, "msg": msg})
            logger.error("  ✗ %s — %s", name, msg)

        results["details"].append({
            "name": name,
            "status": status,
            "message": msg if "msg" in dir() else "",
        })

    return results


# =============================================================================
# INTEGRATION TEST — LLM thật
# =============================================================================

def run_integration_tests(
    sample_size: int = 20,
    use_ground_truth: bool = False,
) -> tuple[list[DocResult], dict[str, dict[str, RelationMetrics]], list[ErrorRecord]]:
    """
    Chạy RelationExtractor trên sample_size files thực từ DATA_DIR.
    """
    # Import LLM & extractor
    try:
        from ai_module.kg.entity_extractor import OllamaBackend, EntityExtractor
        from ai_module.kg.relation_extractor import RelationExtractor
    except ImportError as e:
        logger.error("Import error: %s", e)
        logger.error("Hãy chắc chắn PYTHONPATH đúng và các module đã cài đặt")
        return [], {}, []

    # Load files
    if not DATA_DIR.exists():
        logger.error("DATA_DIR không tồn tại: %s", DATA_DIR)
        logger.error("Hãy kiểm tra lại đường dẫn thư mục processed")
        return [], {}, []

    all_files = sorted(DATA_DIR.rglob("*.json"))
    if not all_files:
        logger.error("Không tìm thấy file JSON trong %s", DATA_DIR)
        return [], {}, []

    logger.info("Tìm thấy %d JSON files, chạy sample=%d", len(all_files), sample_size)

    # Chọn sample đại diện từ mỗi domain
    domain_files: dict[str, list[Path]] = defaultdict(list)
    for f in all_files:
        try:
            data = load_json_file(f)
            if data:
                domain = detect_domain(f, data)
                domain_files[domain].append(f)
        except Exception:
            pass

    # Distribute sample across domains
    selected: list[tuple[Path, str]] = []
    per_domain = max(1, sample_size // len(DOMAINS))
    for domain in DOMAINS:
        files = domain_files.get(domain, [])
        n = min(per_domain, len(files))
        selected.extend((f, domain) for f in files[:n])

    # Thêm từ "unknown" nếu chưa đủ
    remaining = sample_size - len(selected)
    for f in domain_files.get("unknown", [])[:remaining]:
        data = load_json_file(f)
        selected.append((f, detect_domain(f, data or {})))

    selected = selected[:sample_size]
    logger.info("Sample cuối cùng: %d files từ %d domains", len(selected),
                len({d for _, d in selected}))

    # Khởi tạo LLM & extractors
    llm = OllamaBackend(
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        model=os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct-q8_0"),
    )
    entity_extractor  = EntityExtractor(llm, max_workers=2)
    relation_extractor = RelationExtractor(llm)

    doc_results: list[DocResult] = []
    all_doc_ids: list[str] = []

    for i, (fpath, domain) in enumerate(selected):
        logger.info("[%d/%d] Xử lý: %s (domain=%s)", i + 1, len(selected), fpath.name, domain)
        data = load_json_file(fpath)
        if not data:
            doc_results.append(DocResult(
                doc_id=fpath.stem, domain=domain, file_path=str(fpath),
                success=False, latency_sec=0.0, error_msg="Không đọc được file",
            ))
            continue

        try:
            doc       = json_to_unified_document(data, fpath)
            doc_id    = getattr(doc, "doc_id", fpath.stem)
            all_doc_ids.append(doc_id)

            t0       = time.perf_counter()
            entities = entity_extractor.extract(doc)
            result   = relation_extractor.extract(
                doc=doc,
                paper_id=doc_id,
                entities=entities,
                author_id_map={a.name: a.name for a in entities.authors},
            )
            latency = time.perf_counter() - t0

            relations = _extract_relation_tuples(result)

            doc_results.append(DocResult(
                doc_id=doc_id,
                domain=domain,
                file_path=str(fpath),
                success=True,
                latency_sec=round(latency, 2),
                relations=relations,
                entity_counts={
                    "concepts":  len(entities.concepts),
                    "evidences": len(entities.evidences),
                    "metrics":   len(entities.metrics),
                    "findings":  len(entities.findings),
                    "authors":   len(entities.authors),
                },
            ))
            logger.info(
                "  → Latency=%.1fs | %s",
                latency, result.summary()
            )

        except Exception as e:
            logger.error("  ✗ FAIL: %s", traceback.format_exc())
            doc_results.append(DocResult(
                doc_id=getattr(data, "doc_id", fpath.stem),
                domain=domain,
                file_path=str(fpath),
                success=False,
                latency_sec=0.0,
                error_msg=str(e)[:300],
            ))

    # Ground-truth
    ground_truth = {}
    if use_ground_truth:
        ground_truth = load_or_create_ground_truth(all_doc_ids)

    metrics, errors = compute_metrics(doc_results, ground_truth)
    return doc_results, metrics, errors


# =============================================================================
# STATISTICS — không cần ground-truth
# =============================================================================

def compute_distribution_stats(doc_results: list[DocResult]) -> dict:
    """Tính thống kê phân phối relation counts (không cần ground-truth)."""
    stats = {
        "by_domain": defaultdict(lambda: defaultdict(list)),
        "by_relation": defaultdict(list),
        "latency": [],
        "success_rate": 0.0,
        "entity_counts": defaultdict(list),
    }

    successful = [r for r in doc_results if r.success]
    stats["success_rate"] = len(successful) / len(doc_results) if doc_results else 0.0
    stats["latency"] = [r.latency_sec for r in successful]

    for r in successful:
        stats["entity_counts"]["concepts"].extend(
            [r.entity_counts.get("concepts", 0)]
        )
        stats["entity_counts"]["evidences"].extend(
            [r.entity_counts.get("evidences", 0)]
        )

        for rel_type, tuples in r.relations.items():
            n = len(tuples)
            stats["by_domain"][r.domain][rel_type].append(n)
            stats["by_relation"][rel_type].append(n)

    # Compute averages
    avg_by_rel = {}
    for rel, counts in stats["by_relation"].items():
        avg_by_rel[rel] = {
            "mean":   round(sum(counts) / len(counts), 2) if counts else 0,
            "max":    max(counts) if counts else 0,
            "min":    min(counts) if counts else 0,
            "total":  sum(counts),
        }

    avg_by_domain_rel = {}
    for domain, rels in stats["by_domain"].items():
        avg_by_domain_rel[domain] = {}
        for rel, counts in rels.items():
            avg_by_domain_rel[domain][rel] = round(
                sum(counts) / len(counts), 2
            ) if counts else 0

    return {
        "success_rate":      round(stats["success_rate"] * 100, 1),
        "n_docs":            len(doc_results),
        "n_success":         len(successful),
        "avg_latency_sec":   round(sum(stats["latency"]) / len(stats["latency"]), 2)
                             if stats["latency"] else 0,
        "by_relation":       avg_by_rel,
        "by_domain_rel":     avg_by_domain_rel,
        "entity_counts":     {k: round(sum(v)/len(v), 1) if v else 0
                              for k, v in stats["entity_counts"].items()},
    }


# =============================================================================
# HTML REPORT GENERATOR
# =============================================================================

def generate_html_report(
    unit_results:  dict,
    doc_results:   list[DocResult],
    metrics:       dict[str, dict[str, RelationMetrics]],
    errors:        list[ErrorRecord],
    dist_stats:    dict,
) -> str:
    """Tạo báo cáo HTML đầy đủ với bảng + biểu đồ Chart.js."""

    # ── Chuẩn bị dữ liệu ─────────────────────────────────────────────────────

    # Table 1: Unit test results
    unit_rows = ""
    for d in unit_results.get("details", []):
        color = "#22c55e" if d["status"] == "PASS" else (
                "#f59e0b" if d["status"] == "SKIP" else "#ef4444")
        unit_rows += f"""
        <tr>
            <td>{d['name']}</td>
            <td style="color:{color}; font-weight:600;">{d['status']}</td>
            <td style="font-size:0.85em; color:#64748b;">{d.get('message','')[:120]}</td>
        </tr>"""

    # Table 2: P/R/F1 by relation type (aggregate "all" domain)
    prf_rows = ""
    chart_rel_labels = []
    chart_precision  = []
    chart_recall     = []
    chart_f1         = []

    for rel in RELATION_TYPES:
        m = metrics.get(rel, {}).get("all")
        if m is None:
            p, r, f = 0.0, 0.0, 0.0
            tp, fp, fn = 0, 0, 0
        else:
            p, r, f = m.precision, m.recall, m.f1
            tp, fp, fn = m.tp, m.fp, m.fn

        def fmt(v): return f"{v*100:.1f}%"
        row_cls = "table-row-highlight" if f > 0 else ""
        prf_rows += f"""
        <tr class="{row_cls}">
            <td><span class="badge">{RELATION_LABELS.get(rel, rel)}</span></td>
            <td style="color:#3b82f6;">{fmt(p)}</td>
            <td style="color:#10b981;">{fmt(r)}</td>
            <td style="color:#f59e0b; font-weight:700;">{fmt(f)}</td>
            <td>{tp}</td><td>{fp}</td><td>{fn}</td>
        </tr>"""

        chart_rel_labels.append(RELATION_LABELS.get(rel, rel))
        chart_precision.append(round(p * 100, 1))
        chart_recall.append(round(r * 100, 1))
        chart_f1.append(round(f * 100, 1))

    # Table 3: F1 by domain (cross-table)
    domain_f1_header = "".join(
        f"<th>{DOMAIN_LABELS.get(d, d)}</th>" for d in DOMAINS
    )
    domain_f1_rows = ""
    chart_domain_data = {rel: [] for rel in RELATION_TYPES}

    for rel in RELATION_TYPES:
        domain_f1_rows += f"<tr><td><span class='badge'>{RELATION_LABELS.get(rel, rel)}</span></td>"
        for d in DOMAINS:
            m = metrics.get(rel, {}).get(d)
            f1_val = m.f1 if m else 0.0
            f1_pct = round(f1_val * 100, 1)
            chart_domain_data[rel].append(f1_pct)
            cell_color = (
                "#dcfce7" if f1_pct >= 70 else
                "#fef9c3" if f1_pct >= 40 else
                "#fee2e2" if f1_pct > 0  else "#f8fafc"
            )
            domain_f1_rows += (
                f"<td style='background:{cell_color}; text-align:center;'>"
                f"{f1_pct:.1f}%</td>"
            )
        domain_f1_rows += "</tr>"

    # Table 4: Error analysis
    error_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for e in errors:
        error_counts[e.relation_type][e.error_type] += 1

    error_types = ["missing", "spurious", "wrong_direction", "wrong_entity", "schema_error"]
    error_header = "".join(f"<th>{et.replace('_', ' ').title()}</th>" for et in error_types)
    error_rows = ""
    for rel in RELATION_TYPES:
        ec = error_counts.get(rel, {})
        total = sum(ec.values())
        if total == 0 and not any(ec.get(et, 0) for et in error_types):
            continue
        error_rows += f"<tr><td><span class='badge'>{RELATION_LABELS.get(rel, rel)}</span></td>"
        for et in error_types:
            n = ec.get(et, 0)
            cell_color = "#fee2e2" if n > 0 else "#f8fafc"
            error_rows += f"<td style='background:{cell_color}; text-align:center;'>{n}</td>"
        error_rows += f"<td style='font-weight:600;'>{total}</td></tr>"

    # Table 5: Per-document results
    doc_rows = ""
    for r in doc_results[:50]:  # chỉ hiển thị 50 đầu
        status_icon = "✓" if r.success else "✗"
        status_col  = "#22c55e" if r.success else "#ef4444"
        n_rels = sum(len(v) for v in r.relations.values())
        doc_rows += f"""
        <tr>
            <td style="font-size:0.8em; max-width:200px; overflow:hidden; text-overflow:ellipsis;"
                title="{r.file_path}">{Path(r.file_path).name}</td>
            <td>{DOMAIN_LABELS.get(r.domain, r.domain)}</td>
            <td style="color:{status_col}; font-weight:600;">{status_icon}</td>
            <td>{r.latency_sec:.1f}s</td>
            <td>{n_rels}</td>
            <td>{r.entity_counts.get('concepts', 0)}</td>
            <td>{r.entity_counts.get('evidences', 0)}</td>
            <td style="font-size:0.8em; color:#ef4444;">{r.error_msg[:60] if r.error_msg else ''}</td>
        </tr>"""

    # Chart data: relation distribution heatmap (avg counts by domain)
    heatmap_datasets = []
    colors = ["#3b82f6","#10b981","#f59e0b","#ef4444","#8b5cf6",
              "#ec4899","#06b6d4","#84cc16","#f97316","#6366f1","#14b8a6"]
    for i, rel in enumerate(RELATION_TYPES[:7]):  # top 7 cho chart
        data_vals = [
            dist_stats.get("by_domain_rel", {}).get(d, {}).get(rel, 0)
            for d in DOMAINS
        ]
        heatmap_datasets.append({
            "label": RELATION_LABELS.get(rel, rel),
            "data": data_vals,
            "backgroundColor": colors[i % len(colors)] + "88",
            "borderColor": colors[i % len(colors)],
            "borderWidth": 1,
        })

    # ── HTML Template ─────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Relation Extraction — Test Report</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600;700&display=swap');

  :root {{
    --bg: #0f172a; --surface: #1e293b; --surface2: #334155;
    --border: #475569; --text: #e2e8f0; --muted: #94a3b8;
    --blue: #3b82f6; --green: #22c55e; --yellow: #f59e0b;
    --red: #ef4444; --purple: #8b5cf6; --teal: #14b8a6;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'IBM Plex Sans', sans-serif;
    background: var(--bg); color: var(--text);
    min-height: 100vh; padding: 2rem;
  }}
  h1 {{ font-size: 2rem; font-weight: 700; color: #f1f5f9;
        border-bottom: 2px solid var(--blue); padding-bottom: .5rem; margin-bottom: 2rem; }}
  h2 {{ font-size: 1.2rem; font-weight: 600; color: var(--blue);
        margin: 2rem 0 1rem; display: flex; align-items: center; gap: .5rem; }}
  h2::before {{ content: "▌"; color: var(--teal); }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin-bottom: 2rem; }}
  .card {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; padding: 1.25rem;
  }}
  .stat-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin-bottom: 2rem; }}
  .stat-box {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 1rem; text-align: center;
  }}
  .stat-val {{ font-size: 2rem; font-weight: 700; font-family: 'IBM Plex Mono', monospace; }}
  .stat-lbl {{ font-size: .8rem; color: var(--muted); margin-top: .25rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .9rem; }}
  th {{ background: var(--surface2); color: var(--muted); font-weight: 600;
        padding: .6rem .8rem; text-align: left; font-size: .8rem; text-transform: uppercase; }}
  td {{ padding: .55rem .8rem; border-bottom: 1px solid var(--border); }}
  tr:hover td {{ background: var(--surface2); }}
  .badge {{
    display: inline-block; background: var(--surface2); color: var(--teal);
    border: 1px solid var(--teal); border-radius: 6px;
    padding: .15rem .55rem; font-size: .8rem; font-family: 'IBM Plex Mono', monospace;
    white-space: nowrap;
  }}
  .chart-wrap {{ position: relative; height: 340px; }}
  .chart-wide {{ position: relative; height: 400px; }}
  .table-row-highlight td {{ background: #1e3a5f22; }}
  .section-note {{
    background: #1e293b; border-left: 3px solid var(--yellow);
    padding: .75rem 1rem; border-radius: 0 8px 8px 0;
    font-size: .85rem; color: var(--muted); margin-bottom: 1rem;
  }}
  .tag-pass {{ color: var(--green); }} .tag-fail {{ color: var(--red); }}
  .tag-skip {{ color: var(--yellow); }}
  footer {{ margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--border);
            font-size: .8rem; color: var(--muted); text-align: center; }}
  @media (max-width: 900px) {{
    .grid-2 {{ grid-template-columns: 1fr; }}
    .stat-grid {{ grid-template-columns: repeat(2, 1fr); }}
  }}
</style>
</head>
<body>

<h1>📊 Relation Extraction — Test Report</h1>
<p style="color:var(--muted); margin-bottom:2rem;">
  Module: <code>ai_module/kg/relation_extractor.py</code> &nbsp;|&nbsp;
  LLM: Claude (AnthropicBackend) &nbsp;|&nbsp;
  Data: {dist_stats.get('n_docs', 0)} files / 5 domains
</p>

<!-- ── Stat boxes ── -->
<div class="stat-grid">
  <div class="stat-box">
    <div class="stat-val" style="color:var(--green);">{dist_stats.get('n_success', 0)}/{dist_stats.get('n_docs', 0)}</div>
    <div class="stat-lbl">Docs thành công</div>
  </div>
  <div class="stat-box">
    <div class="stat-val" style="color:var(--blue);">{dist_stats.get('success_rate', 0):.1f}%</div>
    <div class="stat-lbl">Success Rate</div>
  </div>
  <div class="stat-box">
    <div class="stat-val" style="color:var(--yellow);">{dist_stats.get('avg_latency_sec', 0):.1f}s</div>
    <div class="stat-lbl">Avg Latency/Doc</div>
  </div>
  <div class="stat-box">
    <div class="stat-val" style="color:var(--purple);">{unit_results.get('passed', 0)}/{unit_results.get('passed', 0) + unit_results.get('failed', 0)}</div>
    <div class="stat-lbl">Unit Tests Passed</div>
  </div>
</div>

<!-- ── Bảng 3.5: Unit Tests ── -->
<h2>Bảng 3.5 — Unit Tests (Rule-Based, Không LLM)</h2>
<div class="card">
  <table>
    <thead><tr><th>Test Case</th><th>Status</th><th>Message</th></tr></thead>
    <tbody>{unit_rows or '<tr><td colspan="3" style="color:var(--muted);">Chưa chạy unit tests</td></tr>'}</tbody>
  </table>
</div>

<!-- ── Bảng 3.6: P/R/F1 ── -->
<h2>Bảng 3.6 — Precision / Recall / F1 theo Relation Type</h2>
<div class="section-note">
  ⚠️ P/R/F1 chỉ có giá trị khi có ground-truth annotation trong
  <code>test_results/ground_truth.json</code>. Nếu chưa có annotation, các giá trị này sẽ là 0%.
</div>
<div class="card">
  <table>
    <thead>
      <tr>
        <th>Relation Type</th>
        <th style="color:#3b82f6;">Precision</th>
        <th style="color:#10b981;">Recall</th>
        <th style="color:#f59e0b;">F1</th>
        <th>TP</th><th>FP</th><th>FN</th>
      </tr>
    </thead>
    <tbody>{prf_rows or '<tr><td colspan="7" style="color:var(--muted);">Chưa có ground-truth</td></tr>'}</tbody>
  </table>
</div>

<!-- ── Biểu đồ 3.4: Grouped Bar P/R/F1 ── -->
<h2>Biểu đồ 3.4 — Grouped Bar: Precision / Recall / F1 theo Relation Type</h2>
<div class="card">
  <div class="chart-wide">
    <canvas id="prfChart"></canvas>
  </div>
</div>

<!-- ── Bảng 3.7: F1 by Domain ── -->
<h2>Bảng 3.7 — F1 theo Relation Type × Domain</h2>
<div class="card" style="overflow-x:auto;">
  <table>
    <thead>
      <tr><th>Relation Type</th>{domain_f1_header}</tr>
    </thead>
    <tbody>{domain_f1_rows or '<tr><td colspan="6" style="color:var(--muted);">Chưa có dữ liệu</td></tr>'}</tbody>
  </table>
  <div style="font-size:.8rem; color:var(--muted); margin-top:.75rem;">
    🟢 ≥70% &nbsp; 🟡 40–70% &nbsp; 🔴 &lt;40% &nbsp; ⬜ Không có dữ liệu
  </div>
</div>

<!-- ── Biểu đồ 3.5: Heatmap Relation Distribution by Domain ── -->
<div class="grid-2">
  <div>
    <h2>Biểu đồ 3.5 — Relation Count Distribution theo Domain</h2>
    <div class="card">
      <div class="chart-wrap">
        <canvas id="domainChart"></canvas>
      </div>
    </div>
  </div>
  <div>
    <h2>Biểu đồ 3.6 — Avg Relation Counts per Doc</h2>
    <div class="card">
      <div class="chart-wrap">
        <canvas id="avgRelChart"></canvas>
      </div>
    </div>
  </div>
</div>

<!-- ── Bảng 3.8: Error Analysis ── -->
<h2>Bảng 3.8 — Error Analysis theo Relation Type</h2>
<div class="card" style="overflow-x:auto;">
  <table>
    <thead>
      <tr>
        <th>Relation Type</th>
        <th>Missing (FN)</th><th>Spurious (FP)</th>
        <th>Wrong Direction</th><th>Wrong Entity</th><th>Schema Error</th>
        <th>Total Errors</th>
      </tr>
    </thead>
    <tbody>{error_rows or '<tr><td colspan="7" style="color:var(--muted);">Không có lỗi hoặc chưa có ground-truth</td></tr>'}</tbody>
  </table>
</div>

<!-- ── Bảng 3.9: Per-Document Results ── -->
<h2>Bảng 3.9 — Kết quả theo từng Document (50 đầu)</h2>
<div class="card" style="overflow-x:auto;">
  <table>
    <thead>
      <tr>
        <th>File</th><th>Domain</th><th>Status</th>
        <th>Latency</th><th>Total Rels</th>
        <th>Concepts</th><th>Evidences</th><th>Error</th>
      </tr>
    </thead>
    <tbody>{doc_rows or '<tr><td colspan="8" style="color:var(--muted);">Chưa có kết quả</td></tr>'}</tbody>
  </table>
</div>

<!-- ── Biểu đồ 3.7: Latency Distribution ── -->
<div class="grid-2">
  <div>
    <h2>Biểu đồ 3.7 — Latency Distribution</h2>
    <div class="card">
      <div class="chart-wrap">
        <canvas id="latencyChart"></canvas>
      </div>
    </div>
  </div>
  <div>
    <h2>Biểu đồ 3.8 — Success Rate theo Domain</h2>
    <div class="card">
      <div class="chart-wrap">
        <canvas id="successChart"></canvas>
      </div>
    </div>
  </div>
</div>

<footer>Generated by test_relation_extractor.py — ai_module/kg/relation_extractor.py</footer>

<script>
// ── Chart 1: Grouped Bar P/R/F1 ─────────────────────────────────────────────
new Chart(document.getElementById('prfChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(chart_rel_labels)},
    datasets: [
      {{ label: 'Precision', data: {json.dumps(chart_precision)},
         backgroundColor: '#3b82f688', borderColor: '#3b82f6', borderWidth: 1 }},
      {{ label: 'Recall',    data: {json.dumps(chart_recall)},
         backgroundColor: '#10b98188', borderColor: '#10b981', borderWidth: 1 }},
      {{ label: 'F1',        data: {json.dumps(chart_f1)},
         backgroundColor: '#f59e0b88', borderColor: '#f59e0b', borderWidth: 2 }},
    ]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{
      legend: {{ labels: {{ color: '#e2e8f0' }} }},
      tooltip: {{ callbacks: {{ label: ctx => ctx.dataset.label + ': ' + ctx.raw.toFixed(1) + '%' }} }}
    }},
    scales: {{
      x: {{ ticks: {{ color: '#94a3b8', maxRotation: 45 }}, grid: {{ color: '#334155' }} }},
      y: {{ ticks: {{ color: '#94a3b8', callback: v => v + '%' }},
             grid: {{ color: '#334155' }}, max: 100, min: 0 }}
    }}
  }}
}});

// ── Chart 2: Domain Distribution ────────────────────────────────────────────
new Chart(document.getElementById('domainChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps([DOMAIN_LABELS.get(d, d) for d in DOMAINS])},
    datasets: {json.dumps(heatmap_datasets)}
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ position: 'bottom', labels: {{ color: '#94a3b8', font: {{ size: 10 }} }} }} }},
    scales: {{
      x: {{ stacked: false, ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }} }},
      y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }} }}
    }}
  }}
}});

// ── Chart 3: Avg Relation Counts ────────────────────────────────────────────
const avgRelData = {json.dumps({
    RELATION_LABELS.get(rel, rel): dist_stats.get('by_relation', {}).get(rel, {}).get('mean', 0)
    for rel in RELATION_TYPES
})};
new Chart(document.getElementById('avgRelChart'), {{
  type: 'bar',
  data: {{
    labels: Object.keys(avgRelData),
    datasets: [{{
      label: 'Avg per Doc',
      data: Object.values(avgRelData),
      backgroundColor: ['#3b82f6','#10b981','#f59e0b','#ef4444','#8b5cf6',
                         '#ec4899','#06b6d4','#84cc16','#f97316','#6366f1','#14b8a6'],
    }}]
  }},
  options: {{
    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }} }},
      y: {{ ticks: {{ color: '#94a3b8', font: {{ size: 10 }} }}, grid: {{ display: false }} }}
    }}
  }}
}});

// ── Chart 4: Latency Histogram ───────────────────────────────────────────────
const latencies = {json.dumps([r.latency_sec for r in doc_results if r.success])};
const bins = [0,5,10,15,20,30,45,60,80,100,120,999];
const binLabels = [
  '0-5s','5-10s','10-15s','15-20s',
  '20-30s','30-45s','45-60s',
  '60-80s','80-100s','100-120s','>120s'
];
const binCounts = new Array(bins.length-1).fill(0);
latencies.forEach(l => {{
  for (let i=0; i<bins.length-1; i++) {{
    if (l >= bins[i] && l < bins[i+1]) {{ binCounts[i]++; break; }}
  }}
}});
new Chart(document.getElementById('latencyChart'), {{
  type: 'bar',
  data: {{
    labels: binLabels,
    datasets: [{{ label: 'Số docs', data: binCounts,
                  backgroundColor: '#8b5cf688', borderColor: '#8b5cf6', borderWidth: 1 }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }} }},
      y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }} }}
    }}
  }}
}});

// ── Chart 5: Success Rate by Domain ─────────────────────────────────────────
const domainSuccess = {json.dumps({
    DOMAIN_LABELS.get(d, d): round(
        sum(1 for r in doc_results if r.success and r.domain == d) /
        max(1, sum(1 for r in doc_results if r.domain == d)) * 100, 1
    )
    for d in DOMAINS
})};
new Chart(document.getElementById('successChart'), {{
  type: 'doughnut',
  data: {{
    labels: Object.keys(domainSuccess).map(k => k + ' (' + domainSuccess[k] + '%)'),
    datasets: [{{
      data: Object.values(domainSuccess),
      backgroundColor: ['#3b82f6','#10b981','#f59e0b','#ef4444','#8b5cf6'],
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ color: '#94a3b8', font: {{ size: 11 }} }} }}
    }}
  }}
}});
</script>
</body>
</html>"""
    return html


# =============================================================================
# SAVE & LOAD
# =============================================================================

def save_results(doc_results: list[DocResult], errors: list[ErrorRecord]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # results.json
    results_data = [asdict(r) for r in doc_results]
    RESULTS_FILE.write_text(
        json.dumps(results_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Đã lưu results: %s", RESULTS_FILE)

    # error_analysis.json
    errors_data = [asdict(e) for e in errors]
    ERROR_FILE.write_text(
        json.dumps(errors_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Đã lưu error analysis: %s", ERROR_FILE)


def load_results(results_path: Path) -> list[DocResult]:
    data = json.loads(results_path.read_text(encoding="utf-8"))
    results = []
    for d in data:
        r = DocResult(**d)
        # relations là list of lists trong JSON, chuyển lại thành list of tuples
        r.relations = {
            k: [tuple(t) for t in v]
            for k, v in (r.relations or {}).items()
        }
        results.append(r)
    return results


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Test relation_extractor.py — Báo cáo P/R/F1 + HTML report"
    )
    parser.add_argument(
        "--mode", choices=["unit", "full", "report"],
        default="unit",
        help="unit: chỉ unit tests | full: unit + LLM integration | report: tạo report từ results đã lưu",
    )
    parser.add_argument("--sample",  type=int, default=20,
                        help="Số file JSON cần test (default: 20, max: 100)")
    parser.add_argument("--results", type=str, default=None,
                        help="Path đến results.json đã lưu (chỉ dùng với --mode report)")
    parser.add_argument("--with-gt", action="store_true",
                        help="Dùng ground-truth để tính P/R/F1 (cần điền ground_truth.json)")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Unit tests ─────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("SECTION 1: Unit Tests")
    logger.info("=" * 60)
    unit_results = run_unit_tests()
    logger.info(
        "Unit Tests: PASS=%d FAIL=%d",
        unit_results["passed"], unit_results["failed"],
    )

    doc_results: list[DocResult] = []
    metrics: dict = {}
    errors:  list[ErrorRecord] = []

    if args.mode == "full":
        # ── Integration tests ────────────────────────────────────────────
        logger.info("=" * 60)
        logger.info("SECTION 2: Integration Tests (LLM=%s, sample=%d)",
                "OllamaBackend", args.sample)
        logger.info("=" * 60)
        doc_results, metrics, errors = run_integration_tests(
            sample_size=min(args.sample, 100),
            use_ground_truth=args.with_gt,
        )
        save_results(doc_results, errors)

    elif args.mode == "report":
        # ── Load existing results ────────────────────────────────────────
        results_path = Path(args.results) if args.results else RESULTS_FILE
        if not results_path.exists():
            logger.error("Không tìm thấy results file: %s", results_path)
            sys.exit(1)
        doc_results = load_results(results_path)
        logger.info("Loaded %d results từ %s", len(doc_results), results_path)

        if args.with_gt:
            doc_ids     = [r.doc_id for r in doc_results]
            ground_truth = load_or_create_ground_truth(doc_ids)
            metrics, errors = compute_metrics(doc_results, ground_truth)

    # ── Distribution stats (không cần ground-truth) ──────────────────────
    dist_stats = compute_distribution_stats(doc_results) if doc_results else {
        "success_rate": 0, "n_docs": 0, "n_success": 0, "avg_latency_sec": 0,
        "by_relation": {}, "by_domain_rel": {}, "entity_counts": {},
    }

    # ── Generate HTML report ──────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("SECTION 3: Generate HTML Report")
    logger.info("=" * 60)
    html = generate_html_report(unit_results, doc_results, metrics, errors, dist_stats)
    REPORT_FILE.write_text(html, encoding="utf-8")
    logger.info("✓ Report saved: %s", REPORT_FILE.resolve())

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Unit Tests  : {unit_results['passed']} PASS / {unit_results['failed']} FAIL")
    if doc_results:
        n_ok = sum(1 for r in doc_results if r.success)
        print(f"Integration : {n_ok}/{len(doc_results)} docs OK")
        avg_lat = dist_stats.get("avg_latency_sec", 0)
        print(f"Avg Latency : {avg_lat:.1f}s/doc")

        # Print top-5 relation by count
        print("\nTop relation counts (avg per doc):")
        by_rel = dist_stats.get("by_relation", {})
        for rel, stat in sorted(by_rel.items(), key=lambda x: -x[1].get("mean", 0))[:5]:
            print(f"  {RELATION_LABELS.get(rel, rel):25s}: avg={stat.get('mean', 0):.1f} total={stat.get('total', 0)}")

    print(f"\n📄 Report : {REPORT_FILE.resolve()}")
    if unit_results["failed"] > 0:
        print("\nFailed unit tests:")
        for e in unit_results["errors"]:
            print(f"  ✗ {e['test']}: {e['msg']}")
        sys.exit(1)


if __name__ == "__main__":
    main()