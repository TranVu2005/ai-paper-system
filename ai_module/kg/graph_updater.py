from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from ingestion.schema.document_schema import UnifiedDocument

from ai_module.kg.entities import (
    ExtractedEntities,
    ConceptEntity,
    EvidenceEntity,
    normalize_entity_name,
)

logger = logging.getLogger(__name__)

# Alias — không định nghĩa lại
_normalize_name = normalize_entity_name


# =============================================================================
# NEO4J CLIENT PROTOCOL
# =============================================================================

@runtime_checkable
class Neo4jClientProtocol(Protocol):

    def run_query(self, cypher: str, params: dict | None = None) -> list[dict]:
        """Chạy Cypher query, trả về list[dict] rows."""
        ...

    def lookup_concept_by_fulltext(
        self, name: str, threshold: float = 0.75
    ) -> Optional[str]:
        """
        Tìm Concept node gần đúng theo name + aliases_text.
        Trả về node name nếu tìm thấy, None nếu không.
        [v3-2] Thay lookup_method_by_fulltext + lookup_task_by_fulltext.
        """
        ...

    def lookup_evidence_by_fulltext(
        self, name: str, threshold: float = 0.75
    ) -> Optional[str]:
        """
        Tìm Evidence node gần đúng.
        [v3-2] Thay lookup_dataset_by_fulltext.
        """
        ...

    def get_paper_by_id(self, paper_id: str) -> Optional[dict]:
        """Trả về Paper node properties theo id."""
        ...

    def set_paper_status(self, paper_id: str, status: str) -> None:
        """Cập nhật processing_status của Paper node."""
        ...


# =============================================================================
# VALUE OBJECTS
# =============================================================================

@dataclass
class MergeResult:
    paper_id:             str
    concepts_merged:      int  = 0   # số cặp Concept node được gộp
    evidences_merged:     int  = 0   # số cặp Evidence node được gộp
    aliases_registered:   int  = 0
    apoc_available:       bool = True


@dataclass
class ProvenanceUpdate:
    node_type:      str    # "Concept" | "Evidence"
    node_name:      str
    paper_id:       str
    source_section: str
    confidence:     float
    evidence:       str


@dataclass
class ConsistencyIssue:
    issue_type:  str
    description: str
    node_type:   str  = ""
    node_name:   str  = ""
    paper_id:    str  = ""
    severity:    str  = "warning"   # "warning" | "error"


_ISSUE_TYPES = {
    "DUPLICATE_EDGE":   "Duplicate edge giữa 2 node",
    "INVERSE_EDGE":     "Edge thuận và nghịch cùng tồn tại",
    "NO_EVIDENCE":      "Entity confidence > threshold nhưng không có evidence",
    "ORPHAN_CONCEPT":   "Concept không có Paper nào link đến",
    "ORPHAN_EVIDENCE":  "Evidence không có Paper nào link đến",
    "LOW_CONFIDENCE":   "Edge confidence dưới ngưỡng tối thiểu",
}


# =============================================================================
# HELPERS
# =============================================================================

def _aliases_to_text(aliases: list[str]) -> str:
    """list alias → pipe-separated string cho Neo4j full-text index."""
    return "|".join(a.strip() for a in aliases if a.strip())


def _text_to_aliases(aliases_text: str) -> list[str]:
    if not aliases_text:
        return []
    return [a.strip() for a in aliases_text.split("|") if a.strip()]


# =============================================================================
# ENTITY MERGER
# =============================================================================

class EntityMerger:
    """
    Phát hiện và gộp Concept/Evidence node trùng nhau.

    Vấn đề: entity_extractor chạy per-section → có thể tạo "phobert" và "pho-bert"
    riêng biệt vì MERGE dùng exact name match.

    Cách hoạt động:
        1. Lấy tất cả Concept/Evidence của paper vừa build
        2. Lookup fulltext → tìm node tương tự đã tồn tại
        3. Nếu tìm thấy → merge 2 node (APOC) hoặc đăng ký alias (fallback)
        4. Nếu không → đăng ký alias mới vào aliases_text

    [fix-2] APOC guard: apoc.refactor.mergeNodes cần APOC plugin.
    Fallback graceful nếu không có APOC (Neo4j Community Edition).
    """

    # [v3-5] Đổi label Method → Concept
    _MERGE_CONCEPT_CYPHER = """
        MATCH (old:Concept {name: $old_name})
        MATCH (new:Concept {name: $new_name})
        WHERE old <> new
        CALL apoc.refactor.mergeNodes([old, new], {
            properties: 'combine',
            mergeRels: true
        }) YIELD node
        SET node.name = $canonical_name
        RETURN node.name AS merged
    """

    # [v3-5] Đổi label Dataset → Evidence
    _MERGE_EVIDENCE_CYPHER = """
        MATCH (old:Evidence {name: $old_name})
        MATCH (new:Evidence {name: $new_name})
        WHERE old <> new
        CALL apoc.refactor.mergeNodes([old, new], {
            properties: 'combine',
            mergeRels: true
        }) YIELD node
        SET node.name = $canonical_name
        RETURN node.name AS merged
    """

    _UPDATE_ALIASES_CYPHER = """
        MATCH (n:{label} {{name: $name}})
        SET n.aliases_text = CASE
            WHEN n.aliases_text IS NULL OR n.aliases_text = ''
                THEN $new_aliases
            WHEN NOT $new_aliases IN split(n.aliases_text, '|')
                THEN n.aliases_text + '|' + $new_aliases
            ELSE n.aliases_text
        END
        RETURN n.name AS name, n.aliases_text AS aliases_text
    """

    _CHECK_APOC_CYPHER = """
        CALL apoc.help('refactor') YIELD name
        WHERE name = 'apoc.refactor.mergeNodes'
        RETURN count(*) AS cnt
    """

    def __init__(self, client: Neo4jClientProtocol) -> None:
        self._client        = client
        self._apoc_available = self._check_apoc()

    def _check_apoc(self) -> bool:
        try:
            rows      = self._client.run_query(self._CHECK_APOC_CYPHER)
            available = bool(rows and rows[0].get("cnt", 0) > 0)
            if not available:
                logger.warning(
                    "EntityMerger: APOC không available — node merge bị skip, "
                    "chỉ alias registration. Thêm APOC jar vào Neo4j plugins/ để enable."
                )
            else:
                logger.debug("EntityMerger: APOC available — node merge enabled.")
            return available
        except Exception as e:
            logger.warning(
                "EntityMerger: không kiểm tra được APOC (%s) — giả định không có.", e
            )
            return False

    def merge_entities(
        self,
        paper_id: str,
        entities: ExtractedEntities,
    ) -> MergeResult:
        """Gộp Concept + Evidence duplicate cho 1 paper vừa build."""
        result = MergeResult(paper_id=paper_id, apoc_available=self._apoc_available)

        # [v3-3] entities.concepts thay entities.methods + tasks
        result.concepts_merged, result.aliases_registered = self._merge_entity_list(
            entities=entities.concepts,
            lookup_fn=self._client.lookup_concept_by_fulltext,
            merge_cypher=self._MERGE_CONCEPT_CYPHER,
            label="Concept",
        )

        # [v3-3] entities.evidences thay entities.datasets
        result.evidences_merged, aliases_ev = self._merge_entity_list(
            entities=entities.evidences,
            lookup_fn=self._client.lookup_evidence_by_fulltext,
            merge_cypher=self._MERGE_EVIDENCE_CYPHER,
            label="Evidence",
        )
        result.aliases_registered += aliases_ev

        logger.info(
            "EntityMerger: paper_id=%s concepts_merged=%d evidences_merged=%d "
            "aliases=%d apoc=%s",
            paper_id,
            result.concepts_merged,
            result.evidences_merged,
            result.aliases_registered,
            "yes" if self._apoc_available else "no (alias only)",
        )
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _merge_entity_list(
        self,
        entities: list,
        lookup_fn,
        merge_cypher: str,
        label: str,
    ) -> tuple[int, int]:
        merged  = 0
        aliases = 0
        seen_normalized: set[str] = set()

        for entity in entities:
            norm = _normalize_name(entity.name)
            if norm in seen_normalized:
                continue
            seen_normalized.add(norm)

            existing_name = lookup_fn(entity.name, threshold=0.75)

            if existing_name and existing_name != entity.name:
                if self._apoc_available:
                    try:
                        self._client.run_query(
                            merge_cypher,
                            params={
                                "old_name":       entity.name,
                                "new_name":       existing_name,
                                "canonical_name": existing_name,
                            },
                        )
                        merged += 1
                        logger.debug(
                            "EntityMerger[%s]: merged '%s' → '%s'",
                            label, entity.name, existing_name,
                        )
                    except Exception:
                        logger.exception(
                            "EntityMerger: merge thất bại '%s' → '%s'",
                            entity.name, existing_name,
                        )
                else:
                    # APOC không có → chỉ đăng ký alias
                    logger.debug(
                        "EntityMerger[%s]: APOC skip — alias '%s' → '%s'",
                        label, entity.name, existing_name,
                    )
                    self._register_alias(label=label, canonical=existing_name, alias=entity.name)
                    aliases += 1
            else:
                # Không có node match → đăng ký alias từ entity
                for alias in getattr(entity, "aliases", []):
                    if alias and _normalize_name(alias) != norm:
                        self._register_alias(label=label, canonical=entity.name, alias=alias)
                        aliases += 1

        return merged, aliases

    def _register_alias(self, label: str, canonical: str, alias: str) -> None:
        cypher = self._UPDATE_ALIASES_CYPHER.format(label=label)
        try:
            self._client.run_query(
                cypher,
                params={"name": canonical, "new_aliases": alias.strip()},
            )
        except Exception:
            logger.warning(
                "EntityMerger._register_alias: thất bại label=%s name=%s alias=%s",
                label, canonical, alias,
            )


# =============================================================================
# PROVENANCE UPDATER
# =============================================================================

class ProvenanceUpdater:
    """
    Đảm bảo mỗi edge có đủ provenance: source_paper, source_section, confidence, evidence.

    [fix-3] / [v3-7] Confidence-aware SET:
        Chỉ overwrite evidence khi confidence mới >= hiện tại.
        EvidenceEntity không có field metric → bỏ r.metric trên EVALUATES_ON edge.
        Metric nằm trên ACHIEVES_METRIC edge riêng — không cần update ở đây.
    """

    # [v3-6] USES_CONCEPT thay USES_METHOD, (c:Concept) thay (m:Method)
    _UPDATE_CONCEPT_EDGE = """
        MATCH (p:Paper {id: $paper_id})-[r:USES_CONCEPT]->(c:Concept {name: $concept_name})
        SET r.source_paper   = $source_paper,
            r.source_section = $source_section,
            r.confidence     = CASE WHEN $confidence >= r.confidence
                               THEN $confidence ELSE r.confidence END,
            r.evidence       = CASE WHEN $confidence >= r.confidence
                               THEN $evidence   ELSE r.evidence   END
        RETURN r.confidence AS confidence
    """

    # [v3-6] (e:Evidence) thay (d:Dataset), [v3-7] bỏ r.metric
    _UPDATE_EVIDENCE_EDGE = """
        MATCH (p:Paper {id: $paper_id})-[r:EVALUATES_ON]->(e:Evidence {name: $evidence_name})
        SET r.source_paper   = $source_paper,
            r.source_section = $source_section,
            r.confidence     = CASE WHEN $confidence >= r.confidence
                               THEN $confidence ELSE r.confidence END,
            r.evidence       = CASE WHEN $confidence >= r.confidence
                               THEN $evidence   ELSE r.evidence   END
        RETURN r.confidence AS confidence
    """

    # NOTE: ACHIEVES_METRIC edge — không cần update provenance ở đây
    # vì Metric được extract từ text với context rõ ràng (value, unit, measured_on/by)
    # ProvenanceUpdater chỉ cần update USES_CONCEPT + EVALUATES_ON

    def __init__(self, client: Neo4jClientProtocol) -> None:
        self._client = client

    def update_provenance(
        self,
        paper_id: str,
        entities: ExtractedEntities,
    ) -> list[ProvenanceUpdate]:
        """Update provenance trên USES_CONCEPT + EVALUATES_ON edges."""
        updates: list[ProvenanceUpdate] = []

        # [v3-3] entities.concepts thay entities.methods + tasks
        for concept in entities.concepts:
            ok = self._update_edge(
                cypher=self._UPDATE_CONCEPT_EDGE,
                params={
                    "paper_id":       paper_id,
                    "concept_name":   concept.name,
                    "source_paper":   paper_id,
                    "source_section": concept.source_section or "unknown",
                    "confidence":     concept.confidence,
                    "evidence":       (concept.evidence or "")[:200],
                },
            )
            if ok:
                updates.append(ProvenanceUpdate(
                    node_type="Concept",
                    node_name=concept.name,
                    paper_id=paper_id,
                    source_section=concept.source_section or "unknown",
                    confidence=concept.confidence,
                    evidence=concept.evidence,
                ))

        # [v3-3] entities.evidences thay entities.datasets
        # [v3-7] không có metric field trên EvidenceEntity
        for ev in entities.evidences:
            ok = self._update_edge(
                cypher=self._UPDATE_EVIDENCE_EDGE,
                params={
                    "paper_id":       paper_id,
                    "evidence_name":  ev.name,
                    "source_paper":   paper_id,
                    "source_section": ev.source_section or "unknown",
                    "confidence":     ev.confidence,
                    "evidence":       (ev.evidence or "")[:200],
                },
            )
            if ok:
                updates.append(ProvenanceUpdate(
                    node_type="Evidence",
                    node_name=ev.name,
                    paper_id=paper_id,
                    source_section=ev.source_section or "unknown",
                    confidence=ev.confidence,
                    evidence=ev.evidence,
                ))

        logger.info(
            "ProvenanceUpdater: paper_id=%s updated %d edges",
            paper_id, len(updates),
        )
        return updates

    def _update_edge(self, cypher: str, params: dict) -> bool:
        try:
            rows = self._client.run_query(cypher, params)
            return bool(rows)
        except Exception:
            logger.exception(
                "ProvenanceUpdater._update_edge: thất bại params=%s",
                {k: v for k, v in params.items() if k != "evidence"},
            )
            return False


# =============================================================================
# CONSISTENCY CHECKER
# =============================================================================

class ConsistencyChecker:
    """
    Kiểm tra tính nhất quán graph sau build + merge + provenance.

    Checks:
        DUPLICATE_EDGE  — cùng (Paper, Concept/Evidence) có 2+ edge
        INVERSE_EDGE    — Concept A BASED_ON B và B BASED_ON A cùng tồn tại
        NO_EVIDENCE     — edge confidence > threshold nhưng evidence rỗng
        ORPHAN_CONCEPT  — Concept không có Paper nào link đến
        ORPHAN_EVIDENCE — Evidence không có Paper nào link đến
        LOW_CONFIDENCE  — edge confidence < min_confidence

    Không auto-fix — chỉ report.
    """

    # [v3-8] Đổi label/edge type toàn bộ
    _CHECK_DUPLICATE_CONCEPT_EDGE = """
        MATCH (p:Paper {id: $paper_id})-[r:USES_CONCEPT]->(c:Concept)
        WITH p, c, count(r) AS cnt
        WHERE cnt > 1
        RETURN c.name AS name, cnt
    """

    _CHECK_DUPLICATE_EVIDENCE_EDGE = """
        MATCH (p:Paper {id: $paper_id})-[r:EVALUATES_ON]->(e:Evidence)
        WITH p, e, count(r) AS cnt
        WHERE cnt > 1
        RETURN e.name AS name, cnt
    """

    _CHECK_INVERSE_BASED_ON = """
        MATCH (a:Concept)-[:BASED_ON]->(b:Concept)-[:BASED_ON]->(a)
        RETURN a.name AS concept_a, b.name AS concept_b
        LIMIT 50
    """

    _CHECK_NO_EVIDENCE_CONCEPT = """
        MATCH (p:Paper {id: $paper_id})-[r:USES_CONCEPT]->(c:Concept)
        WHERE r.confidence > $confidence_threshold
          AND (r.evidence IS NULL OR r.evidence = '')
        RETURN c.name AS name, r.confidence AS confidence
    """

    _CHECK_NO_EVIDENCE_EVIDENCE = """
        MATCH (p:Paper {id: $paper_id})-[r:EVALUATES_ON]->(e:Evidence)
        WHERE r.confidence > $confidence_threshold
          AND (r.evidence IS NULL OR r.evidence = '')
        RETURN e.name AS name, r.confidence AS confidence
    """

    # [v3-9] ORPHAN_CONCEPT thay ORPHAN_METHOD
    _CHECK_ORPHAN_CONCEPT = """
        MATCH (c:Concept)
        WHERE NOT EXISTS { (p:Paper)-[:USES_CONCEPT]->(c) }
        RETURN c.name AS name
        LIMIT 100
    """

    # [v3-9] ORPHAN_EVIDENCE thay ORPHAN_DATASET
    _CHECK_ORPHAN_EVIDENCE = """
        MATCH (e:Evidence)
        WHERE NOT EXISTS { (p:Paper)-[:EVALUATES_ON]->(e) }
        RETURN e.name AS name
        LIMIT 100
    """

    _CHECK_LOW_CONFIDENCE_CONCEPT = """
        MATCH (p:Paper {id: $paper_id})-[r:USES_CONCEPT]->(c:Concept)
        WHERE r.confidence < $min_confidence
        RETURN c.name AS name, r.confidence AS confidence
    """

    def __init__(
        self,
        client: Neo4jClientProtocol,
        min_confidence: float = 0.5,
        evidence_confidence_threshold: float = 0.75,
    ) -> None:
        self._client            = client
        self._min_confidence    = min_confidence
        self._evidence_threshold = evidence_confidence_threshold

    def check_consistency(
        self,
        paper_id: str,
        check_orphans: bool = False,
    ) -> list[ConsistencyIssue]:
        """
        Chạy tất cả check cho paper_id.
        check_orphans=True chỉ dùng cho maintenance job (global query, tốn kém).
        """
        issues: list[ConsistencyIssue] = []

        issues.extend(self._check_duplicate_edges(paper_id))
        issues.extend(self._check_inverse_based_on())
        issues.extend(self._check_no_evidence(paper_id))
        issues.extend(self._check_low_confidence(paper_id))

        if check_orphans:
            issues.extend(self._check_orphans())

        if issues:
            errors   = sum(1 for i in issues if i.severity == "error")
            warnings = sum(1 for i in issues if i.severity == "warning")
            logger.warning(
                "ConsistencyChecker: paper_id=%s — %d issues (error=%d warning=%d)",
                paper_id, len(issues), errors, warnings,
            )
        else:
            logger.info("ConsistencyChecker: paper_id=%s — OK", paper_id)

        return issues

    # ------------------------------------------------------------------
    # Private check methods
    # ------------------------------------------------------------------

    def _check_duplicate_edges(self, paper_id: str) -> list[ConsistencyIssue]:
        issues = []
        params = {"paper_id": paper_id}
        for cypher, node_type in [
            (self._CHECK_DUPLICATE_CONCEPT_EDGE,  "Concept"),
            (self._CHECK_DUPLICATE_EVIDENCE_EDGE, "Evidence"),
        ]:
            try:
                for row in self._client.run_query(cypher, params):
                    issues.append(ConsistencyIssue(
                        issue_type="DUPLICATE_EDGE",
                        description=(
                            f"{node_type} '{row.get('name','?')}' "
                            f"có {row.get('cnt','?')} edge từ cùng paper"
                        ),
                        node_type=node_type,
                        node_name=row.get("name", ""),
                        paper_id=paper_id,
                        severity="error",
                    ))
            except Exception:
                logger.exception(
                    "ConsistencyChecker._check_duplicate_edges: node_type=%s", node_type
                )
        return issues

    def _check_inverse_based_on(self) -> list[ConsistencyIssue]:
        issues = []
        try:
            for row in self._client.run_query(self._CHECK_INVERSE_BASED_ON):
                issues.append(ConsistencyIssue(
                    issue_type="INVERSE_EDGE",
                    description=(
                        f"BASED_ON cycle: '{row['concept_a']}' ↔ '{row['concept_b']}'"
                    ),
                    node_type="Concept",
                    node_name=row["concept_a"],
                    severity="error",
                ))
        except Exception:
            logger.exception("ConsistencyChecker._check_inverse_based_on: query thất bại")
        return issues

    def _check_no_evidence(self, paper_id: str) -> list[ConsistencyIssue]:
        issues = []
        params = {"paper_id": paper_id, "confidence_threshold": self._evidence_threshold}
        for cypher, node_type in [
            (self._CHECK_NO_EVIDENCE_CONCEPT,  "Concept"),
            (self._CHECK_NO_EVIDENCE_EVIDENCE, "Evidence"),
        ]:
            try:
                for row in self._client.run_query(cypher, params):
                    issues.append(ConsistencyIssue(
                        issue_type="NO_EVIDENCE",
                        description=(
                            f"{node_type} '{row.get('name','?')}' "
                            f"confidence={row.get('confidence',0):.2f} không có evidence"
                        ),
                        node_type=node_type,
                        node_name=row.get("name", ""),
                        paper_id=paper_id,
                        severity="warning",
                    ))
            except Exception:
                logger.exception(
                    "ConsistencyChecker._check_no_evidence: node_type=%s", node_type
                )
        return issues

    def _check_low_confidence(self, paper_id: str) -> list[ConsistencyIssue]:
        issues = []
        params = {"paper_id": paper_id, "min_confidence": self._min_confidence}
        try:
            for row in self._client.run_query(self._CHECK_LOW_CONFIDENCE_CONCEPT, params):
                issues.append(ConsistencyIssue(
                    issue_type="LOW_CONFIDENCE",
                    description=(
                        f"Concept '{row['name']}' confidence={row['confidence']:.2f} "
                        f"< threshold={self._min_confidence}"
                    ),
                    node_type="Concept",
                    node_name=row["name"],
                    paper_id=paper_id,
                    severity="warning",
                ))
        except Exception:
            logger.exception("ConsistencyChecker._check_low_confidence: query thất bại")
        return issues

    def _check_orphans(self) -> list[ConsistencyIssue]:
        """Global query — chỉ gọi từ maintenance job."""
        issues = []
        for cypher, node_type, issue_type in [
            (self._CHECK_ORPHAN_CONCEPT,  "Concept",  "ORPHAN_CONCEPT"),
            (self._CHECK_ORPHAN_EVIDENCE, "Evidence", "ORPHAN_EVIDENCE"),
        ]:
            try:
                for row in self._client.run_query(cypher):
                    issues.append(ConsistencyIssue(
                        issue_type=issue_type,
                        description=f"{node_type} '{row.get('name','?')}' không có Paper link đến",
                        node_type=node_type,
                        node_name=row.get("name", ""),
                        severity="warning",
                    ))
            except Exception:
                logger.exception(
                    "ConsistencyChecker._check_orphans: node_type=%s", node_type
                )
        return issues


# =============================================================================
# GRAPH UPDATER — Orchestrator
# =============================================================================

@dataclass
class UpdateReport:
    paper_id:             str
    merge_result:         Optional[MergeResult]        = None
    provenance_updates:   list[ProvenanceUpdate]       = field(default_factory=list)
    consistency_issues:   list[ConsistencyIssue]       = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.consistency_issues)

    @property
    def summary(self) -> str:
        m = self.merge_result
        merge_str = (
            f"concepts_merged={m.concepts_merged} "
            f"evidences_merged={m.evidences_merged} "
            f"aliases={m.aliases_registered} "
            f"apoc={'yes' if m.apoc_available else 'no'}"
        ) if m else "merge=skipped"
        errors = sum(1 for i in self.consistency_issues if i.severity == "error")
        return (
            f"paper_id={self.paper_id} | {merge_str} | "
            f"provenance_updates={len(self.provenance_updates)} | "
            f"issues={len(self.consistency_issues)} (errors={errors})"
        )


class GraphUpdater:
    """
    Orchestrator chạy đầy đủ update flow sau GraphBuilder.build().

    Thứ tự bắt buộc:
        1. merge_entities    — dedup node trước
        2. update_provenance — sau merge để edge trỏ đúng canonical node
        3. check_consistency — sau cùng để kiểm tra kết quả
    """

    def __init__(
        self,
        client: Neo4jClientProtocol,
        min_confidence: float = 0.5,
        evidence_confidence_threshold: float = 0.75,
    ) -> None:
        self._client  = client
        self._merger  = EntityMerger(client)
        self._prov    = ProvenanceUpdater(client)
        self._checker = ConsistencyChecker(
            client,
            min_confidence=min_confidence,
            evidence_confidence_threshold=evidence_confidence_threshold,
        )

    def run_all(
        self,
        paper_id: str,
        entities: ExtractedEntities,
        check_orphans: bool = False,
    ) -> UpdateReport:
        report = UpdateReport(paper_id=paper_id)
        logger.info("GraphUpdater.run_all: start paper_id=%s", paper_id)

        try:
            report.merge_result = self._merger.merge_entities(paper_id, entities)
        except Exception:
            logger.exception("GraphUpdater: EntityMerger thất bại paper_id=%s", paper_id)

        try:
            report.provenance_updates = self._prov.update_provenance(paper_id, entities)
        except Exception:
            logger.exception("GraphUpdater: ProvenanceUpdater thất bại paper_id=%s", paper_id)

        try:
            report.consistency_issues = self._checker.check_consistency(
                paper_id, check_orphans=check_orphans,
            )
        except Exception:
            logger.exception("GraphUpdater: ConsistencyChecker thất bại paper_id=%s", paper_id)

        logger.info("GraphUpdater.run_all: done — %s", report.summary)
        return report

    def merge_entities(self, paper_id: str, entities: ExtractedEntities) -> MergeResult:
        return self._merger.merge_entities(paper_id, entities)

    def update_provenance(
        self, paper_id: str, entities: ExtractedEntities
    ) -> list[ProvenanceUpdate]:
        return self._prov.update_provenance(paper_id, entities)

    def check_consistency(
        self, paper_id: str, check_orphans: bool = False
    ) -> list[ConsistencyIssue]:
        return self._checker.check_consistency(paper_id, check_orphans=check_orphans)


# =============================================================================
# BATCH UPDATER
# =============================================================================

class BatchGraphUpdater:
    """Wrapper chạy GraphUpdater cho nhiều paper."""

    def __init__(
        self,
        client: Neo4jClientProtocol,
        continue_on_error: bool = True,
        **updater_kwargs,
    ) -> None:
        self._updater          = GraphUpdater(client, **updater_kwargs)
        self._client           = client
        self._continue_on_error = continue_on_error

    def update_batch(
        self,
        items: list[tuple[str, ExtractedEntities]],
        check_orphans: bool = False,
    ) -> list[UpdateReport]:
        reports: list[UpdateReport] = []
        success = failed = skipped = 0

        for paper_id, entities in items:
            existing = self._client.get_paper_by_id(paper_id)
            if not existing or existing.get("processing_status") != "kg_built":
                logger.debug("BatchGraphUpdater: skip %s — chưa kg_built", paper_id)
                skipped += 1
                reports.append(UpdateReport(paper_id=paper_id))
                continue

            try:
                report = self._updater.run_all(
                    paper_id, entities, check_orphans=check_orphans,
                )
                reports.append(report)
                success += 1
            except Exception:
                failed += 1
                logger.exception("BatchGraphUpdater: FAILED paper_id=%s", paper_id)
                reports.append(UpdateReport(paper_id=paper_id))
                if not self._continue_on_error:
                    raise

        logger.info(
            "BatchGraphUpdater: done — success=%d failed=%d skipped=%d total=%d",
            success, failed, skipped, len(items),
        )
        return reports
