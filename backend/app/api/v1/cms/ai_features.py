import json
import os
import sys
import logging
from datetime import datetime
from pathlib import Path
import tempfile
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import get_current_user
from app.db.session import SessionLocal, get_db
from app.models.document import Document
from app.models.document_graph import DocumentGraph
from app.models.document_artifact import DocumentArtifact
from app.models.document_job import DocumentJob
from app.models.document_recommendation import DocumentRecommendation
from app.models.document_summary import DocumentSummary
from app.models.qa_history import QAHistory
from app.services.ingestion_runner import run_ingestion_for_document
from app.services.ai_summary_service import generate_summary_from_doc_json, normalize_summary_style
from app.schemas.cms_ai import (
    GraphResponse,
    JobRequestResponse,
    JobStatusResponse,
    QARequest,
    QARequestResponse,
    RecommendationItem,
    RecommendationResponse,
    SearchRequest,
    SearchRequestResponse,
    SearchResponse,
    SearchResult,
    SummaryRequest,
    SummaryResponse,
)


router = APIRouter()
logger = logging.getLogger(__name__)
def _detect_project_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "ingestion").exists() and (parent / "ai_module").exists():
            return parent
    # Fallback for container layout where app code lives under /app/app/*
    return current.parents[4]


PROJECT_ROOT = _detect_project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def get_owned_document(db: Session, document_id: int, user_id: int) -> Document:
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.user_id == user_id,
            Document.is_deleted == False,
        )
        .first()
    )

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    return document


def get_latest_summary(db: Session, document_id: int, summary_style: str | None = None) -> DocumentSummary | None:
    try:
        query = db.query(DocumentSummary).filter(DocumentSummary.document_id == document_id)
        if summary_style:
            query = query.filter(DocumentSummary.summary_style == normalize_summary_style(summary_style))
        return query.order_by(DocumentSummary.created_at.desc(), DocumentSummary.id.desc()).first()
    except ProgrammingError as ex:
        logger.warning(
            "summary_query_programming_error document_id=%s summary_style=%s error=%s",
            document_id,
            summary_style,
            str(ex),
        )
        db.rollback()
        return None


def _load_document_artifact_json(db: Session, document_id: int) -> dict | None:
    artifacts = (
        db.query(DocumentArtifact)
        .filter(
            DocumentArtifact.document_id == document_id,
            DocumentArtifact.artifact_type == "unified_json",
        )
        .order_by(DocumentArtifact.created_at.desc())
        .all()
    )
    if not artifacts:
        return None
    for artifact in artifacts:
        raw_uri = (artifact.uri or "").strip()
        if not raw_uri:
            continue
        path = Path(raw_uri)
        if not path.is_absolute():
            # Prefer artifact paths relative to repository root.
            candidate_paths = [
                (PROJECT_ROOT / raw_uri).resolve(),
                (PROJECT_ROOT / "backend" / raw_uri).resolve(),
            ]
            path = next((p for p in candidate_paths if p.exists()), candidate_paths[0])
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            continue
    return None


def _fallback_summary_from_doc_json(doc_json: dict) -> str:
    abstract = (doc_json.get("abstract") or "").strip()
    if abstract:
        return abstract
    full_text = (doc_json.get("full_text") or "").strip()
    if full_text:
        return full_text[:1800]
    sections = doc_json.get("sections") or []
    content_parts = []
    for sec in sections[:3]:
        if isinstance(sec, dict):
            content = (sec.get("content") or "").strip()
            if content:
                content_parts.append(content[:600])
    return "\n\n".join(content_parts)[:1800]


def _generate_summary_with_ai_module(doc_json: dict, summary_style: str = "academic") -> str:
    try:
        return generate_summary_from_doc_json(
            doc_json=doc_json,
            summary_style=normalize_summary_style(summary_style),
            num_highlights=16,
            chunk_highlights=4,
        )
    except Exception as ex:
        raise RuntimeError(f"AI module summary failed: {ex}") from ex


def _save_summary_row(
    db: Session,
    document_id: int,
    summary_text: str,
    summary_style: str = "academic",
) -> DocumentSummary:
    try:
        style = normalize_summary_style(summary_style)
        latest = get_latest_summary(db, document_id, style)
        if latest:
            latest.summary_style = style
            latest.summary = summary_text
            row = latest
        else:
            row = DocumentSummary(
                document_id=document_id,
                summary_style=style,
                summary=summary_text,
            )
            db.add(row)
        db.commit()
        db.refresh(row)
        return row
    except SQLAlchemyError as ex:
        db.rollback()
        # Some deployments may not have the document_summaries table yet.
        # Do not fail summary generation for this persistence issue.
        msg = str(ex).lower()
        if "document_summaries" in msg and "does not exist" in msg:
            raise RuntimeError("Summary table does not exist.")
        raise RuntimeError(f"Failed to save summary to database: {ex}") from ex


def _extract_searchable_texts(doc_json: dict[str, Any]) -> list[dict[str, Any]]:
    chunks = doc_json.get("chunks")
    rows: list[dict[str, Any]] = []
    if isinstance(chunks, list):
        for i, chunk in enumerate(chunks):
            if isinstance(chunk, dict):
                text = str(chunk.get("text") or chunk.get("content") or "").strip()
                if text:
                    rows.append({"chunk_index": int(chunk.get("chunk_id", i)), "content": text})
            elif isinstance(chunk, str):
                text = chunk.strip()
                if text:
                    rows.append({"chunk_index": i, "content": text})
        if rows:
            return rows

    sections = doc_json.get("sections")
    if isinstance(sections, list):
        for i, sec in enumerate(sections):
            if not isinstance(sec, dict):
                continue
            content = str(sec.get("content") or "").strip()
            if not content:
                continue
            name = str(sec.get("name") or "").strip()
            text = f"{name}\n{content}" if name else content
            rows.append({"chunk_index": int(sec.get("order", i)), "content": text})
        if rows:
            return rows

    full_text = str(doc_json.get("full_text") or "").strip()
    if full_text:
        return [{"chunk_index": 0, "content": full_text}]
    return []


def _local_search_in_doc_json(doc_json: dict, query: str, limit: int) -> list[SearchResult]:
    q = (query or "").strip().lower()
    rows = _extract_searchable_texts(doc_json)
    scored = []
    for row in rows:
        content = row["content"]
        low = content.lower()
        cnt = low.count(q) if q else 0
        if cnt > 0:
            scored.append((cnt, row["chunk_index"], content))
    scored.sort(key=lambda x: (x[0], -x[1]), reverse=True)
    return [
        SearchResult(
            chunk_id=None,
            document_id=0,
            chunk_index=idx,
            content=content[:2500],
            score=float(score),
            embedding_available=False,
        )
        for score, idx, content in scored[:limit]
    ]


def _semantic_search_in_doc_json(doc_json: dict, query: str, limit: int) -> list[SearchResult]:
    try:
        from ai_module.inference.inference_config import InferenceConfig
        from ai_module.retrieval.vector_retriever import VectorRetriever

        cfg = InferenceConfig()
        retriever = VectorRetriever(cfg)
        retriever.build([doc_json])
        rows = retriever.retrieve(query, top_k=limit)
        items: list[SearchResult] = []
        for row in rows:
            raw_chunk_id = row.get("chunk_id", 0)
            try:
                chunk_index = int(raw_chunk_id)
            except Exception:
                chunk_index = 0
            items.append(
                SearchResult(
                    chunk_id=chunk_index,
                    document_id=0,
                    chunk_index=chunk_index,
                    content=str(row.get("text") or "")[:2500],
                    score=float(row.get("score") or 0.0),
                    embedding_available=True,
                )
            )
        if items:
            return items
    except Exception:
        pass

    return _local_search_in_doc_json(doc_json, query, limit)


def _generate_qa_with_ai_module(doc_json: dict, question: str, top_k: int = 5) -> tuple[str, list[dict]]:
    from ai_module.inference.inference_config import InferenceConfig
    from ai_module.inference.llm_engine import LLMEngine
    from ai_module.reasoning.context_builder import compact_sources, format_retrieved_context
    from ai_module.reasoning.prompt_builder import rag_prompt
    from ai_module.retrieval.fusion_retriever import FusionRetriever
    from ai_module.retrieval.graph_retriever import create_graph_retriever_from_env
    from ai_module.retrieval.vector_retriever import VectorRetriever

    cfg = InferenceConfig()
    retriever = VectorRetriever(cfg)
    retriever.build([doc_json])
    vector_rows = retriever.retrieve(question, top_k=max(top_k, cfg.top_k))
    rows = vector_rows[:top_k]
    try:
        graph_retriever = create_graph_retriever_from_env()
        fused = FusionRetriever(retriever, graph_retriever=graph_retriever)
        rows = fused.retrieve(question, top_k=top_k)
    except Exception:
        # Graceful fallback to vector-only when Neo4j/graph service is unavailable.
        rows = vector_rows[:top_k]
    context = format_retrieved_context(rows)
    prompt = rag_prompt(question=question, context=context)
    llm = LLMEngine(cfg)
    answer = llm.generate(prompt, max_new_tokens=cfg.max_new_tokens_qa).strip()
    if not answer:
        answer = "Không tìm thấy thông tin trong tài liệu."
    sources = compact_sources(rows)
    if not sources:
        sources = [{"text": str(r.get("text") or "")[:300], "score": float(r.get("score") or 0.0)} for r in rows[:top_k]]
    return answer, sources


def _build_kg_with_ai_module(doc_json: dict) -> tuple[list[dict], list[dict]]:
    from ai_module.kg.entity_extractor import create_extractor_from_env
    from ai_module.kg.graph_builder import GraphBuilder
    from ingestion.schema.document_schema import UnifiedDocument
    from storage.graph_db.neo4j_client import Neo4jClient, Neo4jConfig

    doc = UnifiedDocument(
        title=str(doc_json.get("title") or "Unknown"),
        authors=[str(a) for a in (doc_json.get("authors") or []) if str(a).strip()],
        year=doc_json.get("year"),
        journal=doc_json.get("journal"),
        doi=doc_json.get("doi"),
        keywords=[str(k) for k in (doc_json.get("keywords") or []) if str(k).strip()],
        abstract=str(doc_json.get("abstract") or ""),
        full_text=str(doc_json.get("full_text") or ""),
        source_file=str(doc_json.get("file") or ""),
        source_type=str(doc_json.get("source_type") or "pdf"),
        language=doc_json.get("language"),
    )
    cfg = Neo4jConfig(
        uri=os.getenv("NEO4J_URI", "").strip(),
        username=os.getenv("NEO4J_USER", os.getenv("NEO4J_USERNAME", "")).strip(),
        password=os.getenv("NEO4J_PASSWORD", "").strip(),
        database=os.getenv("NEO4J_USER_DOC_DB", os.getenv("NEO4J_DATABASE", "neo4j")).strip(),
    )
    if not cfg.uri or not cfg.username or not cfg.password:
        raise RuntimeError("Missing Aura Neo4j config: NEO4J_URI/NEO4J_USER(or NEO4J_USERNAME)/NEO4J_PASSWORD")
    client = Neo4jClient(cfg)
    client.connect()
    try:
        client.ensure_schema()
        extractor = create_extractor_from_env()
        entities = extractor.extract(doc)
        builder = GraphBuilder(client)
        paper_id = builder.build(doc, entities, relations=None, chunk_map=None)
        rows = client.execute_read(
            """
            MATCH (p:Paper {id: $paper_id})-[r]-(n)
            RETURN p, r, n
            LIMIT 250
            """,
            {"paper_id": paper_id},
        )
    finally:
        client.close()

    node_map: dict[str, dict] = {}
    edges: list[dict] = []
    for row in rows:
        for key in ("p", "n"):
            nd = row.get(key)
            if not nd:
                continue
            nid = str(nd.get("id") or getattr(nd, "id", ""))
            if not nid or nid in node_map:
                continue
            labels = list(getattr(nd, "labels", []) or [])
            node_map[nid] = {
                "id": nid,
                "label": str(nd.get("title") or nd.get("name") or nid),
                "type": labels[0] if labels else "Node",
            }
        rel = row.get("r")
        p = row.get("p")
        n = row.get("n")
        if rel and p and n:
            edges.append(
                {
                    "source": str(p.get("id") or getattr(p, "id", "")),
                    "target": str(n.get("id") or getattr(n, "id", "")),
                    "relation": str(getattr(rel, "type", "")),
                }
            )
    return list(node_map.values()), edges


def _build_kg_from_neo4j(document: Document) -> tuple[list[dict], list[dict]]:
    try:
        from neo4j import GraphDatabase
    except Exception:
        return [], []

    uri = os.getenv("NEO4J_URI", "").strip()
    user = os.getenv("NEO4J_USER", os.getenv("NEO4J_USERNAME", "")).strip()
    password = os.getenv("NEO4J_PASSWORD", "").strip()
    database = os.getenv("NEO4J_DATABASE", "neo4j").strip()
    if not uri or not user or not password:
        return [], []

    title = ""
    if document.metadata_record and document.metadata_record.title:
        title = str(document.metadata_record.title).strip()
    if not title:
        title = str(document.filename or "").strip()
    if not title:
        return [], []

    query = """
    MATCH (p:Paper)
    WHERE toLower(coalesce(p.title,'')) CONTAINS toLower($title)
    OPTIONAL MATCH (p)-[r]-(n)
    RETURN p, r, n
    LIMIT 200
    """

    nodes_map: dict[str, dict] = {}
    edges: list[dict] = []
    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        with driver.session(database=database) as session:
            rows = session.run(query, title=title)
            for row in rows:
                for key in ("p", "n"):
                    nd = row.get(key)
                    if not nd:
                        continue
                    nid = str(nd.get("id") or nd.id)
                    if nid in nodes_map:
                        continue
                    labels = list(nd.labels) if hasattr(nd, "labels") else []
                    nodes_map[nid] = {
                        "id": nid,
                        "label": str(nd.get("title") or nd.get("name") or nid),
                        "type": labels[0] if labels else "Node",
                    }
                rel = row.get("r")
                a = row.get("p")
                b = row.get("n")
                if rel and a and b:
                    edges.append(
                        {
                            "source": str(a.get("id") or a.id),
                            "target": str(b.get("id") or b.id),
                            "relation": rel.type,
                        }
                    )
    return list(nodes_map.values()), edges


def _compute_ai_module_recommendations(
    db: Session,
    owner_id: int,
    current_document: Document,
    limit: int = 10,
) -> list[RecommendationItem]:
    from ai_module.inference.inference_config import InferenceConfig
    from ai_module.recommendation.hybrid_recommender import (
        HybridRecommender,
        build_recommendation_index,
    )
    from ai_module.utils.document_schema import extract_document_text

    docs = (
        db.query(Document)
        .filter(Document.user_id == owner_id, Document.is_deleted == False)
        .all()
    )
    corpus: list[tuple[Document, dict]] = []
    for doc in docs:
        doc_json = _load_document_artifact_json(db, doc.id)
        if doc_json:
            corpus.append((doc, doc_json))
    if len(corpus) < 2:
        return []

    query_parts: list[str] = []
    current_meta = current_document.metadata_record
    if current_meta:
        if current_meta.title:
            query_parts.append(str(current_meta.title))
        if current_meta.abstract:
            query_parts.append(str(current_meta.abstract))
        if current_meta.keywords:
            query_parts.extend([str(k) for k in current_meta.keywords if str(k).strip()])
    query = " ".join(query_parts).strip() or current_document.filename

    prepared_docs: list[dict] = []
    file_to_document: dict[str, Document] = {}
    for doc, doc_json in corpus:
        file_key = f"document_{doc.id}"
        prepared_docs.append({"file": file_key, "text": extract_document_text(doc_json)})
        file_to_document[file_key] = doc

    cfg = InferenceConfig()
    recommender = HybridRecommender(cfg)
    with tempfile.TemporaryDirectory(prefix="ai-paper-rec-") as tmpdir:
        build_recommendation_index(prepared_docs, recommender.embedding, tmpdir)
        raw = recommender.recommend(index_path=tmpdir, top_k=max(15, limit), query=query)

    items: list[RecommendationItem] = []
    for rec in raw.get("recommendations", []):
        file_key = str(rec.get("file") or "")
        target = file_to_document.get(file_key)
        if not target or target.id == current_document.id:
            continue
        items.append(
            RecommendationItem(
                id=None,
                recommended_document_id=target.id,
                title=str(rec.get("title") or target.filename),
                reason=f"hybrid semantic score={rec.get('score', 0)}",
                score=float(rec.get("score") or 0.0),
                source="ai_module_hybrid",
                external_url=None,
            )
        )
        if len(items) >= limit:
            break
    return items


def _compute_neo4j_graph_recommendations(
    document: Document,
    limit: int = 10,
) -> list[RecommendationItem]:
    from ai_module.recommendation.graph_recommender import create_graph_recommender_from_env
    from storage.graph_db.neo4j_client import Neo4jClient, Neo4jConfig

    cfg = Neo4jConfig(
        uri=os.getenv("NEO4J_REC_URI", "").strip(),
        username=os.getenv(
            "NEO4J_REC_USER",
            os.getenv("NEO4J_REC_USERNAME", ""),
        ).strip(),
        password=os.getenv("NEO4J_REC_PASSWORD", "").strip(),
        database=os.getenv("NEO4J_REC_DATABASE", "neo4j").strip(),
    )
    if not cfg.uri or not cfg.username or not cfg.password:
        return []
    meta = document.metadata_record
    title = str(meta.title).strip() if meta and meta.title else str(document.filename or "").strip()
    doi = str(meta.doi).strip() if meta and meta.doi else ""

    def _normalize_doi(raw: str) -> str:
        s = (raw or "").strip()
        if not s:
            return ""
        s = s.replace("https://doi.org/", "").replace("http://doi.org/", "")
        s = s.replace("doi:", "").strip()
        return s

    doi = _normalize_doi(doi)

    def _resolve_seed_paper_id() -> str | None:
        seed_paper_id: str | None = None

        if doi or title:
            rows = client.execute_read(
                """
                MATCH (p:Paper)
                WHERE ($doi <> '' AND p.doi = $doi)
                   OR ($title <> '' AND p.title = $title)
                RETURN p.id AS id
                LIMIT 1
                """,
                {"doi": doi, "title": title},
            )
            if rows:
                seed_paper_id = str(rows[0]["id"])
        if not seed_paper_id and title:
            rows = client.execute_read(
                """
                MATCH (p:Paper)
                WHERE toLower(coalesce(p.title,'')) CONTAINS toLower($title)
                   OR toLower($title) CONTAINS toLower(coalesce(p.title,''))
                RETURN p.id AS id, size(coalesce(p.title,'')) AS tlen
                ORDER BY tlen DESC
                LIMIT 1
                """,
                {"title": title},
            )
            if rows:
                seed_paper_id = str(rows[0]["id"])
        return seed_paper_id

    def _fallback_global_recommendations(k: int) -> list[RecommendationItem]:
        rows = client.execute_read(
            """
            MATCH (p:Paper)
            OPTIONAL MATCH (p)<-[:CITES]-(:Paper)
            WITH p, count(*) AS cite_in
            RETURN p.id AS id, p.title AS title, p.doi AS doi, cite_in
            ORDER BY cite_in DESC, p.year DESC
            LIMIT $k
            """,
            {"k": max(k, 10)},
        )
        items: list[RecommendationItem] = []
        for row in rows:
            rec_title = str(row.get("title") or row.get("id") or "Unknown paper")
            doi_target = str(row.get("doi") or "").strip()
            cite_in = int(row.get("cite_in") or 0)
            items.append(
                RecommendationItem(
                    id=None,
                    recommended_document_id=None,
                    recommendation_type="method",
                    title=rec_title,
                    reason=f"global graph fallback; citations={cite_in}",
                    score=float(cite_in),
                    source="neo4j_graph_dump",
                    external_url=(f"https://doi.org/{doi_target}" if doi_target else None),
                )
            )
            if len(items) >= k:
                break
        return items

    client = Neo4jClient(cfg)
    client.connect()
    try:
        seed_paper_id = _resolve_seed_paper_id()

        if not seed_paper_id and title:
            rows = []
            try:
                rows = client.execute_read(
                    """
                    CALL db.index.fulltext.queryNodes('paper_title_ft', $q)
                    YIELD node, score
                    RETURN node.id AS id, score
                    ORDER BY score DESC
                    LIMIT 1
                    """,
                    {"q": title},
                )
            except Exception:
                rows = client.execute_read(
                    """
                    MATCH (p:Paper)
                    WHERE toLower(trim(p.title)) = toLower(trim($title))
                    RETURN p.id AS id
                    LIMIT 1
                    """,
                    {"title": title},
                )
            if rows:
                seed_paper_id = str(rows[0]["id"])

        if not seed_paper_id:
            return _fallback_global_recommendations(limit)

        prev_env = {
            "GRAPH_REC_W_CONCEPT": os.getenv("GRAPH_REC_W_CONCEPT"),
            "GRAPH_REC_W_EVIDENCE": os.getenv("GRAPH_REC_W_EVIDENCE"),
            "GRAPH_REC_W_CITATION": os.getenv("GRAPH_REC_W_CITATION"),
            "GRAPH_REC_W_HIERARCHY": os.getenv("GRAPH_REC_W_HIERARCHY"),
            "GRAPH_REC_W_AUTHOR": os.getenv("GRAPH_REC_W_AUTHOR"),
            "GRAPH_REC_AUTHOR_FILTER_METHOD": os.getenv("GRAPH_REC_AUTHOR_FILTER_METHOD"),
        }
        try:
            os.environ["GRAPH_REC_W_CONCEPT"] = "0.0"
            os.environ["GRAPH_REC_W_EVIDENCE"] = "0.0"
            os.environ["GRAPH_REC_W_CITATION"] = "0.0"
            os.environ["GRAPH_REC_W_HIERARCHY"] = "0.0"
            os.environ["GRAPH_REC_W_AUTHOR"] = "1.0"
            os.environ["GRAPH_REC_AUTHOR_FILTER_METHOD"] = "true"
            recommender = create_graph_recommender_from_env(client=client)
            raw_recs = recommender.recommend(seed_paper_id, top_k=max(limit, 10))
        finally:
            for key, value in prev_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        items: list[RecommendationItem] = []
        for rec in raw_recs:
            rows = client.execute_read(
                "MATCH (p:Paper {id: $pid}) RETURN p.title AS title, p.doi AS doi LIMIT 1",
                {"pid": rec.paper_id},
            )
            p = rows[0] if rows else {}
            rec_title = str(rec.title or p.get("title") or rec.paper_id)
            doi_target = str(p.get("doi") or "").strip()
            reason = (
                f"author-network score={rec.score:.3f}; "
                f"authors={rec.breakdown.get('author_network', 0):.2f}"
            )
            if rec.shared_concepts:
                reason += f"; shared concepts={', '.join(rec.shared_concepts[:3])}"
            items.append(
                RecommendationItem(
                    id=None,
                    recommended_document_id=None,
                    recommendation_type="method",
                    title=rec_title,
                    reason=reason,
                    score=float(rec.score or 0.0),
                    source="neo4j_graph_dump",
                    external_url=(f"https://doi.org/{doi_target}" if doi_target else None),
                )
            )
            if len(items) >= limit:
                break
        return items
    except Exception:
        logger.exception("neo4j graph recommendations failed: document_id=%s", document.id)
        return []
    finally:
        try:
            client.close()
        except Exception:
            pass


def _compute_neo4j_author_recommendations(
    document: Document,
    doc_json: dict | None = None,
    limit: int = 10,
) -> list[RecommendationItem]:
    from storage.graph_db.neo4j_client import Neo4jClient, Neo4jConfig

    cfg = Neo4jConfig(
        uri=os.getenv("NEO4J_REC_URI", "").strip(),
        username=os.getenv(
            "NEO4J_REC_USER",
            os.getenv("NEO4J_REC_USERNAME", ""),
        ).strip(),
        password=os.getenv("NEO4J_REC_PASSWORD", "").strip(),
        database=os.getenv("NEO4J_REC_DATABASE", "neo4j").strip(),
    )
    if not cfg.uri or not cfg.username or not cfg.password:
        return []
    client = Neo4jClient(cfg)
    client.connect()
    try:
        meta = document.metadata_record
        doc_json = doc_json or {}
        title = str(meta.title).strip() if meta and meta.title else str(document.filename or "").strip()
        doi = str(meta.doi).strip() if meta and meta.doi else ""
        meta_authors = [str(a).strip() for a in ((meta.authors if meta else []) or []) if str(a).strip()]
        entity_authors, _ = _extract_recommendation_entities(doc_json, document)
        authors = list(dict.fromkeys([*entity_authors, *meta_authors]))[:20]
        if not title and not doi and not authors:
            return []

        rows = []
        if authors:
            rows = client.execute_read(
                """
                UNWIND $authors AS author_q
                MATCH (a:Author)-[:WROTE]->(p:Paper)
                WHERE toLower(a.name) CONTAINS toLower(author_q)
                   OR toLower(author_q) CONTAINS toLower(a.name)
                RETURN DISTINCT p.id AS id, p.title AS title, p.doi AS doi, a.name AS matched_author
                LIMIT $k
                """,
                {"authors": authors[:10], "k": max(limit, 10)},
            )

        if not rows:
            return []

        items: list[RecommendationItem] = []
        for row in rows:
            rec_title = str(row.get("title") or row.get("id") or "Unknown paper")
            doi_target = str(row.get("doi") or "").strip()
            matched_author = str(row.get("matched_author") or "").strip()
            reason = f"Cùng tác giả '{matched_author}'" if matched_author else "Cùng tác giả"
            items.append(
                RecommendationItem(
                    id=None,
                    recommended_document_id=None,
                    recommendation_type="author",
                    title=rec_title,
                    reason=reason,
                    score=0.8,
                    source="neo4j_author_graph",
                    external_url=(f"https://doi.org/{doi_target}" if doi_target else None),
                )
            )
            if len(items) >= limit:
                break
        return items
    except Exception:
        logger.exception("neo4j author recommendations failed: document_id=%s", document.id)
        return []
    finally:
        try:
            client.close()
        except Exception:
            pass


def _extract_recommendation_entities(doc_json: dict, document: Document | None = None) -> tuple[list[str], list[str]]:
    """
    Extract author + method-like entities from ingested artifact.
    Priority:
    1) Entity extractor output (author + concept categories)
    2) Metadata fallback (authors + keywords)
    """
    doc_json = doc_json or {}
    authors_out: list[str] = []
    methods_out: list[str] = []

    entities = doc_json.get("entities") or {}
    if isinstance(entities, dict):
        raw_authors = entities.get("authors") or []
        for row in raw_authors:
            if isinstance(row, dict):
                name = str(row.get("name") or "").strip()
            else:
                name = str(row or "").strip()
            if name:
                authors_out.append(name)

        raw_concepts = entities.get("concepts") or entities.get("methods") or []
        for row in raw_concepts:
            if isinstance(row, dict):
                name = str(row.get("name") or "").strip()
                category = str(row.get("category") or "").strip().lower()
                if name and (not category or category == "method"):
                    methods_out.append(name)
            else:
                name = str(row or "").strip()
                if name:
                    methods_out.append(name)

    if not authors_out:
        authors_out = [str(a).strip() for a in (doc_json.get("authors") or []) if str(a).strip()]
    if not methods_out:
        methods_out = [str(k).strip() for k in (doc_json.get("keywords") or []) if str(k).strip()]
    if document and document.metadata_record:
        meta = document.metadata_record
        if not authors_out:
            authors_out = [str(a).strip() for a in ((meta.authors or []) if meta else []) if str(a).strip()]
        if not methods_out:
            methods_out = [str(k).strip() for k in ((meta.keywords or []) if meta else []) if str(k).strip()]

    dedup = lambda xs: list(dict.fromkeys([x for x in xs if x]))
    return dedup(authors_out)[:20], dedup(methods_out)[:30]


def _compute_neo4j_entity_recommendations(
    document: Document,
    doc_json: dict | None,
    limit: int = 10,
) -> list[RecommendationItem]:
    from storage.graph_db.neo4j_client import Neo4jClient, Neo4jConfig

    authors, methods = _extract_recommendation_entities(doc_json, document)
    if not authors or not methods:
        return []

    cfg = Neo4jConfig(
        uri=os.getenv("NEO4J_REC_URI", "").strip(),
        username=os.getenv(
            "NEO4J_REC_USER",
            os.getenv("NEO4J_REC_USERNAME", ""),
        ).strip(),
        password=os.getenv("NEO4J_REC_PASSWORD", "").strip(),
        database=os.getenv("NEO4J_REC_DATABASE", "neo4j").strip(),
    )
    if not cfg.uri or not cfg.username or not cfg.password:
        return []
    client = Neo4jClient(cfg)
    client.connect()
    try:
        items: list[RecommendationItem] = []
        rows = client.execute_read(
            """
            MATCH (a:Author)-[:WROTE]->(p:Paper)
            WHERE a.name IN $authors
            MATCH (p)-[:USES_CONCEPT]->(c:Concept)
            WHERE toLower(coalesce(c.category, ''))='method' AND c.name IN $methods
            RETURN DISTINCT
                p.id AS id,
                p.title AS title,
                p.doi AS doi,
                a.name AS matched_author,
                c.name AS matched_method
            LIMIT $k
            """,
            {"authors": authors[:20], "methods": methods[:30], "k": max(limit, 10)},
        )
        for row in rows:
            title = str(row.get("title") or row.get("id") or "Unknown paper")
            doi = str(row.get("doi") or "").strip()
            matched_author = str(row.get("matched_author") or "").strip()
            matched_method = str(row.get("matched_method") or "").strip()
            rec_type = "author" if matched_author else "method"
            items.append(
                RecommendationItem(
                    id=None,
                    recommended_document_id=None,
                    recommendation_type=rec_type,
                    title=title,
                    reason=f"Cùng tác giả '{matched_author}' và method '{matched_method}'",
                    score=1.0,
                    source="neo4j_entity",
                    external_url=(f"https://doi.org/{doi}" if doi else None),
                )
            )

        seen: set[str] = set()
        dedup_items: list[RecommendationItem] = []
        for it in items:
            k = str(it.title or "").strip().lower()
            if not k or k in seen:
                continue
            seen.add(k)
            dedup_items.append(it)
        return dedup_items[:limit]
    except Exception:
        logger.exception("neo4j entity recommendations failed: document_id=%s", document.id)
        return []
    finally:
        try:
            client.close()
        except Exception:
            pass


def _compute_metadata_recommendations(
    db: Session,
    owner_id: int,
    current_document: Document,
    limit: int = 10,
) -> list[RecommendationItem]:
    current_meta = current_document.metadata_record
    current_keywords = {str(k).strip().lower() for k in (current_meta.keywords if current_meta else []) if str(k).strip()}
    current_authors = {str(a).strip().lower() for a in (current_meta.authors if current_meta else []) if str(a).strip()}

    others = (
        db.query(Document)
        .filter(
            Document.user_id == owner_id,
            Document.id != current_document.id,
            Document.is_deleted == False,
        )
        .all()
    )

    items: list[RecommendationItem] = []
    for doc in others:
        meta = doc.metadata_record
        if not meta:
            continue
        kws = {str(k).strip().lower() for k in (meta.keywords or []) if str(k).strip()}
        aus = {str(a).strip().lower() for a in (meta.authors or []) if str(a).strip()}

        kw_overlap = len(current_keywords & kws)
        au_overlap = len(current_authors & aus)
        score = 0.65 * kw_overlap + 0.35 * au_overlap
        if score <= 0:
            continue

        reason_parts = []
        if au_overlap:
            reason_parts.append(f"shared authors={au_overlap}")
        if kw_overlap:
            reason_parts.append(f"shared keywords={kw_overlap}")
        reason = ", ".join(reason_parts) if reason_parts else "metadata similarity"

        title = meta.title or doc.filename
        items.append(
            RecommendationItem(
                id=None,
                recommended_document_id=doc.id,
                recommendation_type=("author" if au_overlap >= kw_overlap else "method"),
                title=title,
                reason=reason,
                score=float(score),
                source="metadata",
                external_url=None,
            )
        )

    items.sort(key=lambda x: x.score or 0.0, reverse=True)
    return items[:limit]


def create_ai_job(
    db: Session,
    document: Document,
    user_id: int,
    job_type: str,
    payload: dict | None = None,
) -> DocumentJob:
    job = DocumentJob(
        document_id=document.id,
        job_type=job_type,
        requested_by_user_id=user_id,
        payload=payload or {},
    )
    db.add(job)

    if document.status in ["uploaded", "failed"]:
        document.status = "processing"

    db.commit()
    db.refresh(job)
    return job


def create_global_ai_job(
    db: Session,
    user_id: int,
    job_type: str,
    payload: dict | None = None,
) -> DocumentJob:
    job = DocumentJob(
        document_id=None,
        job_type=job_type,
        requested_by_user_id=user_id,
        payload=payload or {},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def serialize_job(job: DocumentJob) -> JobStatusResponse:
    return JobStatusResponse(
        id=job.id,
        document_id=job.document_id,
        job_type=job.job_type,
        status=job.status,
        payload=job.payload,
        result=job.result,
        error_message=job.error_message,
        retry_count=job.retry_count or 0,
        created_at=job.created_at,
        last_started_at=job.last_started_at,
        completed_at=job.completed_at,
    )


@router.post(
    "/documents/{document_id}/process/request",
    response_model=JobRequestResponse,
)
def request_document_processing(
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    document = get_owned_document(db, document_id, current_user.id)
    job = create_ai_job(
        db=db,
        document=document,
        user_id=current_user.id,
        job_type="process_document",
        payload={"document_id": document.id},
    )

    run_ingestion_for_document(document.id, use_lm=True)
    db.refresh(job)
    return {
        "document_id": document.id,
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "message": "Document processing executed",
    }


@router.post(
    "/documents/{document_id}/summary/request",
    response_model=QARequestResponse,
)
def request_summary(
    document_id: int,
    payload: SummaryRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _request_summary_by_style(
        document_id=document_id,
        level=payload.level,
        summary_style=payload.summary_style,
        db=db,
        current_user=current_user,
    )


def _request_summary_by_style(
    document_id: int,
    level: str,
    summary_style: str,
    db: Session,
    current_user,
):
    document = get_owned_document(db, document_id, current_user.id)
    job = create_ai_job(
        db=db,
        document=document,
        user_id=current_user.id,
        job_type="summary",
        payload={"level": level, "summary_style": summary_style},
    )

    doc_json = _load_document_artifact_json(db, document.id)
    if not doc_json:
        current_status = str(document.status or "").strip().lower()
        processing_statuses = {
            "uploaded", "processing", "extracting", "chunking", "embedding",
            "graphing", "building_graph",
        }
        if current_status in processing_statuses:
            job.status = "running"
            job.error_message = None
            job.result = {
                "status": "running",
                "message": "Document is still being processed. Retry summary shortly.",
                "document_status": current_status,
                "summary_style": normalize_summary_style(summary_style),
            }
        else:
            job.status = "failed"
            job.error_message = "No ingestion artifact found. Upload/processing must complete first."
            job.completed_at = datetime.utcnow()
        db.commit()
        return {
            "document_id": document.id,
            "question": f"summary:{level}:{summary_style}",
            "status": "running" if current_status in processing_statuses else "failed",
            "job_id": job.id,
            "message": (
                "Tài liệu đang được xử lý ingestion. Vui lòng đợi và thử lại sau."
                if current_status in processing_statuses
                else "No artifact found for summary generation"
            ),
            "summary_style": normalize_summary_style(summary_style),
            "summary_text": None,
        }

    try:
        text = _generate_summary_with_ai_module(doc_json, summary_style=summary_style)
    except Exception as ex:
        job.status = "failed"
        job.error_message = str(ex)
        job.completed_at = datetime.utcnow()
        db.commit()
        return {
            "document_id": document.id,
            "question": f"summary:{level}:{summary_style}",
            "status": "failed",
            "job_id": job.id,
            "message": str(ex),
            "summary_style": normalize_summary_style(summary_style),
            "summary_text": None,
        }
    if not text:
        job.status = "failed"
        job.error_message = "Summary generation returned empty output."
        job.completed_at = datetime.utcnow()
        db.commit()
        return {
            "document_id": document.id,
            "question": f"summary:{level}:{summary_style}",
            "status": "failed",
            "job_id": job.id,
            "message": "Summary generation failed",
            "summary_style": normalize_summary_style(summary_style),
            "summary_text": None,
        }

    try:
        saved_row = _save_summary_row(db, document.id, text, summary_style=summary_style)
    except Exception as ex:
        job.status = "failed"
        job.error_message = str(ex)
        job.completed_at = datetime.utcnow()
        db.commit()
        return {
            "document_id": document.id,
            "question": f"summary:{level}:{summary_style}",
            "status": "failed",
            "job_id": job.id,
            "message": "Không lưu được nội dung tóm tắt.",
            "summary_style": normalize_summary_style(summary_style),
            "summary_text": None,
        }

    if not (saved_row and str(saved_row.summary or "").strip()):
        job.status = "failed"
        job.error_message = "Summary persisted as empty."
        job.completed_at = datetime.utcnow()
        db.commit()
        return {
            "document_id": document.id,
            "question": f"summary:{level}:{summary_style}",
            "status": "failed",
            "job_id": job.id,
            "message": "Nội dung tóm tắt rỗng sau khi lưu.",
            "summary_style": normalize_summary_style(summary_style),
            "summary_text": None,
        }
    job.status = "done"
    job.result = {
        "summary_preview": text[:500],
        "summary_text": text,
        "level": level,
        "summary_style": summary_style,
    }
    job.error_message = None
    job.completed_at = datetime.utcnow()
    db.commit()

    return {
        "document_id": document.id,
        "question": f"summary:{level}:{summary_style}",
        "status": "done",
        "job_id": job.id,
        "message": "Summary generated",
        "summary_style": normalize_summary_style(summary_style),
        "summary_text": text,
    }


@router.post(
    "/documents/{document_id}/summary/request/academic",
    response_model=QARequestResponse,
)
def request_summary_academic(
    document_id: int,
    payload: SummaryRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _request_summary_by_style(
        document_id=document_id,
        level=payload.level,
        summary_style="academic",
        db=db,
        current_user=current_user,
    )


@router.post(
    "/documents/{document_id}/summary/request/semantic",
    response_model=QARequestResponse,
)
def request_summary_semantic(
    document_id: int,
    payload: SummaryRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _request_summary_by_style(
        document_id=document_id,
        level=payload.level,
        summary_style="semantic",
        db=db,
        current_user=current_user,
    )


@router.post(
    "/documents/{document_id}/summary/request/executive",
    response_model=QARequestResponse,
)
def request_summary_executive(
    document_id: int,
    payload: SummaryRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _request_summary_by_style(
        document_id=document_id,
        level=payload.level,
        summary_style="executive",
        db=db,
        current_user=current_user,
    )


@router.get("/documents/{document_id}/summary", response_model=SummaryResponse)
def get_summary(
    document_id: int,
    summary_style: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    document = get_owned_document(db, document_id, current_user.id)
    normalized_style = normalize_summary_style(summary_style) if summary_style else None
    summary = get_latest_summary(db, document.id, normalized_style)
    if summary:
        return {
            "id": summary.id,
            "document_id": document.id,
            "summary_type": summary.summary_style,
            "summary": summary.summary,
            "created_at": summary.created_at,
        }
    logger.info(
        "summary_not_found_in_table document_id=%s summary_style=%s query_result=%s",
        document.id,
        normalized_style,
        None,
    )
    latest_summary_job = (
        db.query(DocumentJob)
        .filter(
            DocumentJob.document_id == document.id,
            DocumentJob.job_type == "summary",
            DocumentJob.status == "done",
        )
        .order_by(DocumentJob.completed_at.desc(), DocumentJob.id.desc())
        .first()
    )
    if latest_summary_job and isinstance(latest_summary_job.result, dict):
        if normalized_style:
            result_style = normalize_summary_style(str(latest_summary_job.result.get("summary_style") or "academic"))
            if result_style != normalized_style:
                latest_summary_job = (
                    db.query(DocumentJob)
                    .filter(
                        DocumentJob.document_id == document.id,
                        DocumentJob.job_type == "summary",
                        DocumentJob.status == "done",
                    )
                    .order_by(DocumentJob.completed_at.desc(), DocumentJob.id.desc())
                    .all()
                )
                latest_job = next(
                    (
                        j
                        for j in latest_summary_job
                        if isinstance(j.result, dict)
                        and normalize_summary_style(str(j.result.get("summary_style") or "academic")) == normalized_style
                    ),
                    None,
                )
                if latest_job is None:
                    raise HTTPException(status_code=404, detail="Summary not found")
                latest_summary_job = latest_job
        text = str(
            latest_summary_job.result.get("summary_text")
            or latest_summary_job.result.get("summary_preview")
            or ""
        ).strip()
        if text:
            return {
                "id": None,
                "document_id": document.id,
                "summary_type": normalized_style or normalize_summary_style(str(latest_summary_job.result.get("summary_style") or "academic")),
                "summary": text,
                "created_at": latest_summary_job.completed_at,
            }
    logger.info(
        "summary_not_found document_id=%s summary_style=%s job_result=%s",
        document.id,
        normalized_style,
        latest_summary_job.result if latest_summary_job and isinstance(latest_summary_job.result, dict) else None,
    )
    raise HTTPException(status_code=404, detail="Summary not found")


@router.post("/documents/{document_id}/qa/request", response_model=QARequestResponse)
def request_qa_answer(
    document_id: int,
    payload: QARequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    document = get_owned_document(db, document_id, current_user.id)
    job = create_ai_job(
        db=db,
        document=document,
        user_id=current_user.id,
        job_type="qa",
        payload={"question": payload.question},
    )

    doc_json = _load_document_artifact_json(db, document.id)
    if not doc_json:
        job.status = "failed"
        job.error_message = "No ingestion artifact found. Upload/processing must complete first."
        job.completed_at = datetime.utcnow()
        db.commit()
        return {
            "document_id": document.id,
            "question": payload.question,
            "status": "failed",
            "job_id": job.id,
            "message": "No artifact found for QA generation",
        }

    job.status = "running"
    job.last_started_at = datetime.utcnow()
    db.commit()
    background_tasks.add_task(
        _run_qa_job_async,
        job.id,
        document.id,
        current_user.id,
        payload.question,
    )

    return {
        "document_id": document.id,
        "question": payload.question,
        "status": "processing",
        "job_id": job.id,
        "message": "QA job accepted",
    }


def _run_qa_job_async(job_id: int, document_id: int, user_id: int, question: str) -> None:
    db = SessionLocal()
    try:
        document = (
            db.query(Document)
            .filter(
                Document.id == document_id,
                Document.user_id == user_id,
                Document.is_deleted == False,
            )
            .first()
        )
        job = db.query(DocumentJob).filter(DocumentJob.id == job_id).first()
        if not document or not job:
            return

        doc_json = _load_document_artifact_json(db, document.id)
        if not doc_json:
            job.status = "failed"
            job.error_message = "No ingestion artifact found. Upload/processing must complete first."
            job.completed_at = datetime.utcnow()
            db.commit()
            return

        answer, sources = _generate_qa_with_ai_module(doc_json, question, top_k=5)
        qa = QAHistory(
            user_id=user_id,
            document_id=document.id,
            question=question,
            answer=answer,
            sources=json.dumps(sources, ensure_ascii=False),
        )
        db.add(qa)
        db.flush()

        job.status = "done"
        job.result = {"qa_history_id": qa.id, "answer_preview": answer[:500], "sources": sources}
        job.error_message = None
        job.completed_at = datetime.utcnow()
        db.commit()
    except Exception as ex:
        db.rollback()
        try:
            job = db.query(DocumentJob).filter(DocumentJob.id == job_id).first()
            if job:
                job.status = "failed"
                job.error_message = str(ex)
                job.completed_at = datetime.utcnow()
                db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_ai_job_status(
    job_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    job = (
        db.query(DocumentJob)
        .join(Document, Document.id == DocumentJob.document_id)
        .filter(
            DocumentJob.id == job_id,
            Document.user_id == current_user.id,
            Document.is_deleted == False,
        )
        .first()
    )

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return serialize_job(job)


@router.post("/documents/{document_id}/search", response_model=SearchResponse)
def search_document(
    document_id: int,
    payload: SearchRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    document = get_owned_document(db, document_id, current_user.id)
    doc_json = _load_document_artifact_json(db, document.id)
    if not doc_json:
        return {"query": payload.query, "items": []}
    items = _semantic_search_in_doc_json(doc_json, payload.query, payload.limit)
    for item in items:
        item.document_id = document.id

    return {
        "query": payload.query,
        "items": items,
    }


@router.post(
    "/documents/{document_id}/search/request",
    response_model=SearchRequestResponse,
)
def request_document_search(
    document_id: int,
    payload: SearchRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    document = get_owned_document(db, document_id, current_user.id)
    job = create_ai_job(
        db=db,
        document=document,
        user_id=current_user.id,
        job_type="search",
        payload={
            "query": payload.query,
            "limit": payload.limit,
            "document_id": document.id,
        },
    )

    doc_json = _load_document_artifact_json(db, document.id)
    if not doc_json:
        job.status = "failed"
        job.error_message = "No ingestion artifact found. Upload/processing must complete first."
        job.completed_at = datetime.utcnow()
        db.commit()
        return {
            "query": payload.query,
            "status": "failed",
            "message": "No artifact found for search",
            "job_id": job.id,
            "job_type": job.job_type,
        }

    items = _semantic_search_in_doc_json(doc_json, payload.query, payload.limit)
    job.status = "done"
    job.result = {
        "query": payload.query,
        "items": [it.dict() for it in items],
    }
    job.error_message = None
    job.completed_at = datetime.utcnow()
    db.commit()

    return {
        "query": payload.query,
        "status": "done",
        "message": "Search completed",
        "job_id": job.id,
        "job_type": job.job_type,
    }


@router.post("/search", response_model=SearchResponse)
def search_my_documents(
    payload: SearchRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    docs = (
        db.query(Document)
        .filter(Document.user_id == current_user.id, Document.is_deleted == False)
        .order_by(Document.created_at.desc())
        .limit(30)
        .all()
    )
    all_items: list[SearchResult] = []
    for d in docs:
        doc_json = _load_document_artifact_json(db, d.id)
        if not doc_json:
            continue
        items = _semantic_search_in_doc_json(doc_json, payload.query, payload.limit)
        for it in items:
            it.document_id = d.id
            all_items.append(it)
    all_items.sort(key=lambda x: float(x.score or 0.0), reverse=True)

    return {
        "query": payload.query,
        "items": all_items[: payload.limit],
    }


@router.post("/search/request", response_model=SearchRequestResponse)
def request_search_my_documents(
    payload: SearchRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    job = create_global_ai_job(
        db=db,
        user_id=current_user.id,
        job_type="search",
        payload={
            "query": payload.query,
            "limit": payload.limit,
            "user_id": current_user.id,
        },
    )

    docs = (
        db.query(Document)
        .filter(Document.user_id == current_user.id, Document.is_deleted == False)
        .order_by(Document.created_at.desc())
        .limit(30)
        .all()
    )
    all_items: list[SearchResult] = []
    for d in docs:
        doc_json = _load_document_artifact_json(db, d.id)
        if not doc_json:
            continue
        items = _semantic_search_in_doc_json(doc_json, payload.query, payload.limit)
        for it in items:
            it.document_id = d.id
            all_items.append(it)
    all_items.sort(key=lambda x: float(x.score or 0.0), reverse=True)

    job.status = "done"
    job.result = {
        "query": payload.query,
        "items": [it.dict() for it in all_items[: payload.limit]],
    }
    job.error_message = None
    job.completed_at = datetime.utcnow()
    db.commit()

    return {
        "query": payload.query,
        "status": "done",
        "message": "Search completed",
        "job_id": job.id,
        "job_type": job.job_type,
    }


@router.get("/documents/{document_id}/graph", response_model=GraphResponse)
def get_knowledge_graph(
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    document = get_owned_document(db, document_id, current_user.id)

    graph = (
        db.query(DocumentGraph)
        .filter(DocumentGraph.document_id == document.id)
        .first()
    )

    if not graph:
        doc_json = _load_document_artifact_json(db, document.id)
        nodes: list[dict] = []
        edges: list[dict] = []
        if doc_json:
            try:
                nodes, edges = _build_kg_with_ai_module(doc_json)
            except Exception:
                nodes, edges = [], []
        if not nodes and not edges:
            try:
                nodes, edges = _build_kg_from_neo4j(document)
            except Exception:
                nodes, edges = [], []
        if nodes or edges:
            graph = DocumentGraph(document_id=document.id, nodes=nodes, edges=edges)
            db.add(graph)
            db.commit()
            db.refresh(graph)
        else:
            return {
                "document_id": document.id,
                "nodes": [],
                "edges": [],
                "updated_at": None,
            }

    return {
        "document_id": document.id,
        "nodes": graph.nodes or [],
        "edges": graph.edges or [],
        "updated_at": graph.updated_at,
    }


@router.get(
    "/documents/{document_id}/recommendations",
    response_model=RecommendationResponse,
)
def get_recommendations(
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    document = get_owned_document(db, document_id, current_user.id)
    doc_json = _load_document_artifact_json(db, document.id)

    try:
        recommendations = (
            db.query(DocumentRecommendation)
            .filter(DocumentRecommendation.document_id == document.id)
            .order_by(DocumentRecommendation.score.desc().nullslast())
            .all()
        )
    except ProgrammingError:
        db.rollback()
        recommendations = []

    if recommendations:
        return {
            "document_id": document.id,
            "items": [
                RecommendationItem(
                    id=item.id,
                    recommended_document_id=item.recommended_document_id,
                    recommendation_type=str(getattr(item, "recommendation_type", None) or "method"),
                    title=item.title,
                    reason=item.reason,
                    score=item.score,
                    source=item.source,
                    external_url=item.external_url,
                )
                for item in recommendations
            ],
        }

    items: list[RecommendationItem] = []
    try:
        items = _compute_neo4j_entity_recommendations(document=document, doc_json=doc_json, limit=10)
    except Exception:
        logger.exception("entity recommendations failed: document_id=%s", document.id)
        items = []

    if not items:
        try:
            items = _compute_neo4j_author_recommendations(document=document, doc_json=doc_json, limit=10)
        except Exception:
            logger.exception("author recommendations failed: document_id=%s", document.id)
            items = []

    if not items:
        try:
            items = _compute_neo4j_graph_recommendations(document=document, limit=10)
            for it in items:
                if not getattr(it, "recommendation_type", None):
                    it.recommendation_type = "method"
        except Exception:
            logger.exception("graph recommendations failed: document_id=%s", document.id)
            items = []

    if not items:
        items = _compute_metadata_recommendations(db, current_user.id, document, limit=10)
        for it in items:
            if not getattr(it, "recommendation_type", None):
                it.recommendation_type = "author"

    seen: dict[tuple[str, str], RecommendationItem] = {}
    for it in items:
        key = (
            str(it.title or "").strip().lower(),
            str(getattr(it, "recommendation_type", "method") or "method").strip().lower(),
        )
        if key not in seen or float(it.score or 0.0) > float(seen[key].score or 0.0):
            seen[key] = it
    items = sorted(seen.values(), key=lambda x: float(x.score or 0.0), reverse=True)

    try:
        db.query(DocumentRecommendation).filter(
            DocumentRecommendation.document_id == document.id
        ).delete()
        for it in items:
            db.add(
                DocumentRecommendation(
                    document_id=document.id,
                    recommended_document_id=it.recommended_document_id,
                    title=it.title,
                    reason=it.reason,
                    score=it.score,
                    source=it.source,
                    recommendation_type=str(getattr(it, "recommendation_type", None) or "method"),
                    external_url=it.external_url,
                )
            )
        db.commit()
    except Exception:
        db.rollback()

    return {"document_id": document.id, "items": items}


@router.get("/recommendations/debug")
def debug_recommendation_backend(current_user=Depends(get_current_user)):
    from storage.graph_db.neo4j_client import Neo4jClient, Neo4jConfig

    def _host(uri: str) -> str:
        return urlparse(uri).netloc if uri else ""

    rec_cfg = Neo4jConfig(
        uri=os.getenv("NEO4J_REC_URI", "").strip(),
        username=os.getenv("NEO4J_REC_USER", os.getenv("NEO4J_REC_USERNAME", "")).strip(),
        password=os.getenv("NEO4J_REC_PASSWORD", "").strip(),
        database=os.getenv("NEO4J_REC_DATABASE", "neo4j").strip(),
    )
    user_cfg = Neo4jConfig(
        uri=os.getenv("NEO4J_URI", "").strip(),
        username=os.getenv("NEO4J_USER", os.getenv("NEO4J_USERNAME", "")).strip(),
        password=os.getenv("NEO4J_PASSWORD", "").strip(),
        database=os.getenv("NEO4J_USER_DOC_DB", os.getenv("NEO4J_DATABASE", "neo4j")).strip(),
    )

    out: dict[str, Any] = {
        "current_user_id": current_user.id,
        "rec": {
            "uri_host": _host(rec_cfg.uri),
            "database": rec_cfg.database,
            "username": rec_cfg.username,
            "configured": bool(rec_cfg.uri and rec_cfg.username and rec_cfg.password),
            "paper_count": None,
            "error": None,
        },
        "user": {
            "uri_host": _host(user_cfg.uri),
            "database": user_cfg.database,
            "username": user_cfg.username,
            "configured": bool(user_cfg.uri and user_cfg.username and user_cfg.password),
            "paper_count": None,
            "error": None,
        },
    }

    for key, cfg in (("rec", rec_cfg), ("user", user_cfg)):
        if not (cfg.uri and cfg.username and cfg.password):
            out[key]["error"] = "missing_env"
            continue
        client = Neo4jClient(cfg)
        try:
            client.connect()
            rows = client.execute_read("MATCH (p:Paper) RETURN count(p) AS c")
            out[key]["paper_count"] = int(rows[0]["c"]) if rows else 0
        except Exception as ex:
            out[key]["error"] = str(ex)
        finally:
            try:
                client.close()
            except Exception:
                pass

    return out
