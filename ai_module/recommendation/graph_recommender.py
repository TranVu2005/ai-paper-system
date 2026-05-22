from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIG
# =============================================================================

@dataclass
class GraphRecommenderConfig:
    """
    Cấu hình GraphRecommender.

    Weights (tổng mặc định = 1.0, không bắt buộc):
        weight_shared_concept    = 0.35   (giảm từ 0.40 để nhường cho author)
        weight_shared_evidence   = 0.20   (giảm từ 0.25)
        weight_citation_network  = 0.15   (giảm từ 0.20)
        weight_concept_hierarchy = 0.15
        weight_author_network    = 0.15   [v1.5-1] MỚI

    Tắt signal = đặt weight về 0.0.

    author_institution_weight:
        Weight nhân thêm khi signal đến từ shared institution thay vì shared author.
        Mặc định 0.6 — shared institution yếu hơn shared author trực tiếp.

    author_concept_filter_method:
        True  → chỉ tính paper của cùng author nếu paper đó cũng dùng ít nhất 1
                concept category="method" giống paper nguồn.
                Lọc nhiễu: tác giả viết nhiều lĩnh vực khác nhau sẽ không bị inflate.
        False → tính tất cả paper của cùng author/institution, không lọc concept.
    """
    top_k:                       int   = 20
    min_score:                   float = 0.05

    # Signal weights  [v1.5-1]
    weight_shared_concept:       float = 0.35   # 0.40 → 0.35
    weight_shared_evidence:      float = 0.20   # 0.25 → 0.20
    weight_citation_network:     float = 0.15   # 0.20 → 0.15
    weight_concept_hierarchy:    float = 0.15
    weight_author_network:       float = 0.15   # MỚI [v1.5-1]

    # Thresholds
    min_shared_concepts:         int   = 1
    min_shared_evidences:        int   = 1

    # Citation network
    citation_weight_cites:       float = 1.0
    citation_weight_supports:    float = 0.8
    citation_weight_contradicts: float = 0.5

    # Concept hierarchy
    concept_hierarchy_depth:     int   = 2

    # Author network  [v1.5-1]
    author_institution_weight:        float = 0.6    # shared institution vs shared author
    author_concept_filter_method:     bool  = True   # chỉ boost nếu cùng method concept

    # Output
    insert_similar_to:           bool  = False

    def __post_init__(self) -> None:
        """
        [fix-6] Validate weights: không âm, tổng phải > 0.
        [fix-10] Revalidate tổng sau khi reset các weight âm.
        """
        weight_fields = {
            "weight_shared_concept":    self.weight_shared_concept,
            "weight_shared_evidence":   self.weight_shared_evidence,
            "weight_citation_network":  self.weight_citation_network,
            "weight_concept_hierarchy": self.weight_concept_hierarchy,
            "weight_author_network":    self.weight_author_network,
        }
        for name, val in weight_fields.items():
            if val < 0.0:
                logger.warning(
                    "GraphRecommenderConfig: %s=%.3f âm — reset về 0.0", name, val
                )
                setattr(self, name, 0.0)

        total = (
            self.weight_shared_concept
            + self.weight_shared_evidence
            + self.weight_citation_network
            + self.weight_concept_hierarchy
            + self.weight_author_network
        )
        if total <= 0.0:
            logger.warning(
                "GraphRecommenderConfig: tổng weight = 0 — "
                "mọi score sẽ bằng 0. Hãy set ít nhất 1 weight > 0."
            )


# =============================================================================
# RESULT DATACLASS
# =============================================================================

@dataclass
class GraphRecommendation:
    """
    Kết quả recommend 1 paper.

    paper_id         : ID paper được recommend.
    title            : Tên bài báo.                         [v1.3-1]
    score            : final weighted score ∈ [0, 1].
    breakdown        : {signal_name: normalized_score}      [v1.5-5] thêm "author_network"
    shared_concepts  : tên Concept (category=method) chung.
    shared_evidences : tên Evidence chung.
    shared_authors   : tên Author chung.                    [v1.5-4] giờ được populate thực sự
    """
    paper_id:         str
    title:            str                = ""
    score:            float              = 0.0
    breakdown:        dict[str, float]   = field(default_factory=dict)
    shared_concepts:  list[str]          = field(default_factory=list)
    shared_evidences: list[str]          = field(default_factory=list)
    shared_authors:   list[str]          = field(default_factory=list)

    def __repr__(self) -> str:
        bd = " ".join(f"{k}={v:.3f}" for k, v in self.breakdown.items())
        return (
            f"GraphRecommendation(paper_id={self.paper_id[:8]}... "
            f"title={self.title[:40]!r} "
            f"score={self.score:.4f} [{bd}])"
        )


# =============================================================================
# GRAPH RECOMMENDER
# =============================================================================

class GraphRecommender:
    """
    Recommend papers dựa trên 5 KG signals.

    Cách dùng điển hình:
        rec     = GraphRecommender(neo4j_client, config)
        results = rec.recommend(paper_id)

    Cho offline batch job:
        reports = rec.batch_recommend(paper_ids, insert_similar_to=True)
    """

    def __init__(
        self,
        client,
        config: GraphRecommenderConfig | None = None,
    ) -> None:
        self._client = client
        self._config = config or GraphRecommenderConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def recommend(
        self,
        paper_id: str,
        top_k:    int | None = None,
        *,
        _insert:  bool | None = None,
    ) -> list[GraphRecommendation]:
        """
        Trả về top-k paper tương tự với paper_id theo 5 graph signals.

        [v1.5-3] Thêm author_scores vào pipeline chuẩn.
        """
        k = top_k or self._config.top_k
        do_insert = self._config.insert_similar_to if _insert is None else _insert

        logger.info("GraphRecommender.recommend: paper_id=%s top_k=%d", paper_id, k)

        # ── Thu thập raw scores từ từng signal ───────────────────────
        concept_scores   = self._signal_shared_concept(paper_id)
        evidence_scores  = self._signal_shared_evidence(paper_id)
        citation_scores  = self._signal_citation_network(paper_id)
        hierarchy_scores = self._signal_concept_hierarchy(paper_id)
        author_scores    = self._signal_author_network(paper_id)   # [v1.5-3]

        # ── Gộp tất cả paper_id xuất hiện ────────────────────────────
        all_ids: set[str] = (
            set(concept_scores)
            | set(evidence_scores)
            | set(citation_scores)
            | set(hierarchy_scores)
            | set(author_scores)
        )
        all_ids.discard(paper_id)

        if not all_ids:
            logger.info("GraphRecommender: không tìm thấy candidate nào — paper_id=%s", paper_id)
            return []

        # ── Soft normalize per signal ─────────────────────────────────
        norm_concept   = _soft_normalize(concept_scores)
        norm_evidence  = _soft_normalize(evidence_scores)
        norm_citation  = _soft_normalize(citation_scores)
        norm_hierarchy = _soft_normalize(hierarchy_scores)
        norm_author    = _soft_normalize(author_scores)    # [v1.5-3]

        cfg = self._config
        total_weight = (
            cfg.weight_shared_concept
            + cfg.weight_shared_evidence
            + cfg.weight_citation_network
            + cfg.weight_concept_hierarchy
            + cfg.weight_author_network
        ) or 1.0

        # ── Tính final score có trọng số ─────────────────────────────
        recommendations: list[GraphRecommendation] = []
        for pid in all_ids:
            sc = norm_concept.get(pid, 0.0)
            se = norm_evidence.get(pid, 0.0)
            sn = norm_citation.get(pid, 0.0)
            sh = norm_hierarchy.get(pid, 0.0)
            sa = norm_author.get(pid, 0.0)     # [v1.5-3]

            final = (
                cfg.weight_shared_concept    * sc
                + cfg.weight_shared_evidence   * se
                + cfg.weight_citation_network  * sn
                + cfg.weight_concept_hierarchy * sh
                + cfg.weight_author_network    * sa
            ) / total_weight

            # [fix-8] < thay vì <=
            if final < cfg.min_score:
                continue

            recommendations.append(GraphRecommendation(
                paper_id=pid,
                score=round(final, 6),
                breakdown={
                    "shared_concept":    round(sc, 4),
                    "shared_evidence":   round(se, 4),
                    "citation_network":  round(sn, 4),
                    "concept_hierarchy": round(sh, 4),
                    "author_network":    round(sa, 4),   # [v1.5-5]
                },
            ))

        # ── Sort + truncate ───────────────────────────────────────────
        recommendations.sort(key=lambda r: r.score, reverse=True)
        recommendations = recommendations[:k]

        # ── Enrich title + shared_concepts + shared_evidences + shared_authors ──
        self._enrich_shared(paper_id, recommendations)

        # ── Insert SIMILAR_TO nếu cần ─────────────────────────────────
        if do_insert:
            self._insert_similar_to(paper_id, recommendations)

        logger.info(
            "GraphRecommender.recommend: done — paper_id=%s found=%d",
            paper_id, len(recommendations),
        )
        return recommendations

    def batch_recommend(
        self,
        paper_ids:         list[str],
        insert_similar_to: bool | None = None,
    ) -> dict[str, list[GraphRecommendation]]:
        """
        Batch recommend cho nhiều paper — dùng trong offline job.
        [fix-5] Không mutate self._config.
        [fix-7] Truyền _insert vào recommend().
        """
        insert_flag: bool = (
            insert_similar_to
            if insert_similar_to is not None
            else self._config.insert_similar_to
        )

        results: dict[str, list[GraphRecommendation]] = {}
        for i, pid in enumerate(paper_ids):
            logger.info(
                "GraphRecommender.batch_recommend: [%d/%d] paper_id=%s",
                i + 1, len(paper_ids), pid,
            )
            try:
                recs = self.recommend(pid, _insert=insert_flag)
                results[pid] = recs
            except Exception:
                logger.exception(
                    "GraphRecommender.batch_recommend: FAILED paper_id=%s — bỏ qua", pid
                )
                results[pid] = []

        return results

    # ------------------------------------------------------------------
    # Signal 1 — Shared Concept
    # ------------------------------------------------------------------

    def _signal_shared_concept(self, paper_id: str) -> dict[str, float]:
        """
        Tìm paper khác cùng USES_CONCEPT → cùng Concept node.
        Score = Σ (conf_src × conf_other × category_weight)
        """
        cfg = self._config
        query = """
        MATCH (src:Paper {id: $paper_id})-[r1:USES_CONCEPT]->(c:Concept)
        MATCH (other:Paper)-[r2:USES_CONCEPT]->(c)
        WHERE other.id <> $paper_id
          AND other.processing_status = 'kg_built'
        RETURN other.id               AS other_id,
               c.name                 AS concept_name,
               c.category             AS category,
               r1.confidence          AS conf_src,
               r2.confidence          AS conf_other
        """
        try:
            rows = self._client.execute_read(query, {"paper_id": paper_id})
        except Exception:
            logger.exception("_signal_shared_concept: query failed paper_id=%s", paper_id)
            return {}

        scores: dict[str, float] = {}
        counts: dict[str, int]   = {}

        for row in rows:
            oid      = row["other_id"]
            category = row.get("category", "concept") or "concept"
            c_src    = float(row.get("conf_src",   0.8) or 0.8)
            c_other  = float(row.get("conf_other", 0.8) or 0.8)
            cat_w    = _category_weight(category)
            scores[oid] = scores.get(oid, 0.0) + (c_src * c_other * cat_w)
            counts[oid] = counts.get(oid, 0) + 1

        return {oid: scores[oid] for oid in scores if counts[oid] >= cfg.min_shared_concepts}

    # ------------------------------------------------------------------
    # Signal 2 — Shared Evidence
    # ------------------------------------------------------------------

    def _signal_shared_evidence(self, paper_id: str) -> dict[str, float]:
        """
        Tìm paper khác cùng EVALUATES_ON → cùng Evidence node.
        Score = Σ (conf_src × conf_other × evidence_type_weight)
        """
        cfg = self._config
        query = """
        MATCH (src:Paper {id: $paper_id})-[r1:EVALUATES_ON]->(e:Evidence)
        MATCH (other:Paper)-[r2:EVALUATES_ON]->(e)
        WHERE other.id <> $paper_id
          AND other.processing_status = 'kg_built'
        RETURN other.id               AS other_id,
               e.name                 AS evidence_name,
               e.evidence_type        AS ev_type,
               r1.confidence          AS conf_src,
               r2.confidence          AS conf_other
        """
        try:
            rows = self._client.execute_read(query, {"paper_id": paper_id})
        except Exception:
            logger.exception("_signal_shared_evidence: query failed paper_id=%s", paper_id)
            return {}

        scores: dict[str, float] = {}
        counts: dict[str, int]   = {}

        for row in rows:
            oid     = row["other_id"]
            ev_type = row.get("ev_type", "dataset") or "dataset"
            c_src   = float(row.get("conf_src",   0.8) or 0.8)
            c_other = float(row.get("conf_other", 0.8) or 0.8)
            ev_w    = _evidence_type_weight(ev_type)
            scores[oid] = scores.get(oid, 0.0) + (c_src * c_other * ev_w)
            counts[oid] = counts.get(oid, 0) + 1

        return {oid: scores[oid] for oid in scores if counts[oid] >= cfg.min_shared_evidences}

    # ------------------------------------------------------------------
    # Signal 3 — Citation Network
    # ------------------------------------------------------------------

    def _signal_citation_network(self, paper_id: str) -> dict[str, float]:
        """
        Tính similarity qua citation graph.
        [fix-2] 4 sub-query riêng: direct_out, direct_in, bib_coupling, co_citation.
        """
        cfg = self._config
        rel_weights = {
            "CITES":       cfg.citation_weight_cites,
            "SUPPORTS":    cfg.citation_weight_supports,
            "CONTRADICTS": cfg.citation_weight_contradicts,
        }

        scores: dict[str, float] = {}

        q_direct_out = """
        MATCH (src:Paper {id: $paper_id})-[r:CITES|SUPPORTS|CONTRADICTS]->(other:Paper)
        WHERE other.id <> $paper_id
          AND other.processing_status IN ['kg_built', 'stub']
        RETURN other.id AS other_id, type(r) AS rel_type, 1.0 AS base_weight
        """
        q_direct_in = """
        MATCH (other:Paper)-[r:CITES|SUPPORTS|CONTRADICTS]->(src:Paper {id: $paper_id})
        WHERE other.id <> $paper_id
          AND other.processing_status IN ['kg_built', 'stub']
        RETURN other.id AS other_id, type(r) AS rel_type, 0.9 AS base_weight
        """
        q_bib = """
        MATCH (src:Paper {id: $paper_id})-[:CITES]->(shared:Paper)<-[:CITES]-(other:Paper)
        WHERE other.id <> $paper_id
          AND other.processing_status = 'kg_built'
        RETURN other.id AS other_id, 'CITES' AS rel_type, 0.7 AS base_weight
        """
        q_cocite = """
        MATCH (citing:Paper)-[:CITES]->(src:Paper {id: $paper_id})
        MATCH (citing)-[:CITES]->(other:Paper)
        WHERE other.id <> $paper_id
          AND other.processing_status = 'kg_built'
        RETURN other.id AS other_id, 'CITES' AS rel_type, 0.6 AS base_weight
        """

        params = {"paper_id": paper_id}
        for label, query in [
            ("direct_out",   q_direct_out),
            ("direct_in",    q_direct_in),
            ("bib_coupling", q_bib),
            ("co_citation",  q_cocite),
        ]:
            try:
                rows = self._client.execute_read(query, params)
            except Exception:
                logger.exception(
                    "_signal_citation_network[%s]: query failed paper_id=%s", label, paper_id
                )
                continue

            for row in rows:
                oid = row.get("other_id")
                if not oid:
                    continue
                rel_type    = row.get("rel_type", "CITES") or "CITES"
                base_w      = float(row.get("base_weight", 1.0) or 1.0)
                rel_w       = rel_weights.get(rel_type, 0.5)
                scores[oid] = scores.get(oid, 0.0) + base_w * rel_w

        return scores

    # ------------------------------------------------------------------
    # Signal 4 — Concept Hierarchy (BASED_ON / EXTENDS)
    # ------------------------------------------------------------------

    def _signal_concept_hierarchy(self, paper_id: str) -> dict[str, float]:
        """
        Tìm paper tương tự qua concept cha/con trong BASED_ON/EXTENDS graph.
        [fix-1] length(path) thực tế. [fix-9] Deduplicate (other_id, hop).
        Score = 1/(hop+1): hop=1→0.5, hop=2→0.33, hop=3→0.25
        """
        depth = max(1, min(self._config.concept_hierarchy_depth, 3))
        params = {"paper_id": paper_id}
        scores: dict[str, float] = {}

        q_ancestor = f"""
        MATCH (src:Paper {{id: $paper_id}})-[:USES_CONCEPT]->(c:Concept)
        MATCH anc_path = (c)-[:BASED_ON|EXTENDS*1..{depth}]->(ancestor:Concept)
        WITH ancestor, length(anc_path) AS hop
        MATCH (other:Paper)-[:USES_CONCEPT]->(ancestor)
        WHERE other.id <> $paper_id
          AND other.processing_status = 'kg_built'
        RETURN other.id AS other_id, ancestor.name AS concept_name, hop AS hop_depth
        """

        q_descendant = f"""
        MATCH (src:Paper {{id: $paper_id}})-[:USES_CONCEPT]->(c:Concept)
        MATCH desc_path = (descendant:Concept)-[:BASED_ON|EXTENDS*1..{depth}]->(c)
        WITH descendant, length(desc_path) AS hop
        MATCH (other:Paper)-[:USES_CONCEPT]->(descendant)
        WHERE other.id <> $paper_id
          AND other.processing_status = 'kg_built'
        RETURN other.id AS other_id, descendant.name AS concept_name, hop AS hop_depth
        """

        for label, query in [("ancestor", q_ancestor), ("descendant", q_descendant)]:
            try:
                rows = self._client.execute_read(query, params)
            except Exception:
                logger.exception(
                    "_signal_concept_hierarchy[%s]: query failed paper_id=%s", label, paper_id
                )
                continue

            seen: set[tuple[str, int]] = set()
            for row in rows:
                oid = row.get("other_id")
                if not oid:
                    continue
                hop = int(row.get("hop_depth", 1) or 1)
                key = (oid, hop)
                if key in seen:
                    continue
                seen.add(key)
                decay = 1.0 / (hop + 1)
                scores[oid] = scores.get(oid, 0.0) + decay

        return scores

    # ------------------------------------------------------------------
    # Signal 5 — Author / Institution Network  [v1.5-2]
    # ------------------------------------------------------------------

    def _signal_author_network(self, paper_id: str) -> dict[str, float]:
        """
        Tìm paper tương tự qua Author và Institution.

        2 sub-signal:
            a) shared_author      — cùng node Author (WRITTEN_BY)
                                    base_score × 1.0
            b) shared_institution — cùng author.affiliation (không nhất thiết cùng node)
                                    base_score × config.author_institution_weight (mặc định 0.6)

        Nếu config.author_concept_filter_method = True:
            Chỉ tính paper (other) nếu paper đó cùng dùng ít nhất 1 Concept
            category = "method" với paper nguồn.
            → Lọc tác giả viết đa lĩnh vực: chỉ recommend khi thực sự overlap kỹ thuật.

        Score cuối = Σ contribution, mỗi (other_id, author_name) đóng góp 1 lần.

        Neo4j schema giả định:
            (Paper)-[:WRITTEN_BY]->(Author)
            Author.affiliation: str   — tên institution
        """
        cfg = self._config
        params: dict = {"paper_id": paper_id}

        # ── Chọn query dựa trên author_concept_filter_method ─────────
        # Nếu filter=True: JOIN thêm điều kiện cùng concept category="method"
        # Nếu filter=False: không cần JOIN concept

        if cfg.author_concept_filter_method:
            # [v1.5-2] Filter: chỉ lấy paper cùng author VÀ cùng ít nhất 1 method concept
            q_shared_author = """
            MATCH (src:Paper {id: $paper_id})-[:WRITTEN_BY]->(a:Author)
            MATCH (other:Paper)-[:WRITTEN_BY]->(a)
            WHERE other.id <> $paper_id
              AND other.processing_status = 'kg_built'
            WITH src, other, a
            MATCH (src)-[:USES_CONCEPT]->(c:Concept {category: 'method'})
            MATCH (other)-[:USES_CONCEPT]->(c)
            RETURN DISTINCT
                other.id    AS other_id,
                a.name      AS author_name,
                'author'    AS signal_type
            """
            q_shared_institution = """
            MATCH (src:Paper {id: $paper_id})-[:WRITTEN_BY]->(a1:Author)
            MATCH (other:Paper)-[:WRITTEN_BY]->(a2:Author)
            WHERE other.id <> $paper_id
              AND other.processing_status = 'kg_built'
              AND a1.affiliation IS NOT NULL
              AND a2.affiliation IS NOT NULL
              AND a1.affiliation = a2.affiliation
              AND a1 <> a2
            WITH src, other, a1.affiliation AS institution
            MATCH (src)-[:USES_CONCEPT]->(c:Concept {category: 'method'})
            MATCH (other)-[:USES_CONCEPT]->(c)
            RETURN DISTINCT
                other.id    AS other_id,
                institution AS author_name,
                'institution' AS signal_type
            """
        else:
            # Không filter concept — lấy tất cả paper cùng author/institution
            q_shared_author = """
            MATCH (src:Paper {id: $paper_id})-[:WRITTEN_BY]->(a:Author)
            MATCH (other:Paper)-[:WRITTEN_BY]->(a)
            WHERE other.id <> $paper_id
              AND other.processing_status = 'kg_built'
            RETURN DISTINCT
                other.id    AS other_id,
                a.name      AS author_name,
                'author'    AS signal_type
            """
            q_shared_institution = """
            MATCH (src:Paper {id: $paper_id})-[:WRITTEN_BY]->(a1:Author)
            MATCH (other:Paper)-[:WRITTEN_BY]->(a2:Author)
            WHERE other.id <> $paper_id
              AND other.processing_status = 'kg_built'
              AND a1.affiliation IS NOT NULL
              AND a2.affiliation IS NOT NULL
              AND a1.affiliation = a2.affiliation
              AND a1 <> a2
            RETURN DISTINCT
                other.id            AS other_id,
                a1.affiliation      AS author_name,
                'institution'       AS signal_type
            """

        scores: dict[str, float] = {}

        for label, query, base_multiplier in [
            ("shared_author",      q_shared_author,      1.0),
            ("shared_institution", q_shared_institution, cfg.author_institution_weight),
        ]:
            try:
                rows = self._client.execute_read(query, params)
            except Exception:
                logger.exception(
                    "_signal_author_network[%s]: query failed paper_id=%s", label, paper_id
                )
                continue

            for row in rows:
                oid = row.get("other_id")
                if not oid:
                    continue
                # Mỗi shared author/institution đóng góp base_multiplier vào score
                scores[oid] = scores.get(oid, 0.0) + base_multiplier

        return scores

    # ------------------------------------------------------------------
    # Enrich shared_concepts / shared_evidences / shared_authors / title
    # ------------------------------------------------------------------

    def _enrich_shared(
        self,
        paper_id: str,
        recommendations: list[GraphRecommendation],
    ) -> None:
        """
        Query shared concepts (category=method), shared evidences, shared authors,
        và title cho top results.
        Chạy 4 queries tuần tự (4 round-trips tổng cộng).

        [fix-4]  Enrich tất cả recommendation, không filter theo score.
        [v1.3-2] Thêm query_title.
        [v1.5-4] Thêm query_author — populate shared_authors thực sự.
                 shared_concepts chỉ lấy category='method' để nhất quán với signal 5.
        """
        if not recommendations:
            return

        target_ids = [r.paper_id for r in recommendations]

        # Chỉ lấy concept category='method' — nhất quán với author_concept_filter_method
        query_concept = """
        MATCH (src:Paper {id: $paper_id})-[:USES_CONCEPT]->(c:Concept {category: 'method'})
        MATCH (other:Paper)-[:USES_CONCEPT]->(c)
        WHERE other.id IN $target_ids
        RETURN other.id AS other_id, c.name AS concept_name
        """
        query_evidence = """
        MATCH (src:Paper {id: $paper_id})-[:EVALUATES_ON]->(e:Evidence)
        MATCH (other:Paper)-[:EVALUATES_ON]->(e)
        WHERE other.id IN $target_ids
        RETURN other.id AS other_id, e.name AS evidence_name
        """
        # [v1.5-4] Shared authors thực sự — cùng node Author
        query_author = """
        MATCH (src:Paper {id: $paper_id})-[:WRITTEN_BY]->(a:Author)
        MATCH (other:Paper)-[:WRITTEN_BY]->(a)
        WHERE other.id IN $target_ids
        RETURN other.id AS other_id, a.name AS author_name
        """
        query_title = """
        MATCH (p:Paper)
        WHERE p.id IN $target_ids
        RETURN p.id AS paper_id, p.title AS title
        """

        params_enrich = {"paper_id": paper_id, "target_ids": target_ids}
        params_title  = {"target_ids": target_ids}

        try:
            c_rows = self._client.execute_read(query_concept,  params_enrich)
            e_rows = self._client.execute_read(query_evidence, params_enrich)
            a_rows = self._client.execute_read(query_author,   params_enrich)   # [v1.5-4]
            t_rows = self._client.execute_read(query_title,    params_title)
        except Exception:
            logger.exception("_enrich_shared: query failed paper_id=%s", paper_id)
            return

        shared_c: dict[str, list[str]] = {}
        for row in c_rows:
            shared_c.setdefault(row["other_id"], []).append(row["concept_name"])

        shared_e: dict[str, list[str]] = {}
        for row in e_rows:
            shared_e.setdefault(row["other_id"], []).append(row["evidence_name"])

        shared_a: dict[str, list[str]] = {}    # [v1.5-4]
        for row in a_rows:
            shared_a.setdefault(row["other_id"], []).append(row["author_name"])

        title_map: dict[str, str] = {
            row["paper_id"]: (row["title"] or "") for row in t_rows
        }

        for rec in recommendations:
            rec.shared_concepts  = shared_c.get(rec.paper_id, [])
            rec.shared_evidences = shared_e.get(rec.paper_id, [])
            rec.shared_authors   = shared_a.get(rec.paper_id, [])   # [v1.5-4]
            rec.title            = title_map.get(rec.paper_id, "")

    # ------------------------------------------------------------------
    # Insert SIMILAR_TO
    # ------------------------------------------------------------------

    def _insert_similar_to(
        self,
        paper_id: str,
        recommendations: list[GraphRecommendation],
    ) -> None:
        """
        Insert (Paper)-[:SIMILAR_TO]->(Paper).
        Idempotent qua merge_similar_to().
        shared_authors giờ có giá trị thực (không còn [] rỗng).  [v1.5-4]
        """
        inserted = 0
        for rec in recommendations:
            try:
                self._client.merge_similar_to(
                    paper_id_1=     paper_id,
                    paper_id_2=     rec.paper_id,
                    score=          rec.score,
                    shared_methods= rec.shared_concepts,
                    shared_authors= rec.shared_authors,   # [v1.5-4] có giá trị thực
                )
                inserted += 1
            except Exception:
                logger.warning(
                    "_insert_similar_to: failed %s → %s",
                    paper_id[:8], rec.paper_id[:8], exc_info=True,
                )

        logger.debug(
            "GraphRecommender._insert_similar_to: paper_id=%s inserted=%d",
            paper_id, inserted,
        )


# =============================================================================
# HELPERS
# =============================================================================

def _soft_normalize(scores: dict[str, float]) -> dict[str, float]:
    """
    Soft normalization: chia max.
    Candidate tốt nhất = 1.0; các candidate khác scaled theo tỷ lệ.
    Trả về {} nếu input rỗng hoặc max <= 0.
    """
    if not scores:
        return {}
    max_v = max(scores.values())
    if max_v <= 0.0:
        return {k: 0.0 for k in scores}
    return {k: v / max_v for k, v in scores.items()}


def _category_weight(category: str) -> float:
    """
    Weight cho category khi tính shared_concept score.
    Core technical concepts được boost.
    """
    high   = {"method", "model", "algorithm", "architecture"}
    medium = {"task", "framework", "technology", "process"}
    if category in high:
        return 1.2
    if category in medium:
        return 1.0
    return 0.8


def _evidence_type_weight(evidence_type: str) -> float:
    """
    Weight cho evidence_type khi tính shared_evidence score.
    """
    if evidence_type == "benchmark":
        return 1.3
    if evidence_type in ("dataset", "corpus"):
        return 1.0
    if evidence_type in ("survey", "census"):
        return 0.8
    return 0.7


# =============================================================================
# FACTORY
# =============================================================================

def create_graph_recommender_from_env(client=None) -> GraphRecommender:
    """
    Tạo GraphRecommender từ biến môi trường.

    .env:
        GRAPH_REC_TOP_K=20
        GRAPH_REC_MIN_SCORE=0.05
        GRAPH_REC_W_CONCEPT=0.35
        GRAPH_REC_W_EVIDENCE=0.20
        GRAPH_REC_W_CITATION=0.15
        GRAPH_REC_W_HIERARCHY=0.15
        GRAPH_REC_W_AUTHOR=0.15
        GRAPH_REC_AUTHOR_INST_WEIGHT=0.6
        GRAPH_REC_AUTHOR_FILTER_METHOD=true
        GRAPH_REC_INSERT_SIMILAR_TO=false
        GRAPH_REC_HIERARCHY_DEPTH=2
    """
    import os

    def _f(key, default): return float(os.getenv(key, str(default)))
    def _i(key, default): return int(os.getenv(key, str(default)))
    def _b(key, default): return os.getenv(key, str(default)).lower() in ("true", "1", "yes")

    config = GraphRecommenderConfig(
        top_k=                        _i("GRAPH_REC_TOP_K",                20),
        min_score=                    _f("GRAPH_REC_MIN_SCORE",             0.05),
        weight_shared_concept=        _f("GRAPH_REC_W_CONCEPT",             0.35),
        weight_shared_evidence=       _f("GRAPH_REC_W_EVIDENCE",            0.20),
        weight_citation_network=      _f("GRAPH_REC_W_CITATION",            0.15),
        weight_concept_hierarchy=     _f("GRAPH_REC_W_HIERARCHY",           0.15),
        weight_author_network=        _f("GRAPH_REC_W_AUTHOR",              0.15),
        author_institution_weight=    _f("GRAPH_REC_AUTHOR_INST_WEIGHT",    0.6),
        author_concept_filter_method= _b("GRAPH_REC_AUTHOR_FILTER_METHOD",  True),
        insert_similar_to=            _b("GRAPH_REC_INSERT_SIMILAR_TO",     False),
        concept_hierarchy_depth=      _i("GRAPH_REC_HIERARCHY_DEPTH",       2),
    )

    if client is None:
        from storage.graph_db.neo4j_client import Neo4jClient, Neo4jConfig
        neo4j_config = Neo4jConfig(
            uri=      os.getenv("NEO4J_URI", ""),
            username= os.getenv("NEO4J_USER", os.getenv("NEO4J_USERNAME", "")),
            password= os.getenv("NEO4J_PASSWORD", ""),
        )
        if not neo4j_config.uri or not neo4j_config.username or not neo4j_config.password:
            raise ValueError("Missing Aura Neo4j config: NEO4J_URI/NEO4J_USER(or NEO4J_USERNAME)/NEO4J_PASSWORD")
        client = Neo4jClient(neo4j_config)
        client.connect()

    logger.info(
        "GraphRecommender: top_k=%d min_score=%.2f "
        "w=[concept=%.2f ev=%.2f cit=%.2f hier=%.2f author=%.2f] "
        "author_inst_w=%.2f filter_method=%s insert=%s",
        config.top_k, config.min_score,
        config.weight_shared_concept, config.weight_shared_evidence,
        config.weight_citation_network, config.weight_concept_hierarchy,
        config.weight_author_network,
        config.author_institution_weight,
        config.author_concept_filter_method,
        config.insert_similar_to,
    )
    return GraphRecommender(client, config)
