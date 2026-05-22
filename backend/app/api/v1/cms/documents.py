from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Depends, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.api.deps import get_current_user
from app.application.document.upload_document import handle_upload_document
from app.services.ingestion_runner import run_ingestion_for_document

from app.models.document import Document
from app.models.document_metadata import DocumentMetadata
from app.models.qa_history import QAHistory
from app.models.document_summary import DocumentSummary
from app.models.workspace import Workspace

from app.schemas.document import (
    DocumentListResponse,
    DocumentMetadataResponse,
    DocumentMetadataUpdate,
    DocumentResponse,
    DocumentStatusUpdate,
    DocumentUploadResponse,
)
from app.services.document_state import can_transition


router = APIRouter()

BACKEND_DIR = Path(__file__).resolve().parents[4]


# ======================================================
# SCHEMAS
# ======================================================

class QAPayload(BaseModel):
    question: str
    answer: str
    sources: str | None = None


def get_owned_document(db: Session, document_id: int, user_id: int) -> Document:
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.user_id == user_id,
            Document.is_deleted == False
        )
        .first()
    )

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    return document


def serialize_metadata(metadata: DocumentMetadata | None):
    if not metadata:
        return None

    return DocumentMetadataResponse(
        id=metadata.id,
        document_id=metadata.document_id,
        title=metadata.title,
        abstract=metadata.abstract,
        publication_year=metadata.publication_year,
        language=metadata.language or "vi",
        authors=metadata.authors or [],
        keywords=metadata.keywords or [],
        topics=metadata.topics or [],
        methods=metadata.methods or [],
        doi=metadata.doi,
        created_at=metadata.created_at,
        updated_at=metadata.updated_at,
    )


def serialize_document(document: Document) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        filename=document.filename,
        file_type=document.file_type,
        status=document.status,
        user_id=document.user_id,
        workspace_id=document.workspace_id,
        created_at=document.created_at,
        metadata=serialize_metadata(document.metadata_record),
    )


def resolve_uploaded_file(filename: str) -> Path | None:
    candidates = [
        BACKEND_DIR / "uploaded_files" / filename,
        Path.cwd() / "uploaded_files" / filename,
    ]

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    return None


# ======================================================
# UPLOAD DOCUMENT
# ======================================================

@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    workspace_id: int | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if workspace_id is not None:
        workspace = (
            db.query(Workspace)
            .filter(
                Workspace.id == workspace_id,
                Workspace.user_id == current_user.id,
                Workspace.is_deleted == False,
            )
            .first()
        )
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

    document = await handle_upload_document(
        db=db,
        file=file,
        user_id=current_user.id,
        workspace_id=workspace_id,
    )
    # Run ingestion in background immediately after upload.
    background_tasks.add_task(run_ingestion_for_document, document.id, True)

    return {
        "message": "Upload successful",
        "document_id": document.id,
        "item": serialize_document(document),
    }


# ======================================================
# LIST DOCUMENTS (PAGINATION)
# ======================================================

@router.get("/", response_model=DocumentListResponse)
def list_documents(
    page: int = 1,
    page_size: int = 10,
    workspace_id: int | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if page < 1:
        page = 1

    page_size = min(max(page_size, 1), 50)

    offset = (page - 1) * page_size

    base_query = db.query(Document).filter(
        Document.user_id == current_user.id,
        Document.is_deleted == False,
    )

    if workspace_id is not None:
        base_query = base_query.filter(Document.workspace_id == workspace_id)

    documents = (
        base_query
        .order_by(Document.created_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    total = base_query.count()

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [serialize_document(document) for document in documents],
    }


# ======================================================
# DOCUMENT DETAIL
# ======================================================

@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    document = get_owned_document(db, document_id, current_user.id)

    return serialize_document(document)


@router.get("/{document_id}/file")
def download_document_file(
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    document = get_owned_document(db, document_id, current_user.id)
    file_path = resolve_uploaded_file(document.filename)

    if not file_path:
        raise HTTPException(status_code=404, detail="Document file not found")

    return FileResponse(
        path=file_path,
        media_type=document.file_type,
        filename=document.filename,
    )


@router.patch("/{document_id}/metadata", response_model=DocumentMetadataResponse)
def update_document_metadata(
    document_id: int,
    payload: DocumentMetadataUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    document = get_owned_document(db, document_id, current_user.id)

    metadata = document.metadata_record
    if not metadata:
        metadata = DocumentMetadata(document_id=document.id)
        db.add(metadata)

    for field, value in payload.dict(exclude_unset=True).items():
        setattr(metadata, field, value)

    db.commit()
    db.refresh(metadata)

    return serialize_metadata(metadata)


@router.get("/{document_id}/metadata", response_model=DocumentMetadataResponse)
def get_document_metadata(
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    document = get_owned_document(db, document_id, current_user.id)

    if not document.metadata_record:
        raise HTTPException(status_code=404, detail="Document metadata not found")

    return serialize_metadata(document.metadata_record)


# ======================================================
# SOFT DELETE
# ======================================================

@router.delete("/{document_id}")
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    document = get_owned_document(db, document_id, current_user.id)

    document.is_deleted = True
    db.commit()

    return {"message": "Document deleted successfully"}


# ======================================================
# STATUS UPDATE (STATE MACHINE)
# ======================================================

@router.patch("/{document_id}/status")
def update_document_status(
    document_id: int,
    payload: DocumentStatusUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    document = get_owned_document(db, document_id, current_user.id)

    if not can_transition(document.status, payload.status):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status transition: {document.status} -> {payload.status}",
        )

    document.status = payload.status
    db.commit()

    return {"message": "Status updated", "status": document.status}


@router.get("/{document_id}/status")
def get_document_status(
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    document = get_owned_document(db, document_id, current_user.id)

    return {"document_id": document.id, "status": document.status}


# ======================================================
# QA HISTORY
# ======================================================

@router.post("/{document_id}/qa")
def save_qa_history(
    document_id: int,
    payload: QAPayload,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.user_id == current_user.id,
            Document.is_deleted == False
        )
        .first()
    )

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    qa = QAHistory(
        user_id=current_user.id,
        document_id=document_id,
        question=payload.question,
        answer=payload.answer,
        sources=payload.sources,
    )

    db.add(qa)
    db.commit()

    return {"message": "Q&A saved"}


@router.get("/{document_id}/qa")
def get_qa_history(
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return (
        db.query(QAHistory)
        .filter(
            QAHistory.document_id == document_id,
            QAHistory.user_id == current_user.id,
        )
        .order_by(QAHistory.created_at.desc())
        .all()
    )


# ======================================================
# TIMELINE
# ======================================================

@router.get("/{document_id}/timeline")
def get_document_timeline(
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.user_id == current_user.id,
            Document.is_deleted == False
        )
        .first()
    )

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    timeline = []

    timeline.append({
        "type": "uploaded",
        "timestamp": document.created_at,
        "detail": "Document uploaded"
    })

    summaries = (
        db.query(DocumentSummary)
        .filter(DocumentSummary.document_id == document_id)
        .all()
    )

    for s in summaries:
        timeline.append({
            "type": "summary_generated",
            "timestamp": s.created_at,
            "detail": "Summary generated"
        })

    qa_history = (
        db.query(QAHistory)
        .filter(
            QAHistory.document_id == document_id,
            QAHistory.user_id == current_user.id,
        )
        .all()
    )

    for qa in qa_history:
        timeline.append({
            "type": "qa",
            "timestamp": qa.created_at,
            "detail": qa.question
        })

    timeline.sort(key=lambda x: x["timestamp"])

    return {
        "document_id": document_id,
        "status": document.status,
        "timeline": timeline,
    }
