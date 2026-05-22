from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import json
from pathlib import Path
from typing import Any

from app.db.session import get_db
from app.models.document import Document

from app.api.internal_deps import verify_internal_token
from app.services.ai_summary_service import generate_summary_from_doc_json, normalize_summary_style

from app.models.document_summary import DocumentSummary
from pydantic import BaseModel, Field
from app.models.document_graph import DocumentGraph
from app.models.document_recommendation import DocumentRecommendation
from app.models.qa_history import QAHistory

from app.services.document_state import can_transition

from app.models.document_job import DocumentJob

from datetime import datetime, timedelta
from app.models.worker_status import WorkerStatus



router = APIRouter()
_RAG_SESSIONS: dict[str, list[dict[str, str]]] = {}

class SummaryPayload(BaseModel):
    job_id: int | None = None
    summary_style: str = "academic"
    summary: str | None = None

class QAResultPayload(BaseModel):
    job_id: int | None = None
    question: str
    answer: str
    sources: list[dict] | None = None

class GraphPayload(BaseModel):
    job_id: int | None = None
    nodes: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)

class RecommendationItemPayload(BaseModel):
    recommended_document_id: int | None = None
    title: str
    reason: str | None = None
    score: float | None = None
    source: str | None = None
    external_url: str | None = None

class RecommendationsPayload(BaseModel):
    job_id: int | None = None
    items: list[RecommendationItemPayload] = Field(default_factory=list)

class WorkerHeartbeat(BaseModel):
    worker_name: str
    current_job_id: int | None = None

class JobCompletePayload(BaseModel):
    result: dict | None = None

class JobFailPayload(BaseModel):
    error_message: str
    result: dict | None = None


class DemoSummaryPayload(BaseModel):
    doc_file: str | None = None
    doc_limit: int = 1
    summary_style: str = "academic"
    num_highlights: int = 20
    chunk_highlights: int = 4
    max_new_tokens_summary: int = 1200
    max_input_chars_higen: int = 200000


class DemoRagQAPayload(BaseModel):
    question: str
    doc_file: str | None = None
    doc_limit: int = 1
    rag_mode: str = "fusion"
    use_graph: bool = True
    seed_paper_id: str | None = None
    top_k: int = 4
    retrieve_k: int = 20
    use_rerank: bool = False
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_device: str = "cpu"
    max_new_tokens_qa: int = 500
    session_id: str | None = None
    use_memory: bool = False
    memory_turns: int = 3
    memory_store_turns: int = 20
    memory_max_chars: int = 4000


class DemoSessionResetPayload(BaseModel):
    session_id: str


def _project_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "ingestion").exists() and (parent / "ai_module").exists():
            return parent
    # Fallback for container layout where app code lives under /app/app/*
    return current.parents[4]


def _processed_root() -> Path:
    return _project_root() / "data" / "processed"


def _resolve_doc_files(doc_file: str | None, doc_limit: int) -> list[Path]:
    root = _processed_root()
    if doc_file:
        matches = list(root.rglob(doc_file))
        if not matches:
            raise HTTPException(status_code=404, detail=f"Cannot find doc_file={doc_file} under {root}")
        return [matches[0]]
    files = sorted(root.rglob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise HTTPException(status_code=404, detail=f"No processed json found under {root}")
    limit = max(1, int(doc_limit or 1))
    return files[:limit]


def _load_docs_from_processed(doc_file: str | None, doc_limit: int) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for fp in _resolve_doc_files(doc_file=doc_file, doc_limit=doc_limit):
        raw = json.loads(fp.read_text(encoding="utf-8"))
        raw["file"] = fp.name
        docs.append(raw)
    return docs


def _memory_context(session_id: str, turns: int, max_chars: int) -> str:
    rows = _RAG_SESSIONS.get(session_id, [])
    if not rows:
        return ""
    blocks: list[str] = []
    for row in rows[-max(1, turns):]:
        blocks.append(f"Q: {row.get('q', '')}\nA: {row.get('a', '')}")
    return "\n\n".join(blocks)[-max(200, max_chars):]


def _append_memory(session_id: str, question: str, answer: str, store_turns: int) -> None:
    rows = _RAG_SESSIONS.get(session_id, [])
    rows.append({"q": question, "a": answer})
    _RAG_SESSIONS[session_id] = rows[-max(1, store_turns):]


def mark_job_done(db: Session, job_id: int | None, result: dict | None = None):
    if not job_id:
        return None

    job = db.query(DocumentJob).filter(DocumentJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job.status = "done"
    job.result = result or {}
    job.completed_at = datetime.utcnow()
    return job


def mark_job_failed(db: Session, job_id: int, error_message: str, result: dict | None = None):
    job = db.query(DocumentJob).filter(DocumentJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job.status = "failed"
    job.error_message = error_message
    job.result = result or {}
    job.completed_at = datetime.utcnow()

    document = (
        db.query(Document)
        .filter(Document.id == job.document_id, Document.is_deleted == False)
        .first()
    )
    if document:
        document.status = "failed"

    return job


@router.get("/documents/{document_id}")
def get_document_for_ai(
    document_id: int,
    db: Session = Depends(get_db),
     _=Depends(verify_internal_token),
):
    document = (
        db.query(Document)
        .filter(Document.id == document_id,
                Document.is_deleted == False
        )
        .first()
    )

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    return {
        "id": document.id,
        "filename": document.filename,
        "raw_text": document.raw_text,
        "status": document.status,
        "user_id": document.user_id,
    }
@router.post("/documents/{document_id}/claim")
def claim_document_for_processing(
    document_id: int,
    db: Session = Depends(get_db),
    _=Depends(verify_internal_token),
):
    document = (
        db.query(Document)
        .filter(Document.id == document_id,
                Document.is_deleted == False
        )
        .first()
    )

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # 🔥 LOCK LOGIC
    if not can_transition(document.status, "processing"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot claim document in status {document.status}"
        )


    document.status = "processing"
    db.commit()
    db.refresh(document)

    return {
        "message": "Document claimed",
        "status": document.status
    }

@router.post("/documents/{document_id}/summary")
def save_summary(
    document_id: int,
    payload: SummaryPayload,
    db: Session = Depends(get_db),
    _=Depends(verify_internal_token),
):
    document = (
        db.query(Document)
        .filter(Document.id == document_id,
                Document.is_deleted == False
                )
        .first()
    )

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    style = normalize_summary_style(payload.summary_style)
    summary_text = (payload.summary or "").strip()
    summary = DocumentSummary(
        document_id=document_id,
        summary_style=style,
        summary=summary_text or None,
    )

    db.add(summary)

    # update status -> processed
    document.status = "processed"
    mark_job_done(
        db,
        payload.job_id,
        {
            "summary": summary_text,
            "summary_style": style,
        },
    )

    db.commit()

    return {"message": "Summary saved"}


@router.post("/documents/{document_id}/qa")
def save_qa_answer(
    document_id: int,
    payload: QAResultPayload,
    db: Session = Depends(get_db),
    _=Depends(verify_internal_token),
):
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.is_deleted == False,
        )
        .first()
    )

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    qa = QAHistory(
        user_id=document.user_id,
        document_id=document.id,
        question=payload.question,
        answer=payload.answer,
        sources=json.dumps(payload.sources or []),
    )

    db.add(qa)
    mark_job_done(
        db,
        payload.job_id,
        {
            "qa_history_id": None,
            "question": payload.question,
            "answer": payload.answer,
            "sources": payload.sources or [],
        },
    )
    db.commit()
    db.refresh(qa)

    if payload.job_id:
        job = db.query(DocumentJob).filter(DocumentJob.id == payload.job_id).first()
        if job:
            job.result = {
                "qa_history_id": qa.id,
                "question": payload.question,
                "answer": payload.answer,
                "sources": payload.sources or [],
            }
            db.commit()

    return {"message": "Q&A answer saved", "qa_history_id": qa.id}


@router.post("/documents/{document_id}/graph")
def save_knowledge_graph(
    document_id: int,
    payload: GraphPayload,
    db: Session = Depends(get_db),
    _=Depends(verify_internal_token),
):
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.is_deleted == False,
        )
        .first()
    )

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    graph = (
        db.query(DocumentGraph)
        .filter(DocumentGraph.document_id == document.id)
        .first()
    )

    if not graph:
        graph = DocumentGraph(document_id=document.id)
        db.add(graph)

    graph.nodes = payload.nodes
    graph.edges = payload.edges
    mark_job_done(
        db,
        payload.job_id,
        {"nodes": payload.nodes, "edges": payload.edges},
    )

    db.commit()

    return {"message": "Knowledge graph saved"}


@router.post("/documents/{document_id}/recommendations")
def save_recommendations(
    document_id: int,
    payload: RecommendationsPayload,
    db: Session = Depends(get_db),
    _=Depends(verify_internal_token),
):
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.is_deleted == False,
        )
        .first()
    )

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    (
        db.query(DocumentRecommendation)
        .filter(DocumentRecommendation.document_id == document.id)
        .delete()
    )

    for item in payload.items:
        db.add(
            DocumentRecommendation(
                document_id=document.id,
                recommended_document_id=item.recommended_document_id,
                title=item.title,
                reason=item.reason,
                score=item.score,
                source=item.source,
                external_url=item.external_url,
            )
        )

    mark_job_done(
        db,
        payload.job_id,
        {"count": len(payload.items)},
    )

    db.commit()

    return {
        "message": "Recommendations saved",
        "count": len(payload.items),
    }

@router.post("/jobs/next")
def get_next_job(
    job_type: str | None = None,
    db: Session = Depends(get_db),
    _=Depends(verify_internal_token),
):
    query = db.query(DocumentJob).filter(DocumentJob.status == "pending")

    if job_type:
        query = query.filter(DocumentJob.job_type == job_type)

    job = query.order_by(DocumentJob.created_at.asc()).first()

    if not job:
        return {"message": "No jobs"}

    job.status = "running"
    job.last_started_at = datetime.utcnow()

    document = (
        db.query(Document)
        .filter(Document.id == job.document_id, Document.is_deleted == False)
        .first()
    )
    if document and document.status in ["uploaded", "failed"]:
        document.status = "processing"

    db.commit()
    db.refresh(job)

    return {
        "job_id": job.id,
        "document_id": job.document_id,
        "job_type": job.job_type,
        "payload": job.payload or {},
    }


@router.post("/jobs/{job_id}/complete")
def complete_job(
    job_id: int,
    payload: JobCompletePayload,
    db: Session = Depends(get_db),
    _=Depends(verify_internal_token),
):
    job = mark_job_done(db, job_id, payload.result)

    document = (
        db.query(Document)
        .filter(Document.id == job.document_id, Document.is_deleted == False)
        .first()
    )
    if document and job.job_type == "process_document":
        document.status = "processed"

    db.commit()

    return {
        "message": "Job completed",
        "job_id": job.id,
        "status": job.status,
    }


@router.post("/jobs/{job_id}/fail")
def fail_job(
    job_id: int,
    payload: JobFailPayload,
    db: Session = Depends(get_db),
    _=Depends(verify_internal_token),
):
    job = mark_job_failed(
        db=db,
        job_id=job_id,
        error_message=payload.error_message,
        result=payload.result,
    )
    db.commit()

    return {
        "message": "Job failed",
        "job_id": job.id,
        "status": job.status,
    }



@router.post("/jobs/recover")
def recover_stuck_jobs(
    db: Session = Depends(get_db),
    _=Depends(verify_internal_token),
):
    timeout_minutes = 10

    stuck_jobs = (
        db.query(DocumentJob)
        .filter(
            DocumentJob.status == "running",
            DocumentJob.last_started_at < datetime.utcnow() - timedelta(minutes=timeout_minutes),
            DocumentJob.retry_count < 3,
        )
        .all()
    )

    for job in stuck_jobs:
        job.status = "pending"
        job.retry_count += 1

    db.commit()

    return {
        "recovered_jobs": len(stuck_jobs)
    }

@router.post("/workers/heartbeat")
def worker_heartbeat(
    payload: WorkerHeartbeat,
    db: Session = Depends(get_db),
    _=Depends(verify_internal_token),
):
    worker = (
        db.query(WorkerStatus)
        .filter(WorkerStatus.worker_name == payload.worker_name)
        .first()
    )

    if not worker:
        worker = WorkerStatus(
            worker_name=payload.worker_name,
            current_job_id=payload.current_job_id,
        )
        db.add(worker)
    else:
        worker.current_job_id = payload.current_job_id
        worker.last_heartbeat = datetime.utcnow()
        worker.status = "online"

    db.commit()

    return {"message": "heartbeat received"}

@router.get("/workers")
def list_workers(
    db: Session = Depends(get_db),
    _=Depends(verify_internal_token),
):
    workers = db.query(WorkerStatus).all()

    return workers

@router.get("/system/dashboard")
def system_dashboard(
    db: Session = Depends(get_db),
    _=Depends(verify_internal_token),
):
    documents_total = db.query(Document).count()

    documents_processing = (
        db.query(Document)
        .filter(Document.status == "processing")
        .count()
    )

    documents_processed = (
        db.query(Document)
        .filter(Document.status == "processed")
        .count()
    )

    queue_pending = (
        db.query(DocumentJob)
        .filter(DocumentJob.status == "pending")
        .count()
    )

    workers_online = (
        db.query(WorkerStatus)
        .count()
    )

    return {
        "documents_total": documents_total,
        "documents_processing": documents_processing,
        "documents_processed": documents_processed,
        "queue_pending": queue_pending,
        "workers_online": workers_online,
    }


@router.post("/demo/summary")
def demo_summary(
    payload: DemoSummaryPayload,
    _=Depends(verify_internal_token),
):
    return _demo_summary_with_style(payload, payload.summary_style)


def _demo_summary_with_style(payload: DemoSummaryPayload, summary_style: str):
    from ai_module.inference.inference_config import InferenceConfig
    from ai_module.reasoning.prompt_builder import collection_summary_prompt

    docs = _load_docs_from_processed(payload.doc_file, payload.doc_limit)
    cfg = InferenceConfig(
        max_new_tokens_summary=payload.max_new_tokens_summary,
        max_input_chars_higen=payload.max_input_chars_higen,
        temperature=0.0,
    )

    per_doc = []
    style = normalize_summary_style(summary_style)
    for doc in docs:
        text = generate_summary_from_doc_json(
            doc_json=doc,
            summary_style=style,
            num_highlights=payload.num_highlights,
            chunk_highlights=payload.chunk_highlights,
        )
        result = {"file": str(doc.get("file") or ""), "result": {"summary": text, "highlights": ""}}
        per_doc.append(result)

    if len(per_doc) == 1:
        final_summary = per_doc[0].get("result", {}).get("summary", "")
    else:
        merged = "\n\n".join([x.get("result", {}).get("summary", "") for x in per_doc])
        from ai_module.inference.llm_engine import LLMEngine
        llm = LLMEngine(cfg)
        final_summary = llm.generate(
            collection_summary_prompt(merged),
            max_new_tokens=cfg.max_new_tokens_summary,
        )

    return {
        "docs": len(docs),
        "summary_style": style,
        "final_summary": final_summary,
        "items": per_doc,
    }


@router.post("/demo/summary/academic")
def demo_summary_academic(
    payload: DemoSummaryPayload,
    _=Depends(verify_internal_token),
):
    return _demo_summary_with_style(payload, "academic")


@router.post("/demo/summary/semantic")
def demo_summary_semantic(
    payload: DemoSummaryPayload,
    _=Depends(verify_internal_token),
):
    return _demo_summary_with_style(payload, "semantic")


@router.post("/demo/summary/executive")
def demo_summary_executive(
    payload: DemoSummaryPayload,
    _=Depends(verify_internal_token),
):
    return _demo_summary_with_style(payload, "executive")


@router.post("/demo/rag-qa")
def demo_rag_qa(
    payload: DemoRagQAPayload,
    _=Depends(verify_internal_token),
):
    from ai_module.inference.inference_config import InferenceConfig
    from ai_module.inference.llm_engine import LLMEngine
    from ai_module.reasoning.context_builder import compact_sources, format_retrieved_context
    from ai_module.reasoning.prompt_builder import rag_prompt
    from ai_module.retrieval.vector_retriever import VectorRetriever
    from ai_module.retrieval.graph_retriever import GraphRetriever

    docs = _load_docs_from_processed(payload.doc_file, payload.doc_limit)
    cfg = InferenceConfig(
        top_k=max(1, payload.top_k),
        max_new_tokens_qa=payload.max_new_tokens_qa,
        temperature=0.0,
    )

    retriever = VectorRetriever(cfg)
    retriever.build(docs)
    vector_hits = retriever.retrieve(payload.question, top_k=max(payload.top_k, payload.retrieve_k))
    selected_vector_hits = vector_hits[: payload.top_k]

    graph_hits: list[dict[str, Any]] = []
    graph_error: str | None = None
    if payload.use_graph:
        try:
            graph_bundle = {"nodes": [], "edges": []}
            for hit in selected_vector_hits:
                text = str(hit.get("text") or "")
                if text:
                    graph_bundle["nodes"].append({"label": text[:120]})
            g = GraphRetriever(graph_bundle=graph_bundle)
            raw_graph_hits = g.retrieve(payload.question, top_k=payload.top_k)
            graph_hits = [{"text": h.get("node", {}).get("label", ""), "score": h.get("score", 0.0)} for h in raw_graph_hits]
        except Exception as ex:
            graph_error = str(ex)

    rag_mode = (payload.rag_mode or "fusion").lower()
    if rag_mode == "vector":
        final_hits = selected_vector_hits
    elif rag_mode == "graph":
        final_hits = graph_hits
    else:
        final_hits = selected_vector_hits + graph_hits
        final_hits = final_hits[: payload.top_k]

    memory_used = bool(payload.use_memory and payload.session_id)
    memory_text = ""
    if memory_used:
        memory_text = _memory_context(
            session_id=payload.session_id or "",
            turns=payload.memory_turns,
            max_chars=payload.memory_max_chars,
        )

    context = format_retrieved_context(selected_vector_hits)
    if memory_text:
        context = f"[Conversation memory]\n{memory_text}\n\n{context}"
    llm = LLMEngine(cfg)
    answer = llm.generate(
        rag_prompt(question=payload.question, context=context),
        max_new_tokens=cfg.max_new_tokens_qa,
    ).strip()
    if not answer:
        answer = "Khong du du lieu de tra loi."

    if memory_used:
        _append_memory(
            session_id=payload.session_id or "",
            question=payload.question,
            answer=answer,
            store_turns=payload.memory_store_turns,
        )

    return {
        "backend_used": cfg.llm_backend,
        "rag_mode": rag_mode,
        "graph_error": graph_error,
        "session_id": payload.session_id,
        "memory_used": memory_used,
        "vector_hits": compact_sources(selected_vector_hits),
        "graph_hits": graph_hits,
        "hits": compact_sources(selected_vector_hits) if rag_mode != "graph" else graph_hits,
        "answer": answer,
    }


@router.post("/demo/rag-qa/session/reset")
def demo_rag_qa_reset(
    payload: DemoSessionResetPayload,
    _=Depends(verify_internal_token),
):
    existed = payload.session_id in _RAG_SESSIONS
    _RAG_SESSIONS.pop(payload.session_id, None)
    return {"ok": True, "session_id": payload.session_id, "cleared": existed}
