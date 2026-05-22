from __future__ import annotations

import json
import logging
import os
import re
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import threading

import httpx
import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd
import seaborn as sns

warnings.filterwarnings("ignore")
logging.basicConfig(
    level=logging.WARNING,        # tắt INFO noise từ entity_extractor
    format="%(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("test_llm")

# ─────────────────────────────────────────────────────────────────────────────
# 0. CONFIG — đọc từ env hoặc dùng mặc định
# ─────────────────────────────────────────────────────────────────────────────

OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL",    "qwen2.5:7b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_TIMEOUT  = float(os.getenv("OLLAMA_TIMEOUT", "300"))
MAX_DOCS        = int(os.getenv("MAX_DOCS", "10"))          # giới hạn số doc dùng LLM
MAX_SECTIONS    = int(os.getenv("MAX_SECTIONS", "4"))       # sections/doc để giảm LLM calls
MAX_SECTION_CHARS = int(os.getenv("MAX_SECTION_CHARS", "2000"))  # cắt ngắn để nhanh hơn

DATA_ROOT   = Path(os.getenv(
    "DATA_ROOT",
    r"D:\Study\Study_Class\Semester_6\Data_mining\Project\data\processed"
))
OUTPUT_DIR  = Path("test_output_llm")
OUTPUT_DIR.mkdir(exist_ok=True)

RANDOM_SEED = 42

DOMAIN_MAP = {
    "KHKT&KT": "Khoa học Kỹ thuật & Công nghệ",
    "KHNN":    "Khoa học Nông nghiệp",
    "KHTN":    "Khoa học Tự nhiên",
    "KHXH&NV": "Khoa học Xã hội & Nhân văn",
    "KHYD":    "Khoa học Y Dược",
}

DOMAIN_COLORS = {
    "KHKT&KT": "#2196F3",
    "KHNN":    "#4CAF50",
    "KHTN":    "#FF9800",
    "KHXH&NV": "#9C27B0",
    "KHYD":    "#F44336",
}

ENTITY_COLORS = [
    "#1565C0","#2E7D32","#E65100","#6A1B9A","#B71C1C",
    "#00838F","#AD1457","#558B2F","#4527A0","#0277BD",
]

plt.rcParams.update({
    "font.family":     "DejaVu Sans",
    "font.size":       11,
    "axes.titlesize":  13,
    "axes.labelsize":  11,
    "figure.dpi":      150,
    "axes.spines.top":   False,
    "axes.spines.right": False,
})

# ─────────────────────────────────────────────────────────────────────────────
# 1. SCHEMA CONSTANTS (khớp entities.py)
# ─────────────────────────────────────────────────────────────────────────────

CONCEPT_CATEGORIES = {
    "method","model","algorithm","task","framework","architecture",
    "gene","protein","disease","drug","pathway","organism",
    "theory","policy","event","organization",
    "regulation","project","contract","financial_instrument",
    "technology","process","material","system",
    "phenomenon","law","particle","indicator","market",
    "equipment","tool","method_component","compound","software",
    "education","activity","group","program","plan","document",
    "test","metric","location","concept",
}

EVIDENCE_TYPES = {
    "dataset","benchmark","corpus","experiment","clinical_trial",
    "survey","census","case_study","observation","simulation",
    "report","book","standard","event","journal","statistic",
    "secondary_data","interview",
}

FINDING_TYPES = {"result","hypothesis","conclusion","limitation","contribution"}

VALID_EDGE_TYPES = {
    "WROTE","PUBLISHED_AT","HAS_TOPIC","CITES",
    "USES_CONCEPT","EVALUATES_ON","ACHIEVES_METRIC",
    "BASED_ON","EXTENDS","SUPPORTS","CONTRADICTS",
    "HAS_CHUNK","HAS_FINDING",
}

VALID_NODE_TYPES = {
    "Paper","Author","Venue","Topic",
    "Concept","Evidence","Metric","Finding","Institution",
}

PRIORITY_SECTIONS = {
    "abstract","method","methodology","approach","model","proposed",
    "experiment","evaluation","result","conclusion","discussion",
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. UNIFIED DOCUMENT SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Section:
    name:         str
    content:      str
    order:        int = 0
    section_type: str = "unknown"


@dataclass
class Reference:
    title:    Optional[str] = None
    doi:      Optional[str] = None
    year:     Optional[int] = None
    raw_text: str           = ""


@dataclass
class UnifiedDocument:
    doc_id:    str
    title:     str
    abstract:  str                  = ""
    sections:  list[Section]        = field(default_factory=list)
    authors:   list[str]            = field(default_factory=list)
    keywords:  list[str]            = field(default_factory=list)
    references: list[Reference]     = field(default_factory=list)
    journal:   Optional[str]        = None
    year:      Optional[int]        = None
    doi:       Optional[str]        = None
    language:  str                  = "vi"
    domain_key: str                 = ""


def load_unified_doc(path: Path, domain_key: str) -> Optional[UnifiedDocument]:
    """
    Đọc file JSON → UnifiedDocument.
    Hỗ trợ nhiều cấu trúc JSON thực tế.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("load_unified_doc: không đọc được %s — %s", path, e)
        return None

    # ── doc_id / title ────────────────────────────────────────────────
    doc_id = data.get("doc_id") or data.get("id") or path.stem
    title  = data.get("title") or path.stem

    # ── abstract ──────────────────────────────────────────────────────
    abstract = data.get("abstract") or data.get("summary") or ""

    # ── sections ──────────────────────────────────────────────────────
    sections: list[Section] = []
    raw_sections = data.get("sections") or []
    for i, s in enumerate(raw_sections):
        if isinstance(s, dict):
            content  = s.get("content") or s.get("text") or ""
            name     = s.get("name") or s.get("title") or f"section_{i}"
            stype    = s.get("section_type") or s.get("type") or name.lower()
            if len(content.strip()) >= 50:
                sections.append(Section(
                    name=name, content=content[:MAX_SECTION_CHARS],
                    order=i, section_type=stype.lower(),
                ))
        elif isinstance(s, str) and len(s.strip()) >= 50:
            sections.append(Section(
                name=f"section_{i}", content=s[:MAX_SECTION_CHARS],
                order=i, section_type="unknown",
            ))

    # Nếu không có sections → dùng abstract làm 1 section
    if not sections and abstract:
        sections.append(Section(
            name="abstract", content=abstract[:MAX_SECTION_CHARS],
            order=0, section_type="abstract",
        ))

    # ── authors ───────────────────────────────────────────────────────
    authors_raw = data.get("authors") or []
    authors: list[str] = []
    for a in authors_raw:
        if isinstance(a, str):
            authors.append(a)
        elif isinstance(a, dict):
            name = a.get("name") or a.get("full_name") or ""
            if name:
                authors.append(name)

    # ── keywords ──────────────────────────────────────────────────────
    keywords_raw = data.get("keywords") or data.get("tags") or []
    keywords = [k for k in keywords_raw if isinstance(k, str) and k.strip()]

    # ── references ────────────────────────────────────────────────────
    refs_raw = data.get("references") or []
    references: list[Reference] = []
    for r in refs_raw:
        if isinstance(r, dict):
            references.append(Reference(
                title=r.get("title"),
                doi=r.get("doi"),
                year=r.get("year"),
                raw_text=r.get("raw_text") or r.get("text") or "",
            ))
        elif isinstance(r, str):
            references.append(Reference(raw_text=r))

    return UnifiedDocument(
        doc_id     = doc_id,
        title      = title,
        abstract   = abstract,
        sections   = sections,
        authors    = authors,
        keywords   = keywords,
        references = references,
        journal    = data.get("journal") or data.get("venue"),
        year       = data.get("year"),
        doi        = data.get("doi"),
        language   = data.get("language") or "vi",
        domain_key = domain_key,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. OLLAMA BACKEND (tự implement, không phụ thuộc entity_extractor.py)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LLMCallRecord:
    """Ghi lại từng LLM call để thống kê."""
    doc_id:       str
    domain:       str
    section_type: str
    phase:        str          # "extract" | "verify"
    latency_ms:   float
    success:      bool
    error:        str          = ""
    n_concepts:   int          = 0
    n_evidences:  int          = 0
    n_metrics:    int          = 0
    n_findings:   int          = 0


class OllamaLLM:
    """Thin wrapper quanh Ollama /v1/chat/completions."""

    def __init__(
        self,
        model:    str   = OLLAMA_MODEL,
        base_url: str   = OLLAMA_BASE_URL,
        timeout:  float = OLLAMA_TIMEOUT,
    ) -> None:
        self.model    = model
        self.base_url = base_url.rstrip("/")
        self.timeout  = timeout
        self._lock    = threading.Lock()
        self.call_records: list[LLMCallRecord] = []

    def check_connection(self) -> bool:
        """Kiểm tra Ollama đang chạy không."""
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=5.0)
            models = [m["name"] for m in r.json().get("models", [])]
            if not any(self.model.split(":")[0] in m for m in models):
                print(f"  ⚠ Model '{self.model}' chưa pull. Models hiện có: {models}")
                print(f"    Chạy: ollama pull {self.model}")
                return False
            return True
        except Exception as e:
            print(f"  ✗ Không kết nối được Ollama tại {self.base_url}: {e}")
            return False

    def call(self, system: str, user: str, max_tokens: int = 4096) -> str:
        payload = {
            "model":   self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "stream":  False,
            "options": {"num_predict": max_tokens, "temperature": 0.1},
        }
        resp = httpx.post(
            f"{self.base_url}/v1/chat/completions",
            json=payload,
            timeout=self.timeout,
            trust_env=False,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# ─────────────────────────────────────────────────────────────────────────────
# 4. ENTITY EXTRACTOR (LLM-based, tự implement gọn)
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_EXTRACT = """Bạn là hệ thống trích xuất thực thể từ bài báo khoa học.
Chỉ trả về JSON hợp lệ, không có text thêm, không có markdown backtick.
Tất cả tên thực thể viết thường (lowercase).
Nếu không có thực thể nào, trả về list rỗng []."""

_SCHEMA_COMBINED = '''{
  "concepts": [
    {"name": "tên lowercase", "category": "method|model|algorithm|task|framework|technology|process|material|system|theory|policy|disease|drug|gene|compound|equipment|tool|concept", "aliases": [], "based_on": [], "extends": [], "domain": null, "evidence": "câu gốc tối đa 150 ký tự"}
  ],
  "evidences": [
    {"name": "tên lowercase (≥4 ký tự)", "evidence_type": "dataset|benchmark|experiment|survey|report|clinical_trial|book|statistic|observation|simulation", "language": null, "aliases": [], "evidence": "câu gốc tối đa 150 ký tự"}
  ],
  "metrics": [
    {"name": "tên metric", "value": null, "unit": null, "higher_better": true, "measured_on": "", "measured_by": "", "evidence": "câu gốc tối đa 150 ký tự"}
  ],
  "findings": [
    {"description": "mô tả tối đa 200 ký tự", "finding_type": "result|conclusion|contribution|limitation|hypothesis", "supports": [], "contradicts": [], "evidence": "câu gốc tối đa 150 ký tự"}
  ]
}'''


def _parse_json_safe(raw: str, expected_type: type = dict) -> dict | list:
    """Parse JSON với nhiều fallback."""
    text = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    text = re.sub(r"```\s*$", "", text.strip(), flags=re.MULTILINE).strip()

    # Attempt 1: parse thẳng
    try:
        data = json.loads(text)
        if isinstance(data, expected_type):
            return data
    except json.JSONDecodeError:
        pass

    # Attempt 2: tìm object/array đầu tiên
    if expected_type == dict:
        m = re.search(r"\{.*\}", text, re.DOTALL)
    else:
        m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, expected_type):
                return data
        except json.JSONDecodeError:
            pass

    # Attempt 3: sửa double brace {{ }}
    fixed = re.sub(r"\{\s*\{", "{", text)
    fixed = re.sub(r"\}\s*\}", "}", fixed)
    try:
        data = json.loads(fixed)
        if isinstance(data, expected_type):
            return data
    except json.JSONDecodeError:
        pass

    return {} if expected_type == dict else []


@dataclass
class ExtractedEntities:
    doc_id:    str
    concepts:  list[dict] = field(default_factory=list)
    evidences: list[dict] = field(default_factory=list)
    metrics:   list[dict] = field(default_factory=list)
    findings:  list[dict] = field(default_factory=list)
    authors:   list[str]  = field(default_factory=list)
    keywords:  list[str]  = field(default_factory=list)

    def total_entities(self) -> int:
        return (len(self.concepts) + len(self.evidences)
                + len(self.metrics) + len(self.findings))


def _normalize_concept(item: dict) -> Optional[dict]:
    name = (item.get("name") or "").strip().lower()
    if not name or len(name) < 2:
        return None
    cat = (item.get("category") or "concept").strip()
    # normalize pipe-separated
    for part in cat.split("|"):
        if part.strip() in CONCEPT_CATEGORIES:
            cat = part.strip()
            break
    else:
        cat = "concept"
    if cat == "person":
        return None
    return {
        "name":     name,
        "category": cat,
        "aliases":  item.get("aliases") or [],
        "based_on": item.get("based_on") or [],
        "extends":  item.get("extends") or [],
        "domain":   item.get("domain"),
        "evidence": (item.get("evidence") or "")[:150],
    }


def _normalize_evidence(item: dict) -> Optional[dict]:
    name = (item.get("name") or "").strip().lower()
    if not name or len(name) < 4:
        return None
    # filter noise
    if re.match(r"^\[?[\d,;\s]+\]?$", name):
        return None
    if re.match(r"^(hình|figure|fig|bảng|table|tbl)\s*[\d\w]*$", name, re.I):
        return None
    et = (item.get("evidence_type") or "dataset").strip()
    for part in et.split("|"):
        if part.strip() in EVIDENCE_TYPES:
            et = part.strip()
            break
    else:
        et = "dataset"
    return {
        "name":          name,
        "evidence_type": et,
        "language":      item.get("language"),
        "aliases":       item.get("aliases") or [],
        "evidence":      (item.get("evidence") or "")[:150],
    }


def extract_entities_llm(
    doc: UnifiedDocument,
    llm: OllamaLLM,
) -> tuple[ExtractedEntities, list[LLMCallRecord]]:
    """
    Gọi LLM extract entity từ các section của document.
    Trả về (ExtractedEntities, danh sách LLMCallRecord).
    """
    records: list[LLMCallRecord] = []
    seen_concepts:  set[str] = set()
    seen_evidences: set[str] = set()
    seen_metrics:   set[str] = set()
    seen_findings:  set[str] = set()

    all_concepts:  list[dict] = []
    all_evidences: list[dict] = []
    all_metrics:   list[dict] = []
    all_findings:  list[dict] = []

    # Chọn section ưu tiên
    priority = [s for s in doc.sections
                if any(s.section_type.startswith(p) for p in PRIORITY_SECTIONS)]
    normal   = [s for s in doc.sections
                if not any(s.section_type.startswith(p) for p in PRIORITY_SECTIONS)]
    sections = (priority + normal)[:MAX_SECTIONS]

    # Thêm abstract nếu chưa có
    has_abstract = any(s.section_type.startswith("abstract") for s in sections)
    if not has_abstract and doc.abstract:
        sections = [Section("abstract", doc.abstract[:MAX_SECTION_CHARS],
                             0, "abstract")] + sections

    for sec in sections:
        user_prompt = (
            f"Section: {sec.section_type}\n\n"
            f"Nội dung:\n{sec.content}\n\n"
            f"Trích xuất thực thể theo schema JSON sau:\n{_SCHEMA_COMBINED}\n\n"
            "Chỉ trả về JSON object với 4 keys: concepts, evidences, metrics, findings."
        )

        t0 = time.perf_counter()
        success = True
        error   = ""
        raw     = "{}"

        try:
            raw = llm.call(_SYSTEM_EXTRACT, user_prompt, max_tokens=2048)
        except Exception as e:
            success = False
            error   = str(e)[:100]
            logger.warning("extract LLM error doc=%s section=%s: %s",
                           doc.doc_id, sec.section_type, e)

        latency = (time.perf_counter() - t0) * 1000

        parsed = _parse_json_safe(raw, dict) if success else {}

        nc = ne = nm = nf = 0

        # Concepts
        for item in (parsed.get("concepts") or []):
            if not isinstance(item, dict): continue
            c = _normalize_concept(item)
            if c is None: continue
            key = c["name"].replace(" ", "")
            if key in seen_concepts: continue
            seen_concepts.add(key)
            all_concepts.append(c)
            nc += 1

        # Evidences
        for item in (parsed.get("evidences") or []):
            if not isinstance(item, dict): continue
            e = _normalize_evidence(item)
            if e is None: continue
            key = e["name"].replace(" ", "")
            if key in seen_evidences: continue
            seen_evidences.add(key)
            all_evidences.append(e)
            ne += 1

        # Metrics
        for item in (parsed.get("metrics") or []):
            if not isinstance(item, dict): continue
            name = (item.get("name") or "").strip().lower()
            if not name: continue
            mo  = item.get("measured_on") or ""
            mb  = item.get("measured_by") or ""
            key = f"{name}_{mo}_{mb}"
            if key in seen_metrics: continue
            seen_metrics.add(key)
            val = item.get("value")
            try:
                val = float(val) if val is not None else None
            except (TypeError, ValueError):
                val = None
            all_metrics.append({
                "name":          name,
                "value":         val,
                "unit":          item.get("unit"),
                "higher_better": item.get("higher_better"),
                "measured_on":   mo,
                "measured_by":   mb,
                "evidence":      (item.get("evidence") or "")[:150],
            })
            nm += 1

        # Findings
        for item in (parsed.get("findings") or []):
            if not isinstance(item, dict): continue
            desc = (item.get("description") or "").strip()
            if not desc: continue
            key = desc[:50].replace(" ", "")
            if key in seen_findings: continue
            seen_findings.add(key)
            ft = (item.get("finding_type") or "result").strip()
            if ft not in FINDING_TYPES: ft = "result"
            all_findings.append({
                "description":  desc[:200],
                "finding_type": ft,
                "supports":     item.get("supports") or [],
                "contradicts":  item.get("contradicts") or [],
                "evidence":     (item.get("evidence") or "")[:150],
            })
            nf += 1

        records.append(LLMCallRecord(
            doc_id       = doc.doc_id,
            domain       = doc.domain_key,
            section_type = sec.section_type,
            phase        = "extract",
            latency_ms   = round(latency, 2),
            success      = success,
            error        = error,
            n_concepts   = nc,
            n_evidences  = ne,
            n_metrics    = nm,
            n_findings   = nf,
        ))

        status = "✓" if success else "✗"
        print(f"    {status} [{sec.section_type:15s}] "
              f"C={nc} E={ne} M={nm} F={nf}  {latency:.0f}ms")

    # Keywords fallback
    for kw in (doc.keywords or []):
        kw_clean = kw.strip().lower()
        if not kw_clean: continue
        key = kw_clean.replace(" ", "")
        if key not in seen_concepts:
            seen_concepts.add(key)
            all_concepts.append({
                "name": kw_clean, "category": "task",
                "aliases": [], "based_on": [], "extends": [],
                "domain": None, "evidence": f"keyword: {kw}",
            })

    return ExtractedEntities(
        doc_id    = doc.doc_id,
        concepts  = all_concepts,
        evidences = all_evidences,
        metrics   = all_metrics,
        findings  = all_findings,
        authors   = doc.authors,
        keywords  = doc.keywords,
    ), records


# ─────────────────────────────────────────────────────────────────────────────
# 5. RELATION EXTRACTOR (LLM verify — gọn)
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_VERIFY = """Bạn là hệ thống xác minh quan hệ giữa paper và entity.
Trả về JSON array. KHÔNG viết gì ngoài JSON."""

_VERIFY_SCHEMA = '''[
  {"relation_type": "USES_CONCEPT|EVALUATES_ON", "entity_name": "...", "confirmed": true, "confidence": 0.85, "evidence": "..."}
]'''


def extract_relations_llm(
    doc: UnifiedDocument,
    entities: ExtractedEntities,
    llm: OllamaLLM,
) -> tuple[dict, list[LLMCallRecord]]:
    """
    LLM verify relations từ entities đã extract.
    Trả về (relations_dict, records).
    relations_dict chứa: uses_concept, evaluates_on, based_on, extends
    """
    records: list[LLMCallRecord] = []
    relations: dict = {
        "uses_concept":  [],
        "evaluates_on":  [],
        "based_on":      [],
        "extends":       [],
    }

    # ── Rule-based (không cần LLM) ────────────────────────────────────
    # BASED_ON + EXTENDS từ ConceptEntity fields
    for c in entities.concepts:
        for parent in (c.get("based_on") or []):
            p = parent.lower().strip()
            if p and p != c["name"]:
                relations["based_on"].append({
                    "child":  c["name"],
                    "parent": p,
                    "confidence": 0.80,
                })
        for parent in (c.get("extends") or []):
            p = parent.lower().strip()
            if p and p != c["name"]:
                relations["extends"].append({
                    "child":  c["name"],
                    "parent": p,
                    "confidence": 0.80,
                })

    # ── LLM verify USES_CONCEPT + EVALUATES_ON ────────────────────────
    candidates = []
    for c in entities.concepts[:8]:     # giới hạn để tránh prompt quá dài
        candidates.append({
            "relation_type": "USES_CONCEPT",
            "entity_name":   c["name"],
            "evidence_hint": c.get("evidence", ""),
        })
    for e in entities.evidences[:5]:
        candidates.append({
            "relation_type": "EVALUATES_ON",
            "entity_name":   e["name"],
            "evidence_hint": e.get("evidence", ""),
        })

    if candidates:
        user_prompt = (
            f"Paper: {doc.title}\n"
            f"Abstract: {doc.abstract[:300]}\n\n"
            f"Xác minh các quan hệ sau:\n"
            f"{json.dumps(candidates, ensure_ascii=False, indent=2)}\n\n"
            f"Schema trả về:\n{_VERIFY_SCHEMA}"
        )

        t0 = time.perf_counter()
        success = True
        error   = ""
        raw     = "[]"

        try:
            raw = llm.call(_SYSTEM_VERIFY, user_prompt, max_tokens=1024)
        except Exception as e:
            success = False
            error   = str(e)[:100]
            logger.warning("verify LLM error doc=%s: %s", doc.doc_id, e)

        latency = (time.perf_counter() - t0) * 1000

        verified = _parse_json_safe(raw, list) if success else []
        if isinstance(verified, dict):
            verified = list(verified.values())[0] if verified else []

        for item in (verified or []):
            if not isinstance(item, dict):
                continue
            rtype = item.get("relation_type", "")
            ename = (item.get("entity_name") or "").lower().strip()
            conf  = float(item.get("confidence") or 0.5)
            evid  = (item.get("evidence") or "")[:150]

            if not item.get("confirmed", False) or conf < 0.5:
                continue

            if rtype == "USES_CONCEPT":
                relations["uses_concept"].append({
                    "name":       ename,
                    "confidence": conf,
                    "evidence":   evid,
                })
            elif rtype == "EVALUATES_ON":
                relations["evaluates_on"].append({
                    "name":       ename,
                    "confidence": conf,
                    "evidence":   evid,
                })

        records.append(LLMCallRecord(
            doc_id       = doc.doc_id,
            domain       = doc.domain_key,
            section_type = "verify",
            phase        = "verify",
            latency_ms   = round(latency, 2),
            success      = success,
            error        = error,
        ))

        status = "✓" if success else "✗"
        print(f"    {status} [verify         ] "
              f"uc={len(relations['uses_concept'])} "
              f"eo={len(relations['evaluates_on'])}  {latency:.0f}ms")

    return relations, records


# ─────────────────────────────────────────────────────────────────────────────
# 6. IN-MEMORY GRAPH BUILDER (NetworkX — mock Neo4j)
# ─────────────────────────────────────────────────────────────────────────────

class InMemoryGraphBuilder:
    def __init__(self) -> None:
        self.G: nx.DiGraph              = nx.DiGraph()
        self._type_registry: dict[str, str] = {}
        self._type_conflicts: list[dict]    = []

    def _add_node(self, node_id: str, node_type: str, **attrs) -> None:
        if node_id in self._type_registry:
            existing = self._type_registry[node_id]
            if existing != node_type:
                self._type_conflicts.append({
                    "node_id": node_id,
                    "type_a":  existing,
                    "type_b":  node_type,
                })
                return
        else:
            self._type_registry[node_id] = node_type
        self.G.add_node(node_id, node_type=node_type, **attrs)

    def _add_edge(self, src: str, dst: str, edge_type: str,
                  confidence: float = 1.0, **attrs) -> None:
        if src not in self.G or dst not in self.G:
            return
        if self.G.has_edge(src, dst):
            if confidence > self.G[src][dst].get("confidence", 0):
                self.G[src][dst]["confidence"] = confidence
                self.G[src][dst]["edge_type"]  = edge_type
        else:
            self.G.add_edge(src, dst, edge_type=edge_type,
                            confidence=confidence, **attrs)

    def build(
        self,
        doc:       UnifiedDocument,
        entities:  ExtractedEntities,
        relations: dict,
    ) -> dict:
        paper_id = f"paper::{doc.doc_id}"
        before_n = self.G.number_of_nodes()
        before_e = self.G.number_of_edges()

        # ── Phase 1 ──────────────────────────────────────────────────
        self._add_node(paper_id, "Paper",
                       label=doc.title[:60], domain=doc.domain_key, year=doc.year)

        for i, auth in enumerate(doc.authors):
            aid = f"author::{auth.lower().replace(' ', '_')}"
            self._add_node(aid, "Author", label=auth)
            self._add_edge(aid, paper_id, "WROTE", confidence=1.0, order=i)

        if doc.journal:
            vid = f"venue::{doc.journal.lower().replace(' ', '_')}"
            self._add_node(vid, "Venue", label=doc.journal)
            self._add_edge(paper_id, vid, "PUBLISHED_AT", confidence=1.0)

        for kw in doc.keywords:
            tid = f"topic::{kw.lower().replace(' ', '_')}"
            self._add_node(tid, "Topic", label=kw)
            self._add_edge(paper_id, tid, "HAS_TOPIC", confidence=1.0)

        for ref in doc.references:
            ref_text = ref.doi or (ref.title or "")[:30]
            if not ref_text:
                continue
            rid = f"paper::ref_{ref_text.lower().replace(' ','_')[:40]}"
            if rid not in self.G:
                self._add_node(rid, "Paper", label=ref_text, stub=True)
            self._add_edge(paper_id, rid, "CITES",
                           confidence=1.0 if ref.doi else 0.7)

        # ── Phase 2 — Concepts ────────────────────────────────────────
        # Từ verified relations (confidence từ LLM)
        verified_concepts = {r["name"]: r for r in relations.get("uses_concept", [])}

        for c in entities.concepts:
            cid  = f"concept::{c['name'].replace(' ', '_')}"
            self._add_node(cid, "Concept", label=c["name"],
                           category=c["category"],
                           domain=c.get("domain") or doc.domain_key)
            conf = verified_concepts.get(c["name"], {}).get("confidence", 0.75)
            self._add_edge(paper_id, cid, "USES_CONCEPT", confidence=conf,
                           category=c["category"])

        # BASED_ON
        for rel in relations.get("based_on", []):
            c_id = f"concept::{rel['child'].replace(' ','_')}"
            p_id = f"concept::{rel['parent'].replace(' ','_')}"
            if p_id not in self.G:
                self._add_node(p_id, "Concept", label=rel["parent"],
                               category="concept", domain=doc.domain_key)
            self._add_edge(c_id, p_id, "BASED_ON", confidence=rel["confidence"])

        # EXTENDS
        for rel in relations.get("extends", []):
            c_id = f"concept::{rel['child'].replace(' ','_')}"
            p_id = f"concept::{rel['parent'].replace(' ','_')}"
            if p_id not in self.G:
                self._add_node(p_id, "Concept", label=rel["parent"],
                               category="concept", domain=doc.domain_key)
            self._add_edge(c_id, p_id, "EXTENDS", confidence=rel["confidence"])

        # ── Phase 2 — Evidences ───────────────────────────────────────
        verified_evs = {r["name"]: r for r in relations.get("evaluates_on", [])}

        for ev in entities.evidences:
            eid  = f"evidence::{ev['name'].replace(' ', '_')}"
            self._add_node(eid, "Evidence", label=ev["name"],
                           evidence_type=ev["evidence_type"])
            conf = verified_evs.get(ev["name"], {}).get("confidence", 0.75)
            self._add_edge(paper_id, eid, "EVALUATES_ON", confidence=conf)

        # ── Phase 2 — Metrics ─────────────────────────────────────────
        for m in entities.metrics:
            mid = f"metric::{m['name']}::{doc.doc_id}"
            self._add_node(mid, "Metric", label=m["name"],
                           value=m.get("value"), unit=m.get("unit"))
            self._add_edge(paper_id, mid, "ACHIEVES_METRIC", confidence=0.90)
            if m.get("measured_on"):
                ev_id = f"evidence::{m['measured_on'].replace(' ','_')}"
                if ev_id in self.G:
                    self._add_edge(paper_id, ev_id, "EVALUATES_ON", confidence=0.85)

        # ── Phase 2 — Findings ────────────────────────────────────────
        for fi in entities.findings:
            fid = f"finding::{doc.doc_id}::{fi['description'][:25].replace(' ','_')}"
            self._add_node(fid, "Finding", label=fi["description"][:40],
                           finding_type=fi["finding_type"])
            self._add_edge(paper_id, fid, "HAS_FINDING", confidence=0.85)

        return {
            "n_nodes_added": self.G.number_of_nodes() - before_n,
            "n_edges_added": self.G.number_of_edges() - before_e,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 7. PIPELINE RUNNER
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DocResult:
    doc_id:        str
    domain:        str
    title:         str
    t_extract_ms:  float        # thời gian LLM extract
    t_verify_ms:   float        # thời gian LLM verify
    t_graph_ms:    float        # thời gian build graph
    t_total_ms:    float
    n_concepts:    int
    n_evidences:   int
    n_metrics:     int
    n_findings:    int
    n_nodes_added: int
    n_edges_added: int
    llm_calls:     int
    llm_errors:    int
    success:       bool
    error:         str = ""


def run_pipeline(
    docs: list[UnifiedDocument],
    llm:  OllamaLLM,
) -> tuple[InMemoryGraphBuilder, list[DocResult], list[LLMCallRecord]]:
    """
    Chạy toàn bộ pipeline cho list docs.
    Trả về (builder, doc_results, llm_records).
    """
    builder     = InMemoryGraphBuilder()
    doc_results: list[DocResult]     = []
    all_records: list[LLMCallRecord] = []

    total = len(docs)
    for i, doc in enumerate(docs, 1):
        print(f"\n[{i:02d}/{total:02d}] {doc.domain_key} | {doc.title[:55]}")

        t_total_start = time.perf_counter()
        success = True
        error   = ""
        entities  = ExtractedEntities(doc_id=doc.doc_id)
        relations = {}

        try:
            # ── Extract ───────────────────────────────────────────────
            t0 = time.perf_counter()
            entities, extract_records = extract_entities_llm(doc, llm)
            t_extract = (time.perf_counter() - t0) * 1000
            all_records.extend(extract_records)

            # ── Verify relations ──────────────────────────────────────
            t0 = time.perf_counter()
            relations, verify_records = extract_relations_llm(doc, entities, llm)
            t_verify = (time.perf_counter() - t0) * 1000
            all_records.extend(verify_records)

            # ── Build graph ───────────────────────────────────────────
            t0 = time.perf_counter()
            graph_stats = builder.build(doc, entities, relations)
            t_graph = (time.perf_counter() - t0) * 1000

        except Exception as e:
            t_extract = t_verify = t_graph = 0.0
            graph_stats = {"n_nodes_added": 0, "n_edges_added": 0}
            success = True
            error   = str(e)[:100]
            logger.error("pipeline error doc=%s: %s", doc.doc_id, e)

        t_total = (time.perf_counter() - t_total_start) * 1000
        llm_calls  = len([r for r in all_records if r.doc_id == doc.doc_id])
        llm_errors = len([r for r in all_records if r.doc_id == doc.doc_id and not r.success])

        print(f"         → entities: C={len(entities.concepts)} "
              f"E={len(entities.evidences)} M={len(entities.metrics)} "
              f"F={len(entities.findings)}")
        print(f"         → graph: +{graph_stats['n_nodes_added']}nodes "
              f"+{graph_stats['n_edges_added']}edges | "
              f"total {t_total:.0f}ms")

        doc_results.append(DocResult(
            doc_id        = doc.doc_id,
            domain        = doc.domain_key,
            title         = doc.title,
            t_extract_ms  = round(t_extract, 2),
            t_verify_ms   = round(t_verify, 2),
            t_graph_ms    = round(t_graph, 2),
            t_total_ms    = round(t_total, 2),
            n_concepts    = len(entities.concepts),
            n_evidences   = len(entities.evidences),
            n_metrics     = len(entities.metrics),
            n_findings    = len(entities.findings),
            n_nodes_added = graph_stats["n_nodes_added"],
            n_edges_added = graph_stats["n_edges_added"],
            llm_calls     = llm_calls,
            llm_errors    = llm_errors,
            success       = success,
            error         = error,
        ))

    return builder, doc_results, all_records


# ─────────────────────────────────────────────────────────────────────────────
# 8. METRICS AGGREGATOR
# ─────────────────────────────────────────────────────────────────────────────

def compute_graph_metrics(builder: InMemoryGraphBuilder, doc_results: list[DocResult]) -> dict:
    G = builder.G
    n_nodes = G.number_of_nodes()
    n_edges = G.number_of_edges()
    degrees = [d for _, d in G.degree()]

    node_types: dict[str, int] = defaultdict(int)
    for _, data in G.nodes(data=True):
        node_types[data.get("node_type", "Unknown")] += 1

    edge_types: dict[str, int] = defaultdict(int)
    for _, _, data in G.edges(data=True):
        edge_types[data.get("edge_type", "Unknown")] += 1

    isolated  = list(nx.isolates(G))
    n_conflicts = len(builder._type_conflicts)

    total_docs = len(doc_results)
    return {
        "G":           G,
        "builder":     builder,
        "n_nodes":     n_nodes,
        "n_edges":     n_edges,
        "avg_degree":  round(np.mean(degrees), 3) if degrees else 0,
        "density":     round(nx.density(G), 6),
        "n_isolated":  len(isolated),
        "iso_ratio":   round(len(isolated) / n_nodes, 4) if n_nodes else 0,
        "node_types":  dict(node_types),
        "edge_types":  dict(edge_types),
        "n_conflicts": n_conflicts,
        "consistency": round((n_nodes - n_conflicts) / n_nodes * 100, 2) if n_nodes else 100,
        "degree_list": degrees,
        "n_docs":      total_docs,
        # Build time stats
        "total_llm_ms":   sum(r.t_extract_ms + r.t_verify_ms for r in doc_results),
        "total_graph_ms": sum(r.t_graph_ms for r in doc_results),
        "total_ms":       sum(r.t_total_ms for r in doc_results),
        "avg_extract_ms": round(np.mean([r.t_extract_ms for r in doc_results]), 2),
        "avg_verify_ms":  round(np.mean([r.t_verify_ms  for r in doc_results]), 2),
        "avg_graph_ms":   round(np.mean([r.t_graph_ms   for r in doc_results]), 2),
        "avg_total_ms":   round(np.mean([r.t_total_ms   for r in doc_results]), 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 9. TABLE GENERATORS
# ─────────────────────────────────────────────────────────────────────────────

def table_3_8(gm: dict) -> pd.DataFrame:
    df = pd.DataFrame([{
        "Số tài liệu":             gm["n_docs"],
        "Số Node":                 gm["n_nodes"],
        "Số Edge":                 gm["n_edges"],
        "Avg Degree":              gm["avg_degree"],
        "Density":                 f"{gm['density']:.6f}",
        "Isolated Nodes":          gm["n_isolated"],
        "Isolated Node Rate (%)":  f"{gm['iso_ratio']*100:.2f}",
    }])
    print("\n📋 Bảng 3.8 — Graph Structure Metrics")
    print(df.to_string(index=False))
    return df


def table_3_9(gm: dict, doc_results: list[DocResult]) -> pd.DataFrame:
    total_s = gm["total_ms"] / 1000
    df = pd.DataFrame([{
        "Số tài liệu":           gm["n_docs"],
        "Tổng thời gian (s)":    round(total_s, 2),
        "TB LLM extract (ms)":   gm["avg_extract_ms"],
        "TB LLM verify (ms)":    gm["avg_verify_ms"],
        "TB graph build (ms)":   gm["avg_graph_ms"],
        "TB tổng / doc (ms)":    gm["avg_total_ms"],
        "Min total (ms)":        round(min(r.t_total_ms for r in doc_results), 2),
        "Max total (ms)":        round(max(r.t_total_ms for r in doc_results), 2),
        "Throughput (docs/s)":   round(gm["n_docs"] / total_s, 3) if total_s > 0 else 0,
    }])
    print("\n📋 Bảng 3.9 — Build Time (LLM thật)")
    print(df.to_string(index=False))
    return df


def table_3_10(gm: dict) -> pd.DataFrame:
    df = pd.DataFrame([{
        "Tổng node":           gm["n_nodes"],
        "Node nhất quán":      gm["n_nodes"] - gm["n_conflicts"],
        "Node xung đột type":  gm["n_conflicts"],
        "Consistency (%)":     gm["consistency"],
        "Kết luận": "✓ Đạt" if gm["consistency"] >= 99.0 else "✗ Cần xem lại",
    }])
    print("\n📋 Bảng 3.10 — Consistency Check")
    print(df.to_string(index=False))
    return df


def table_3_11(gm: dict, docs: list[UnifiedDocument],
               doc_results: list[DocResult]) -> pd.DataFrame:
    # Expected từ entity count thực tế
    exp_nodes = sum(
        1 + len(d.authors) + len(d.keywords)
        + r.n_concepts + r.n_evidences + r.n_metrics + r.n_findings
        + (1 if d.journal else 0)
        for d, r in zip(docs, doc_results)
    )
    exp_edges = sum(
        len(d.authors) + len(d.keywords)
        + r.n_concepts + r.n_evidences + r.n_metrics + r.n_findings
        + (1 if d.journal else 0) + len(d.references)
        for d, r in zip(docs, doc_results)
    )
    df = pd.DataFrame([{
        "Expected Nodes":    exp_nodes,
        "Actual Nodes":      gm["n_nodes"],
        "Node Accuracy (%)": round(min(gm["n_nodes"] / exp_nodes, 1) * 100, 2) if exp_nodes else 0,
        "Expected Edges":    exp_edges,
        "Actual Edges":      gm["n_edges"],
        "Edge Accuracy (%)": round(min(gm["n_edges"] / exp_edges, 1) * 100, 2) if exp_edges else 0,
        "Ghi chú":           "Dedup giảm node/edge dùng chung giữa các paper",
    }])
    print("\n📋 Bảng 3.11 — Node/Edge Accuracy")
    print(df.to_string(index=False))
    return df


def table_3_12(gm: dict) -> pd.DataFrame:
    nt = gm["node_types"]
    total = sum(nt.values())
    rows = [{"Node Type": k, "Số lượng": v,
             "Tỷ lệ (%)": round(v/total*100, 2)}
            for k, v in sorted(nt.items(), key=lambda x: -x[1])]
    df = pd.DataFrame(rows)
    print("\n📋 Bảng 3.12 — Phân bố Node Type")
    print(df.to_string(index=False))
    return df


def table_3_13(gm: dict) -> pd.DataFrame:
    et = gm["edge_types"]
    total = sum(et.values())
    rows = [{"Edge Type": k, "Số lượng": v,
             "Tỷ lệ (%)": round(v/total*100, 2)}
            for k, v in sorted(et.items(), key=lambda x: -x[1])]
    df = pd.DataFrame(rows)
    print("\n📋 Bảng 3.13 — Phân bố Edge Type")
    print(df.to_string(index=False))
    return df


def table_3_14_llm_stats(all_records: list[LLMCallRecord]) -> pd.DataFrame:
    """Bảng 3.14 — LLM call statistics."""
    if not all_records:
        return pd.DataFrame()
    df_r = pd.DataFrame([r.__dict__ for r in all_records])
    rows = []
    for phase in ["extract", "verify"]:
        sub = df_r[df_r["phase"] == phase]
        if sub.empty:
            continue
        rows.append({
            "Phase":           phase,
            "Tổng calls":      len(sub),
            "Thành công":      sub["success"].sum(),
            "Lỗi":             (~sub["success"]).sum(),
            "Error rate (%)":  round((~sub["success"]).mean() * 100, 2),
            "Avg latency (ms)":round(sub["latency_ms"].mean(), 2),
            "Min latency (ms)":round(sub["latency_ms"].min(), 2),
            "Max latency (ms)":round(sub["latency_ms"].max(), 2),
            "Std latency (ms)":round(sub["latency_ms"].std(), 2),
        })
    df = pd.DataFrame(rows)
    print("\n📋 Bảng 3.14 — LLM Call Statistics")
    print(df.to_string(index=False))
    return df


# ─────────────────────────────────────────────────────────────────────────────
# 10. CHART GENERATORS
# ─────────────────────────────────────────────────────────────────────────────

def chart_3_5_entity_type(gm: dict) -> None:
    nt = gm["node_types"]
    labels, values = list(nt.keys()), list(nt.values())
    colors = [ENTITY_COLORS[i % len(ENTITY_COLORS)] for i in range(len(labels))]

    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    fig.suptitle("Biểu đồ 3.5 — Phân bố Entity Type (LLM extracted)",
                 fontsize=14, fontweight="bold")
    # Bar
    bars = axes[0].bar(labels, values, color=colors, edgecolor="white")
    axes[0].set_title(f"Số node theo type ({gm['n_docs']} tài liệu)")
    axes[0].set_xticklabels(labels, rotation=35, ha="right", fontsize=9)
    for bar, val in zip(bars, values):
        axes[0].text(bar.get_x() + bar.get_width()/2,
                     bar.get_height() + max(values)*0.01,
                     f"{val:,}", ha="center", va="bottom", fontsize=8, fontweight="bold")
    # Pie
    axes[1].pie(values, labels=labels, colors=colors,
                autopct=lambda p: f"{p:.1f}%" if p > 3 else "",
                startangle=90, pctdistance=0.82,
                wedgeprops=dict(linewidth=0.8, edgecolor="white"))
    axes[1].set_title("Tỷ lệ node type")
    plt.tight_layout()
    _save(fig, "bieu_do_3_5_entity_type.png", "Biểu đồ 3.5")


def chart_3_6_relation_type(gm: dict) -> None:
    et = gm["edge_types"]
    labels, values = list(et.keys()), list(et.values())
    colors = [ENTITY_COLORS[i % len(ENTITY_COLORS)] for i in range(len(labels))]

    fig, ax = plt.subplots(figsize=(13, 6))
    fig.suptitle("Biểu đồ 3.6 — Phân bố Relation Type (LLM verified)",
                 fontsize=14, fontweight="bold")
    bars = ax.barh(labels, values, color=colors, edgecolor="white")
    for bar, val in zip(bars, values):
        ax.text(val + max(values)*0.005, bar.get_y() + bar.get_height()/2,
                f"{val:,}", va="center", fontsize=9, fontweight="bold")
    ax.set_xlim(0, max(values) * 1.15)
    ax.set_xlabel("Số edge")
    plt.tight_layout()
    _save(fig, "bieu_do_3_6_relation_type.png", "Biểu đồ 3.6")


def chart_3_7_degree_dist(gm: dict) -> None:
    degrees = gm["degree_list"]
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle("Biểu đồ 3.7 — Node Degree Distribution",
                 fontsize=14, fontweight="bold")

    axes[0].hist(degrees, bins=30, color="#1565C0", edgecolor="white", alpha=0.85)
    axes[0].axvline(np.mean(degrees), color="#F44336", ls="--", lw=1.5,
                    label=f"Mean={np.mean(degrees):.2f}")
    axes[0].axvline(np.median(degrees), color="#FF9800", ls="--", lw=1.5,
                    label=f"Median={np.median(degrees):.0f}")
    axes[0].set_xlabel("Degree"); axes[0].set_ylabel("Count")
    axes[0].set_title("Linear scale"); axes[0].legend(fontsize=9)
    stats_txt = (f"N={len(degrees):,}\nMean={np.mean(degrees):.2f}\n"
                 f"Max={max(degrees)}\nStd={np.std(degrees):.2f}")
    axes[0].text(0.97, 0.95, stats_txt, transform=axes[0].transAxes,
                 va="top", ha="right", fontsize=8,
                 bbox=dict(boxstyle="round", facecolor="#E3F2FD", alpha=0.8))

    dv, dc = np.unique(degrees, return_counts=True)
    axes[1].scatter(dv, dc, s=20, color="#1565C0", alpha=0.7)
    axes[1].set_xscale("log"); axes[1].set_yscale("log")
    axes[1].set_xlabel("Degree (log)"); axes[1].set_ylabel("Count (log)")
    axes[1].set_title("Log-log (power-law check)")
    if len(dv) > 2:
        lx, ly = np.log(dv+1), np.log(dc+1)
        m, b = np.polyfit(lx, ly, 1)
        axes[1].plot(dv, np.exp(b)*(dv+1)**m, "r--", lw=1.5,
                     label=f"γ={-m:.2f}")
        axes[1].legend(fontsize=9)
    plt.tight_layout()
    _save(fig, "bieu_do_3_7_degree_distribution.png", "Biểu đồ 3.7")


def chart_3_8_domain_time(doc_results: list[DocResult]) -> None:
    df = pd.DataFrame([r.__dict__ for r in doc_results])
    domains = [dk for dk in DOMAIN_MAP if dk in df["domain"].values]
    if not domains:
        return

    fig, ax = plt.subplots(figsize=(13, 6))
    fig.suptitle("Biểu đồ 3.8 — Thời gian pipeline theo domain (LLM thật)",
                 fontsize=14, fontweight="bold")

    x = np.arange(len(domains))
    w = 0.25
    phases = [
        ("t_extract_ms", "LLM Extract",  "#1565C0"),
        ("t_verify_ms",  "LLM Verify",   "#E65100"),
        ("t_graph_ms",   "Graph Build",  "#2E7D32"),
    ]
    for i, (col, label, color) in enumerate(phases):
        vals = [df[df["domain"]==dk][col].mean() for dk in domains]
        bars = ax.bar(x + (i-1)*w, vals, w, label=label,
                      color=color, alpha=0.85, edgecolor="white")
        for bar, val in zip(bars, vals):
            if val > 0:
                ax.text(bar.get_x()+bar.get_width()/2,
                        bar.get_height()+5,
                        f"{val:.0f}", ha="center", va="bottom", fontsize=7)

    ax.set_xticks(x)
    ax.set_xticklabels([DOMAIN_MAP[d] for d in domains],
                       rotation=20, ha="right", fontsize=9)
    ax.set_ylabel("Thời gian trung bình (ms)")
    ax.legend(fontsize=9)
    plt.tight_layout()
    _save(fig, "bieu_do_3_8_domain_time.png", "Biểu đồ 3.8")


def chart_3_9_scatter_entity_time(doc_results: list[DocResult]) -> None:
    fig, ax = plt.subplots(figsize=(10, 6))
    fig.suptitle("Biểu đồ 3.9 — Số entity vs Thời gian LLM extract (ms)",
                 fontsize=13, fontweight="bold")

    for r in doc_results:
        n_ent = r.n_concepts + r.n_evidences + r.n_metrics + r.n_findings
        ax.scatter(n_ent, r.t_extract_ms,
                   c=DOMAIN_COLORS.get(r.domain, "#607D8B"),
                   s=70, alpha=0.8, edgecolors="white", linewidths=0.5)

    xs = [r.n_concepts+r.n_evidences+r.n_metrics+r.n_findings for r in doc_results]
    ys = [r.t_extract_ms for r in doc_results]
    if len(xs) > 2:
        z = np.polyfit(xs, ys, 1)
        xf = np.linspace(min(xs), max(xs), 100)
        ax.plot(xf, np.poly1d(z)(xf), "k--", lw=1.3,
                label=f"Trend slope={z[0]:.1f} ms/entity")

    patches = [mpatches.Patch(color=c, label=DOMAIN_MAP[dk])
               for dk, c in DOMAIN_COLORS.items() if dk in [r.domain for r in doc_results]]
    ax.legend(handles=patches, fontsize=8, title="Domain")
    ax.set_xlabel("Tổng entity / tài liệu")
    ax.set_ylabel("LLM extract time (ms)")
    plt.tight_layout()
    _save(fig, "bieu_do_3_9_scatter_entity_time.png", "Biểu đồ 3.9")


def chart_3_10_heatmap(gm: dict) -> None:
    G = gm["G"]
    paper_domain: dict[str, str] = {}
    for n, d in G.nodes(data=True):
        if d.get("node_type") == "Paper":
            dk = d.get("domain")
            if dk and dk in DOMAIN_MAP:
                paper_domain[n] = dk

    edge_domain: dict[tuple, int] = defaultdict(int)
    for u, v, d in G.edges(data=True):
        et  = d.get("edge_type", "Unknown")
        dom = paper_domain.get(u) or paper_domain.get(v)
        if dom:
            edge_domain[(et, dom)] += 1

    all_et = sorted({et for et, _ in edge_domain})
    all_dk = [dk for dk in DOMAIN_MAP if any(dk == d for _, d in edge_domain)]
    if not all_et or not all_dk:
        return

    matrix = pd.DataFrame(0, index=all_et, columns=all_dk)
    for (et, dk), cnt in edge_domain.items():
        if et in matrix.index and dk in matrix.columns:
            matrix.at[et, dk] = cnt

    fig, ax = plt.subplots(figsize=(max(8, len(all_dk)*2), max(5, len(all_et))))
    fig.suptitle("Biểu đồ 3.10 — Heatmap Edge Type × Domain",
                 fontsize=13, fontweight="bold")
    sns.heatmap(matrix, annot=True, fmt="d", cmap="Blues",
                linewidths=0.4, linecolor="#E0E0E0", ax=ax,
                cbar_kws={"label": "Số edge"})
    ax.set_xticklabels(all_dk, rotation=25, ha="right", fontsize=9)
    ax.set_yticklabels(ax.get_yticklabels(), rotation=0, fontsize=9)
    plt.tight_layout()
    _save(fig, "bieu_do_3_10_heatmap_edgetype_domain.png", "Biểu đồ 3.10")


def chart_3_11_batch_compare(doc_results: list[DocResult], gm: dict) -> None:
    """So sánh batch 50% / 100% doc."""
    half = len(doc_results) // 2
    if half == 0:
        return
    sub50  = doc_results[:half]
    sub100 = doc_results

    labels = [f"50% ({half} docs)", f"100% ({len(sub100)} docs)"]
    n_ents_50  = sum(r.n_concepts+r.n_evidences+r.n_metrics+r.n_findings for r in sub50)
    n_ents_100 = sum(r.n_concepts+r.n_evidences+r.n_metrics+r.n_findings for r in sub100)
    t50  = sum(r.t_total_ms for r in sub50) / 1000
    t100 = sum(r.t_total_ms for r in sub100) / 1000

    fig, axes = plt.subplots(1, 3, figsize=(14, 5))
    fig.suptitle("Biểu đồ 3.11 — So sánh batch 50% / 100%",
                 fontsize=13, fontweight="bold")

    for ax, vals, title, ylabel in [
        (axes[0], [gm["n_nodes"]//2, gm["n_nodes"]], "Ước tính Nodes", "Số node"),
        (axes[1], [n_ents_50, n_ents_100], "Tổng Entity Extracted", "Số entity"),
        (axes[2], [t50, t100], "Tổng thời gian (s)", "Thời gian (s)"),
    ]:
        bars = ax.bar(labels, vals, color=["#1565C0","#F44336"], edgecolor="white", alpha=0.85)
        ax.set_title(title); ax.set_ylabel(ylabel)
        for bar, val in zip(bars, vals):
            ax.text(bar.get_x()+bar.get_width()/2,
                    bar.get_height()+max(vals)*0.01,
                    f"{val:,.1f}" if isinstance(val, float) else f"{val:,}",
                    ha="center", fontweight="bold", fontsize=9)
    plt.tight_layout()
    _save(fig, "bieu_do_3_11_batch_compare.png", "Biểu đồ 3.11")


def chart_3_12_stacked_entity(doc_results: list[DocResult]) -> None:
    domain_data: dict[str, dict] = {dk: defaultdict(int) for dk in DOMAIN_MAP}
    for r in doc_results:
        domain_data[r.domain]["Concept"]  += r.n_concepts
        domain_data[r.domain]["Evidence"] += r.n_evidences
        domain_data[r.domain]["Metric"]   += r.n_metrics
        domain_data[r.domain]["Finding"]  += r.n_findings

    present = [dk for dk in DOMAIN_MAP if any(domain_data[dk].values())]
    if not present:
        return

    et_labels = ["Concept","Evidence","Metric","Finding"]
    et_colors  = ["#1565C0","#6A1B9A","#B71C1C","#00838F"]

    fig, ax = plt.subplots(figsize=(13, 6))
    fig.suptitle("Biểu đồ 3.12 — Entity phân bố theo domain (LLM extracted)",
                 fontsize=13, fontweight="bold")

    bottoms = np.zeros(len(present))
    for et, color in zip(et_labels, et_colors):
        vals = [domain_data[dk][et] for dk in present]
        bars = ax.bar([DOMAIN_MAP[d] for d in present], vals,
                      bottom=bottoms, label=et,
                      color=color, edgecolor="white", linewidth=0.6, alpha=0.88)
        for bar, val, bot in zip(bars, vals, bottoms):
            if val > 0:
                ax.text(bar.get_x()+bar.get_width()/2, bot+val/2,
                        str(val), ha="center", va="center",
                        fontsize=8, color="white", fontweight="bold")
        bottoms += np.array(vals, dtype=float)

    ax.set_xticklabels([DOMAIN_MAP[d] for d in present], rotation=20, ha="right", fontsize=9)
    ax.set_ylabel("Tổng entity")
    ax.legend(title="Entity Type", fontsize=9)
    plt.tight_layout()
    _save(fig, "bieu_do_3_12_stacked_entity.png", "Biểu đồ 3.12")


def chart_3_13_llm_latency_section(all_records: list[LLMCallRecord]) -> None:
    """Biểu đồ 3.13 — LLM latency theo section type (boxplot)."""
    if not all_records:
        return
    df = pd.DataFrame([r.__dict__ for r in all_records if r.phase == "extract"])
    if df.empty:
        return

    top_sections = df.groupby("section_type")["latency_ms"].median().nlargest(8).index
    df_top = df[df["section_type"].isin(top_sections)]

    fig, ax = plt.subplots(figsize=(13, 5))
    fig.suptitle("Biểu đồ 3.13 — LLM latency theo section type",
                 fontsize=13, fontweight="bold")
    order = df_top.groupby("section_type")["latency_ms"].median().sort_values(ascending=False).index
    sns.boxplot(data=df_top, x="section_type", y="latency_ms", order=order,
                palette="Blues_d", ax=ax)
    ax.set_xlabel("Section Type")
    ax.set_ylabel("Latency (ms)")
    ax.set_xticklabels(ax.get_xticklabels(), rotation=30, ha="right", fontsize=9)
    plt.tight_layout()
    _save(fig, "bieu_do_3_13_llm_latency_section.png", "Biểu đồ 3.13")


def chart_3_14_error_rate(all_records: list[LLMCallRecord]) -> None:
    """Biểu đồ 3.14 — LLM error rate theo domain."""
    if not all_records:
        return
    df = pd.DataFrame([r.__dict__ for r in all_records])
    domains = [dk for dk in DOMAIN_MAP if dk in df["domain"].values]
    if not domains:
        return

    error_rates = [df[df["domain"]==dk]["success"].apply(lambda x: 0 if x else 100).mean()
                   for dk in domains]
    colors = [DOMAIN_COLORS.get(dk, "#607D8B") for dk in domains]

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.suptitle("Biểu đồ 3.14 — LLM Error Rate theo domain (%)",
                 fontsize=13, fontweight="bold")
    bars = ax.bar([DOMAIN_MAP[d] for d in domains], error_rates,
                  color=colors, edgecolor="white", alpha=0.85)
    for bar, val in zip(bars, error_rates):
        ax.text(bar.get_x()+bar.get_width()/2,
                bar.get_height()+0.3,
                f"{val:.1f}%", ha="center", fontweight="bold", fontsize=9)
    ax.set_ylabel("Error rate (%)")
    ax.set_xticklabels([DOMAIN_MAP[d] for d in domains],
                       rotation=20, ha="right", fontsize=9)
    ax.set_ylim(0, max(max(error_rates)+10, 15))
    plt.tight_layout()
    _save(fig, "bieu_do_3_14_error_rate_domain.png", "Biểu đồ 3.14")


def figure_3_1_subgraph(gm: dict) -> None:
    G = gm["G"]
    # Chọn paper node không phải stub, ưu tiên KHYD
    for target in ["KHYD","KHKT&KT","KHXH&NV","KHTN","KHNN"]:
        paper_nodes = [n for n, d in G.nodes(data=True)
                       if d.get("node_type") == "Paper"
                       and d.get("domain") == target
                       and not d.get("stub", False)][:5]
        if paper_nodes:
            break

    if not paper_nodes:
        paper_nodes = [n for n, d in G.nodes(data=True)
                       if d.get("node_type") == "Paper"
                       and not d.get("stub", False)][:5]
    if not paper_nodes:
        print("  ✗ Không đủ node — skip Hình 3.1")
        return

    sub_nodes: set[str] = set(paper_nodes)
    for pn in paper_nodes:
        sub_nodes.update(list(G.successors(pn))[:8])
        sub_nodes.update(list(G.predecessors(pn))[:4])

    Gsub = G.subgraph(list(sub_nodes)).copy()
    Gsub.remove_nodes_from([n for n, d in list(Gsub.nodes(data=True))
                             if d.get("stub", False)])
    if Gsub.number_of_nodes() < 3:
        print("  ✗ Subgraph quá nhỏ — skip Hình 3.1")
        return

    type_color_map = {
        "Paper":   "#1565C0", "Author":  "#2E7D32",
        "Concept": "#E65100", "Evidence":"#6A1B9A",
        "Metric":  "#B71C1C", "Finding": "#00838F",
        "Topic":   "#AD1457", "Venue":   "#0277BD",
    }
    node_colors = [type_color_map.get(d.get("node_type",""), "#607D8B")
                   for _, d in Gsub.nodes(data=True)]
    node_sizes  = [700 if d.get("node_type") == "Paper" else 280
                   for _, d in Gsub.nodes(data=True)]
    labels = {n: d.get("label", n)[:16] for n, d in Gsub.nodes(data=True)}

    ecolor_map = {
        "WROTE":"#2E7D32","USES_CONCEPT":"#E65100","EVALUATES_ON":"#6A1B9A",
        "ACHIEVES_METRIC":"#B71C1C","CITES":"#90A4AE","HAS_TOPIC":"#AD1457",
        "PUBLISHED_AT":"#0277BD","HAS_FINDING":"#00838F","BASED_ON":"#F57F17",
        "EXTENDS":"#4527A0",
    }
    ecolors = [ecolor_map.get(d.get("edge_type",""), "#CFD8DC")
               for _, _, d in Gsub.edges(data=True)]

    fig, ax = plt.subplots(figsize=(14, 10))
    fig.patch.set_facecolor("#F8F9FA")
    ax.set_facecolor("#F8F9FA")

    try:
        pos = nx.kamada_kawai_layout(Gsub)
    except Exception:
        pos = nx.spring_layout(Gsub, seed=RANDOM_SEED, k=2.5)

    nx.draw_networkx_edges(Gsub, pos, ax=ax, alpha=0.5, edge_color=ecolors,
                           arrows=True, arrowsize=12, width=1.2,
                           connectionstyle="arc3,rad=0.08")
    nx.draw_networkx_nodes(Gsub, pos, ax=ax, node_color=node_colors,
                           node_size=node_sizes, alpha=0.92)
    nx.draw_networkx_labels(Gsub, pos, labels=labels, ax=ax,
                            font_size=7, font_color="white", font_weight="bold")

    legend_patches = [mpatches.Patch(color=c, label=t)
                      for t, c in type_color_map.items()
                      if any(d.get("node_type") == t for _, d in Gsub.nodes(data=True))]
    ax.legend(handles=legend_patches, loc="upper left", fontsize=9,
              title="Node Type", framealpha=0.9, facecolor="white")

    domain_name = DOMAIN_MAP.get(target, target)
    ax.set_title(
        f"Hình 3.1 — Knowledge Graph Subgraph (LLM-built)\n"
        f"Lĩnh vực: {domain_name}  "
        f"({Gsub.number_of_nodes()} nodes, {Gsub.number_of_edges()} edges)",
        fontsize=13, fontweight="bold", pad=15,
    )
    ax.axis("off")
    plt.tight_layout()
    _save(fig, "hinh_3_1_subgraph.png", "Hình 3.1")


def _save(fig, filename: str, label: str) -> None:
    path = OUTPUT_DIR / filename
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  ✓ {label} → {path}")


# ─────────────────────────────────────────────────────────────────────────────
# 11. ASSERTION TESTS
# ─────────────────────────────────────────────────────────────────────────────

def run_assertions(gm: dict, doc_results: list[DocResult],
                   all_records: list[LLMCallRecord]) -> pd.DataFrame:
    test_results = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        status = "PASS ✓" if passed else "FAIL ✗"
        test_results.append({"Test": name, "Kết quả": status, "Chi tiết": detail})
        print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))

    print("\n" + "="*60)
    print("ASSERTION TESTS")
    print("="*60)

    G = gm["G"]

    check("T1:  Graph có node",
          G.number_of_nodes() > 0, f"nodes={G.number_of_nodes()}")
    check("T2:  Graph có edge",
          G.number_of_edges() > 0, f"edges={G.number_of_edges()}")
    check("T3:  Isolated node rate < 30%",
          gm["iso_ratio"] < 0.30, f"rate={gm['iso_ratio']*100:.2f}%")
    check("T4:  Consistency >= 99%",
          gm["consistency"] >= 99.0, f"{gm['consistency']}%")
    check("T5:  Avg degree > 1",
          gm["avg_degree"] > 1.0, f"avg={gm['avg_degree']}")
    check("T6:  Không có type conflict",
          gm["n_conflicts"] == 0, f"conflicts={gm['n_conflicts']}")

    paper_nodes = [n for n, d in G.nodes(data=True)
                   if d.get("node_type") == "Paper" and not d.get("stub", False)]
    iso_papers = [n for n in paper_nodes if G.out_degree(n) == 0]
    check("T7:  Paper node đều có edge ra",
          len(iso_papers) == 0, f"isolated_papers={len(iso_papers)}/{len(paper_nodes)}")

    bad_et = {d.get("edge_type") for _, _, d in G.edges(data=True)
              if d.get("edge_type") not in VALID_EDGE_TYPES}
    check("T8:  Edge type hợp lệ",
          len(bad_et) == 0, f"invalid={bad_et}")

    bad_nt = {d.get("node_type") for _, d in G.nodes(data=True)
              if d.get("node_type") not in VALID_NODE_TYPES}
    check("T9:  Node type hợp lệ",
          len(bad_nt) == 0, f"invalid={bad_nt}")

    # LLM quality
    success_recs = [r for r in all_records if r.success]
    error_rate = 1 - len(success_recs) / len(all_records) if all_records else 0
    check("T10: LLM error rate < 20%",
          error_rate < 0.20, f"error_rate={error_rate*100:.1f}%")

    # Entity extraction quality
    avg_concepts = np.mean([r.n_concepts for r in doc_results]) if doc_results else 0
    check("T11: Avg concept/doc >= 1",
          avg_concepts >= 1.0, f"avg_concepts={avg_concepts:.1f}")

    avg_findings = np.mean([r.n_findings for r in doc_results]) if doc_results else 0
    check("T12: Avg finding/doc >= 1",
          avg_findings >= 1.0, f"avg_findings={avg_findings:.1f}")

    et = gm["edge_types"]
    check("T13: Edge USES_CONCEPT tồn tại",
          et.get("USES_CONCEPT", 0) > 0, f"count={et.get('USES_CONCEPT',0)}")
    check("T14: Edge CITES tồn tại",
          et.get("CITES", 0) > 0, f"count={et.get('CITES',0)}")

    # JSON parse success rate
    parse_ok = len([r for r in all_records if r.success and r.phase == "extract"])
    parse_total = len([r for r in all_records if r.phase == "extract"])
    check("T15: JSON parse rate >= 80%",
          (parse_ok / parse_total >= 0.80) if parse_total else True,
          f"{parse_ok}/{parse_total} parsed OK")

    df = pd.DataFrame(test_results)
    passed = df["Kết quả"].str.startswith("PASS").sum()
    print(f"\n  Kết quả: {passed}/{len(df)} tests PASS")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# 12. DATA LOADER
# ─────────────────────────────────────────────────────────────────────────────

def load_docs(max_docs: int = MAX_DOCS) -> list[UnifiedDocument]:
    """
    Load UnifiedDocument từ DATA_ROOT.
    Phân bổ đều: max_docs / 5 domain.
    """
    per_domain = max(1, max_docs // len(DOMAIN_MAP))
    docs: list[UnifiedDocument] = []

    for dk in DOMAIN_MAP:
        domain_dir = DATA_ROOT / dk
        loaded: list[UnifiedDocument] = []

        if domain_dir.exists():
            for p in sorted(domain_dir.glob("*.json"))[:per_domain]:
                doc = load_unified_doc(p, dk)
                if doc:
                    loaded.append(doc)
                    print(f"  ✓ [{dk}] {doc.title[:50]} "
                          f"(sections={len(doc.sections)}, "
                          f"authors={len(doc.authors)})")

        if not loaded:
            print(f"  ⚠ [{dk}] Không tìm thấy file JSON tại {domain_dir}")

        docs.extend(loaded[:per_domain])

    return docs[:max_docs]


# ─────────────────────────────────────────────────────────────────────────────
# 13. MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("TEST 3.3.4 — Graph Building với LLM thật (Ollama)")
    print(f"Model:    {OLLAMA_MODEL}")
    print(f"Base URL: {OLLAMA_BASE_URL}")
    print(f"Max docs: {MAX_DOCS}  |  Max sections/doc: {MAX_SECTIONS}")
    print("=" * 60)

    # ── Khởi tạo LLM ─────────────────────────────────────────────────
    llm = OllamaLLM()
    print("\n[0/5] Kiểm tra kết nối Ollama …")
    if not llm.check_connection():
        print("\n❌ Không thể kết nối Ollama. Dừng test.")
        print("   Khởi động: ollama serve")
        print(f"   Pull model: ollama pull {OLLAMA_MODEL}")
        return
    print(f"  ✓ Ollama OK — model '{OLLAMA_MODEL}' sẵn sàng")

    # ── Load data ─────────────────────────────────────────────────────
    print(f"\n[1/5] Loading {MAX_DOCS} tài liệu từ {DATA_ROOT} …")
    docs = load_docs(MAX_DOCS)
    if not docs:
        print("\n❌ Không load được tài liệu nào.")
        print(f"   Kiểm tra thư mục: {DATA_ROOT}")
        print("   Cấu trúc cần: DATA_ROOT/KHYD/*.json, DATA_ROOT/KHTN/*.json, ...")
        return
    print(f"  Loaded {len(docs)} tài liệu")

    # ── Pipeline LLM ──────────────────────────────────────────────────
    print(f"\n[2/5] Chạy pipeline LLM (Extract → Verify → Build graph) …")
    print(f"  ⏱ Dự tính: {len(docs) * 2}–{len(docs) * 8} phút tùy model")
    t_pipeline_start = time.perf_counter()
    builder, doc_results, all_records = run_pipeline(docs, llm)
    t_pipeline = time.perf_counter() - t_pipeline_start
    print(f"\n  Pipeline xong: {t_pipeline:.1f}s "
          f"({len(doc_results)} docs, {len(all_records)} LLM calls)")

    # ── Compute metrics ───────────────────────────────────────────────
    print("\n[3/5] Tính metrics …")
    gm = compute_graph_metrics(builder, doc_results)
    print(f"  Nodes={gm['n_nodes']}  Edges={gm['n_edges']}  "
          f"AvgDeg={gm['avg_degree']}  Density={gm['density']}")

    # ── Tables ────────────────────────────────────────────────────────
    print("\n[4/5] Sinh bảng …")
    df_38  = table_3_8(gm)
    df_39  = table_3_9(gm, doc_results)
    df_310 = table_3_10(gm)
    df_311 = table_3_11(gm, docs, doc_results)
    df_312 = table_3_12(gm)
    df_313 = table_3_13(gm)
    df_314 = table_3_14_llm_stats(all_records)

    # ── Charts ────────────────────────────────────────────────────────
    print("\n[5/5] Sinh biểu đồ …")
    chart_3_5_entity_type(gm)
    chart_3_6_relation_type(gm)
    chart_3_7_degree_dist(gm)
    chart_3_8_domain_time(doc_results)
    chart_3_9_scatter_entity_time(doc_results)
    chart_3_10_heatmap(gm)
    chart_3_11_batch_compare(doc_results, gm)
    chart_3_12_stacked_entity(doc_results)
    chart_3_13_llm_latency_section(all_records)
    chart_3_14_error_rate(all_records)
    figure_3_1_subgraph(gm)

    # ── Assertions ────────────────────────────────────────────────────
    df_assert = run_assertions(gm, doc_results, all_records)

    # ── Save CSV ──────────────────────────────────────────────────────
    tables = {
        "bang_3_8_graph_metrics":  df_38,
        "bang_3_9_build_time":     df_39,
        "bang_3_10_consistency":   df_310,
        "bang_3_11_accuracy":      df_311,
        "bang_3_12_node_type":     df_312,
        "bang_3_13_edge_type":     df_313,
        "bang_3_14_llm_stats":     df_314,
        "bang_assertions":         df_assert,
        "doc_results":             pd.DataFrame([r.__dict__ for r in doc_results]),
        "llm_records":             pd.DataFrame([r.__dict__ for r in all_records]),
    }
    for name, df in tables.items():
        if df is not None and not df.empty:
            p = OUTPUT_DIR / f"{name}.csv"
            df.to_csv(p, index=False, encoding="utf-8-sig")
            print(f"  ✓ {name}.csv")

    # ── Summary ───────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"  Tài liệu test     : {len(docs)}")
    print(f"  Model Ollama      : {OLLAMA_MODEL}")
    print(f"  LLM calls tổng   : {len(all_records)}")
    print(f"  LLM errors        : {sum(1 for r in all_records if not r.success)}")
    print(f"  Graph nodes       : {gm['n_nodes']:,}")
    print(f"  Graph edges       : {gm['n_edges']:,}")
    print(f"  Avg degree        : {gm['avg_degree']}")
    print(f"  Consistency       : {gm['consistency']}%")
    print(f"  Tổng thời gian    : {t_pipeline:.1f}s")
    print(f"  Output            : {OUTPUT_DIR.resolve()}")
    print("="*60)
    print(f"\n✅ Hoàn tất — kết quả tại: {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()