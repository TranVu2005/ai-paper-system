from __future__ import annotations

import logging
import uuid

from storage.graph_db.neo4j_client import Neo4jClient
from ingestion.schema.document_schema import UnifiedDocument, Reference, ChunkMeta

from ai_module.kg.entities import (
    AuthorEntity,
    ConceptEntity,
    EvidenceEntity,
    MetricEntity,
    FindingEntity,
    ExtractedEntities,
)
from ai_module.kg.relation_extractor import (
    RelationResult,
    BasedOnRelation,
    ExtendsRelation,
)

logger = logging.getLogger(__name__)

_MIN_CITATION_TITLE_LEN = 20


# =============================================================================
# GRAPH BUILDER
# =============================================================================

class GraphBuilder:
    """
    Orchestrate toàn bộ KG build flow cho 1 paper.

    Cách dùng đầy đủ:
        builder   = GraphBuilder(neo4j_client)
        entities  = entity_extractor.extract(doc)
        paper_id  = builder.resolve_paper_id(doc)

        # author_id_map cần thiết cho relation_extractor
        author_id_map = builder.build_phase1(doc, entities, paper_id=paper_id)
        #   → trả về {author_name: author_id}

        relations = relation_extractor.extract(doc, paper_id, entities, author_id_map)
        builder.build_phase2(paper_id, entities, relations=relations)
        builder.build_phase3(paper_id, chunk_map)
        builder._client.set_paper_status(paper_id, "kg_built")

    Cách dùng nhanh (build() tự orchestrate):
        builder.build(doc, entities, relations=relations, chunk_map=chunk_map)
    """

    _UUID5_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

    def __init__(self, client: Neo4jClient) -> None:
        self._client = client

    # ------------------------------------------------------------------
    # Entry point chính
    # ------------------------------------------------------------------

    def build(
        self,
        doc: UnifiedDocument,
        entities: ExtractedEntities,
        relations: RelationResult | None = None,
        chunk_map: dict[str, ChunkMeta] | None = None,
    ) -> str:
        paper_id = self.resolve_paper_id(doc)
        logger.info("GraphBuilder.build: start paper_id=%s title='%s'", paper_id, doc.title)

        try:
            author_id_map = self.build_phase1(doc, entities, paper_id=paper_id)
            self.build_phase2(paper_id, entities, relations=relations)
            if chunk_map:
                self.build_phase3(paper_id, chunk_map)
            self._client.set_paper_status(paper_id, "kg_built")
        except Exception:
            logger.exception("GraphBuilder.build: FAILED paper_id=%s", paper_id)
            raise

        logger.info("GraphBuilder.build: done paper_id=%s", paper_id)
        return paper_id

    # ------------------------------------------------------------------
    # Giai đoạn 1 — Paper / Author / Venue / Topic / Citation
    # ------------------------------------------------------------------

    def build_phase1(
        self,
        doc: UnifiedDocument,
        entities: ExtractedEntities,
        paper_id: str | None = None,
    ) -> dict[str, str]:
        """
        Giai đoạn 1: insert Paper, Author, Institution, Venue, Topic, Citation stubs.

        Returns:
            author_id_map: {author_name: author_id}
            — relation_extractor cần map này để build WROTE edge.
        """
        paper_id = paper_id or self.resolve_paper_id(doc)

        # 1. Paper node
        self._merge_paper(doc, paper_id, entities)

        # 2 + 3. Author + Institution
        author_id_map: dict[str, str] = {}
        if not entities.authors:
            logger.warning(
                "build_phase1: paper_id=%s không có author — "
                "entity_extractor có thể parse thất bại.",
                paper_id,
            )
        for order, author in enumerate(entities.authors):
            author_id = self._merge_author(author, paper_id, order)
            author_id_map[author.name] = author_id
            if author.affiliation:
                self._merge_institution(author, author_id)

        # 4. Venue
        if doc.journal:
            self._merge_venue(doc, paper_id)

        # 5. Topics từ keywords
        if doc.keywords:
            self._client.merge_topics(
                paper_id=paper_id,
                topics=doc.keywords,
                source="keyword",
            )

        # 6. Citation stubs
        for ref in (doc.references or []):
            self._merge_citation(ref, paper_id)

        return author_id_map

    # ------------------------------------------------------------------
    # Giai đoạn 2 — Concept / Evidence / Metric nodes + edges
    # ------------------------------------------------------------------

    def build_phase2(
        self,
        paper_id: str,
        entities: ExtractedEntities,
        relations: RelationResult | None = None,
    ) -> None:
        """
        Giai đoạn 2: insert Concept, Evidence, Metric, Finding nodes và edges.

        Strategy:
            Bước 1 — insert node + edge từ entities (baseline)
            Bước 2 — nếu có RelationResult, override edges với confidence đã LLM verify
                      (neo4j_client ON MATCH SET giữ confidence cao nhất)
            Bước 3 — BASED_ON + EXTENDS sau khi tất cả Concept node đã tồn tại
            Bước 4 — SUPPORTS + CONTRADICTS từ FindingEntity
        """
        # ── Bước 1: node + edge từ entities ──────────────────────────────

        # [v3-3] Concept — gom method + task + theory + ...
        for concept in entities.concepts:
            self._client.merge_concept(
                paper_id=paper_id,
                concept_name=concept.name,
                category=concept.category,
                aliases=concept.aliases,
                domain=concept.domain,
                source_section=concept.source_section or "unknown",
                confidence=concept.confidence,
                evidence=concept.evidence,
            )

        # [v3-3] Evidence — gom dataset + survey + experiment + ...
        for ev in entities.evidences:
            self._client.merge_evidence(
                paper_id=paper_id,
                evidence_name=ev.name,
                evidence_type=ev.evidence_type,
                language=ev.language,
                aliases=ev.aliases,
                source_section=ev.source_section or "unknown",
                confidence=ev.confidence,
                evidence=ev.evidence,
            )

        # [v3-3] Metric — node độc lập, edge ACHIEVES_METRIC
        for metric in entities.metrics:
            self._client.merge_metric(
                paper_id=paper_id,
                metric_name=metric.name,
                value=metric.value,
                unit=metric.unit,
                higher_better=metric.higher_better,
                measured_on=metric.measured_on,
                measured_by=metric.measured_by,
                evidence=metric.evidence,
            )

        # Finding — lưu node, SUPPORTS/CONTRADICTS được build ở bước 4
        for finding in entities.findings:
            self._client.merge_finding(
                paper_id=paper_id,
                description=finding.description,
                finding_type=finding.finding_type,
                confidence=finding.confidence,
                evidence=finding.evidence,
            )

        # ── Bước 2: override edges từ RelationResult nếu có ─────────────
        if relations is not None:
            logger.debug("build_phase2: override edges bằng RelationResult đã LLM verify")
            self._insert_edges_from_relations(paper_id, relations)
        else:
            logger.debug("build_phase2: không có RelationResult — dùng entity confidence")

        # ── Bước 3: BASED_ON + EXTENDS — sau khi tất cả Concept node tồn tại
        self._merge_concept_hierarchy(paper_id, entities, relations)

        # ── Bước 4: SUPPORTS + CONTRADICTS — từ FindingEntity
        self._insert_finding_edges(paper_id, entities, relations)

    def _insert_edges_from_relations(
        self,
        paper_id: str,
        relations: RelationResult,
    ) -> None:
        """Override edges với confidence đã LLM verify từ RelationResult."""

        # USES_CONCEPT  [v3-7]
        for r in relations.uses_concept:
            self._client.merge_concept(
                paper_id=paper_id,
                concept_name=r.concept_name,
                category=r.category,
                source_section=r.source_section,
                confidence=r.confidence,
                evidence=r.evidence,
            )

        # EVALUATES_ON  [v3-7]
        for r in relations.evaluates_on:
            self._client.merge_evidence(
                paper_id=paper_id,
                evidence_name=r.evidence_name,
                evidence_type=r.evidence_type,
                source_section=r.source_section,
                confidence=r.confidence,
                evidence=r.evidence,
            )

        # ACHIEVES_METRIC  [v3-5]
        for r in relations.achieves_metric:
            self._client.merge_metric(
                paper_id=paper_id,
                metric_name=r.metric_name,
                value=r.value,
                unit=r.unit,
                measured_on=r.measured_on,
                measured_by=r.measured_by,
                confidence=r.confidence,
                evidence=r.evidence,
            )

    def _merge_concept_hierarchy(
        self,
        paper_id: str,
        entities: ExtractedEntities,
        relations: RelationResult | None,
    ) -> None:
        """
        Insert BASED_ON + EXTENDS edges sau khi tất cả Concept node đã tồn tại.
        Ưu tiên RelationResult nếu có; fallback về ConceptEntity fields.

        [v3.1-1] In-memory DFS cycle detection chạy trước mỗi MERGE để tránh
        tạo cycle trong local graph của paper này. Neo4j Cypher guard trong
        neo4j_client vẫn là lớp 2 defense cho cross-paper cycles.
        """
        based_on_list: list[BasedOnRelation] = []
        extends_list:  list[ExtendsRelation] = []

        if relations is not None:
            based_on_list = relations.based_on
            extends_list  = relations.extends
        else:
            # Fallback từ entities
            for concept in entities.concepts:
                for parent in (concept.based_on or []):
                    based_on_list.append(BasedOnRelation(
                        child_concept=concept.name,
                        parent_concept=parent.lower().strip(),
                        confidence=concept.confidence,
                    ))
                for parent in (concept.extends or []):
                    extends_list.append(ExtendsRelation(
                        child_concept=concept.name,
                        parent_concept=parent.lower().strip(),
                        confidence=concept.confidence,
                    ))

        # [v3.1-1] In-memory graph để detect cycle cục bộ trước khi query Neo4j.
        # Mỗi edge type (BASED_ON, EXTENDS) có local_graph riêng.
        based_on_local: dict[str, set[str]] = {}
        extends_local:  dict[str, set[str]] = {}

        # BASED_ON
        for rel in based_on_list:
            child  = rel.child_concept
            parent = rel.parent_concept

            # Check cycle in-memory: nếu đi từ parent đã reach được child → sẽ tạo cycle
            if self._would_create_cycle(based_on_local, parent, child):
                logger.warning(
                    "_merge_concept_hierarchy: skip BASED_ON cycle '%s' → '%s'",
                    child, parent,
                )
                continue

            # Update local graph trước khi gọi Neo4j
            based_on_local.setdefault(child, set()).add(parent)

            try:
                # Auto-create parent placeholder nếu chưa có
                self._client.merge_concept(
                    paper_id=paper_id,
                    concept_name=parent,
                    category="concept",
                    source_section="based_on_placeholder",
                    confidence=0.5,
                    evidence=f"parent of {child}",
                )
                self._client.merge_concept_based_on(
                    child_concept=child,
                    parent_concept=parent,
                    confidence=rel.confidence,
                )
                logger.debug("BASED_ON: '%s' → '%s'", child, parent)
            except Exception:
                logger.warning(
                    "_merge_concept_hierarchy: BASED_ON thất bại '%s' → '%s'",
                    child, parent, exc_info=True,
                )

        # EXTENDS  [v3-5]
        for rel in extends_list:
            child  = rel.child_concept
            parent = rel.parent_concept

            # Check cycle in-memory
            if self._would_create_cycle(extends_local, parent, child):
                logger.warning(
                    "_merge_concept_hierarchy: skip EXTENDS cycle '%s' → '%s'",
                    child, parent,
                )
                continue

            extends_local.setdefault(child, set()).add(parent)

            try:
                self._client.merge_concept(
                    paper_id=paper_id,
                    concept_name=parent,
                    category="concept",
                    source_section="extends_placeholder",
                    confidence=0.5,
                    evidence=f"parent of {child}",
                )
                self._client.merge_concept_extends(
                    child_concept=child,
                    parent_concept=parent,
                    confidence=rel.confidence,
                )
                logger.debug("EXTENDS: '%s' → '%s'", child, parent)
            except Exception:
                logger.warning(
                    "_merge_concept_hierarchy: EXTENDS thất bại '%s' → '%s'",
                    child, parent, exc_info=True,
                )

    @staticmethod
    def _would_create_cycle(graph: dict[str, set[str]], start: str, target: str) -> bool:
        """
        DFS check: từ node `start`, đi theo graph có reach được `target` không.
        Nếu có → thêm edge (target → start) sẽ tạo cycle.

        Args:
            graph:  adjacency dict {child: {parent1, parent2, ...}}
            start:  node bắt đầu DFS (= parent của edge đang xét)
            target: node cần kiểm tra có reachable không (= child của edge đang xét)

        Returns:
            True nếu sẽ tạo cycle, False nếu an toàn.
        """
        visited: set[str] = set()
        stack:   list[str] = [start]
        while stack:
            node = stack.pop()
            if node == target:
                return True
            if node in visited:
                continue
            visited.add(node)
            stack.extend(graph.get(node, []))
        return False

    def _insert_finding_edges(
        self,
        paper_id: str,
        entities: ExtractedEntities,
        relations: RelationResult | None,
    ) -> None:
        """
        Insert SUPPORTS + CONTRADICTS edges.
        Nguồn ưu tiên: RelationResult (đã validate target paper_id tồn tại).
        Fallback: FindingEntity.supports/contradicts trực tiếp.
        """
        if relations is not None:
            for r in relations.supports:
                try:
                    self._client.merge_paper_supports(
                        source_paper_id=r.source_paper_id,
                        target_paper_id=r.target_paper_id,
                        finding_desc=r.finding_desc,
                        confidence=r.confidence,
                    )
                except Exception:
                    logger.warning(
                        "_insert_finding_edges: SUPPORTS thất bại %s → %s",
                        r.source_paper_id, r.target_paper_id, exc_info=True,
                    )

            for r in relations.contradicts:
                try:
                    self._client.merge_paper_contradicts(
                        source_paper_id=r.source_paper_id,
                        target_paper_id=r.target_paper_id,
                        finding_desc=r.finding_desc,
                        confidence=r.confidence,
                    )
                except Exception:
                    logger.warning(
                        "_insert_finding_edges: CONTRADICTS thất bại %s → %s",
                        r.source_paper_id, r.target_paper_id, exc_info=True,
                    )
        else:
            # Fallback từ FindingEntity trực tiếp — target_paper_id có thể chưa tồn tại
            for finding in entities.findings:
                for target_id in (finding.supports or []):
                    if target_id and target_id != paper_id:
                        try:
                            self._client.merge_paper_supports(
                                source_paper_id=paper_id,
                                target_paper_id=target_id,
                                finding_desc=finding.description[:200],
                                confidence=finding.confidence,
                            )
                        except Exception:
                            logger.debug(
                                "_insert_finding_edges: SUPPORTS fallback thất bại %s → %s",
                                paper_id, target_id,
                            )
                for target_id in (finding.contradicts or []):
                    if target_id and target_id != paper_id:
                        try:
                            self._client.merge_paper_contradicts(
                                source_paper_id=paper_id,
                                target_paper_id=target_id,
                                finding_desc=finding.description[:200],
                                confidence=finding.confidence,
                            )
                        except Exception:
                            logger.debug(
                                "_insert_finding_edges: CONTRADICTS fallback thất bại %s → %s",
                                paper_id, target_id,
                            )

    # ------------------------------------------------------------------
    # Giai đoạn 3 — Chunk nodes
    # ------------------------------------------------------------------

    def build_phase3(
        self,
        paper_id: str,
        chunk_map: dict[str, ChunkMeta],
    ) -> None:
        for qdrant_id, meta in chunk_map.items():
            self._client.merge_chunk(
                paper_id=paper_id,
                qdrant_id=qdrant_id,
                section=meta.section,
                page=meta.page,
                chunk_text=meta.text,
            )
        logger.debug(
            "build_phase3: paper_id=%s inserted %d chunks",
            paper_id, len(chunk_map),
        )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def resolve_paper_id(self, doc: UnifiedDocument) -> str:
        """
        Sinh paper_id nhất quán từ UnifiedDocument.

        Ưu tiên:
            1. doi        → uuid5 deterministic
            2. title+year → uuid5
            3. fallback   → random UUID (không idempotent)

        ⚠️ 2-source collision: nếu cùng paper ingest 2 lần — lần 1 có doi,
           lần 2 không — sẽ tạo 2 Paper node khác nhau.
           Pre-dedup trước khi ingest: ưu tiên source có doi.
        """
        if doc.doi and doc.doi.strip():
            raw = f"doi:{doc.doi.strip().lower()}"
        elif doc.title and doc.title.strip() and doc.title != "Unknown":
            year = doc.year or 0
            raw  = f"title:{doc.title.strip().lower()}:year:{year}"
        else:
            logger.warning(
                "resolve_paper_id: không có doi lẫn title — dùng random UUID (không idempotent)"
            )
            return str(uuid.uuid4())
        return str(uuid.uuid5(self._UUID5_NS, raw))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _merge_paper(
        self,
        doc: UnifiedDocument,
        paper_id: str,
        entities: ExtractedEntities,
    ) -> None:
        """Chuẩn bị params từ doc + entities.paper rồi gọi client.merge_paper()."""
        # [v3-8] Lấy domain từ PaperEntity nếu có
        domain = entities.paper.domain if entities.paper else None

        self._client.merge_paper({
            "id":             paper_id,
            "title":          doc.title,
            "year":           doc.year,
            "doi":            doc.doi,
            "abstract":       doc.abstract,
            "language":       getattr(doc, "language", None) or "vi",
            "domain":         domain,
            "source_file":    getattr(doc, "source_file", None),
            "source_type":    getattr(doc, "source_type", None),
            "page_count":     getattr(doc, "page_count", None),
            "citation_count": getattr(doc, "citation_count", None),
            "chunk_ids":      getattr(doc, "chunk_ids", None) or [],
        })
        logger.debug("_merge_paper: paper_id=%s doi=%s domain=%s", paper_id, doc.doi, domain)

    def _merge_author(
        self,
        author: AuthorEntity,
        paper_id: str,
        order: int,
    ) -> str:
        existing_id = self._client.lookup_author_by_fulltext(
            name=author.name,
            affiliation=author.affiliation,
            threshold=0.8,
        )
        author_raw = (
            f"author:{author.name.strip().lower()}"
            f":{(author.affiliation or '').strip().lower()}"
        )
        author_id = existing_id or str(uuid.uuid5(self._UUID5_NS, author_raw))

        self._client.merge_author(
            author_id=author_id,
            name=author.name,
            paper_id=paper_id,
            order=order,
            email=author.email,
            affiliation=author.affiliation,
        )
        logger.debug(
            "_merge_author: '%s' order=%d %s",
            author.name, order,
            "matched existing" if existing_id else "created new",
        )
        return author_id

    def _merge_institution(self, author: AuthorEntity, author_id: str) -> None:
        self._client.merge_institution(
            author_id=author_id,
            display_name=author.affiliation,  # type: ignore[arg-type]
            country=author.country,
            inst_type=author.institution_type,
        )

    def _merge_venue(self, doc: UnifiedDocument, paper_id: str) -> None:
        self._client.merge_venue(
            paper_id=paper_id,
            venue_name=doc.journal,           # type: ignore[arg-type]
            venue_type=self._infer_venue_type(doc.journal or ""),
            publisher=getattr(doc, "publisher", None),
            volume=getattr(doc, "volume", None),
            issue=getattr(doc, "issue", None),
            pages=getattr(doc, "pages", None),
        )

    def _merge_citation(self, ref: Reference, citing_paper_id: str) -> None:
        if not ref.doi and not ref.title:
            return
        if not ref.doi and ref.title and len(ref.title.strip()) < _MIN_CITATION_TITLE_LEN:
            logger.debug(
                "_merge_citation: bỏ qua title ngắn '%s'", ref.title,
            )
            return

        self._client.merge_citation(
            citing_paper_id=citing_paper_id,
            cited_doc_id=getattr(ref, "internal_doc_id", None),   # Reference.internal_doc_id mới
            cited_doi=getattr(ref, "doi", None),
            cited_title=getattr(ref, "title", None),
            cited_year=getattr(ref, "year", None),
            raw_ref_text=getattr(ref, "raw_text", ""),
        )

    @staticmethod
    def _infer_venue_type(venue_name: str) -> str:
        name_lower = venue_name.lower()
        if any(kw in name_lower for kw in (
            "conference", "proceedings", "workshop",
            "acl", "emnlp", "naacl", "coling",
        )):
            return "conference"
        if any(kw in name_lower for kw in ("arxiv", "preprint")):
            return "preprint"
        return "journal"


# =============================================================================
# BATCH BUILDER
# =============================================================================

class BatchGraphBuilder:
    """
    Wrapper cho GraphBuilder để xử lý list paper.
    Dùng trong scripts/build_kg.py hoặc celery_worker.py.
    """

    def __init__(
        self,
        client: Neo4jClient,
        continue_on_error: bool = True,
    ) -> None:
        self._builder          = GraphBuilder(client)
        self._client           = client
        self._continue_on_error = continue_on_error

    def build_batch(
        self,
        items: list[tuple[UnifiedDocument, ExtractedEntities]],
        relations_map: dict[str, RelationResult] | None = None,
        chunk_maps: dict[str, dict[str, ChunkMeta]] | None = None,
    ) -> dict[str, str]:
        """
        Build KG cho list (doc, entities).

        Args:
            items:         list of (UnifiedDocument, ExtractedEntities)
            relations_map: {paper_id: RelationResult} — optional
            chunk_maps:    {paper_id: {qdrant_id: ChunkMeta}} — optional

        Returns:
            {paper_id: paper_title}
        """
        results: dict[str, str] = {}
        success = failed = skipped = 0

        for doc, entities in items:
            paper_id = self._builder.resolve_paper_id(doc)

            existing = self._client.get_paper_by_id(paper_id)
            if existing and existing.get("processing_status") == "kg_built":
                logger.debug("build_batch: skip '%s' — already kg_built", doc.title)
                skipped += 1
                results[paper_id] = doc.title
                continue

            try:
                relations = (relations_map or {}).get(paper_id)
                chunk_map = (chunk_maps or {}).get(paper_id)
                self._builder.build(
                    doc, entities,
                    relations=relations,
                    chunk_map=chunk_map,
                )
                results[paper_id] = doc.title
                success += 1
            except Exception:
                failed += 1
                logger.exception("build_batch: FAILED '%s'", doc.title)
                if not self._continue_on_error:
                    raise

        logger.info(
            "build_batch: done — success=%d failed=%d skipped=%d total=%d",
            success, failed, skipped, len(items),
        )
        return results
