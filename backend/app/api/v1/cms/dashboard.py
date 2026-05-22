from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.document import Document
from app.schemas.document import DocumentDashboardResponse, DocumentResponse


router = APIRouter()


def serialize_document(document: Document) -> DocumentResponse:
    metadata = document.metadata_record

    return DocumentResponse(
        id=document.id,
        filename=document.filename,
        file_type=document.file_type,
        status=document.status,
        user_id=document.user_id,
        workspace_id=document.workspace_id,
        created_at=document.created_at,
        metadata=None if metadata is None else {
            "id": metadata.id,
            "document_id": metadata.document_id,
            "title": metadata.title,
            "abstract": metadata.abstract,
            "publication_year": metadata.publication_year,
            "language": metadata.language or "vi",
            "authors": metadata.authors or [],
            "keywords": metadata.keywords or [],
            "topics": metadata.topics or [],
            "methods": metadata.methods or [],
            "doi": metadata.doi,
            "created_at": metadata.created_at,
            "updated_at": metadata.updated_at,
        },
    )


def count_by_status(db: Session, user_id: int, status: str) -> int:
    return (
        db.query(Document)
        .filter(
            Document.user_id == user_id,
            Document.status == status,
            Document.is_deleted == False,
        )
        .count()
    )


@router.get("/overview", response_model=DocumentDashboardResponse)
def get_cms_dashboard_overview(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    base_query = db.query(Document).filter(
        Document.user_id == current_user.id,
        Document.is_deleted == False,
    )

    recent_documents = (
        base_query
        .order_by(Document.created_at.desc())
        .limit(5)
        .all()
    )

    return {
        "total_documents": base_query.count(),
        "uploaded": count_by_status(db, current_user.id, "uploaded"),
        "processing": count_by_status(db, current_user.id, "processing"),
        "processed": count_by_status(db, current_user.id, "processed"),
        "failed": count_by_status(db, current_user.id, "failed"),
        "recent_documents": [
            serialize_document(document)
            for document in recent_documents
        ],
    }
