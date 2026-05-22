from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from ingestion.schema.document_schema import UnifiedDocument
from ai_module.kg.entities import (
    ConceptEntity,
    EvidenceEntity,
    MetricEntity,
    FindingEntity,
    ExtractedEntities,
    normalize_entity_name,
)
from ai_module.kg.entity_extractor import LLMBackend

logger = logging.getLogger(__name__)

_MIN_CITATION_TITLE_LEN  = 20
_LLM_VERIFY_BATCH_SIZE   = 10
_COOCCURRENCE_WINDOW      = 250


# =============================================================================
# RELATION DATACLASSES
# =============================================================================

@dataclass
class WroteRelation:
    """(Author)-[:WROTE {order}]->(Paper)"""
    author_id: str
    paper_id:  str
    order:     int


@dataclass
class PublishedAtRelation:
    """(Paper)-[:PUBLISHED_AT]->(Venue)"""
    paper_id:   str
    venue_name: str
    year:       Optional[int] = None
    venue_type: str           = "journal"
    publisher:  Optional[str] = None
    volume:     Optional[str] = None
    issue:      Optional[str] = None
    pages:      Optional[str] = None


@dataclass
class HasTopicRelation:
    """(Paper)-[:HAS_TOPIC]->(Topic)"""
    paper_id:   str
    topic_name: str
    source:     str = "keyword"


@dataclass
class CitesRelation:
    """(Paper)-[:CITES]->(Paper)"""
    citing_paper_id:  str
    cited_doc_id:     Optional[str] = None   # internal doc_id nếu đã có trong DB
    cited_doi:        Optional[str] = None
    cited_title:      Optional[str] = None
    cited_year:       Optional[int] = None
    raw_ref_text:     str           = ""
    confidence:       float         = 1.0


@dataclass
class UsesConceptRelation:
    """
    (Paper)-[:USES_CONCEPT {category}]->(Concept)
    Thay UsesMethodRelation — dùng chung cho mọi category (method/task/theory/...).
    category: khớp ConceptEntity.category
    """
    paper_id:       str
    concept_name:   str
    category:       str   = "concept"   # ConceptEntity.category
    source_section: str   = "method"
    confidence:     float = 1.0
    evidence:       str   = ""


@dataclass
class EvaluatesOnRelation:
    """(Paper)-[:EVALUATES_ON]->(Evidence)"""
    paper_id:       str
    evidence_name:  str
    evidence_type:  str           = "dataset"   # EvidenceEntity.evidence_type
    source_section: str           = "experiment"
    confidence:     float         = 1.0
    evidence:       str           = ""


@dataclass
class AchievesMetricRelation:
    """
    (Paper)-[:ACHIEVES_METRIC]->(Metric)
    NEW — thay thế metric field trong EvaluatesOnRelation.
    Cho phép link trực tiếp Paper → Metric node trong KG.
    """
    paper_id:    str
    metric_name: str
    value:       Optional[float] = None
    unit:        Optional[str]   = None
    measured_on: Optional[str]   = None   # tên EvidenceEntity
    measured_by: Optional[str]   = None   # tên ConceptEntity
    confidence:  float           = 1.0
    evidence:    str             = ""


@dataclass
class BasedOnRelation:
    """(Concept)-[:BASED_ON]->(Concept)"""
    child_concept:  str
    parent_concept: str
    confidence:     float = 1.0


@dataclass
class ExtendsRelation:
    """
    (Concept)-[:EXTENDS]->(Concept)
    NEW — ConceptEntity.extends → kế thừa / cải tiến.
    """
    child_concept:  str
    parent_concept: str
    confidence:     float = 1.0


@dataclass
class SupportsRelation:
    """
    (Paper)-[:SUPPORTS]->(Paper)
    NEW — từ FindingEntity.supports.
    """
    source_paper_id: str
    target_paper_id: str
    finding_desc:    str   = ""
    confidence:      float = 1.0


@dataclass
class ContradictsFindingRelation:
    """
    (Paper)-[:CONTRADICTS]->(Paper)
    NEW — từ FindingEntity.contradicts.
    """
    source_paper_id: str
    target_paper_id: str
    finding_desc:    str   = ""
    confidence:      float = 1.0


@dataclass
class RelationResult:
    # Giai đoạn 1 — rule-only
    wrote:        list[WroteRelation]        = field(default_factory=list)
    published_at: list[PublishedAtRelation]  = field(default_factory=list)
    has_topic:    list[HasTopicRelation]     = field(default_factory=list)
    cites:        list[CitesRelation]        = field(default_factory=list)

    # Giai đoạn 2 — hybrid
    uses_concept:    list[UsesConceptRelation]    = field(default_factory=list)
    evaluates_on:    list[EvaluatesOnRelation]    = field(default_factory=list)
    achieves_metric: list[AchievesMetricRelation] = field(default_factory=list)
    based_on:        list[BasedOnRelation]        = field(default_factory=list)
    extends:         list[ExtendsRelation]        = field(default_factory=list)

    # Giai đoạn 3 — từ FindingEntity
    supports:    list[SupportsRelation]           = field(default_factory=list)
    contradicts: list[ContradictsFindingRelation] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"wrote={len(self.wrote)} published_at={len(self.published_at)} "
            f"has_topic={len(self.has_topic)} cites={len(self.cites)} "
            f"uses_concept={len(self.uses_concept)} evaluates_on={len(self.evaluates_on)} "
            f"achieves_metric={len(self.achieves_metric)} "
            f"based_on={len(self.based_on)} extends={len(self.extends)} "
            f"supports={len(self.supports)} contradicts={len(self.contradicts)}"
        )


# =============================================================================
# CANDIDATE PAIR — cho LLM verify
# =============================================================================

@dataclass
class _CandidatePair:
    relation_type:   str    # "USES_CONCEPT" | "EVALUATES_ON"
    paper_id:        str
    entity_name:     str
    evidence:        str
    rule_confidence: float
    category:        str = "concept"   # ConceptEntity.category — chỉ dùng cho USES_CONCEPT


# =============================================================================
# VERB PATTERNS — co-occurrence detection
# =============================================================================

_RE_CONCEPT_USE = re.compile(
    r"(?:s[uử][\s]*d[uụ]ng|áp\s+d[uụ]ng|đ[eề]\s+xu[aấ]t|propose[sd]?|use[sd]?|"
    r"apply|appli(?:es|ed)|adopt(?:ed)?|employ(?:ed)?|fine[\s\-]tun(?:e[sd]?|ing)|"
    r"pre[\s\-]train(?:ed)?|implement(?:ed)?)",
    re.IGNORECASE,
)

_RE_EVIDENCE_EVAL = re.compile(
    r"(?:đánh\s+giá|thử\s+nghiệm|thực\s+nghiệm|evaluate[sd]?|experiment(?:ed)?|"
    r"test(?:ed)?|benchmark(?:ed)?|train(?:ed)?\s+on|evaluat(?:e|es|ed|ing)\s+on)",
    re.IGNORECASE,
)

_SECTION_CONFIDENCE: dict[str, float] = {
    "method": 0.90, "methodology": 0.90, "approach": 0.88,
    "model":  0.88, "proposed":    0.88,
    "experiment": 0.85, "evaluation": 0.85,
    "result":     0.83, "discussion": 0.80,
    "conclusion": 0.78, "abstract":   0.75,
    "introduction": 0.70, "background": 0.68,
}
_DEFAULT_CONF = 0.65


def _section_conf(stype: str) -> float:
    s = (stype or "").lower().strip()
    for key, c in _SECTION_CONFIDENCE.items():
        if s.startswith(key):
            return c
    return _DEFAULT_CONF


# =============================================================================
# RULE EXTRACTOR
# =============================================================================

class RuleRelationExtractor:

    _RULE_HIGH = 0.85
    _RULE_LOW  = 0.50

    def extract(
        self,
        doc: UnifiedDocument,
        paper_id: str,
        entities: ExtractedEntities,
        author_id_map: dict[str, str],
    ) -> tuple[RelationResult, list[_CandidatePair]]:
        result     = RelationResult()
        candidates: list[_CandidatePair] = []
        full_text  = self._build_full_text(doc)

        # ── Giai đoạn 1 — rule-only ───────────────────────────────────────

        # WROTE
        for order, author in enumerate(entities.authors):
            author_id = author_id_map.get(author.name)
            if author_id:
                result.wrote.append(WroteRelation(
                    author_id=author_id, paper_id=paper_id, order=order,
                ))

        # PUBLISHED_AT
        journal = getattr(doc, "journal", None)
        if journal:
            result.published_at.append(PublishedAtRelation(
                paper_id=paper_id,
                venue_name=journal,
                year=getattr(doc, "year", None),
                venue_type=self._infer_venue_type(journal),
                publisher=getattr(doc, "publisher", None),
                volume=getattr(doc, "volume", None),
                issue=getattr(doc, "issue", None),
                pages=getattr(doc, "pages", None),
            ))

        # HAS_TOPIC
        for kw in (getattr(doc, "keywords", None) or []):
            kw_norm = kw.lower().strip()
            if kw_norm:
                result.has_topic.append(HasTopicRelation(
                    paper_id=paper_id, topic_name=kw_norm, source="keyword",
                ))

        # CITES — dùng Reference.internal_doc_id nếu có  [v3-1]
        for ref in (getattr(doc, "references", None) or []):
            if not ref.doi and not ref.title:
                continue
            if not ref.doi and ref.title and len(ref.title.strip()) < _MIN_CITATION_TITLE_LEN:
                logger.debug("CITES: bỏ qua title ngắn '%s'", ref.title)
                continue
            confidence = 1.0 if ref.doi else (0.8 if ref.year else 0.6)
            result.cites.append(CitesRelation(
                citing_paper_id=paper_id,
                cited_doc_id=getattr(ref, "internal_doc_id", None),
                cited_doi=getattr(ref, "doi", None),
                cited_title=getattr(ref, "title", None),
                cited_year=getattr(ref, "year", None),
                raw_ref_text=getattr(ref, "raw_text", ""),
                confidence=confidence,
            ))

        # ── Giai đoạn 2 — hybrid ─────────────────────────────────────────

        # USES_CONCEPT — từ entities.concepts  [v3-7, v3-9]
        for concept in entities.concepts:
            conf, evidence = self._score_concept_relation(concept, full_text)
            if conf >= self._RULE_HIGH:
                result.uses_concept.append(UsesConceptRelation(
                    paper_id=paper_id,
                    concept_name=concept.name,
                    category=concept.category,
                    source_section=concept.source_section or "unknown",
                    confidence=conf,
                    evidence=evidence,
                ))
            elif conf >= self._RULE_LOW:
                candidates.append(_CandidatePair(
                    relation_type="USES_CONCEPT",
                    paper_id=paper_id,
                    entity_name=concept.name,
                    evidence=evidence,
                    rule_confidence=conf,
                    category=concept.category,
                ))

        # EVALUATES_ON — từ entities.evidences  [v3-8, v3-9]
        # NOTE: EvidenceEntity không có field metric — metric nằm ở MetricEntity
        for ev in entities.evidences:
            conf, evidence = self._score_evidence_relation(ev, full_text)
            if conf >= self._RULE_HIGH:
                result.evaluates_on.append(EvaluatesOnRelation(
                    paper_id=paper_id,
                    evidence_name=ev.name,
                    evidence_type=ev.evidence_type,
                    source_section=ev.source_section or "unknown",
                    confidence=conf,
                    evidence=evidence,
                ))
            elif conf >= self._RULE_LOW:
                candidates.append(_CandidatePair(
                    relation_type="EVALUATES_ON",
                    paper_id=paper_id,
                    entity_name=ev.name,
                    evidence=evidence,
                    rule_confidence=conf,
                ))

        # ACHIEVES_METRIC — rule-only, confidence từ MetricEntity  [v3-3]
        for metric in entities.metrics:
            result.achieves_metric.append(AchievesMetricRelation(
                paper_id=paper_id,
                metric_name=metric.name,
                value=metric.value,
                unit=metric.unit,
                measured_on=metric.measured_on,
                measured_by=metric.measured_by,
                confidence=0.90,   # extract từ paper text → high confidence
                evidence=metric.evidence,
            ))

        # BASED_ON + EXTENDS — từ ConceptEntity fields  [v3-4]
        # BASED_ON
        for concept in entities.concepts:
            for parent in (concept.based_on or []):
                parent_norm = parent.lower().strip()
                # [NEW] Skip self-loop — LLM hay sinh based_on: ["chính nó"]
                if normalize_entity_name(parent_norm) == normalize_entity_name(concept.name):
                    logger.debug(
                        "RuleRelationExtractor: skip self-loop BASED_ON '%s'", concept.name
                    )
                    continue
                result.based_on.append(BasedOnRelation(
                    child_concept=concept.name,
                    parent_concept=parent_norm,
                    confidence=concept.confidence,
                ))
            # EXTENDS — tương tự
            for parent in (concept.extends or []):
                parent_norm = parent.lower().strip()
                if normalize_entity_name(parent_norm) == normalize_entity_name(concept.name):
                    logger.debug(
                        "RuleRelationExtractor: skip self-loop EXTENDS '%s'", concept.name
                    )
                    continue
                result.extends.append(ExtendsRelation(
                    child_concept=concept.name,
                    parent_concept=parent_norm,
                    confidence=concept.confidence,
                ))

        return result, candidates

    # ── Scoring helpers ───────────────────────────────────────────────

    def _score_concept_relation(
        self, concept: ConceptEntity, full_text: str
    ) -> tuple[float, str]:
        base     = concept.confidence
        evidence = concept.evidence

        _, found = self._find_cooccurrence(full_text, concept.name, _RE_CONCEPT_USE)
        if found:
            evidence = found
            base     = min(1.0, base + 0.10)

        stype = concept.source_section or ""
        if any(stype.startswith(p) for p in ("method", "proposed", "approach")):
            base = min(1.0, base + 0.05)

        return round(base, 3), evidence

    def _score_evidence_relation(
        self, ev: EvidenceEntity, full_text: str
    ) -> tuple[float, str]:
        base     = ev.confidence
        evidence = ev.evidence

        _, found = self._find_cooccurrence(full_text, ev.name, _RE_EVIDENCE_EVAL)
        if found:
            evidence = found
            base     = min(1.0, base + 0.10)

        stype = ev.source_section or ""
        if any(stype.startswith(p) for p in ("experiment", "evaluation", "result")):
            base = min(1.0, base + 0.05)

        return round(base, 3), evidence

    @staticmethod
    def _find_cooccurrence(
        text: str,
        entity_name: str,
        verb_pattern: re.Pattern,
    ) -> tuple[str, str]:
        text_lower   = text.lower()
        entity_lower = entity_name.lower()

        for match in re.finditer(re.escape(entity_lower), text_lower):
            start  = max(0, match.start() - _COOCCURRENCE_WINDOW)
            end    = min(len(text), match.end() + _COOCCURRENCE_WINDOW)
            window = text[start:end]
            if verb_pattern.search(window):
                for line in window.splitlines():
                    if entity_lower in line.lower():
                        return window, line.strip()[:200]
                return window, window[:200]
        return "", ""

    @staticmethod
    def _infer_venue_type(venue_name: str) -> str:
        name_lower = venue_name.lower()
        if any(k in name_lower for k in (
            "conference", "proceedings", "workshop",
            "acl", "emnlp", "naacl", "coling",
        )):
            return "conference"
        if any(k in name_lower for k in ("arxiv", "preprint")):
            return "preprint"
        return "journal"

    @staticmethod
    def _build_full_text(doc: UnifiedDocument) -> str:
        parts = [getattr(doc, "abstract", "") or ""]
        for sec in (getattr(doc, "sections", None) or []):
            parts.append(sec.content or "")
        return "\n".join(p for p in parts if p)


# =============================================================================
# LLM VERIFIER
# =============================================================================

_VERIFY_SYSTEM = """\
Bạn là hệ thống xác minh quan hệ giữa bài báo khoa học và entity.
Với mỗi cặp (paper, entity, relation_type), xác minh relation có tồn tại không.
Trả về JSON array. KHÔNG viết gì ngoài JSON.

Mỗi phần tử:
{
  "relation_type": "USES_CONCEPT|EVALUATES_ON",
  "entity_name":   "tên entity lowercase, giống hệt input",
  "confirmed":     true|false,
  "confidence":    0.0-1.0,
  "evidence":      "câu văn gốc làm bằng chứng, tối đa 200 ký tự"
}
"""

_VERIFY_USER_TEMPLATE = """\
Bài báo: {title}

Tóm tắt: {abstract}

Hãy xác minh các quan hệ sau:
{candidates_json}
"""


@dataclass
class _VerifyResult:
    relation_type: str
    entity_name:   str
    confirmed:     bool
    confidence:    float
    evidence:      str
    category:      str = "concept"   # chỉ dùng cho USES_CONCEPT


class LLMRelationVerifier:

    def __init__(self, llm: LLMBackend, confidence_threshold: float = 0.6) -> None:
        self._llm                  = llm
        self._confidence_threshold = confidence_threshold

    def verify(
        self,
        doc: UnifiedDocument,
        candidates: list[_CandidatePair],
    ) -> list[_VerifyResult]:
        if not candidates:
            return []

        prompt = self._build_prompt(doc, candidates)
        try:
            raw = self._llm.extract(system=_VERIFY_SYSTEM, user=prompt)
            return self._parse_response(raw, candidates)
        except Exception as e:
            logger.warning(
                "LLMRelationVerifier.verify: LLM fail title='%s' — cap confidence: %s",
                doc.title, e,
            )
            return [
                _VerifyResult(
                    relation_type=c.relation_type,
                    entity_name=c.entity_name,
                    confirmed=True,
                    confidence=min(c.rule_confidence, self._confidence_threshold - 0.01),
                    evidence=c.evidence,
                    category=c.category,
                )
                for c in candidates
            ]

    def _build_prompt(self, doc: UnifiedDocument, candidates: list[_CandidatePair]) -> str:
        cands_json = json.dumps(
            [
                {
                    "relation_type": c.relation_type,
                    "entity_name":   c.entity_name,
                    "evidence_hint": c.evidence,
                }
                for c in candidates
            ],
            ensure_ascii=False,
            indent=2,
        )
        return _VERIFY_USER_TEMPLATE.format(
            title=doc.title,
            abstract=(getattr(doc, "abstract", "") or "")[:500],
            candidates_json=cands_json,
        )

    def _parse_response(
        self,
        raw: str,
        candidates: list[_CandidatePair],
    ) -> list[_VerifyResult]:
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"```\s*$", "", cleaned.strip(), flags=re.MULTILINE).strip()

        cand_map = {
            (c.relation_type, normalize_entity_name(c.entity_name)): c
            for c in candidates
        }

        data = None

        # Attempt 1: parse thẳng
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Attempt 2: json-repair
        if data is None:
            try:
                from json_repair import repair_json
                data = json.loads(repair_json(cleaned, return_objects=False))
                logger.debug("LLMRelationVerifier: json-repair OK")
            except Exception:
                pass

        # Attempt 3: extract từng object đã hoàn chỉnh (khi string bị truncate)
        if data is None:
            data = self._extract_partial_json_array(cleaned)
            if data:
                logger.warning(
                    "LLMRelationVerifier: partial extraction %d/%d items",
                    len(data), len(candidates),
                )

        if not data:
            logger.warning(
                "LLMRelationVerifier._parse_response: all attempts failed — fallback"
            )
            return self._fallback_results(candidates)

        if not isinstance(data, list):
            data = [data]

        results: list[_VerifyResult] = []
        for item in data:
            try:
                rel_type    = str(item.get("relation_type", ""))
                entity_name = str(item.get("entity_name", "")).lower().strip()
                confirmed   = bool(item.get("confirmed", False))
                confidence  = float(item.get("confidence", 0.5))
                evidence    = str(item.get("evidence", ""))[:200]

                orig     = cand_map.get((rel_type, normalize_entity_name(entity_name)))
                category = orig.category if orig else "concept"

                results.append(_VerifyResult(
                    relation_type=rel_type,
                    entity_name=entity_name,
                    confirmed=confirmed,
                    confidence=confidence,
                    evidence=evidence,
                    category=category,
                ))
            except Exception as e:
                logger.debug("LLMRelationVerifier: skip malformed item — %s", e)

        return results

    def _fallback_results(self, candidates: list[_CandidatePair]) -> list[_VerifyResult]:
        """Cap confidence về dưới threshold khi parse thất bại hoàn toàn."""
        return [
            _VerifyResult(
                relation_type=c.relation_type,
                entity_name=c.entity_name,
                confirmed=True,
                confidence=min(c.rule_confidence, self._confidence_threshold - 0.01),
                evidence=c.evidence,
                category=c.category,
            )
            for c in candidates
        ]

    @staticmethod
    def _extract_partial_json_array(text: str) -> list:
        """
        Từ JSON array bị truncate giữa chừng,
        lấy những object đã balanced (đóng đủ ngoặc).
        VD: '[{"a":1}, {"b":2}, {"c": "unterm...' → [{"a":1}, {"b":2}]
        """
        results = []
        depth = 0
        start = -1
        for i, ch in enumerate(text):
            if ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start != -1:
                    try:
                        results.append(json.loads(text[start:i + 1]))
                    except json.JSONDecodeError:
                        pass
                    start = -1
        return results


# =============================================================================
# RELATION EXTRACTOR — public API
# =============================================================================

class RelationExtractor:
    """
    Public API — orchestrate Rule → LLM verify → merge RelationResult.

    Cách dùng:
        llm       = AnthropicBackend()
        extractor = RelationExtractor(llm)
        relations = extractor.extract(doc, paper_id, entities, author_id_map)
        # relations: RelationResult → truyền thẳng vào GraphBuilder.build()
    """

    def __init__(self, llm: LLMBackend, confidence_threshold: float = 0.6) -> None:
        self._confidence_threshold = confidence_threshold
        self._rule     = RuleRelationExtractor()
        self._verifier = LLMRelationVerifier(llm, confidence_threshold)

    def extract(
        self,
        doc: UnifiedDocument,
        paper_id: str,
        entities: ExtractedEntities,
        author_id_map: dict[str, str],
    ) -> RelationResult:
        logger.info("RelationExtractor.extract: start paper_id=%s", paper_id)

        rule_result, candidates = self._rule.extract(
            doc, paper_id, entities, author_id_map
        )
        logger.debug(
            "RelationExtractor: rule done — %s | candidates=%d",
            rule_result.summary(), len(candidates),
        )

        if candidates:
            total_batches = -(-len(candidates) // _LLM_VERIFY_BATCH_SIZE)
            for i in range(0, len(candidates), _LLM_VERIFY_BATCH_SIZE):
                batch = candidates[i : i + _LLM_VERIFY_BATCH_SIZE]
                logger.debug(
                    "RelationExtractor: LLM verify batch %d/%d (%d candidates)",
                    i // _LLM_VERIFY_BATCH_SIZE + 1, total_batches, len(batch),
                )
                verified = self._verifier.verify(doc, batch)
                self._merge_verified(rule_result, verified, paper_id, entities)
        else:
            logger.debug("RelationExtractor: không có candidate cần LLM verify")

        logger.info("RelationExtractor.extract: done — %s", rule_result.summary())
        return rule_result

    def _merge_verified(
        self,
        result: RelationResult,
        verified: list[_VerifyResult],
        paper_id: str,
        entities: ExtractedEntities,
    ) -> None:
        existing_concepts  = {normalize_entity_name(r.concept_name)  for r in result.uses_concept}
        existing_evidences = {normalize_entity_name(r.evidence_name) for r in result.evaluates_on}

        for v in verified:
            if not v.confirmed or v.confidence < self._confidence_threshold:
                logger.debug(
                    "RelationExtractor: bỏ qua '%s' %s — conf=%.2f",
                    v.entity_name, v.relation_type, v.confidence,
                )
                continue

            norm = normalize_entity_name(v.entity_name)

            if v.relation_type == "USES_CONCEPT" and norm not in existing_concepts:
                source_sec = next(
                    (c.source_section for c in entities.concepts
                     if normalize_entity_name(c.name) == norm),
                    "unknown",
                )
                result.uses_concept.append(UsesConceptRelation(
                    paper_id=paper_id,
                    concept_name=v.entity_name,
                    category=v.category,
                    source_section=source_sec or "unknown",
                    confidence=v.confidence,
                    evidence=v.evidence,
                ))
                existing_concepts.add(norm)

            elif v.relation_type == "EVALUATES_ON" and norm not in existing_evidences:
                source_sec  = next(
                    (e.source_section  for e in entities.evidences
                     if normalize_entity_name(e.name) == norm),
                    "unknown",
                )
                evidence_type = next(
                    (e.evidence_type for e in entities.evidences
                     if normalize_entity_name(e.name) == norm),
                    "dataset",
                )
                result.evaluates_on.append(EvaluatesOnRelation(
                    paper_id=paper_id,
                    evidence_name=v.entity_name,
                    evidence_type=evidence_type,
                    source_section=source_sec or "unknown",
                    confidence=v.confidence,
                    evidence=v.evidence,
                ))
                existing_evidences.add(norm)
