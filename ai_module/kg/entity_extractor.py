from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import httpx

from ingestion.schema.document_schema import UnifiedDocument, Section
from ai_module.kg.entities import (
    AuthorEntity,
    ConceptEntity,
    EvidenceEntity,
    MetricEntity,
    FindingEntity,
    PaperEntity,
    ExtractedEntities,
    CONCEPT_CATEGORIES,
    EVIDENCE_TYPES,
    EVIDENCE_TYPE_REDIRECT_TO_CONCEPT,
    normalize_entity_name,
)

logger = logging.getLogger(__name__)


# =============================================================================
# LLM BACKEND — Strategy pattern
# =============================================================================

class LLMBackend(ABC):
    @abstractmethod
    def extract(self, system: str, user: str) -> str:
        """Gọi LLM, trả về response string."""

    @property
    @abstractmethod
    def model(self) -> str:
        """Tên model đang dùng."""


class AnthropicBackend(LLMBackend):
    """Production backend — Claude Sonnet qua Anthropic API."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 8192,
    ) -> None:
        try:
            import anthropic
            self._client = anthropic.Anthropic()
        except ImportError:
            raise ImportError("pip install anthropic>=0.25.0")
        self._model      = model
        self._max_tokens = max_tokens

    @property
    def model(self) -> str:
        return self._model

    def extract(self, system: str, user: str) -> str:
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text


class OllamaBackend(LLMBackend):
    """Local/dev backend — Ollama (Qwen2.5, Llama3, ...)."""

    def __init__(
        self,
        model: str = "qwen2.5:7b",
        base_url: str = "http://localhost:11434",
        timeout: float = 300.0,
        max_tokens: int = 8192, 
    ) -> None:
        self._model    = model
        self._base_url = base_url.rstrip("/")
        self._timeout  = timeout
        self._max_tokens = max_tokens

    @property
    def model(self) -> str:
        return self._model

    def extract(self, system: str, user: str) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "stream": False,
            "options": {"num_predict": self._max_tokens},
        }

        resp = httpx.post(
            f"{self._base_url}/v1/chat/completions",
            json=payload,
            timeout=self._timeout,
            trust_env=False
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# =============================================================================
# AUTHOR PARSER — regex-based, không cần LLM
# =============================================================================

_AUTHOR_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("paren", re.compile(
        r"^(?P<name>[^(]+?)\s*\((?P<affil>[^)]+)\)\s*$"
    )),
    ("comma_dash", re.compile(
        r"^(?P<name>[^,\-]+?)\s*[,\-]\s*(?P<affil>.+)$"
    )),
    ("superscript", re.compile(
        r"^(?P<name>[^\d¹²³⁴⁵]+?)\s*[\d¹²³⁴⁵,]+\s*$"
    )),
]

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}")

_INST_KEYWORDS: dict[str, str] = {
    "university": "university", "đại học": "university", "trường": "university",
    "institute":  "institute",  "viện":    "institute",
    "company":    "company",    "corporation": "company",
    "corp":       "company",    "ltd":     "company",    "co.": "company",
}

_COUNTRY_KEYWORDS: dict[str, str] = {
    "vietnam": "VN", "việt nam": "VN", "vnu": "VN", "hust": "VN",
    "hcmut":   "VN", "đại học": "VN", "đh quốc gia": "VN",
    "học viện": "VN", "usa": "US", "united states": "US",
    "japan":   "JP", "china": "CN", "korea": "KR",
}


def _parse_author_string(raw: str) -> AuthorEntity:
    raw = raw.strip()
    email_match = _EMAIL_RE.search(raw)
    email = email_match.group(0) if email_match else None
    if email:
        raw = raw.replace(email, "").strip(" ,;")

    name        = raw
    affiliation = None

    for _pname, pattern in _AUTHOR_PATTERNS:
        m = pattern.match(raw)
        if m:
            name        = m.group("name").strip()
            affiliation = m.group("affil").strip() if "affil" in pattern.groupindex else None
            break

    inst_type = None
    country   = None
    if affiliation:
        aff_lower = affiliation.lower()
        for kw, itype in _INST_KEYWORDS.items():
            if kw in aff_lower:
                inst_type = itype
                break
        for kw, ccode in _COUNTRY_KEYWORDS.items():
            if kw in aff_lower:
                country = ccode
                break

    return AuthorEntity(
        name=name, affiliation=affiliation, email=email,
        country=country, institution_type=inst_type,
    )


def parse_authors(raw_authors: list[str]) -> list[AuthorEntity]:
    result = []
    for raw in raw_authors:
        if not raw.strip():
            continue
        try:
            result.append(_parse_author_string(raw))
        except Exception:
            logger.warning("parse_authors: không parse được '%s'", raw)
            result.append(AuthorEntity(name=raw.strip()))
    return result


# =============================================================================
# SECTION-WEIGHTED CONFIDENCE
# =============================================================================

_SECTION_CONFIDENCE: dict[str, float] = {
    "method": 0.90, "methodology": 0.90, "approach": 0.88,
    "model":  0.88, "proposed":    0.88,
    "experiment": 0.85, "evaluation": 0.85,
    "result":     0.83, "discussion": 0.80,
    "conclusion": 0.78, "abstract":   0.75,
    "introduction": 0.70, "background": 0.68,
    "review": 0.68, "theory": 0.68, "framework": 0.70,
    "table_caption": 0.76, "figure_caption": 0.74,
    "keywords": 0.65, "other": 0.65,
}
_DEFAULT_CONFIDENCE = 0.65


def _section_confidence(section_type: str) -> float:
    stype = (section_type or "").lower().strip()
    for key, conf in _SECTION_CONFIDENCE.items():
        if stype.startswith(key):
            return conf
    return _DEFAULT_CONFIDENCE


# =============================================================================
# NOISE FILTER
# =============================================================================

_CITATION_NUMBER_RE = re.compile(r"^\s*[\[\(]\s*[\d,;\s]+\s*[\]\)]\s*$")

# [v5.2-4] Figure/table reference noise — "hình 4", "table_1", "figure 2a" không phải evidence
_FIGURE_TABLE_RE = re.compile(
    r"^(hình|figure|fig|bảng|table|tbl|phụ lục|appendix|sơ đồ|chart)\s*[\d_\-\.a-z]*$",
    re.IGNORECASE,
)


def _is_noise_evidence(name: str) -> bool:
    """Trả về True nếu tên evidence là nhiễu."""
    name = name.strip()
    if len(name) < 4:
        return True
    if _CITATION_NUMBER_RE.match(name):
        return True
    if not re.search(r"[a-zA-ZÀ-ỹ]", name):
        return True
    # [v5.2-4] Filter figure/table reference không phải evidence thật
    if _FIGURE_TABLE_RE.match(name):
        return True
    return False


# =============================================================================
# LANGUAGE HELPER
# =============================================================================

def _lang_hint(language: Optional[str]) -> str:
    mapping = {
        "vi": "tiếng Việt", "en": "English",
        "zh": "Chinese",    "ja": "Japanese",
        "ko": "Korean",     "fr": "French", "de": "German",
    }
    return mapping.get((language or "vi").lower().strip(), "tiếng Việt")


# =============================================================================
# CATEGORY / EVIDENCE TYPE HELPERS  [v5.2-2]
# =============================================================================

def _resolve_category(raw: str) -> str:
    """
    Normalize pipe-separated category LLM hay sinh ra.  [v5.2-2]

    Ví dụ:
        "method|algorithm"   → "method"   (phần đầu match CONCEPT_CATEGORIES)
        "model|architecture" → "model"
        "disease|phenomenon" → "disease"
        "study|experiment"   → "concept"  (không phần nào match → fallback)
        "tourism_model"      → "concept"  (không match → fallback)

    Lấy phần đầu tiên trong chuỗi pipe-separated mà nằm trong CONCEPT_CATEGORIES.
    Nếu không có phần nào match → trả về "concept".
    Không raise — ConceptEntity.__post_init__ sẽ warn nếu cần.
    """
    for part in (raw or "").split("|"):
        part = part.strip()
        if part in CONCEPT_CATEGORIES:
            return part
    return "concept"


def _resolve_evidence_type(raw: str) -> str:
    """
    Normalize pipe-separated evidence_type.  [v5.2-2]

    Ví dụ:
        "dataset|benchmark" → "dataset"
        "journal|report"    → "journal"
        "table"             → "dataset"  (không match → fallback)

    Lấy phần đầu tiên match EVIDENCE_TYPES.
    Nếu không match → "dataset" (EvidenceEntity.__post_init__ sẽ warn).
    """
    for part in (raw or "").split("|"):
        part = part.strip()
        if part in EVIDENCE_TYPES:
            return part
    return "dataset"

def _resolve_finding_type(raw: str) -> str:
    """
    Normalize pipe-separated finding_type — tương tự _resolve_category.
    VD: 'result|结果' → 'result'
    """
    from ai_module.kg.entities import FINDING_TYPES
    for part in (raw or "").split("|"):
        part = part.strip()
        if part in FINDING_TYPES:
            return part
    return "result"


# =============================================================================
# LLM PROMPTS
# =============================================================================

_SYSTEM_EXTRACT_TEMPLATE = """Bạn là hệ thống trích xuất thực thể từ bài báo khoa học viết bằng {lang_hint}.
Nhiệm vụ: đọc đoạn văn được cung cấp và trích xuất thực thể theo yêu cầu.
Chỉ trả về JSON hợp lệ, không có text thêm, không có markdown backtick.
Nếu không tìm thấy thực thể nào cho 1 key, trả về list rỗng [].
Tất cả tên thực thể phải viết thường (lowercase).
Lĩnh vực có thể bao gồm: khoa học máy tính, kinh tế, xã hội, y tế, kỹ thuật, môi trường, pháp luật, văn học, v.v."""

# =============================================================================
# [v5.1-1] FIX DOUBLE BRACE BUG
# =============================================================================

_PROMPT_COMBINED_HEADER = (
    "Từ đoạn văn sau (section: {section_type}), hãy trích xuất đồng thời 4 loại thực thể.\n\n"
    "Đoạn văn:\n{content}\n\n"
    "Trả về JSON object với đúng 4 keys sau. Mỗi key là một array:\n"
)

_PROMPT_COMBINED_SCHEMA = '''
{
  "concepts": [
    {
      "name": "tên canonical lowercase",
      "category": "method|model|algorithm|task|framework|architecture|theory|policy|event|organization|regulation|project|contract|financial_instrument|technology|process|material|system|phenomenon|law|indicator|market|equipment|tool|compound|software|concept",
      "aliases":  ["tên khác nếu có"],
      "based_on": ["tên concept mà entity này dùng/phụ thuộc vào"],
      "extends":  ["tên concept mà entity này kế thừa/cải tiến"],
      "domain":   "cs|bio|social|physics|economics|null",
      "evidence": "câu văn gốc chứng minh (tối đa 200 ký tự)"
    }
  ],
  "evidences": [
    {
      "name":          "tên dataset/nguồn lowercase (tối thiểu 4 ký tự, có tên cụ thể)",
      "evidence_type": "dataset|benchmark|corpus|experiment|clinical_trial|survey|census|case_study|observation|simulation|report|software|book|standard|event",
      "language":      "vi|en|multilingual|null",
      "aliases":       ["tên khác nếu có"],
      "evidence":      "câu văn gốc chứng minh (tối đa 200 ký tự)"
    }
  ],
  "metrics": [
    {
      "name":          "tên metric lowercase",
      "value":         null,
      "unit":          "%, score, eV, ... hoặc null",
      "higher_better": true,
      "measured_on":   "tên dataset/evidence mà metric được đo trên đó, hoặc null",
      "measured_by":   "tên method/model thực hiện phép đo, hoặc null",
      "evidence":      "câu văn gốc chứng minh (tối đa 200 ký tự)"
    }
  ],
  "findings": [
    {
      "description":  "mô tả ngắn finding (tối đa 300 ký tự)",
      "finding_type": "result|hypothesis|conclusion|limitation|contribution",
      "supports":     [],
      "contradicts":  [],
      "evidence":     "câu văn gốc chứng minh (tối đa 200 ký tự)"
    }
  ]
}

Lưu ý:
- KHÔNG trích xuất evidence là số citation như [1], [2,3] hoặc tên ngắn hơn 4 ký tự
- KHÔNG trích xuất organization, material, equipment làm evidence — chúng là concept
- Nếu section không có entity nào cho 1 loại, để array rỗng []
- Chỉ trả về JSON object, không có text thêm
'''

_PROMPT_CONCEPTS_FALLBACK_HEADER = (
    "Từ đoạn văn sau (section: {section_type}), trích xuất PHƯƠNG PHÁP, MÔ HÌNH, THUẬT TOÁN, "
    "TÁC VỤ, FRAMEWORK, LÝ THUYẾT, CHÍNH SÁCH, QUY TRÌNH, VẬT LIỆU hoặc KHÁI NIỆM CHUYÊN NGÀNH.\n\n"
    "Đoạn văn:\n{content}\n\n"
    "Trả về JSON array. Mỗi phần tử:\n"
)

_PROMPT_CONCEPTS_FALLBACK_SCHEMA = (
    '{"name": "lowercase", "category": "method|model|algorithm|task|framework|architecture|'
    'theory|policy|regulation|project|contract|financial_instrument|technology|process|material|'
    'system|equipment|tool|compound|software|concept", '
    '"aliases": [], "based_on": [], "extends": [], "domain": "cs|bio|social|physics|economics|null", '
    '"evidence": "tối đa 200 ký tự"}'
)

_PROMPT_EVIDENCES_FALLBACK_HEADER = (
    "Từ đoạn văn sau (section: {section_type}), trích xuất DỮ LIỆU, BỘ DỮ LIỆU, "
    "NGUỒN SỐ LIỆU, BÁO CÁO, SÁCH THAM KHẢO hoặc TIÊU CHUẨN KỸ THUẬT.\n"
    "KHÔNG trích xuất số citation như [1], [2,3] hoặc tên ngắn hơn 4 ký tự.\n"
    "KHÔNG trích xuất organization, material, equipment — chúng là concept.\n\n"
    "Đoạn văn:\n{content}\n\n"
    "Trả về JSON array. Mỗi phần tử:\n"
)

_PROMPT_EVIDENCES_FALLBACK_SCHEMA = (
    '{"name": "lowercase tối thiểu 4 ký tự", '
    '"evidence_type": "dataset|benchmark|corpus|experiment|clinical_trial|survey|census|'
    'case_study|observation|simulation|report|software|book|standard|event", '
    '"language": "vi|en|multilingual|null", "aliases": [], "evidence": "tối đa 200 ký tự"}'
)


def _build_combined_prompt(section_type: str, content: str) -> str:
    header = _PROMPT_COMBINED_HEADER.format(section_type=section_type, content=content)
    return header + _PROMPT_COMBINED_SCHEMA


def _build_concepts_fallback_prompt(section_type: str, content: str) -> str:
    header = _PROMPT_CONCEPTS_FALLBACK_HEADER.format(section_type=section_type, content=content)
    return header + _PROMPT_CONCEPTS_FALLBACK_SCHEMA


def _build_evidences_fallback_prompt(section_type: str, content: str) -> str:
    header = _PROMPT_EVIDENCES_FALLBACK_HEADER.format(section_type=section_type, content=content)
    return header + _PROMPT_EVIDENCES_FALLBACK_SCHEMA


# =============================================================================
# HELPERS
# =============================================================================

def _make_fake_section(content: str, section_type: str) -> Section:
    return Section(name=section_type, content=content, order=0, section_type=section_type)


def _chunk_content(content: str, max_chars: int) -> list[str]:
    if len(content) <= max_chars:
        return [content]
    chunks    = []
    remaining = content
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        chunk       = remaining[:max_chars]
        last_period = chunk.rfind(".")
        if last_period > max_chars * 0.6:
            chunk = chunk[:last_period + 1]
        chunks.append(chunk)
        remaining = remaining[len(chunk):].lstrip()
    return chunks


# =============================================================================
# RESULT ACCUMULATOR — thread-safe merge cho parallel processing
# =============================================================================

class _ExtractionAccumulator:
    """
    Accumulate entity results từ nhiều section/chunk song song.
    Mỗi section worker trả về dict raw items, accumulator dedup và build entity objects.
    """

    def __init__(self, doc_id: Optional[str]) -> None:
        self.doc_id = doc_id
        self.section_results: list[tuple[str, float, dict]] = []

    def add(self, stype: str, conf: float, raw: dict) -> None:
        self.section_results.append((stype, conf, raw))

    def build_entities(self, doc: UnifiedDocument) -> tuple[
        list[ConceptEntity],
        list[EvidenceEntity],
        list[MetricEntity],
        list[FindingEntity],
    ]:
        """Merge tất cả section results, dedup, build entity objects."""
        seen_concepts:  set[str] = set()
        seen_evidences: set[str] = set()
        seen_metrics:   set[str] = set()
        seen_findings:  set[str] = set()

        concepts:  list[ConceptEntity]  = []
        evidences: list[EvidenceEntity] = []
        metrics:   list[MetricEntity]   = []
        findings:  list[FindingEntity]  = []

        for stype, conf, raw in self.section_results:

            # ── Concepts ──────────────────────────────────────────────
            for item in (raw.get("concepts") or []):
                if not isinstance(item, dict):   # [NEW] guard
                    continue
                name = (item.get("name") or "").strip().lower()
                if not name:
                    continue

                # [v5.2-3] Skip entity tên người — LLM hay nhầm author thành Concept
                raw_category = (item.get("category") or "concept").strip()
                if raw_category == "person":
                    logger.debug("build_entities: skip person entity '%s'", name)
                    continue

                # [v5.2-2] Normalize pipe-separated: "method|algorithm" → "method"
                category = _resolve_category(raw_category)

                norm = normalize_entity_name(name)
                if norm in seen_concepts:
                    continue
                seen_concepts.add(norm)
                for alias in (item.get("aliases") or []):
                    seen_concepts.add(normalize_entity_name(alias))

                concepts.append(ConceptEntity(
                    name=name,
                    category=category,
                    aliases=item.get("aliases") or [],
                    based_on=item.get("based_on") or [],
                    extends=item.get("extends") or [],
                    domain=item.get("domain") or None,
                    source_paper=self.doc_id,
                    source_section=stype,
                    confidence=conf,
                    evidence=(item.get("evidence") or "")[:200],
                ))

            # ── Evidences ─────────────────────────────────────────────
            for item in (raw.get("evidences") or []):
                if not isinstance(item, dict):   # [NEW] guard
                    continue
                name = (item.get("name") or "").strip().lower()
                if not name or _is_noise_evidence(name):
                    continue

                # [v5.2-4] Redirect evidence_type thuộc về Concept → skip
                # VD: organization, material, equipment nên là ConceptEntity
                raw_ev_type       = (item.get("evidence_type") or "dataset").strip()
                raw_ev_type_first = raw_ev_type.split("|")[0].strip()
                if raw_ev_type_first in EVIDENCE_TYPE_REDIRECT_TO_CONCEPT:
                    logger.debug(
                        "build_entities: skip evidence '%s' type='%s' — should be Concept",
                        name, raw_ev_type_first,
                    )
                    continue

                # [v5.2-2] Normalize pipe-separated: "dataset|benchmark" → "dataset"
                evidence_type = _resolve_evidence_type(raw_ev_type)

                norm = normalize_entity_name(name)
                if norm in seen_evidences:
                    continue
                seen_evidences.add(norm)
                for alias in (item.get("aliases") or []):
                    seen_evidences.add(normalize_entity_name(alias))

                evidences.append(EvidenceEntity(
                    name=name,
                    evidence_type=evidence_type,
                    language=item.get("language") or None,
                    aliases=item.get("aliases") or [],
                    source_paper=self.doc_id,
                    source_section=stype,
                    confidence=conf,
                    evidence=(item.get("evidence") or "")[:200],
                ))

            # ── Metrics ───────────────────────────────────────────────
            for item in (raw.get("metrics") or []):
                if not isinstance(item, dict):   # [NEW] guard
                    continue
                name = (item.get("name") or "").strip().lower()
                if not name:
                    continue
                measured_on = (item.get("measured_on") or "")
                measured_by = (item.get("measured_by") or "")
                dedup_key   = normalize_entity_name(f"{name}_{measured_on}_{measured_by}")
                if dedup_key in seen_metrics:
                    continue
                seen_metrics.add(dedup_key)

                value_raw = item.get("value")
                try:
                    value = float(value_raw) if value_raw is not None else None
                except (TypeError, ValueError):
                    value = None

                metrics.append(MetricEntity(
                    name=name,
                    value=value,
                    unit=item.get("unit") or None,
                    higher_better=item.get("higher_better"),
                    measured_on=measured_on or None,
                    measured_by=measured_by or None,
                    source_paper=self.doc_id,
                    evidence=(item.get("evidence") or "")[:200],
                ))

            # ── Findings ──────────────────────────────────────────────
            for item in (raw.get("findings") or []):
                if not isinstance(item, dict):   # [NEW] guard
                    continue
                desc = (item.get("description") or "").strip()
                if not desc:
                    continue
                dedup_key = normalize_entity_name(desc[:60])
                if dedup_key in seen_findings:
                    continue
                seen_findings.add(dedup_key)

                findings.append(FindingEntity(
                    description=desc[:300],
                    finding_type=_resolve_finding_type(item.get("finding_type") or "result"),
                    supports=item.get("supports") or [],
                    contradicts=item.get("contradicts") or [],
                    source_paper=self.doc_id,
                    source_section=stype,
                    confidence=conf,
                    evidence=(item.get("evidence") or "")[:200],
                ))

        # Keywords fallback → ConceptEntity(category="task")
        kw_conf = _section_confidence("keywords")
        for kw in (getattr(doc, "keywords", None) or []):
            kw_clean = kw.strip().lower()
            if not kw_clean:
                continue
            norm = normalize_entity_name(kw_clean)
            if norm in seen_concepts:
                continue
            seen_concepts.add(norm)
            concepts.append(ConceptEntity(
                name=kw_clean,
                category="task",
                source_paper=self.doc_id,
                source_section="keywords",
                confidence=kw_conf,
                evidence=f"keyword: {kw}",
            ))

        return concepts, evidences, metrics, findings


# =============================================================================
# ENTITY EXTRACTOR
# =============================================================================

class EntityExtractor:
    """
    Trích xuất thực thể từ UnifiedDocument → ExtractedEntities.

    [v5-1]   Gộp 4 LLM call/chunk → 1 call với combined prompt.
    [v5-2]   Parallel sections bằng ThreadPoolExecutor.
    [v5-3]   Giới hạn _MAX_SECTIONS_PER_DOC sections/doc.
    [v5.1-1] Fix double-brace prompt bug.
    [v5.2-1] Fix Qwen2.5 double-brace output bug — _repair_double_brace.
    [v5.2-2] Normalize pipe-separated category/evidence_type.
    [v5.2-3] Filter person entity.
    [v5.2-4] Redirect evidence type thuộc về Concept.

    Cách dùng:
        llm       = AnthropicBackend()
        extractor = EntityExtractor(llm, max_workers=4)
        entities  = extractor.extract(doc)
    """

    _SKIP_SECTION_TYPES   = {"page_header", "page_footer", "watermark"}
    _MIN_SECTION_CHARS    = 50
    _MAX_SECTIONS_PER_DOC = 8

    _PRIORITY_SECTION_TYPES = {
        "abstract", "method", "methodology", "approach", "model", "proposed",
        "experiment", "evaluation", "result", "conclusion", "discussion",
        "finding", "contribution", "limitation",
    }

    _MAX_RETRIES  = 2
    _RETRY_DELAYS = [1.0, 3.0]

    def __init__(
        self,
        llm: LLMBackend,
        max_section_chars: int = 4000,
        max_workers: int = 4,
    ) -> None:
        self._llm         = llm
        self._max_chars   = max_section_chars
        self._max_workers = max_workers

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def extract(self, doc: UnifiedDocument) -> ExtractedEntities:
        lang = getattr(doc, "language", None) or "vi"
        logger.info("EntityExtractor.extract: title='%s' language='%s'", doc.title, lang)

        paper   = self._build_paper_entity(doc)
        authors = self._extract_authors(doc)
        concepts, evidences, metrics, findings = self._extract_all(doc)

        result = ExtractedEntities(
            paper=paper, authors=authors, concepts=concepts,
            evidences=evidences, metrics=metrics, findings=findings,
        )
        logger.info("EntityExtractor.extract: done — %s", result.summary())
        return result

    # ------------------------------------------------------------------
    # PaperEntity
    # ------------------------------------------------------------------

    def _build_paper_entity(self, doc: UnifiedDocument) -> PaperEntity:
        return PaperEntity(
            doc_id=getattr(doc, "doc_id", None) or doc.title,
            title=doc.title or "Unknown",
            year=getattr(doc, "year", None),
            doi=getattr(doc, "doi", None),
            venue=getattr(doc, "journal", None),
            domain=None,
        )

    # ------------------------------------------------------------------
    # AuthorEntity
    # ------------------------------------------------------------------

    def _extract_authors(self, doc: UnifiedDocument) -> list[AuthorEntity]:
        if not doc.authors:
            logger.warning("_extract_authors: doc.authors rỗng — title='%s'", doc.title)
            return []
        return parse_authors(doc.authors)

    # ------------------------------------------------------------------
    # Core extraction — combined prompt + parallel
    # ------------------------------------------------------------------

    def _extract_all(self, doc: UnifiedDocument) -> tuple[
        list[ConceptEntity],
        list[EvidenceEntity],
        list[MetricEntity],
        list[FindingEntity],
    ]:
        sections      = self._get_sections_prioritized(doc)
        system_prompt = self._make_system_prompt(doc)
        doc_id        = getattr(doc, "doc_id", None)
        accumulator   = _ExtractionAccumulator(doc_id)

        has_abstract = any(
            (s.section_type or "").lower().startswith("abstract") for s in sections
        )
        if not has_abstract and doc.abstract:
            sections = [_make_fake_section(doc.abstract, "abstract")] + list(sections)

        caption_content = self._build_caption_section(doc)
        if caption_content:
            sections = list(sections) + [_make_fake_section(caption_content, "table_caption")]

        def process_section(section: Section) -> tuple[str, float, dict]:
            stype = section.section_type or section.name
            conf  = _section_confidence(stype)
            merged_raw: dict = {"concepts": [], "evidences": [], "metrics": [], "findings": []}

            for chunk in _chunk_content(section.content or "", self._max_chars):
                user_prompt = _build_combined_prompt(section_type=stype, content=chunk)
                raw         = self._call_llm_raw(system_prompt, user_prompt, section_name=stype)
                parsed      = self._parse_json_object(raw, context=stype)

                if not parsed:
                    parsed = self._fallback_extract(chunk, stype, system_prompt)

                for key in ("concepts", "evidences", "metrics", "findings"):
                    merged_raw[key].extend(parsed.get(key) or [])

            return stype, conf, merged_raw

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {executor.submit(process_section, s): s for s in sections}
            for future in as_completed(futures):
                section = futures[future]
                stype   = section.section_type or section.name
                try:
                    stype_result, conf, raw = future.result()
                    accumulator.add(stype_result, conf, raw)
                    logger.debug(
                        "_extract_all: section='%s' — "
                        "concepts=%d evidences=%d metrics=%d findings=%d",
                        stype,
                        len(raw.get("concepts", [])),
                        len(raw.get("evidences", [])),
                        len(raw.get("metrics", [])),
                        len(raw.get("findings", [])),
                    )
                except Exception:
                    logger.exception("_extract_all: section='%s' thất bại — bỏ qua", stype)

        return accumulator.build_entities(doc)

    def _fallback_extract(self, chunk: str, stype: str, system_prompt: str) -> dict:
        """Fallback khi combined JSON parse thất bại — gọi riêng concepts + evidences."""
        logger.warning("_fallback_extract: combined parse thất bại section='%s' — fallback", stype)
        result: dict = {"concepts": [], "evidences": [], "metrics": [], "findings": []}

        user_c = _build_concepts_fallback_prompt(stype, chunk)
        raw_c  = self._call_llm_raw(system_prompt, user_c, section_name=f"fallback_concepts_{stype}")
        result["concepts"] = self._parse_json_list(raw_c, context=f"fallback_concepts_{stype}")

        user_e = _build_evidences_fallback_prompt(stype, chunk)
        raw_e  = self._call_llm_raw(system_prompt, user_e, section_name=f"fallback_evidences_{stype}")
        result["evidences"] = self._parse_json_list(raw_e, context=f"fallback_evidences_{stype}")

        return result

    # ------------------------------------------------------------------
    # Section helpers
    # ------------------------------------------------------------------

    def _get_sections_prioritized(self, doc: UnifiedDocument) -> list[Section]:
        seen_content: set[str] = set()
        priority: list[Section] = []
        normal:   list[Section] = []

        for s in doc.sections:
            content = (s.content or "").strip()
            if len(content) < self._MIN_SECTION_CHARS:
                continue

            stype = (s.section_type or s.name or "").lower().strip()
            if any(stype.startswith(skip) for skip in self._SKIP_SECTION_TYPES):
                continue

            content_key = content[:100]
            if content_key in seen_content:
                continue
            seen_content.add(content_key)

            if any(stype.startswith(p) for p in self._PRIORITY_SECTION_TYPES):
                priority.append(s)
            else:
                normal.append(s)

        result = priority + normal
        if len(result) > self._MAX_SECTIONS_PER_DOC:
            logger.debug(
                "_get_sections_prioritized: cắt %d → %d sections (priority=%d normal=%d)",
                len(result), self._MAX_SECTIONS_PER_DOC, len(priority), len(normal),
            )
            result = result[:self._MAX_SECTIONS_PER_DOC]

        return result

    def _build_caption_section(self, doc: UnifiedDocument) -> str:
        parts: list[str] = []
        for t in (getattr(doc, "tables", None) or []):
            if t.caption and t.caption.strip():
                parts.append(t.caption.strip())
        for f in (getattr(doc, "figures", None) or []):
            if f.caption and f.caption.strip():
                parts.append(f.caption.strip())
        combined = " ".join(parts)
        return combined[:self._max_chars] if combined else ""

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def _make_system_prompt(self, doc: UnifiedDocument) -> str:
        lang = getattr(doc, "language", None) or "vi"
        return _SYSTEM_EXTRACT_TEMPLATE.format(lang_hint=_lang_hint(lang))

    # ------------------------------------------------------------------
    # LLM call helpers
    # ------------------------------------------------------------------

    def _call_llm_raw(self, system: str, user: str, section_name: str = "") -> str:
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                return self._llm.extract(system=system, user=user)
            except Exception as exc:
                if attempt < self._MAX_RETRIES:
                    delay = self._RETRY_DELAYS[attempt]
                    logger.warning(
                        "_call_llm_raw: attempt %d/%d thất bại section='%s', retry %.1fs — %s",
                        attempt + 1, self._MAX_RETRIES + 1, section_name, delay, exc,
                    )
                    time.sleep(delay)
                else:
                    logger.exception(
                        "_call_llm_raw: tất cả %d attempts thất bại section='%s'",
                        self._MAX_RETRIES + 1, section_name,
                    )
        return "{}"

    def _call_llm(
        self,
        prompt_template: str,
        section: Section,
        system_override: str | None = None,
    ) -> str:
        """DEPRECATED — dùng _call_llm_raw() thay thế."""
        user_prompt = prompt_template.format(
            section_type=section.section_type or section.name,
            content=section.content or "",
        )
        system = system_override or _SYSTEM_EXTRACT_TEMPLATE.format(lang_hint="tiếng Việt")
        return self._call_llm_raw(system, user_prompt, section_name=section.name)

    @staticmethod
    def _repair_double_brace(text: str) -> str:
        """
        Sửa lỗi LLM sinh {{ thay vì { trong JSON array elements.
        Dùng cách scan từng char thay vì regex để tránh corrupt nested JSON.
        """
        result = []
        i = 0
        while i < len(text):
            if text[i] == '{':
                # Look-ahead: bỏ qua whitespace, kiểm tra có { tiếp theo không
                j = i + 1
                while j < len(text) and text[j] in ' \t\n\r':
                    j += 1
                if j < len(text) and text[j] == '{':
                    # Double brace — bỏ outer brace + whitespace
                    i = j  # skip đến inner {
                    # Tìm closing }} tương ứng và bỏ outer }
                    # Cần track depth để tìm đúng closing
                    result.append(text[i])
                    i += 1
                    depth = 1
                    while i < len(text) and depth > 0:
                        if text[i] == '{':
                            depth += 1
                        elif text[i] == '}':
                            depth -= 1
                            if depth == 0:
                                # Đây là } đóng inner — bỏ outer } tiếp theo
                                result.append('}')
                                i += 1
                                # Skip whitespace + outer }
                                while i < len(text) and text[i] in ' \t\n\r':
                                    i += 1
                                if i < len(text) and text[i] == '}':
                                    i += 1  # bỏ outer }
                                break
                        result.append(text[i])
                        i += 1
                    continue
            result.append(text[i])
            i += 1
        return ''.join(result)

    @staticmethod
    def _parse_json_object(raw: str, context: str = "") -> dict:
        text = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        text = re.sub(r"```\s*$", "", text.strip(), flags=re.MULTILINE).strip()

        expected = {"concepts", "evidences", "metrics", "findings"}

        def _is_valid_dict(data) -> bool:
            """Kiểm tra data là dict với expected keys, và mỗi value là list of dicts."""
            if not isinstance(data, dict):
                return False
            if not (expected & data.keys()):
                return False
            # [NEW] Validate từng array — mỗi item phải là dict, không phải list
            for key in expected:
                for item in (data.get(key) or []):
                    if not isinstance(item, dict):
                        return False
            return True

        # Attempt 1: parse thẳng
        try:
            data = json.loads(text)
            if _is_valid_dict(data):
                return data
        except json.JSONDecodeError:
            pass

        # Attempt 2: json-repair
        try:
            from json_repair import repair_json
            repaired = repair_json(text, return_objects=False)
            data = json.loads(repaired)
            if _is_valid_dict(data):
                logger.debug("_parse_json_object[%s]: json-repair OK", context)
                return data
        except Exception:
            pass

        # Attempt 3: _repair_double_brace cũ
        try:
            repaired = EntityExtractor._repair_double_brace(text)
            data = json.loads(repaired)
            if _is_valid_dict(data):
                logger.debug("_parse_json_object[%s]: double-brace repair OK", context)
                return data
            if isinstance(data, dict):
                logger.warning(
                    "_parse_json_object[%s]: dict không có expected keys — %s",
                    context, list(data.keys())[:5],
                )
                return {}
        except json.JSONDecodeError:
            pass

        logger.warning("_parse_json_object[%s]: JSON parse error — %s", context, text[:150])
        return {}

    @staticmethod
    def _parse_json_list(raw: str, context: str = "") -> list[dict]:
        text = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        text = re.sub(r"```\s*$", "", text.strip(), flags=re.MULTILINE).strip()

        # Attempt 1: parse thẳng
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for key in ("items", "results", "entities"):
                    if isinstance(data.get(key), list):
                        return data[key]
        except json.JSONDecodeError:
            pass

        # Attempt 2: json-repair
        try:
            from json_repair import repair_json
            repaired = repair_json(text, return_objects=False)
            data = json.loads(repaired)
            if isinstance(data, list):
                logger.debug("_parse_json_list[%s]: json-repair OK", context)
                return data
            if isinstance(data, dict):
                for key in ("items", "results", "entities"):
                    if isinstance(data.get(key), list):
                        return data[key]
        except Exception:
            pass

        # Attempt 3: _repair_double_brace cũ
        try:
            repaired = EntityExtractor._repair_double_brace(text)
            data = json.loads(repaired)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

        logger.warning("_parse_json_list[%s]: JSON error — %s", context, text[:100])
        return []


# =============================================================================
# FACTORY
# =============================================================================

def create_extractor_from_env() -> EntityExtractor:
    """
    Tạo EntityExtractor từ biến môi trường.

    .env:
        LLM_BACKEND=anthropic          # hoặc ollama
        ANTHROPIC_MODEL=claude-sonnet-4-20250514
        OLLAMA_MODEL=qwen2.5:7b
        OLLAMA_BASE_URL=http://localhost:11434
        OLLAMA_TIMEOUT=300
        MAX_WORKERS=4                  # số thread parallel (default: 4)
        MAX_SECTION_CHARS=4000         # độ dài tối đa mỗi chunk (default: 4000)
    """
    import os
    # KG extractor uses its own backend switch to avoid conflicting with
    # global RAG backend (LLM_BACKEND=vllm/transformers).
    backend = os.getenv("KG_LLM_BACKEND", os.getenv("LLM_BACKEND", "anthropic")).lower()

    if backend == "ollama":
        llm = OllamaBackend(
            model=os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            timeout=float(os.getenv("OLLAMA_TIMEOUT", "300")),
        )
    else:
        llm = AnthropicBackend(
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
        )

    max_workers       = int(os.getenv("MAX_WORKERS", "4"))
    max_section_chars = int(os.getenv("MAX_SECTION_CHARS", "4000"))

    logger.info(
        "EntityExtractor: backend=%s model=%s max_workers=%d max_section_chars=%d",
        type(llm).__name__, llm.model, max_workers, max_section_chars,
    )
    return EntityExtractor(llm, max_section_chars=max_section_chars, max_workers=max_workers)
