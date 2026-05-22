from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional, Any

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIG
# =============================================================================

@dataclass
class GraphRetrieverConfig:
    """
    Config cho GraphRetriever.

    database        : Neo4j database name — "user_doc_kg" cho KG từ file user upload.
                      Truyền "neo4j" hoặc "system_kg" nếu muốn query KG 1000 file.
    top_k           : số GraphChunk tối đa trả về
    concept_depth   : số hop traversal cho BASED_ON / EXTENDS chain
    min_score       : score tối thiểu để giữ chunk
    fulltext_threshold : ngưỡng fulltext search Neo4j (0.0–1.0)
    """
    database:             str   = "user_doc_kg"
    top_k:                int   = 10
    concept_depth:        int   = 2
    min_score:            float = 0.3
    fulltext_threshold:   float = 0.6
    max_concepts_per_query: int = 5
    max_evidences_per_query: int = 3


# =============================================================================
# OUTPUT DATA CLASSES
# =============================================================================

@dataclass
class GraphChunk:
    """
    Một đơn vị context từ graph — tương thích với VectorChunk của vector_retriever.

    text     : nội dung text để đưa vào LLM context
    score    : relevance score (0.0–1.0)
    source   : "graph_concept" | "graph_evidence" | "graph_metric" |
               "graph_finding" | "graph_citation" | "graph_paper"
    metadata : thông tin thêm cho fusion_retriever (paper_id, concept_name, ...)
    """
    text:     str
    score:    float
    source:   str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConceptNode:
    name:           str
    category:       str
    domain:         Optional[str]
    confidence:     float
    source_section: Optional[str]
    evidence:       str
    aliases:        list[str] = field(default_factory=list)


@dataclass
class EvidenceNode:
    name:          str
    evidence_type: str
    language:      Optional[str]
    confidence:    float


@dataclass
class MetricNode:
    name:        str
    value:       Optional[float]
    unit:        Optional[str]
    measured_on: str
    measured_by: str
    confidence:  float


@dataclass
class FindingNode:
    description:  str
    finding_type: str
    confidence:   float


@dataclass
class PaperNode:
    paper_id:  str
    title:     str
    year:      Optional[int]
    domain:    Optional[str]
    language:  Optional[str]


@dataclass
class ConceptEdge:
    """(Concept)-[:BASED_ON|EXTENDS]->(Concept)"""
    child:        str
    parent:       str
    relation:     str   # "BASED_ON" | "EXTENDS"
    confidence:   float


@dataclass
class SubgraphContext:
    """
    Structured subgraph — context_builder.py dùng để build prompt.

    paper        : Paper node trung tâm
    concepts     : Concept nodes liên quan đến query
    evidences    : Evidence nodes
    metrics      : Metric nodes
    findings     : Finding nodes
    concept_edges: BASED_ON + EXTENDS edges (hierarchy)
    cited_papers : Paper nodes được cite bởi paper trung tâm
    """
    paper:         Optional[PaperNode]   = None
    concepts:      list[ConceptNode]     = field(default_factory=list)
    evidences:     list[EvidenceNode]    = field(default_factory=list)
    metrics:       list[MetricNode]      = field(default_factory=list)
    findings:      list[FindingNode]     = field(default_factory=list)
    concept_edges: list[ConceptEdge]     = field(default_factory=list)
    cited_papers:  list[PaperNode]       = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.paper or self.concepts or self.evidences
            or self.metrics or self.findings
        )

    def to_text_summary(self) -> str:
        """
        Convert subgraph → text ngắn gọn.
        Dùng bởi _build_text_chunks() và context_builder.py.
        """
        parts: list[str] = []

        if self.paper:
            year_str = f" ({self.paper.year})" if self.paper.year else ""
            parts.append(f"Paper: {self.paper.title}{year_str}")
            if self.paper.domain:
                parts.append(f"Domain: {self.paper.domain}")

        if self.concepts:
            by_category: dict[str, list[str]] = {}
            for c in self.concepts:
                by_category.setdefault(c.category, []).append(c.name)
            for cat, names in by_category.items():
                parts.append(f"{cat.capitalize()}: {', '.join(names[:5])}")

        if self.evidences:
            ev_names = [e.name for e in self.evidences[:5]]
            parts.append(f"Datasets/Evidence: {', '.join(ev_names)}")

        if self.metrics:
            metric_parts = []
            for m in self.metrics[:5]:
                v = f"={m.value}{m.unit or ''}" if m.value is not None else ""
                metric_parts.append(f"{m.name}{v}")
            parts.append(f"Metrics: {', '.join(metric_parts)}")

        if self.findings:
            for f in self.findings[:3]:
                parts.append(f"Finding ({f.finding_type}): {f.description[:150]}")

        if self.concept_edges:
            for e in self.concept_edges[:5]:
                parts.append(f"  {e.child} --[{e.relation}]--> {e.parent}")

        if self.cited_papers:
            cited_titles = [p.title[:60] for p in self.cited_papers[:3]]
            parts.append(f"Cites: {'; '.join(cited_titles)}")

        return "\n".join(parts)

    def to_cypher_summary(self) -> str:
        """
        Dạng Cypher-like summary — dùng cho debug hoặc context_builder nâng cao.
        VD: (paper:Paper {title: "..."}) -[:USES_CONCEPT]-> (c:Concept {name: "bert"})
        """
        lines: list[str] = []
        if self.paper:
            for c in self.concepts[:10]:
                lines.append(
                    f'(:Paper {{title:"{self.paper.title[:40]}"}})'
                    f'-[:USES_CONCEPT {{category:"{c.category}"}}]->'
                    f'(:Concept {{name:"{c.name}"}})'
                )
            for e in self.evidences[:5]:
                lines.append(
                    f'(:Paper {{title:"{self.paper.title[:40]}"}})'
                    f'-[:EVALUATES_ON]->'
                    f'(:Evidence {{name:"{e.name}", type:"{e.evidence_type}"}})'
                )
        for edge in self.concept_edges[:5]:
            lines.append(
                f'(:Concept {{name:"{edge.child}"}})'
                f'-[:{edge.relation}]->'
                f'(:Concept {{name:"{edge.parent}"}})'
            )
        return "\n".join(lines)


@dataclass
class QueryEntities:
    """
    Entities đã được extract từ query text trước khi vào retriever.
    Thường được sinh bởi entity_extractor hoặc một NER nhỏ hơn.
    """
    concepts:  list[str] = field(default_factory=list)  # tên concept
    evidences: list[str] = field(default_factory=list)  # tên evidence/dataset
    keywords:  list[str] = field(default_factory=list)  # fallback keywords

@dataclass
class QueryInfo:
    """Debug / trace info."""
    query_text:        str
    paper_id:          Optional[str]
    matched_concepts:  list[str]   = field(default_factory=list)
    matched_evidences: list[str]   = field(default_factory=list)
    cypher_calls:      int         = 0
    total_nodes_found: int         = 0
    retrieval_ms:      float       = 0.0


@dataclass
class GraphRetrievalResult:
    """
    Kết quả trả về từ GraphRetriever.

    chunks   : list[GraphChunk]  — dạng text, tương thích fusion_retriever
    subgraph : SubgraphContext   — dạng structured, dùng cho context_builder
    query_info: QueryInfo        — debug / trace
    """
    chunks:     list[GraphChunk]
    subgraph:   SubgraphContext
    query_info: QueryInfo

    def is_empty(self) -> bool:
        return not self.chunks and self.subgraph.is_empty()


# =============================================================================
# HELPERS
# =============================================================================

def _escape_lucene(text: str) -> str:
    """Escape Lucene special chars — khớp với neo4j_client._escape_lucene."""
    special = set('+-&|!(){}[]^"~*?:\\/')
    return "".join(f"\\{c}" if c in special else c for c in text)


def _normalize(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def _extract_keywords(query_text: str) -> list[str]:
    """
    Tách keywords từ query text.
    Bỏ stopwords tiếng Việt + tiếng Anh phổ biến.
    Trả về list token có độ dài >= 3.
    """
    _STOPWORDS_VI = {
        "của", "và", "là", "có", "trong", "với", "được", "này", "đó",
        "các", "một", "cho", "để", "theo", "từ", "trên", "về", "như",
        "khi", "bởi", "tại", "hay", "hoặc", "nếu", "thì", "mà", "sẽ",
        "đã", "đang", "những", "rằng", "vì", "nên", "cũng", "vậy",
    }
    _STOPWORDS_EN = {
        "the", "and", "for", "are", "was", "were", "with", "this",
        "that", "have", "has", "had", "not", "but", "they", "from",
        "what", "how", "can", "which", "when", "where", "who",
    }
    stopwords = _STOPWORDS_VI | _STOPWORDS_EN

    tokens = re.findall(r"[a-zA-ZÀ-ỹ][a-zA-ZÀ-ỹ\-_]{2,}", query_text)
    return [
        t.lower() for t in tokens
        if t.lower() not in stopwords
    ]


def _score_by_section(source_section: Optional[str]) -> float:
    """Score boost dựa trên section type — khớp với entity_extractor._SECTION_CONFIDENCE."""
    _SECTION_BOOST = {
        "method": 0.15, "methodology": 0.15, "approach": 0.12,
        "experiment": 0.10, "evaluation": 0.10,
        "result": 0.08, "discussion": 0.07,
        "abstract": 0.05, "conclusion": 0.05,
        "introduction": 0.02, "background": 0.02,
    }
    stype = (source_section or "").lower().strip()
    for key, boost in _SECTION_BOOST.items():
        if stype.startswith(key):
            return boost
    return 0.0


# =============================================================================
# GRAPH RETRIEVER
# =============================================================================

class GraphRetriever:
    """
    Query Knowledge Graph để lấy context hỗ trợ RAG.

    Dùng cùng Neo4jClient từ storage/graph_db/neo4j_client.py.
    Database mặc định: "user_doc_kg" — KG được build từ file user upload.

    Cách dùng:
        from storage.graph_db.neo4j_client import Neo4jClient, Neo4jConfig
        from ai_module.retrieval.graph_retriever import GraphRetriever, GraphRetrieverConfig

        config   = GraphRetrieverConfig(database="user_doc_kg")
        client   = Neo4jClient(Neo4jConfig(database="user_doc_kg"))
        client.connect()

        retriever = GraphRetriever(client, config)
        result    = retriever.retrieve(query_text="BERT là gì?", paper_id="abc-123")

        # Dùng trong fusion_retriever:
        for chunk in result.chunks:
            print(chunk.text, chunk.score)

        # Dùng trong context_builder:
        context_text = result.subgraph.to_text_summary()
    """

    def __init__(
        self,
        client,   # Neo4jClient — không import trực tiếp để tránh circular
        config: GraphRetrieverConfig | None = None,
    ) -> None:
        self._client = client
        self._config = config or GraphRetrieverConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query_text:     str,
        paper_id:       Optional[str]          = None,
        query_entities: Optional[QueryEntities] = None,  # 🔥 THÊM
        top_k:          Optional[int]           = None,
    ) -> GraphRetrievalResult:
        """
        Entry point chính.
 
        Args:
            query_text     : câu hỏi của user
            paper_id       : paper đang được hỏi — nếu có thì ưu tiên query theo paper này
            query_entities : entities đã extract từ query — nếu có thì skip fulltext search,
                             dùng thẳng concept/evidence names để query Neo4j
            top_k          : override config.top_k
 
        Returns:
            GraphRetrievalResult với chunks + subgraph + query_info
        """
        import time
        t0    = time.time()
        top_k = top_k or self._config.top_k
        info  = QueryInfo(query_text=query_text, paper_id=paper_id)
 
        # ── Bước 1: Resolve matched_concepts + matched_evidences + keywords ──
        #
        # Ưu tiên:
        #   (a) query_entities có sẵn → dùng thẳng, score mặc định 0.9 (đã qua NER)
        #   (b) fallback → keyword extraction + fulltext search Neo4j
        #
        has_entities = query_entities and (
            query_entities.concepts or query_entities.evidences
        )
 
        if has_entities:
            matched_concepts = [
                {"name": n, "category": "concept", "score": 0.9}
                for n in query_entities.concepts          # type: ignore[union-attr]
            ]
            matched_evidences = [
                {"name": n, "evidence_type": "dataset", "score": 0.9}
                for n in query_entities.evidences         # type: ignore[union-attr]
            ]
            # Keywords: dùng từ query_entities nếu có, fallback sang extract từ text
            keywords = (
                query_entities.keywords                   # type: ignore[union-attr]
                or _extract_keywords(query_text)
            )
            logger.debug(
                "GraphRetriever.retrieve: dùng extracted entities — "
                "concepts=%d evidences=%d keywords=%d",
                len(matched_concepts), len(matched_evidences), len(keywords),
            )
        else:
            # Fallback: keyword extraction + fulltext search
            keywords          = _extract_keywords(query_text)
            matched_concepts  = self._match_concepts(keywords)
            matched_evidences = self._match_evidences(keywords)
            logger.debug(
                "GraphRetriever.retrieve: fallback keywords=%s", keywords
            )
 
        info.matched_concepts  = [c["name"] for c in matched_concepts]
        info.matched_evidences = [e["name"] for e in matched_evidences]
        logger.debug(
            "GraphRetriever: matched concepts=%d evidences=%d",
            len(matched_concepts), len(matched_evidences),
        )
 
        # ── Bước 2: Query subgraph ────────────────────────────────────
        subgraph = SubgraphContext()
 
        if paper_id:
            paper_node = self._retrieve_paper(paper_id)
            if paper_node:
                subgraph.paper = paper_node
                info.cypher_calls += 1
 
            subgraph.concepts     = self._retrieve_concepts_by_paper(paper_id)
            subgraph.evidences    = self._retrieve_evidences_by_paper(paper_id)
            subgraph.metrics      = self._retrieve_metrics_by_paper(paper_id)
            subgraph.findings     = self._retrieve_findings_by_paper(paper_id)
            subgraph.cited_papers = self._retrieve_cited_papers(paper_id)
            info.cypher_calls += 5
 
        # Enrich với entity-based retrieval
        if matched_concepts:
            concept_names   = [c["name"] for c in matched_concepts]
            entity_concepts = self._retrieve_concepts_by_names(
                concept_names, paper_id=paper_id
            )
            existing_names = {c.name for c in subgraph.concepts}
            for c in entity_concepts:
                if c.name not in existing_names:
                    subgraph.concepts.append(c)
                    existing_names.add(c.name)
            info.cypher_calls += 1
 
        if matched_evidences:
            evidence_names   = [e["name"] for e in matched_evidences]
            entity_evidences = self._retrieve_evidences_by_names(
                evidence_names, paper_id=paper_id
            )
            existing_ev_names = {e.name for e in subgraph.evidences}
            for e in entity_evidences:
                if e.name not in existing_ev_names:
                    subgraph.evidences.append(e)
                    existing_ev_names.add(e.name)
            info.cypher_calls += 1
 
        # Concept hierarchy: BASED_ON + EXTENDS chain
        if subgraph.concepts:
            top_concept_names = [
                c.name for c in sorted(
                    subgraph.concepts, key=lambda x: x.confidence, reverse=True
                )[:self._config.max_concepts_per_query]
            ]
            subgraph.concept_edges = self._retrieve_concept_chain(
                top_concept_names,
                depth=self._config.concept_depth,
            )
            info.cypher_calls += 1
 
        # ── Bước 3: Build text chunks ─────────────────────────────────
        chunks = self._build_text_chunks(subgraph, query_text, keywords)
 
        # Sort + limit
        chunks.sort(key=lambda c: c.score, reverse=True)
        chunks = chunks[:top_k]
 
        info.total_nodes_found = (
            len(subgraph.concepts) + len(subgraph.evidences)
            + len(subgraph.metrics) + len(subgraph.findings)
        )
        info.retrieval_ms = (time.time() - t0) * 1000
 
        logger.info(
            "GraphRetriever: query='%s' paper_id=%s entities=%s "
            "→ chunks=%d nodes=%d (%.0fms)",
            query_text[:50], paper_id,
            "extracted" if has_entities else "fulltext",
            len(chunks), info.total_nodes_found, info.retrieval_ms,
        )
 
        return GraphRetrievalResult(
            chunks=chunks,
            subgraph=subgraph,
            query_info=info,
        )

    # ------------------------------------------------------------------
    # Entity Matching — fulltext search
    # ------------------------------------------------------------------

    def _match_concepts(self, keywords: list[str]) -> list[dict]:
        """
        Match keywords → Concept nodes qua Neo4j fulltext index.
        Trả về list[{name, category, score}].
        """
        if not keywords:
            return []
        results: list[dict] = []
        seen: set[str] = set()

        for kw in keywords[:10]:   # giới hạn để tránh quá nhiều query
            query_str = _escape_lucene(kw)
            cypher = """
            CALL db.index.fulltext.queryNodes('concept_ft', $query_str)
            YIELD node, score
            WHERE score >= $threshold
            RETURN node.name     AS name,
                   node.category AS category,
                   node.domain   AS domain,
                   score
            ORDER BY score DESC
            LIMIT 3
            """
            try:
                rows = self._client.execute_read(cypher, {
                    "query_str": query_str,
                    "threshold": self._config.fulltext_threshold,
                })
                for row in rows:
                    name = row.get("name", "")
                    if name and name not in seen:
                        seen.add(name)
                        results.append({
                            "name":     name,
                            "category": row.get("category", "concept"),
                            "domain":   row.get("domain"),
                            "score":    float(row.get("score", 0.5)),
                        })
            except Exception:
                logger.debug("_match_concepts: fulltext query thất bại kw='%s'", kw)

        # Sort by score, giới hạn
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:self._config.max_concepts_per_query]

    def _match_evidences(self, keywords: list[str]) -> list[dict]:
        """Match keywords → Evidence nodes."""
        if not keywords:
            return []
        results: list[dict] = []
        seen: set[str] = set()

        for kw in keywords[:5]:
            query_str = _escape_lucene(kw)
            cypher = """
            CALL db.index.fulltext.queryNodes('evidence_ft', $query_str)
            YIELD node, score
            WHERE score >= $threshold
            RETURN node.name          AS name,
                   node.evidence_type AS evidence_type,
                   score
            ORDER BY score DESC
            LIMIT 2
            """
            try:
                rows = self._client.execute_read(cypher, {
                    "query_str": query_str,
                    "threshold": self._config.fulltext_threshold,
                })
                for row in rows:
                    name = row.get("name", "")
                    if name and name not in seen:
                        seen.add(name)
                        results.append({
                            "name":          name,
                            "evidence_type": row.get("evidence_type", "dataset"),
                            "score":         float(row.get("score", 0.5)),
                        })
            except Exception:
                logger.debug("_match_evidences: fulltext query thất bại kw='%s'", kw)

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:self._config.max_evidences_per_query]

    # ------------------------------------------------------------------
    # Paper-level retrieval
    # ------------------------------------------------------------------

    def _retrieve_paper(self, paper_id: str) -> Optional[PaperNode]:
        cypher = """
        MATCH (p:Paper {id: $paper_id})
        RETURN p.id       AS paper_id,
               p.title    AS title,
               p.year     AS year,
               p.domain   AS domain,
               p.language AS language
        """
        try:
            rows = self._client.execute_read(cypher, {"paper_id": paper_id})
            if rows:
                r = rows[0]
                return PaperNode(
                    paper_id=r.get("paper_id", paper_id),
                    title=r.get("title", "Unknown"),
                    year=r.get("year"),
                    domain=r.get("domain"),
                    language=r.get("language"),
                )
        except Exception:
            logger.debug("_retrieve_paper: thất bại paper_id=%s", paper_id)
        return None

    def _retrieve_concepts_by_paper(self, paper_id: str) -> list[ConceptNode]:
        cypher = """
        MATCH (p:Paper {id: $paper_id})-[r:USES_CONCEPT]->(c:Concept)
        RETURN c.name           AS name,
               c.category       AS category,
               c.domain         AS domain,
               c.aliases_text   AS aliases_text,
               r.confidence     AS confidence,
               r.source_section AS source_section,
               r.evidence       AS evidence
        ORDER BY r.confidence DESC
        LIMIT $limit
        """
        try:
            rows = self._client.execute_read(cypher, {
                "paper_id": paper_id,
                "limit":    self._config.top_k * 2,
            })
            result = []
            for r in rows:
                aliases_raw = r.get("aliases_text") or ""
                aliases = [a for a in aliases_raw.split("|") if a.strip()]
                result.append(ConceptNode(
                    name=r.get("name", ""),
                    category=r.get("category", "concept"),
                    domain=r.get("domain"),
                    confidence=float(r.get("confidence") or 0.5),
                    source_section=r.get("source_section"),
                    evidence=r.get("evidence") or "",
                    aliases=aliases,
                ))
            return result
        except Exception:
            logger.debug("_retrieve_concepts_by_paper: thất bại paper_id=%s", paper_id)
            return []

    def _retrieve_evidences_by_paper(self, paper_id: str) -> list[EvidenceNode]:
        cypher = """
        MATCH (p:Paper {id: $paper_id})-[r:EVALUATES_ON]->(e:Evidence)
        RETURN e.name          AS name,
               e.evidence_type AS evidence_type,
               e.language      AS language,
               r.confidence    AS confidence
        ORDER BY r.confidence DESC
        LIMIT $limit
        """
        try:
            rows = self._client.execute_read(cypher, {
                "paper_id": paper_id,
                "limit":    self._config.top_k,
            })
            return [
                EvidenceNode(
                    name=r.get("name", ""),
                    evidence_type=r.get("evidence_type", "dataset"),
                    language=r.get("language"),
                    confidence=float(r.get("confidence") or 0.5),
                )
                for r in rows
            ]
        except Exception:
            logger.debug("_retrieve_evidences_by_paper: thất bại paper_id=%s", paper_id)
            return []

    def _retrieve_metrics_by_paper(self, paper_id: str) -> list[MetricNode]:
        cypher = """
        MATCH (p:Paper {id: $paper_id})-[r:ACHIEVES_METRIC]->(m:Metric)
        RETURN m.name        AS name,
               r.value       AS value,
               r.unit        AS unit,
               r.measured_on AS measured_on,
               r.measured_by AS measured_by,
               r.confidence  AS confidence
        ORDER BY r.confidence DESC
        LIMIT $limit
        """
        try:
            rows = self._client.execute_read(cypher, {
                "paper_id": paper_id,
                "limit":    self._config.top_k,
            })
            result = []
            for r in rows:
                value_raw = r.get("value")
                try:
                    value = float(value_raw) if value_raw is not None else None
                except (TypeError, ValueError):
                    value = None
                result.append(MetricNode(
                    name=r.get("name", ""),
                    value=value,
                    unit=r.get("unit"),
                    measured_on=r.get("measured_on") or "",
                    measured_by=r.get("measured_by") or "",
                    confidence=float(r.get("confidence") or 0.5),
                ))
            return result
        except Exception:
            logger.debug("_retrieve_metrics_by_paper: thất bại paper_id=%s", paper_id)
            return []

    def _retrieve_findings_by_paper(self, paper_id: str) -> list[FindingNode]:
        cypher = """
        MATCH (p:Paper {id: $paper_id})-[r:HAS_FINDING]->(f:Finding)
        RETURN f.description  AS description,
               f.finding_type AS finding_type,
               r.confidence   AS confidence
        ORDER BY r.confidence DESC
        LIMIT $limit
        """
        try:
            rows = self._client.execute_read(cypher, {
                "paper_id": paper_id,
                "limit":    self._config.top_k,
            })
            return [
                FindingNode(
                    description=r.get("description", ""),
                    finding_type=r.get("finding_type", "result"),
                    confidence=float(r.get("confidence") or 0.5),
                )
                for r in rows
            ]
        except Exception:
            logger.debug("_retrieve_findings_by_paper: thất bại paper_id=%s", paper_id)
            return []

    def _retrieve_cited_papers(self, paper_id: str) -> list[PaperNode]:
        cypher = """
        MATCH (p:Paper {id: $paper_id})-[:CITES]->(cited:Paper)
        WHERE cited.title IS NOT NULL AND cited.title <> ''
        RETURN cited.id       AS paper_id,
               cited.title    AS title,
               cited.year     AS year,
               cited.domain   AS domain,
               cited.language AS language
        LIMIT 10
        """
        try:
            rows = self._client.execute_read(cypher, {"paper_id": paper_id})
            return [
                PaperNode(
                    paper_id=r.get("paper_id", ""),
                    title=r.get("title", ""),
                    year=r.get("year"),
                    domain=r.get("domain"),
                    language=r.get("language"),
                )
                for r in rows if r.get("title")
            ]
        except Exception:
            logger.debug("_retrieve_cited_papers: thất bại paper_id=%s", paper_id)
            return []

    # ------------------------------------------------------------------
    # Entity-based retrieval — query theo concept / evidence name
    # ------------------------------------------------------------------

    def _retrieve_concepts_by_names(
        self,
        names: list[str],
        paper_id: Optional[str] = None,
    ) -> list[ConceptNode]:
        """
        Query Concept nodes theo tên, optionally filter theo paper_id.
        Nếu paper_id không có, lấy tất cả paper liên quan đến concept này.
        """
        if not names:
            return []

        if paper_id:
            cypher = """
            UNWIND $names AS concept_name
            MATCH (p:Paper {id: $paper_id})-[r:USES_CONCEPT]->(c:Concept {name: concept_name})
            RETURN c.name           AS name,
                   c.category       AS category,
                   c.domain         AS domain,
                   c.aliases_text   AS aliases_text,
                   r.confidence     AS confidence,
                   r.source_section AS source_section,
                   r.evidence       AS evidence
            ORDER BY r.confidence DESC
            """
            params = {"names": names, "paper_id": paper_id}
        else:
            cypher = """
            UNWIND $names AS concept_name
            MATCH (c:Concept {name: concept_name})
            OPTIONAL MATCH (p:Paper)-[r:USES_CONCEPT]->(c)
            RETURN c.name           AS name,
                   c.category       AS category,
                   c.domain         AS domain,
                   c.aliases_text   AS aliases_text,
                   coalesce(max(r.confidence), 0.7) AS confidence,
                   r.source_section AS source_section,
                   r.evidence       AS evidence
            ORDER BY confidence DESC
            """
            params = {"names": names}

        try:
            rows = self._client.execute_read(cypher, params)
            result = []
            seen: set[str] = set()
            for r in rows:
                name = r.get("name", "")
                if not name or name in seen:
                    continue
                seen.add(name)
                aliases_raw = r.get("aliases_text") or ""
                aliases = [a for a in aliases_raw.split("|") if a.strip()]
                result.append(ConceptNode(
                    name=name,
                    category=r.get("category", "concept"),
                    domain=r.get("domain"),
                    confidence=float(r.get("confidence") or 0.5),
                    source_section=r.get("source_section"),
                    evidence=r.get("evidence") or "",
                    aliases=aliases,
                ))
            return result
        except Exception:
            logger.debug("_retrieve_concepts_by_names: thất bại names=%s", names)
            return []

    def _retrieve_evidences_by_names(
        self,
        names: list[str],
        paper_id: Optional[str] = None,
    ) -> list[EvidenceNode]:
        if not names:
            return []

        if paper_id:
            cypher = """
            UNWIND $names AS ev_name
            MATCH (p:Paper {id: $paper_id})-[r:EVALUATES_ON]->(e:Evidence {name: ev_name})
            RETURN e.name          AS name,
                   e.evidence_type AS evidence_type,
                   e.language      AS language,
                   r.confidence    AS confidence
            ORDER BY r.confidence DESC
            """
            params = {"names": names, "paper_id": paper_id}
        else:
            cypher = """
            UNWIND $names AS ev_name
            MATCH (e:Evidence {name: ev_name})
            RETURN e.name          AS name,
                   e.evidence_type AS evidence_type,
                   e.language      AS language,
                   0.7             AS confidence
            """
            params = {"names": names}

        try:
            rows = self._client.execute_read(cypher, params)
            return [
                EvidenceNode(
                    name=r.get("name", ""),
                    evidence_type=r.get("evidence_type", "dataset"),
                    language=r.get("language"),
                    confidence=float(r.get("confidence") or 0.5),
                )
                for r in rows if r.get("name")
            ]
        except Exception:
            logger.debug("_retrieve_evidences_by_names: thất bại names=%s", names)
            return []

    # ------------------------------------------------------------------
    # Concept hierarchy traversal — BASED_ON + EXTENDS chain
    # ------------------------------------------------------------------

    def _retrieve_concept_chain(
        self,
        concept_names: list[str],
        depth: int = 2,
    ) -> list[ConceptEdge]:
        """
        Query BASED_ON + EXTENDS edges theo chain từ concept_names.
        depth: số hop tối đa (1–3, không nên > 3 để tránh timeout).
        """
        if not concept_names or depth < 1:
            return []

        depth = min(depth, 3)  # hard cap

        cypher = """
        UNWIND $names AS concept_name
        MATCH (c1:Concept {name: concept_name})
        CALL {
            WITH c1
            MATCH path = (c1)-[:BASED_ON*1..$depth]->(c2:Concept)
            RETURN c2, 'BASED_ON' AS rel_type,
                   last(relationships(path)) AS last_rel
            UNION
            WITH c1
            MATCH path = (c1)-[:EXTENDS*1..$depth]->(c2:Concept)
            RETURN c2, 'EXTENDS' AS rel_type,
                   last(relationships(path)) AS last_rel
        }
        RETURN c1.name           AS child,
               c2.name           AS parent,
               rel_type,
               coalesce(last_rel.confidence, 0.8) AS confidence
        LIMIT 20
        """
        # Note: Neo4j không support $depth trong range *1..$depth với parameter
        # → hardcode depth trong query string
        cypher_with_depth = cypher.replace("*1..$depth", f"*1..{depth}")

        try:
            rows = self._client.execute_read(
                cypher_with_depth, {"names": concept_names}
            )
            seen: set[tuple] = set()
            result: list[ConceptEdge] = []
            for r in rows:
                child  = r.get("child", "")
                parent = r.get("parent", "")
                rel    = r.get("rel_type", "BASED_ON")
                if not child or not parent:
                    continue
                key = (child, parent, rel)
                if key in seen:
                    continue
                seen.add(key)
                result.append(ConceptEdge(
                    child=child,
                    parent=parent,
                    relation=rel,
                    confidence=float(r.get("confidence") or 0.8),
                ))
            return result
        except Exception:
            logger.debug(
                "_retrieve_concept_chain: thất bại names=%s depth=%d",
                concept_names, depth,
            )
            return []

    # ------------------------------------------------------------------
    # Build text chunks — convert subgraph → list[GraphChunk]
    # ------------------------------------------------------------------

    def _build_text_chunks(
        self,
        subgraph: SubgraphContext,
        query_text: str,
        keywords: list[str],
    ) -> list[GraphChunk]:
        """
        Convert subgraph → list[GraphChunk] tương thích với fusion_retriever.

        Mỗi loại node → 1 hoặc nhiều chunk riêng với score riêng.
        Score = base_score + section_boost + keyword_match_boost.
        """
        chunks: list[GraphChunk] = []

        # ── Paper summary chunk ────────────────────────────────────────
        if subgraph.paper:
            p = subgraph.paper
            year_str = f" ({p.year})" if p.year else ""
            text = f"Paper: {p.title}{year_str}"
            if p.domain:
                text += f"\nDomain: {p.domain}"
            if p.language:
                text += f"\nLanguage: {p.language}"
            chunks.append(GraphChunk(
                text=text,
                score=0.7,
                source="graph_paper",
                metadata={
                    "paper_id": p.paper_id,
                    "title":    p.title,
                    "year":     p.year,
                },
            ))

        # ── Concept chunks ─────────────────────────────────────────────
        for concept in subgraph.concepts:
            base_score = concept.confidence
            # Boost nếu concept name / alias match keyword
            kw_boost = self._keyword_match_boost(
                concept.name, keywords, aliases=concept.aliases
            )
            section_boost = _score_by_section(concept.source_section)
            score = min(1.0, base_score + kw_boost + section_boost)

            if score < self._config.min_score:
                continue

            text_parts = [f"Concept [{concept.category}]: {concept.name}"]
            if concept.domain:
                text_parts.append(f"Domain: {concept.domain}")
            if concept.aliases:
                text_parts.append(f"Also known as: {', '.join(concept.aliases[:3])}")
            if concept.evidence:
                text_parts.append(f"Evidence: {concept.evidence[:200]}")

            chunks.append(GraphChunk(
                text="\n".join(text_parts),
                score=round(score, 4),
                source="graph_concept",
                metadata={
                    "concept_name":   concept.name,
                    "category":       concept.category,
                    "source_section": concept.source_section,
                    "paper_id":       subgraph.paper.paper_id if subgraph.paper else None,
                },
            ))

        # ── Evidence chunks ────────────────────────────────────────────
        for ev in subgraph.evidences:
            base_score = ev.confidence
            kw_boost   = self._keyword_match_boost(ev.name, keywords)
            score      = min(1.0, base_score + kw_boost)

            if score < self._config.min_score:
                continue

            text_parts = [f"Evidence [{ev.evidence_type}]: {ev.name}"]
            if ev.language:
                text_parts.append(f"Language: {ev.language}")

            chunks.append(GraphChunk(
                text="\n".join(text_parts),
                score=round(score, 4),
                source="graph_evidence",
                metadata={
                    "evidence_name": ev.name,
                    "evidence_type": ev.evidence_type,
                    "paper_id":      subgraph.paper.paper_id if subgraph.paper else None,
                },
            ))

        # ── Metric chunks ──────────────────────────────────────────────
        for metric in subgraph.metrics:
            kw_boost = self._keyword_match_boost(metric.name, keywords)
            score    = min(1.0, metric.confidence + kw_boost)

            if score < self._config.min_score:
                continue

            value_str = f"={metric.value}{metric.unit or ''}" if metric.value is not None else ""
            text_parts = [f"Metric: {metric.name}{value_str}"]
            if metric.measured_on:
                text_parts.append(f"Measured on: {metric.measured_on}")
            if metric.measured_by:
                text_parts.append(f"Measured by: {metric.measured_by}")

            chunks.append(GraphChunk(
                text="\n".join(text_parts),
                score=round(score, 4),
                source="graph_metric",
                metadata={
                    "metric_name": metric.name,
                    "value":       metric.value,
                    "unit":        metric.unit,
                    "measured_on": metric.measured_on,
                    "measured_by": metric.measured_by,
                    "paper_id":    subgraph.paper.paper_id if subgraph.paper else None,
                },
            ))

        # ── Finding chunks ─────────────────────────────────────────────
        for finding in subgraph.findings:
            kw_boost = self._keyword_match_boost(finding.description, keywords)
            score    = min(1.0, finding.confidence + kw_boost)

            if score < self._config.min_score:
                continue

            chunks.append(GraphChunk(
                text=f"Finding [{finding.finding_type}]: {finding.description}",
                score=round(score, 4),
                source="graph_finding",
                metadata={
                    "finding_type": finding.finding_type,
                    "paper_id":     subgraph.paper.paper_id if subgraph.paper else None,
                },
            ))

        # ── Concept hierarchy chunk ────────────────────────────────────
        if subgraph.concept_edges:
            hierarchy_lines = []
            for edge in subgraph.concept_edges[:8]:
                hierarchy_lines.append(
                    f"  {edge.child} --[{edge.relation}]--> {edge.parent}"
                )
            if hierarchy_lines:
                chunks.append(GraphChunk(
                    text="Concept hierarchy:\n" + "\n".join(hierarchy_lines),
                    score=0.6,
                    source="graph_concept",
                    metadata={"type": "concept_hierarchy"},
                ))

        # ── Citation chunk ─────────────────────────────────────────────
        if subgraph.cited_papers:
            cited_lines = []
            for cp in subgraph.cited_papers[:5]:
                year_str = f" ({cp.year})" if cp.year else ""
                cited_lines.append(f"  - {cp.title[:80]}{year_str}")
            if cited_lines:
                chunks.append(GraphChunk(
                    text="Related papers (cited):\n" + "\n".join(cited_lines),
                    score=0.5,
                    source="graph_citation",
                    metadata={"type": "citation_list"},
                ))

        return chunks

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _keyword_match_boost(
        text: str,
        keywords: list[str],
        aliases: Optional[list[str]] = None,
    ) -> float:
        """
        Tính boost score nếu keyword xuất hiện trong text hoặc aliases.
        Mỗi keyword match → +0.05, tối đa +0.20.
        """
        if not keywords:
            return 0.0

        text_norm = _normalize(text)
        all_texts = [text_norm] + [_normalize(a) for a in (aliases or [])]

        boost = 0.0
        for kw in keywords:
            kw_norm = _normalize(kw)
            if any(kw_norm in t for t in all_texts):
                boost += 0.05
        return min(boost, 0.20)


# =============================================================================
# FACTORY
# =============================================================================

def create_graph_retriever_from_env() -> GraphRetriever:
    """
    Tạo GraphRetriever từ biến môi trường.

    .env:
        NEO4J_URI=neo4j+s://<your-instance>.databases.neo4j.io
        NEO4J_USER=<your-username>
        NEO4J_PASSWORD=<your-password>
        NEO4J_USER_DOC_DB=neo4j
        GRAPH_RETRIEVER_TOP_K=10
        GRAPH_RETRIEVER_DEPTH=2
        GRAPH_RETRIEVER_MIN_SCORE=0.3
        GRAPH_RETRIEVER_FULLTEXT_THRESHOLD=0.6
    """
    import os
    from storage.graph_db.neo4j_client import Neo4jClient, Neo4jConfig

    database = os.getenv("NEO4J_USER_DOC_DB", os.getenv("NEO4J_DATABASE", "neo4j"))

    neo4j_client = Neo4jClient(Neo4jConfig(
        uri=      os.getenv("NEO4J_URI", ""),
        username= os.getenv("NEO4J_USER", os.getenv("NEO4J_USERNAME", "")),
        password= os.getenv("NEO4J_PASSWORD", ""),
        database= database,
    ))
    if not neo4j_client.config.uri or not neo4j_client.config.username or not neo4j_client.config.password:
        raise ValueError("Missing Aura Neo4j config: NEO4J_URI/NEO4J_USER(or NEO4J_USERNAME)/NEO4J_PASSWORD")
    neo4j_client.connect()

    config = GraphRetrieverConfig(
        database=             database,
        top_k=                int(os.getenv("GRAPH_RETRIEVER_TOP_K",    "10")),
        concept_depth=        int(os.getenv("GRAPH_RETRIEVER_DEPTH",     "2")),
        min_score=            float(os.getenv("GRAPH_RETRIEVER_MIN_SCORE", "0.3")),
        fulltext_threshold=   float(os.getenv("GRAPH_RETRIEVER_FULLTEXT_THRESHOLD", "0.6")),
    )

    logger.info(
        "GraphRetriever: database=%s top_k=%d depth=%d",
        database, config.top_k, config.concept_depth,
    )
    return GraphRetriever(neo4j_client, config)
