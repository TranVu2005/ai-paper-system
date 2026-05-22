"""
storage/graph_db/neo4j_client.py

Neo4j client cho Paper RAG + Knowledge Graph System.
Implement toàn bộ MERGE template từ neo4j_schema.cypher.
Bám sát UnifiedDocument / Reference schema từ document_schema.py.

Requires:
    pip install neo4j>=5.0.0

Changelog:
    [fix-1]  ensure_schema đưa vào trong class Neo4jClient
    [fix-2]  merge_method / merge_dataset / merge_task thêm ON MATCH SET confidence
    [fix-3]  merge_citation thêm note về title collision khi year=null
    [fix-4]  Thêm run_query() — graph_updater.py dùng qua Neo4jClientProtocol
    [fix-5]  Thêm lookup_method_by_fulltext() và lookup_dataset_by_fulltext()
    [fix-6]  merge_author KHÔNG raise ValueError khi paper_id không tìm thấy
    [fix-7]  merge_paper_by_title_year normalize year=None → 0 (sentinel)
    [fix-8]  merge_institution đổi raise thành log error + raise Neo4jMergeError
    [fix-9]  merge_citation normalize cited_year=None → 0 (sentinel)
    [fix-10] merge_method ON MATCH SET bổ sung category và aliases_text
             merge_dataset ON MATCH SET bổ sung language và aliases_text
    [fix-11] ensure_schema bổ sung constraint chunk_qdrant_id
    [fix-12] merge_metric: measured_on/measured_by dùng "" thay None làm MERGE key
             tránh lỗi "Cannot merge with null property value"
    [fix-13] Thêm _escape_lucene() — escape Lucene special chars trước khi
             truyền vào db.index.fulltext.queryNodes, tránh ParseException
             khi tên entity chứa [], (), -, /, ... (thiết bị, báo cáo tiếng Việt)
    [fix-14] merge_paper_by_doi + merge_paper_by_title_year: MERGE theo id thay vì
             doi / title+year để tương thích với try_claim_paper (đã dùng MERGE id).
             Tránh ConstraintError khi stub node đã tồn tại theo id.
             try_claim_paper: đổi MATCH → MERGE để tạo stub nếu paper chưa tồn tại.

    [v2-1]  Thêm merge_concept()          — thay merge_method() + merge_task()
    [v2-2]  Thêm merge_evidence()         — thay merge_dataset()
    [v2-3]  Thêm merge_metric()           — node Metric + edge ACHIEVES_METRIC
    [v2-4]  Thêm merge_finding()          — node Finding + edge HAS_FINDING
    [v2-5]  Thêm merge_concept_based_on() — thay merge_method_based_on()
    [v2-6]  Thêm merge_concept_extends()  — edge EXTENDS mới
    [v2-7]  Thêm merge_paper_supports()   — edge SUPPORTS từ FindingEntity
    [v2-8]  Thêm merge_paper_contradicts()— edge CONTRADICTS từ FindingEntity
    [v2-9]  Thêm lookup_concept_by_fulltext()  — thay lookup_method_by_fulltext()
    [v2-10] Thêm lookup_evidence_by_fulltext() — thay lookup_dataset_by_fulltext()
    [v2-11] merge_citation thêm param cited_doc_id — graph_builder truyền internal_doc_id
    [v2-12] ensure_schema thêm constraints + fulltext indexes cho schema mới
    [v2-13] Giữ nguyên merge_method/dataset/task + lookup cũ để backward compat
            (legacy code có thể vẫn gọi) — đánh dấu DEPRECATED

    [v2-14] merge_concept_based_on: thêm Cypher cycle guard (*1..3) trước MERGE.
            Đây là lớp 2 defense — lớp 1 là in-memory DFS trong graph_builder.py.
            Nếu phát hiện cycle: log WARNING + return, không raise exception,
            để pipeline tiếp tục xử lý các edge còn lại.
    [v2-15] merge_concept_extends: tương tự v2-14, thêm Cypher cycle guard cho EXTENDS.
"""

from __future__ import annotations

import logging
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Optional

from neo4j import GraphDatabase, Session
from neo4j.exceptions import ServiceUnavailable, TransientError

logger = logging.getLogger(__name__)


# =============================================================================
# CUSTOM EXCEPTION  [fix-6]
# =============================================================================

class Neo4jMergeError(Exception):
    """
    Raise khi MERGE thất bại vì node prerequisite không tồn tại.

    Lý do dùng custom exception thay vì ValueError:
        - Caller (graph_builder, BatchGraphBuilder) có thể catch
          Neo4jMergeError riêng mà không accidentally catch ValueError
          từ logic khác.
        - BatchGraphBuilder.continue_on_error sẽ log + continue đúng behavior.
    """


# =============================================================================
# CONFIG
# =============================================================================

@dataclass
class Neo4jConfig:
    uri:                      str   = ""
    username:                 str   = ""
    password:                 str   = ""
    database:                 str   = "neo4j"
    max_connection_pool_size: int   = 50
    connection_timeout:       float = 30.0
    max_retry_time:           float = 30.0


# =============================================================================
# HELPERS
# =============================================================================

def _normalize_name(text: str) -> str:
    """
    Lowercase + bỏ dấu tiếng Việt + collapse whitespace.
    Dùng cho Institution.name (MERGE key) và Topic.name.
    """
    nfkd      = unicodedata.normalize("NFKD", text)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(ascii_str.lower().split())


def _to_ascii(text: str) -> str:
    """Bỏ dấu, giữ nguyên case — dùng cho name_ascii (full-text index)."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _new_id() -> str:
    return str(uuid.uuid4())


def _escape_lucene(text: str) -> str:
    """
    Escape Lucene special characters trước khi truyền vào
    db.index.fulltext.queryNodes().  [fix-13]

    Lucene special chars: + - & | ! ( ) { } [ ] ^ " ~ * ? : \\ /
    Nếu không escape, tên entity chứa các ký tự này (VD: "gc-2030 (shimadzu)",
    "[2]", "qđ attp", "/btnmt") sẽ gây ParseException / TokenMgrError.

    Chỉ escape — không xóa — để vẫn giữ nguyên ngữ nghĩa tìm kiếm.
    """
    special = set('+-&|!(){}[]^"~*?:\\/')
    return "".join(f"\\{c}" if c in special else c for c in text)


class Neo4jClient:
    """
    Thread-safe Neo4j client với connection pool.

    Dùng context manager:
        with Neo4jClient(config) as client:
            client.merge_paper(doc_dict)
    """

    def __init__(self, config: Neo4jConfig):
        self._config = config
        self._driver = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> None:
        self._driver = GraphDatabase.driver(
            self._config.uri,
            auth=(self._config.username, self._config.password),
            max_connection_pool_size=self._config.max_connection_pool_size,
            connection_timeout=self._config.connection_timeout,
        )
        self._driver.verify_connectivity()
        logger.info("Neo4j connected: %s", self._config.uri)

    def close(self) -> None:
        if self._driver:
            self._driver.close()
            self._driver = None
            logger.info("Neo4j connection closed.")

    def __enter__(self) -> "Neo4jClient":
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    @contextmanager
    def _session(self):
        if not self._driver:
            raise RuntimeError("Neo4jClient chưa connect. Gọi client.connect() trước.")
        session: Session = self._driver.session(database=self._config.database)
        try:
            yield session
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Schema setup  [fix-1] [v2-12]
    # ------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """
        Tạo constraints + indexes cho toàn bộ schema mới.
        Thứ tự: Constraints → Property Indexes → Fulltext Indexes.
        Idempotent — an toàn khi chạy lại.
        """
        constraints = [
            # ── Paper ──────────────────────────────────────────────────
            "CREATE CONSTRAINT paper_id_unique IF NOT EXISTS FOR (p:Paper) REQUIRE p.id IS UNIQUE",
            "CREATE CONSTRAINT paper_doi_unique IF NOT EXISTS FOR (p:Paper) REQUIRE p.doi IS UNIQUE",

            # ── Author / Institution / Venue ───────────────────────────
            "CREATE CONSTRAINT author_id_unique IF NOT EXISTS FOR (a:Author) REQUIRE a.id IS UNIQUE",
            "CREATE CONSTRAINT institution_name_unique IF NOT EXISTS FOR (i:Institution) REQUIRE i.name IS UNIQUE",
            "CREATE CONSTRAINT venue_name_unique IF NOT EXISTS FOR (v:Venue) REQUIRE v.name IS UNIQUE",

            # ── Topic ──────────────────────────────────────────────────
            "CREATE CONSTRAINT topic_name_unique IF NOT EXISTS FOR (t:Topic) REQUIRE t.name IS UNIQUE",

            # ── Schema mới [v2-12] ─────────────────────────────────────
            "CREATE CONSTRAINT concept_name_unique IF NOT EXISTS FOR (c:Concept) REQUIRE c.name IS UNIQUE",
            "CREATE CONSTRAINT evidence_name_unique IF NOT EXISTS FOR (e:Evidence) REQUIRE e.name IS UNIQUE",
            # Metric không có global unique key vì cùng tên metric có thể đo trên dataset khác nhau.
            # MERGE key = (name, measured_on, measured_by) — enforce ở tầng Python.

            # ── Chunk [fix-11] ─────────────────────────────────────────
            "CREATE CONSTRAINT chunk_qdrant_id_unique IF NOT EXISTS FOR (c:Chunk) REQUIRE c.qdrant_id IS UNIQUE",

            # ── Legacy — giữ để backward compat [v2-13] ────────────────
            "CREATE CONSTRAINT method_name_unique IF NOT EXISTS FOR (m:Method) REQUIRE m.name IS UNIQUE",
            "CREATE CONSTRAINT dataset_name_unique IF NOT EXISTS FOR (d:Dataset) REQUIRE d.name IS UNIQUE",
            "CREATE CONSTRAINT task_name_unique IF NOT EXISTS FOR (tk:Task) REQUIRE tk.name IS UNIQUE",
        ]
        for cypher in constraints:
            self.execute_write(cypher)
        logger.info("ensure_schema: constraints OK")

        property_indexes = [
            # Paper
            "CREATE INDEX paper_year IF NOT EXISTS FOR (p:Paper) ON (p.year)",
            "CREATE INDEX paper_language IF NOT EXISTS FOR (p:Paper) ON (p.language)",
            "CREATE INDEX paper_status IF NOT EXISTS FOR (p:Paper) ON (p.processing_status)",
            "CREATE INDEX paper_title_year IF NOT EXISTS FOR (p:Paper) ON (p.title, p.year)",
            # Author / Institution
            "CREATE INDEX author_affiliation IF NOT EXISTS FOR (a:Author) ON (a.affiliation)",
            "CREATE INDEX institution_country IF NOT EXISTS FOR (i:Institution) ON (i.country)",
            # Schema mới [v2-12]
            "CREATE INDEX concept_category IF NOT EXISTS FOR (c:Concept) ON (c.category)",
            "CREATE INDEX concept_domain IF NOT EXISTS FOR (c:Concept) ON (c.domain)",
            "CREATE INDEX evidence_type IF NOT EXISTS FOR (e:Evidence) ON (e.evidence_type)",
            "CREATE INDEX evidence_language IF NOT EXISTS FOR (e:Evidence) ON (e.language)",
            "CREATE INDEX finding_type IF NOT EXISTS FOR (f:Finding) ON (f.finding_type)",
            # SIMILAR_TO
            "CREATE INDEX similar_to_score IF NOT EXISTS FOR ()-[r:SIMILAR_TO]-() ON (r.score)",
            # Legacy
            "CREATE INDEX method_category IF NOT EXISTS FOR (m:Method) ON (m.category)",
            "CREATE INDEX dataset_language IF NOT EXISTS FOR (d:Dataset) ON (d.language)",
        ]
        for cypher in property_indexes:
            self.execute_write(cypher)
        logger.info("ensure_schema: property indexes OK")

        fulltext_indexes = [
            # Author / Paper / Institution
            "CREATE FULLTEXT INDEX author_ft IF NOT EXISTS FOR (a:Author) ON EACH [a.name, a.name_ascii]",
            "CREATE FULLTEXT INDEX paper_title_ft IF NOT EXISTS FOR (p:Paper) ON EACH [p.title]",
            "CREATE FULLTEXT INDEX institution_ft IF NOT EXISTS FOR (i:Institution) ON EACH [i.name, i.name_ascii]",
            # Schema mới [v2-12]
            "CREATE FULLTEXT INDEX concept_ft IF NOT EXISTS FOR (c:Concept) ON EACH [c.name, c.aliases_text]",
            "CREATE FULLTEXT INDEX evidence_ft IF NOT EXISTS FOR (e:Evidence) ON EACH [e.name, e.aliases_text]",
            # Legacy [v2-13]
            "CREATE FULLTEXT INDEX method_ft IF NOT EXISTS FOR (m:Method) ON EACH [m.name, m.aliases_text]",
            "CREATE FULLTEXT INDEX dataset_ft IF NOT EXISTS FOR (d:Dataset) ON EACH [d.name, d.aliases_text]",
        ]
        for cypher in fulltext_indexes:
            self.execute_write(cypher)
        logger.info("ensure_schema: fulltext indexes OK")

    # ------------------------------------------------------------------
    # Low-level execute helpers
    # ------------------------------------------------------------------

    def execute_write(self, query: str, params: dict[str, Any] | None = None) -> list[dict]:
        params = params or {}
        with self._session() as session:
            result = session.execute_write(lambda tx: list(tx.run(query, params)))
            return [dict(r) for r in result]

    def execute_read(self, query: str, params: dict[str, Any] | None = None) -> list[dict]:
        params = params or {}
        with self._session() as session:
            result = session.execute_read(lambda tx: list(tx.run(query, params)))
            return [dict(r) for r in result]

    def run_query(self, cypher: str, params: dict | None = None) -> list[dict]:
        """
        Generic query method — dùng bởi graph_updater.py qua Neo4jClientProtocol.  [fix-4]
        Dùng execute_write vì graph_updater chủ yếu SET/MERGE.
        """
        return self.execute_write(cypher, params)

    def try_claim_paper(self, paper_id: str) -> bool:
        """
        Atomic claim paper để tránh 2 worker cùng xử lý 1 paper.
        [fix-14] Dùng MERGE thay MATCH — tạo stub nếu paper chưa tồn tại trong DB.
                 MATCH cũ sẽ fail khi DB trống, khiến mọi paper bị skip.
        """
        query = """
        MERGE (p:Paper {id: $id})
        ON CREATE SET
            p.processing_status = 'processing',
            p.created_at        = datetime()
        ON MATCH SET
            p.processing_status = CASE
                WHEN p.processing_status IN ['parsed', 'stub']
                THEN 'processing'
                ELSE p.processing_status
            END
        WITH p
        WHERE p.processing_status = 'processing'
        RETURN p.id AS claimed
        """
        result = self.execute_write(query, {"id": paper_id})
        return bool(result)

    # ------------------------------------------------------------------
    # PAPER
    # ------------------------------------------------------------------

    def merge_paper_by_doi(
        self,
        *,
        paper_id:       str,
        doi:            str,
        title:          str,
        year:           Optional[int],
        abstract:       str           = "",
        language:       str           = "vi",
        domain:         Optional[str] = None,
        source_file:    str           = "",
        source_type:    str           = "",
        citation_count: Optional[int] = None,
        chunk_ids:      list[str] | None = None,
        page_count:     Optional[int] = None,
    ) -> None:
        assert doi, "merge_paper_by_doi: doi không được rỗng"
        # [fix-14] MERGE theo id thay vì doi — tương thích với try_claim_paper.
        # try_claim_paper tạo stub theo id, nếu merge_paper_by_doi dùng MERGE doi
        # sẽ tạo node thứ 2 với cùng id → ConstraintError paper_id_unique.
        query = """
        MERGE (p:Paper {id: $id})
        ON CREATE SET
            p.doi               = $doi,
            p.title             = $title,
            p.year              = $year,
            p.abstract          = $abstract,
            p.language          = $language,
            p.domain            = $domain,
            p.source_file       = $source_file,
            p.source_type       = $source_type,
            p.page_count        = $page_count,
            p.citation_count    = $citation_count,
            p.chunk_ids         = $chunk_ids,
            p.processing_status = 'parsed',
            p.created_at        = datetime()
        ON MATCH SET
            p.doi               = $doi,
            p.title             = $title,
            p.year              = $year,
            p.abstract          = CASE WHEN $abstract <> '' THEN $abstract ELSE p.abstract END,
            p.language          = $language,
            p.domain            = CASE WHEN $domain IS NOT NULL THEN $domain ELSE p.domain END,
            p.source_file       = $source_file,
            p.source_type       = $source_type,
            p.page_count        = $page_count,
            p.citation_count    = $citation_count,
            p.chunk_ids         = $chunk_ids,
            p.processing_status = 'parsed'
        """
        self.execute_write(query, {
            "id":             paper_id,
            "doi":            doi,
            "title":          title,
            "year":           year,
            "abstract":       abstract,
            "language":       language,
            "domain":         domain,
            "source_file":    source_file,
            "source_type":    source_type,
            "page_count":     page_count,
            "citation_count": citation_count,
            "chunk_ids":      chunk_ids or [],
        })
        logger.debug("merge_paper_by_doi: doi=%s", doi)

    def merge_paper_by_title_year(
        self,
        *,
        paper_id:    str,
        title:       str,
        year:        Optional[int],
        language:    str           = "vi",
        domain:      Optional[str] = None,
        source_file: str           = "",
        source_type: str           = "",
        chunk_ids:   list[str] | None = None,
        page_count:  Optional[int] = None,
    ) -> None:
        """
        Fallback MERGE khi doi = None.
        [fix-7]  year=None → normalize thành 0 (sentinel).
        [fix-14] MERGE theo id thay vì title+year — tương thích với try_claim_paper.
        """
        year_safe = year if year is not None else 0
        query = """
        MERGE (p:Paper {id: $id})
        ON CREATE SET
            p.title             = $title,
            p.year              = $year,
            p.language          = $language,
            p.domain            = $domain,
            p.source_file       = $source_file,
            p.source_type       = $source_type,
            p.page_count        = $page_count,
            p.chunk_ids         = $chunk_ids,
            p.processing_status = 'parsed',
            p.created_at        = datetime()
        ON MATCH SET
            p.title             = $title,
            p.year              = $year,
            p.language          = $language,
            p.domain            = CASE WHEN $domain IS NOT NULL THEN $domain ELSE p.domain END,
            p.chunk_ids         = $chunk_ids,
            p.processing_status = CASE
                WHEN p.processing_status IN ['stub', 'processing'] THEN 'parsed'
                ELSE p.processing_status
            END
        """
        self.execute_write(query, {
            "id":          paper_id,
            "title":       title,
            "year":        year_safe,
            "language":    language,
            "domain":      domain,
            "source_file": source_file,
            "source_type": source_type,
            "page_count":  page_count,
            "chunk_ids":   chunk_ids or [],
        })
        logger.debug("merge_paper_by_title_year: title=%s year=%s", title, year_safe)

    def merge_paper(self, doc_dict: dict) -> None:
        """
        Entry point cho graph_builder.py.
        Tự động chọn strategy doi vs title+year.

        doc_dict keys: id, title, year, doi, abstract, language, domain,
                       source_file, source_type, page_count, citation_count, chunk_ids
        """
        doi = doc_dict.get("doi") or ""
        if doi.strip():
            self.merge_paper_by_doi(
                paper_id=       doc_dict["id"],
                doi=            doi.strip(),
                title=          doc_dict.get("title", "Unknown"),
                year=           doc_dict.get("year"),
                abstract=       doc_dict.get("abstract", ""),
                language=       doc_dict.get("language", "vi"),
                domain=         doc_dict.get("domain"),
                source_file=    doc_dict.get("source_file", ""),
                source_type=    doc_dict.get("source_type", ""),
                citation_count= doc_dict.get("citation_count"),
                chunk_ids=      doc_dict.get("chunk_ids", []),
                page_count=     doc_dict.get("page_count"),
            )
        else:
            self.merge_paper_by_title_year(
                paper_id=    doc_dict["id"],
                title=       doc_dict.get("title", "Unknown"),
                year=        doc_dict.get("year"),
                language=    doc_dict.get("language", "vi"),
                domain=      doc_dict.get("domain"),
                source_file= doc_dict.get("source_file", ""),
                source_type= doc_dict.get("source_type", ""),
                chunk_ids=   doc_dict.get("chunk_ids", []),
                page_count=  doc_dict.get("page_count"),
            )

    # ------------------------------------------------------------------
    # AUTHOR
    # ------------------------------------------------------------------

    def lookup_author_by_fulltext(
        self,
        name:        str,
        affiliation: Optional[str] = None,
        threshold:   float         = 0.8,
    ) -> Optional[str]:
        # [fix-13] escape Lucene special chars
        query_str = _escape_lucene(name.strip())
        if affiliation and affiliation.strip():
            query_str = f"{query_str} {_escape_lucene(affiliation.strip())}"
        query = """
        CALL db.index.fulltext.queryNodes('author_ft', $query_str)
        YIELD node, score
        WHERE score >= $threshold
        RETURN node.id AS id, node.name AS name, score
        ORDER BY score DESC
        LIMIT 1
        """
        results = self.execute_read(query, {"query_str": query_str, "threshold": threshold})
        if results:
            logger.debug("lookup_author_by_fulltext: found '%s' score=%.2f",
                         results[0]["name"], results[0]["score"])
            return results[0]["id"]
        return None

    def merge_author(
        self,
        *,
        author_id:   str,
        name:        str,
        paper_id:    str,
        order:       int,
        email:       Optional[str] = None,
        affiliation: Optional[str] = None,
    ) -> None:
        """
        MERGE Author + edge (Author)-[:WROTE {order}]->(Paper).
        [fix-6] raise Neo4jMergeError thay vì ValueError khi paper_id không tìm thấy.
        """
        name_ascii = _to_ascii(name)
        query = """
        MERGE (a:Author {id: $author_id})
        ON CREATE SET
            a.name        = $name,
            a.name_ascii  = $name_ascii,
            a.email       = $email,
            a.affiliation = $affiliation
        WITH a
        MATCH (p:Paper {id: $paper_id})
        MERGE (a)-[:WROTE {order: $order}]->(p)
        RETURN p.id AS paper_found
        """
        result = self.execute_write(query, {
            "author_id":   author_id,
            "name":        name,
            "name_ascii":  name_ascii,
            "email":       email,
            "affiliation": affiliation,
            "paper_id":    paper_id,
            "order":       order,
        })
        if not result:
            logger.error(
                "merge_author: paper_id='%s' không tồn tại. Author '%s' không được link.",
                paper_id, name,
            )
            raise Neo4jMergeError(
                f"merge_author: paper_id '{paper_id}' không tìm thấy — "
                f"author '{name}' không được link vào graph."
            )

    # ------------------------------------------------------------------
    # INSTITUTION
    # ------------------------------------------------------------------

    def merge_institution(
        self,
        *,
        author_id:    str,
        display_name: str,
        country:      Optional[str] = None,
        inst_type:    Optional[str] = None,
    ) -> None:
        """[fix-8] raise Neo4jMergeError thay vì ValueError."""
        name_normalized = _normalize_name(display_name)
        name_ascii      = _to_ascii(display_name)
        query = """
        MERGE (i:Institution {name: $name_normalized})
        ON CREATE SET
            i.id           = randomUUID(),
            i.display_name = $display_name,
            i.name_ascii   = $name_ascii,
            i.country      = $country,
            i.type         = $inst_type
        WITH i
        MATCH (a:Author {id: $author_id})
        MERGE (a)-[:AFFILIATED_WITH]->(i)
        RETURN a.id AS author_found
        """
        result = self.execute_write(query, {
            "name_normalized": name_normalized,
            "display_name":    display_name,
            "name_ascii":      name_ascii,
            "country":         country,
            "inst_type":       inst_type,
            "author_id":       author_id,
        })
        if not result:
            logger.error(
                "merge_institution: author_id='%s' không tồn tại. Institution '%s' không link.",
                author_id, display_name,
            )
            raise Neo4jMergeError(
                f"merge_institution: author_id '{author_id}' không tìm thấy — "
                f"institution '{display_name}' không được link."
            )

    # ------------------------------------------------------------------
    # VENUE
    # ------------------------------------------------------------------

    def merge_venue(
        self,
        *,
        paper_id:   str,
        venue_name: str,
        venue_type: str           = "journal",
        publisher:  Optional[str] = None,
        volume:     Optional[str] = None,
        issue:      Optional[str] = None,
        pages:      Optional[str] = None,
    ) -> None:
        name_normalized = _normalize_name(venue_name)
        query = """
        MERGE (v:Venue {name: $name_normalized})
        ON CREATE SET
            v.id        = randomUUID(),
            v.type      = $venue_type,
            v.publisher = $publisher
        WITH v
        MATCH (p:Paper {id: $paper_id})
        MERGE (p)-[r:PUBLISHED_AT]->(v)
        ON CREATE SET r.volume = $volume, r.issue = $issue, r.pages = $pages
        ON MATCH SET  r.volume = $volume, r.issue = $issue, r.pages = $pages
        """
        self.execute_write(query, {
            "name_normalized": name_normalized,
            "venue_type":      venue_type,
            "publisher":       publisher,
            "paper_id":        paper_id,
            "volume":          volume,
            "issue":           issue,
            "pages":           pages,
        })

    # ------------------------------------------------------------------
    # TOPIC
    # ------------------------------------------------------------------

    def merge_topics(
        self,
        *,
        paper_id: str,
        topics:   list[str],
        source:   str = "keyword",
    ) -> None:
        normalized = [t.lower().strip() for t in topics if t.strip()]
        if not normalized:
            return
        if not self.get_paper_by_id(paper_id):
            raise Neo4jMergeError(
                f"merge_topics: paper_id '{paper_id}' không tìm thấy."
            )
        query = """
        UNWIND $topics AS topic_name
        MERGE (t:Topic {name: topic_name})
        ON CREATE SET t.id = randomUUID()
        WITH t, topic_name
        MATCH (p:Paper {id: $paper_id})
        MERGE (p)-[r:HAS_TOPIC]->(t)
        ON CREATE SET r.source = $source
        ON MATCH SET  r.source = $source
        """
        self.execute_write(query, {"topics": normalized, "paper_id": paper_id, "source": source})

    # ------------------------------------------------------------------
    # CONCEPT  [v2-1]
    # ------------------------------------------------------------------

    def merge_concept(
        self,
        *,
        paper_id:       str,
        concept_name:   str,
        category:       str           = "concept",
        aliases:        list[str] | None = None,
        domain:         Optional[str] = None,
        source_section: str           = "method",
        confidence:     float         = 1.0,
        evidence:       str           = "",
    ) -> None:
        name_canonical = concept_name.lower().strip()
        aliases_text   = "|".join(a.lower().strip() for a in (aliases or []) if a.strip())
        query = """
        MERGE (c:Concept {name: $concept_name})
        ON CREATE SET
            c.id           = randomUUID(),
            c.category     = $category,
            c.domain       = $domain,
            c.aliases_text = $aliases_text
        ON MATCH SET
            c.category     = CASE
                WHEN $category IS NOT NULL AND $category <> 'concept'
                THEN $category ELSE c.category END,
            c.domain       = CASE
                WHEN $domain IS NOT NULL THEN $domain ELSE c.domain END,
            c.aliases_text = CASE
                WHEN $aliases_text IS NOT NULL AND $aliases_text <> ''
                    AND (c.aliases_text IS NULL OR c.aliases_text = '')
                THEN $aliases_text
                WHEN $aliases_text IS NOT NULL AND $aliases_text <> ''
                    AND NOT $aliases_text IN split(c.aliases_text, '|')
                THEN c.aliases_text + '|' + $aliases_text
                ELSE c.aliases_text
            END
        WITH c
        MATCH (p:Paper {id: $paper_id})
        MERGE (p)-[r:USES_CONCEPT]->(c)
        ON CREATE SET
            r.category       = $category,
            r.source_section = $source_section,
            r.confidence     = $confidence,
            r.evidence       = $evidence
        ON MATCH SET
            r.category       = $category,
            r.confidence     = CASE WHEN $confidence > r.confidence
                               THEN $confidence ELSE r.confidence END,
            r.evidence       = CASE WHEN $confidence > r.confidence
                               THEN $evidence ELSE r.evidence END
        """
        self.execute_write(query, {
            "concept_name":   name_canonical,
            "category":       category,
            "domain":         domain,
            "aliases_text":   aliases_text,
            "paper_id":       paper_id,
            "source_section": source_section,
            "confidence":     confidence,
            "evidence":       evidence[:200],
        })
        logger.debug("merge_concept: '%s' category=%s paper=%s", name_canonical, category, paper_id)

    def lookup_concept_by_fulltext(
        self,
        name:      str,
        threshold: float = 0.75,
    ) -> Optional[str]:
        """
        Full-text search Concept node theo name và aliases_text.  [v2-9]
        [fix-13] escape Lucene special chars trước khi query.
        """
        query = """
        CALL db.index.fulltext.queryNodes('concept_ft', $query_str)
        YIELD node, score
        WHERE score >= $threshold
        RETURN node.name AS name, score
        ORDER BY score DESC
        LIMIT 1
        """
        query_str = _escape_lucene(name.strip())
        results = self.execute_read(query, {"query_str": query_str, "threshold": threshold})
        if results:
            logger.debug("lookup_concept_by_fulltext: found '%s' score=%.2f",
                         results[0]["name"], results[0]["score"])
            return results[0]["name"]
        return None

    # ------------------------------------------------------------------
    # EVIDENCE  [v2-2]
    # ------------------------------------------------------------------

    def merge_evidence(
        self,
        *,
        paper_id:       str,
        evidence_name:  str,
        evidence_type:  str           = "dataset",
        language:       Optional[str] = None,
        aliases:        list[str] | None = None,
        source_section: str           = "experiment",
        confidence:     float         = 1.0,
        evidence:       str           = "",
    ) -> None:
        name_canonical = evidence_name.lower().strip()
        aliases_text   = "|".join(a.lower().strip() for a in (aliases or []) if a.strip())
        query = """
        MERGE (e:Evidence {name: $evidence_name})
        ON CREATE SET
            e.id            = randomUUID(),
            e.evidence_type = $evidence_type,
            e.language      = $language,
            e.aliases_text  = $aliases_text
        ON MATCH SET
            e.evidence_type = CASE
                WHEN $evidence_type IS NOT NULL THEN $evidence_type ELSE e.evidence_type END,
            e.language      = CASE
                WHEN $language IS NOT NULL THEN $language ELSE e.language END,
            e.aliases_text  = CASE
                WHEN $aliases_text IS NOT NULL AND $aliases_text <> ''
                    AND (e.aliases_text IS NULL OR e.aliases_text = '')
                THEN $aliases_text
                WHEN $aliases_text IS NOT NULL AND $aliases_text <> ''
                    AND NOT $aliases_text IN split(e.aliases_text, '|')
                THEN e.aliases_text + '|' + $aliases_text
                ELSE e.aliases_text
            END
        WITH e
        MATCH (p:Paper {id: $paper_id})
        MERGE (p)-[r:EVALUATES_ON]->(e)
        ON CREATE SET
            r.source_section = $source_section,
            r.confidence     = $confidence,
            r.evidence       = $evidence
        ON MATCH SET
            r.confidence = CASE WHEN $confidence > r.confidence
                           THEN $confidence ELSE r.confidence END,
            r.evidence   = CASE WHEN $confidence > r.confidence
                           THEN $evidence ELSE r.evidence END
        """
        self.execute_write(query, {
            "evidence_name":  name_canonical,
            "evidence_type":  evidence_type,
            "language":       language,
            "aliases_text":   aliases_text,
            "paper_id":       paper_id,
            "source_section": source_section,
            "confidence":     confidence,
            "evidence":       evidence[:200],
        })
        logger.debug("merge_evidence: '%s' type=%s paper=%s", name_canonical, evidence_type, paper_id)

    def lookup_evidence_by_fulltext(
        self,
        name:      str,
        threshold: float = 0.75,
    ) -> Optional[str]:
        """
        Full-text search Evidence node theo name và aliases_text.  [v2-10]
        [fix-13] escape Lucene special chars trước khi query.
        """
        query = """
        CALL db.index.fulltext.queryNodes('evidence_ft', $query_str)
        YIELD node, score
        WHERE score >= $threshold
        RETURN node.name AS name, score
        ORDER BY score DESC
        LIMIT 1
        """
        query_str = _escape_lucene(name.strip())
        results = self.execute_read(query, {"query_str": query_str, "threshold": threshold})
        if results:
            logger.debug("lookup_evidence_by_fulltext: found '%s' score=%.2f",
                         results[0]["name"], results[0]["score"])
            return results[0]["name"]
        return None

    # ------------------------------------------------------------------
    # METRIC  [v2-3]
    # ------------------------------------------------------------------

    def merge_metric(
        self,
        *,
        paper_id:      str,
        metric_name:   str,
        value:         Optional[float] = None,
        unit:          Optional[str]   = None,
        higher_better: Optional[bool]  = None,
        measured_on:   Optional[str]   = None,
        measured_by:   Optional[str]   = None,
        confidence:    float           = 1.0,
        evidence:      str             = "",
    ) -> None:
        """
        MERGE Metric node + edge (Paper)-[:ACHIEVES_METRIC]->(Metric).
        [fix-12] measured_on/measured_by dùng "" thay None làm MERGE key
                 để tránh lỗi "Cannot merge with null property value".
        """
        name_canonical   = metric_name.lower().strip()
        measured_on_safe = (measured_on or "").lower().strip()
        measured_by_safe = (measured_by or "").lower().strip()
        query = """
        MERGE (m:Metric {name: $metric_name})
        ON CREATE SET
            m.id            = randomUUID(),
            m.higher_better = $higher_better
        ON MATCH SET
            m.higher_better = CASE
                WHEN $higher_better IS NOT NULL THEN $higher_better ELSE m.higher_better END
        WITH m
        MATCH (p:Paper {id: $paper_id})
        MERGE (p)-[r:ACHIEVES_METRIC {measured_on: $measured_on, measured_by: $measured_by}]->(m)
        ON CREATE SET
            r.value        = $value,
            r.unit         = $unit,
            r.confidence   = $confidence,
            r.evidence     = $evidence
        ON MATCH SET
            r.value        = CASE WHEN $value IS NOT NULL THEN $value ELSE r.value END,
            r.unit         = CASE WHEN $unit  IS NOT NULL THEN $unit  ELSE r.unit  END,
            r.confidence   = CASE WHEN $confidence > r.confidence
                             THEN $confidence ELSE r.confidence END,
            r.evidence     = CASE WHEN $confidence > r.confidence
                             THEN $evidence ELSE r.evidence END
        """
        self.execute_write(query, {
            "metric_name":   name_canonical,
            "higher_better": higher_better,
            "paper_id":      paper_id,
            "measured_on":   measured_on_safe,
            "measured_by":   measured_by_safe,
            "value":         value,
            "unit":          unit,
            "confidence":    confidence,
            "evidence":      evidence[:200],
        })
        logger.debug("merge_metric: '%s'=%.4g paper=%s",
                     name_canonical, value or 0, paper_id)

    # ------------------------------------------------------------------
    # FINDING  [v2-4]
    # ------------------------------------------------------------------

    def merge_finding(
        self,
        *,
        paper_id:     str,
        description:  str,
        finding_type: str   = "result",
        confidence:   float = 1.0,
        evidence:     str   = "",
    ) -> None:
        desc_key = description[:200].strip()
        query = """
        MERGE (f:Finding {description: $description})
        ON CREATE SET
            f.id           = randomUUID(),
            f.finding_type = $finding_type
        ON MATCH SET
            f.finding_type = CASE
                WHEN $finding_type IS NOT NULL THEN $finding_type ELSE f.finding_type END
        WITH f
        MATCH (p:Paper {id: $paper_id})
        MERGE (p)-[r:HAS_FINDING]->(f)
        ON CREATE SET
            r.confidence = $confidence,
            r.evidence   = $evidence
        ON MATCH SET
            r.confidence = CASE WHEN $confidence > r.confidence
                           THEN $confidence ELSE r.confidence END,
            r.evidence   = CASE WHEN $confidence > r.confidence
                           THEN $evidence ELSE r.evidence END
        """
        self.execute_write(query, {
            "description":  desc_key,
            "finding_type": finding_type,
            "paper_id":     paper_id,
            "confidence":   confidence,
            "evidence":     evidence[:200],
        })
        logger.debug("merge_finding: type=%s paper=%s", finding_type, paper_id)

    # ------------------------------------------------------------------
    # CONCEPT HIERARCHY  [v2-5] [v2-6] [v2-14] [v2-15]
    # ------------------------------------------------------------------

    def merge_concept_based_on(
        self,
        *,
        child_concept:  str,
        parent_concept: str,
        confidence:     float = 1.0,
    ) -> None:
        """
        MERGE edge (child)-[:BASED_ON]->(parent).

        [v2-14] Lớp 2 defense: kiểm tra cycle bằng Cypher *1..3 trước MERGE.
        Lớp 1 (in-memory DFS) đã chạy trong graph_builder._merge_concept_hierarchy —
        guard này bắt các cross-paper cycle mà in-memory check không thấy.

        Nếu phát hiện cycle: log WARNING + return ngay, KHÔNG raise exception,
        để pipeline tiếp tục xử lý các edge còn lại bình thường.

        Giới hạn *1..3: đủ để bắt cycle ngắn trong 1 lần ingest mà không
        scan toàn bộ graph (tránh timeout với graph lớn).
        """
        child  = child_concept.lower().strip()
        parent = parent_concept.lower().strip()

        # Guard: nếu đi từ parent theo BASED_ON *1..3 bước có reach được child → cycle
        cycle_check_query = """
        MATCH (c1:Concept {name: $parent}), (c2:Concept {name: $child})
        MATCH path = (c1)-[:BASED_ON*1..3]->(c2)
        RETURN count(path) AS cycle_count
        """
        try:
            rows = self.execute_read(cycle_check_query, {"parent": parent, "child": child})
            if rows and rows[0].get("cycle_count", 0) > 0:
                logger.warning(
                    "merge_concept_based_on: CYCLE DETECTED '%s' → '%s' — skip MERGE.",
                    child, parent,
                )
                return
        except Exception:
            # Nếu cycle check lỗi (VD: node chưa tồn tại) → bỏ qua guard, tiếp tục MERGE bình thường
            logger.debug(
                "merge_concept_based_on: cycle check exception '%s' → '%s' — bỏ qua guard.",
                child, parent,
            )

        query = """
        MATCH (c1:Concept {name: $child})
        MATCH (c2:Concept {name: $parent})
        MERGE (c1)-[r:BASED_ON]->(c2)
        ON CREATE SET r.confidence = $confidence
        ON MATCH SET  r.confidence = CASE WHEN $confidence > r.confidence
                                     THEN $confidence ELSE r.confidence END
        RETURN c1.name AS child_found, c2.name AS parent_found
        """
        result = self.execute_write(query, {
            "child":      child,
            "parent":     parent,
            "confidence": confidence,
        })
        if not result:
            logger.error(
                "merge_concept_based_on: '%s' hoặc '%s' không tồn tại.",
                child_concept, parent_concept,
            )
            raise Neo4jMergeError(
                f"merge_concept_based_on: concept '{child_concept}' hoặc "
                f"'{parent_concept}' không tìm thấy"
            )

    def merge_concept_extends(
        self,
        *,
        child_concept:  str,
        parent_concept: str,
        confidence:     float = 1.0,
    ) -> None:
        """
        MERGE edge (child)-[:EXTENDS]->(parent).

        [v2-15] Tương tự v2-14: Cypher cycle guard *1..3 trước MERGE.
        Lớp 2 defense cho cross-paper EXTENDS cycles.
        Nếu phát hiện cycle: log WARNING + return, không raise.
        """
        child  = child_concept.lower().strip()
        parent = parent_concept.lower().strip()

        # Guard: nếu đi từ parent theo EXTENDS *1..3 bước có reach được child → cycle
        cycle_check_query = """
        MATCH (c1:Concept {name: $parent}), (c2:Concept {name: $child})
        MATCH path = (c1)-[:EXTENDS*1..3]->(c2)
        RETURN count(path) AS cycle_count
        """
        try:
            rows = self.execute_read(cycle_check_query, {"parent": parent, "child": child})
            if rows and rows[0].get("cycle_count", 0) > 0:
                logger.warning(
                    "merge_concept_extends: CYCLE DETECTED '%s' → '%s' — skip MERGE.",
                    child, parent,
                )
                return
        except Exception:
            logger.debug(
                "merge_concept_extends: cycle check exception '%s' → '%s' — bỏ qua guard.",
                child, parent,
            )

        query = """
        MATCH (c1:Concept {name: $child})
        MATCH (c2:Concept {name: $parent})
        MERGE (c1)-[r:EXTENDS]->(c2)
        ON CREATE SET r.confidence = $confidence
        ON MATCH SET  r.confidence = CASE WHEN $confidence > r.confidence
                                     THEN $confidence ELSE r.confidence END
        RETURN c1.name AS child_found, c2.name AS parent_found
        """
        result = self.execute_write(query, {
            "child":      child,
            "parent":     parent,
            "confidence": confidence,
        })
        if not result:
            logger.error(
                "merge_concept_extends: '%s' hoặc '%s' không tồn tại.",
                child_concept, parent_concept,
            )
            raise Neo4jMergeError(
                f"merge_concept_extends: concept '{child_concept}' hoặc "
                f"'{parent_concept}' không tìm thấy"
            )

    # ------------------------------------------------------------------
    # CITATION GRAPH  [v2-7] [v2-8]
    # ------------------------------------------------------------------

    def merge_paper_supports(
        self,
        *,
        source_paper_id: str,
        target_paper_id: str,
        finding_desc:    str   = "",
        confidence:      float = 1.0,
    ) -> None:
        query = """
        MATCH (p1:Paper {id: $source_id})
        MERGE (p2:Paper {id: $target_id})
        ON CREATE SET
            p2.processing_status = 'stub',
            p2.created_at        = datetime()
        MERGE (p1)-[r:SUPPORTS]->(p2)
        ON CREATE SET r.finding_desc = $finding_desc, r.confidence = $confidence
        ON MATCH SET  r.confidence   = CASE WHEN $confidence > r.confidence
                                       THEN $confidence ELSE r.confidence END
        RETURN p1.id AS source_found
        """
        result = self.execute_write(query, {
            "source_id":    source_paper_id,
            "target_id":    target_paper_id,
            "finding_desc": finding_desc[:200],
            "confidence":   confidence,
        })
        if not result:
            logger.error(
                "merge_paper_supports: source_paper_id='%s' không tồn tại.",
                source_paper_id,
            )
            raise Neo4jMergeError(
                f"merge_paper_supports: source '{source_paper_id}' không tìm thấy"
            )

    def merge_paper_contradicts(
        self,
        *,
        source_paper_id: str,
        target_paper_id: str,
        finding_desc:    str   = "",
        confidence:      float = 1.0,
    ) -> None:
        query = """
        MATCH (p1:Paper {id: $source_id})
        MERGE (p2:Paper {id: $target_id})
        ON CREATE SET
            p2.processing_status = 'stub',
            p2.created_at        = datetime()
        MERGE (p1)-[r:CONTRADICTS]->(p2)
        ON CREATE SET r.finding_desc = $finding_desc, r.confidence = $confidence
        ON MATCH SET  r.confidence   = CASE WHEN $confidence > r.confidence
                                       THEN $confidence ELSE r.confidence END
        RETURN p1.id AS source_found
        """
        result = self.execute_write(query, {
            "source_id":    source_paper_id,
            "target_id":    target_paper_id,
            "finding_desc": finding_desc[:200],
            "confidence":   confidence,
        })
        if not result:
            logger.error(
                "merge_paper_contradicts: source_paper_id='%s' không tồn tại.",
                source_paper_id,
            )
            raise Neo4jMergeError(
                f"merge_paper_contradicts: source '{source_paper_id}' không tìm thấy"
            )

    # ------------------------------------------------------------------
    # CITATION  [fix-3] [fix-9] [v2-11]
    # ------------------------------------------------------------------

    def merge_citation(
        self,
        *,
        citing_paper_id: str,
        cited_doc_id:    Optional[str] = None,
        cited_doi:       Optional[str] = None,
        cited_title:     Optional[str] = None,
        cited_year:      Optional[int] = None,
        raw_ref_text:    str           = "",
    ) -> None:
        # Path 1: internal doc_id
        if cited_doc_id:
            query = """
            MATCH (p1:Paper {id: $citing_id})
            MATCH (p2:Paper {id: $cited_id})
            MERGE (p1)-[r:CITES]->(p2)
            ON CREATE SET r.confidence = 1.0, r.raw_ref_text = $raw_ref_text
            RETURN p1.id AS src, p2.id AS tgt
            """
            result = self.execute_write(query, {
                "citing_id":    citing_paper_id,
                "cited_id":     cited_doc_id,
                "raw_ref_text": raw_ref_text[:500],
            })
            if result:
                return
            logger.warning(
                "merge_citation: cited_doc_id='%s' không tồn tại — fallback sang doi/title.",
                cited_doc_id,
            )

        # Path 2: doi
        if cited_doi:
            query = """
            MATCH (p1:Paper {id: $citing_id})
            MERGE (p2:Paper {doi: $cited_doi})
            ON CREATE SET
                p2.id                = randomUUID(),
                p2.title             = $cited_title,
                p2.year              = $cited_year,
                p2.processing_status = 'stub',
                p2.created_at        = datetime()
            MERGE (p1)-[r:CITES]->(p2)
            ON CREATE SET r.confidence = 1.0, r.raw_ref_text = $raw_ref_text
            """
            self.execute_write(query, {
                "citing_id":    citing_paper_id,
                "cited_doi":    cited_doi,
                "cited_title":  cited_title or "",
                "cited_year":   cited_year,
                "raw_ref_text": raw_ref_text[:500],
            })
            return

        # Path 3: title + year
        if cited_title:
            cited_year_safe = cited_year if cited_year is not None else 0
            confidence      = 0.8 if cited_year else 0.6
            query = """
            MATCH (p1:Paper {id: $citing_id})
            MERGE (p2:Paper {title: $cited_title, year: $cited_year})
            ON CREATE SET
                p2.id                = randomUUID(),
                p2.processing_status = 'stub',
                p2.created_at        = datetime()
            ON MATCH SET
                p2.processing_status = CASE
                    WHEN p2.processing_status = 'stub' THEN 'stub'
                    ELSE p2.processing_status
                END
            MERGE (p1)-[r:CITES]->(p2)
            ON CREATE SET r.confidence = $confidence, r.raw_ref_text = $raw_ref_text
            """
            self.execute_write(query, {
                "citing_id":    citing_paper_id,
                "cited_title":  cited_title,
                "cited_year":   cited_year_safe,
                "confidence":   confidence,
                "raw_ref_text": raw_ref_text[:500],
            })
            return

        logger.warning(
            "merge_citation: citing=%s — cited paper không có doc_id, doi, lẫn title. Bỏ qua.",
            citing_paper_id,
        )

    # ------------------------------------------------------------------
    # CHUNK  [fix-11]
    # ------------------------------------------------------------------

    def merge_chunk(
        self,
        *,
        paper_id:   str,
        qdrant_id:  str,
        section:    str           = "",
        page:       Optional[int] = None,
        chunk_text: str           = "",
    ) -> None:
        _LIMIT   = 1000
        text_out = chunk_text[:_LIMIT]
        if len(chunk_text) > _LIMIT:
            logger.warning(
                "merge_chunk: qdrant_id='%s' text truncate %d→%d chars.",
                qdrant_id, len(chunk_text), _LIMIT,
            )
        query = """
        MERGE (c:Chunk {qdrant_id: $qdrant_id})
        ON CREATE SET
            c.id      = randomUUID(),
            c.section = $section,
            c.page    = $page,
            c.text    = $chunk_text
        WITH c
        MATCH (p:Paper {id: $paper_id})
        MERGE (p)-[:HAS_CHUNK]->(c)
        RETURN p.id AS paper_found
        """
        result = self.execute_write(query, {
            "qdrant_id":  qdrant_id,
            "section":    section,
            "page":       page,
            "chunk_text": text_out,
            "paper_id":   paper_id,
        })
        if not result:
            logger.error("merge_chunk: paper_id='%s' không tồn tại.", paper_id)
            raise Neo4jMergeError(f"merge_chunk: paper_id '{paper_id}' không tìm thấy")

    # ------------------------------------------------------------------
    # SIMILAR_TO
    # ------------------------------------------------------------------

    def merge_similar_to(
        self,
        *,
        paper_id_1:     str,
        paper_id_2:     str,
        score:          float,
        shared_methods: list[str] | None = None,
        shared_authors: list[str] | None = None,
    ) -> None:
        query = """
        MATCH (p1:Paper {id: $id1})
        MATCH (p2:Paper {id: $id2})
        MERGE (p1)-[r:SIMILAR_TO]->(p2)
        ON CREATE SET
            r.score          = $score,
            r.shared_methods = $shared_methods,
            r.shared_authors = $shared_authors
        ON MATCH SET r.score = $score
        RETURN p1.id AS p1_found, p2.id AS p2_found
        """
        result = self.execute_write(query, {
            "id1":            paper_id_1,
            "id2":            paper_id_2,
            "score":          round(score, 4),
            "shared_methods": "|".join(shared_methods or []),
            "shared_authors": "|".join(shared_authors or []),
        })
        if not result:
            logger.error(
                "merge_similar_to: '%s' hoặc '%s' không tồn tại.",
                paper_id_1, paper_id_2,
            )
            raise Neo4jMergeError(
                f"merge_similar_to: '{paper_id_1}' hoặc '{paper_id_2}' không tìm thấy"
            )

    # ------------------------------------------------------------------
    # LEGACY — giữ để backward compat  [v2-13]
    # DEPRECATED: dùng merge_concept / merge_evidence / merge_concept_based_on thay thế.
    # ------------------------------------------------------------------

    def merge_method(self, *, paper_id, method_name, category=None, aliases=None,
                     source_section="method", confidence=1.0, evidence="") -> None:
        """DEPRECATED — dùng merge_concept() thay thế."""
        logger.warning("merge_method() is DEPRECATED — dùng merge_concept()")
        name_canonical = method_name.lower().strip()
        aliases_text   = "|".join(a.lower().strip() for a in (aliases or []) if a.strip())
        query = """
        MERGE (m:Method {name: $method_name})
        ON CREATE SET m.id=randomUUID(), m.category=$category, m.aliases_text=$aliases_text
        ON MATCH SET
            m.category     = CASE WHEN $category IS NOT NULL THEN $category ELSE m.category END,
            m.aliases_text = CASE
                WHEN $aliases_text IS NOT NULL AND $aliases_text <> ''
                    AND (m.aliases_text IS NULL OR m.aliases_text = '')
                THEN $aliases_text
                WHEN $aliases_text IS NOT NULL AND $aliases_text <> ''
                    AND NOT $aliases_text IN split(m.aliases_text, '|')
                THEN m.aliases_text + '|' + $aliases_text
                ELSE m.aliases_text
            END
        WITH m
        MATCH (p:Paper {id: $paper_id})
        MERGE (p)-[r:USES_METHOD]->(m)
        ON CREATE SET r.source_section=$source_section, r.confidence=$confidence, r.evidence=$evidence
        ON MATCH SET
            r.confidence=CASE WHEN $confidence>r.confidence THEN $confidence ELSE r.confidence END,
            r.evidence=CASE WHEN $confidence>r.confidence THEN $evidence ELSE r.evidence END
        """
        self.execute_write(query, {
            "method_name": name_canonical, "category": category, "aliases_text": aliases_text,
            "paper_id": paper_id, "source_section": source_section,
            "confidence": confidence, "evidence": evidence[:200],
        })

    def lookup_method_by_fulltext(self, name: str, threshold: float = 0.75) -> Optional[str]:
        """DEPRECATED — dùng lookup_concept_by_fulltext() thay thế."""
        logger.warning("lookup_method_by_fulltext() is DEPRECATED — dùng lookup_concept_by_fulltext()")
        return self.lookup_concept_by_fulltext(name, threshold)

    def merge_dataset(self, *, paper_id, dataset_name, dataset_language=None, aliases=None,
                      source_section="experiment", confidence=1.0, evidence="", metric=None) -> None:
        """DEPRECATED — dùng merge_evidence() thay thế."""
        logger.warning("merge_dataset() is DEPRECATED — dùng merge_evidence()")
        self.merge_evidence(
            paper_id=paper_id, evidence_name=dataset_name, evidence_type="dataset",
            language=dataset_language, aliases=aliases, source_section=source_section,
            confidence=confidence, evidence=evidence,
        )

    def lookup_dataset_by_fulltext(self, name: str, threshold: float = 0.75) -> Optional[str]:
        """DEPRECATED — dùng lookup_evidence_by_fulltext() thay thế."""
        logger.warning("lookup_dataset_by_fulltext() is DEPRECATED — dùng lookup_evidence_by_fulltext()")
        return self.lookup_evidence_by_fulltext(name, threshold)

    def merge_task(self, *, paper_id, task_name, source_section="abstract",
                   confidence=1.0, evidence="") -> None:
        """DEPRECATED — dùng merge_concept(category='task') thay thế."""
        logger.warning("merge_task() is DEPRECATED — dùng merge_concept(category='task')")
        self.merge_concept(paper_id=paper_id, concept_name=task_name, category="task",
                           source_section=source_section, confidence=confidence, evidence=evidence)

    def merge_method_based_on(self, *, child_method, parent_method, confidence=1.0) -> None:
        """DEPRECATED — dùng merge_concept_based_on() thay thế."""
        logger.warning("merge_method_based_on() is DEPRECATED — dùng merge_concept_based_on()")
        self.merge_concept_based_on(child_concept=child_method, parent_concept=parent_method,
                                    confidence=confidence)

    # ------------------------------------------------------------------
    # UTILITY
    # ------------------------------------------------------------------

    def set_paper_status(self, paper_id: str, status: str) -> None:
        valid = {"stub", "parsed", "kg_built", "embedded"}
        if status not in valid:
            raise ValueError(f"set_paper_status: status không hợp lệ '{status}'. Phải là {valid}")
        query = "MATCH (p:Paper {id: $id}) SET p.processing_status = $status"
        self.execute_write(query, {"id": paper_id, "status": status})

    def get_paper_by_id(self, paper_id: str) -> Optional[dict]:
        query = "MATCH (p:Paper {id: $id}) RETURN p"
        rows  = self.execute_read(query, {"id": paper_id})
        return dict(rows[0]["p"]) if rows else None

    def get_papers_by_status(self, status: str) -> list[dict]:
        query = "MATCH (p:Paper {processing_status: $status}) RETURN p"
        rows  = self.execute_read(query, {"status": status})
        return [dict(r["p"]) for r in rows]

    # ------------------------------------------------------------------
    # HEALTH CHECK
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        try:
            self.execute_read("RETURN 1 AS ok")
            return True
        except (ServiceUnavailable, TransientError) as e:
            logger.error("Neo4j ping failed: %s", e)
            return False
