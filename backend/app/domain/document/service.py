from sqlalchemy.orm import Session
from app.models.document import Document
from app.models.document_chunk import DocumentChunk


def create_document(db: Session, filename: str, file_type: str, user_id: int):
    doc = Document(
        filename=filename,
        file_type=file_type,
        user_id=user_id,
        status="uploaded"
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def add_chunks(db: Session, document_id: int, chunks: list):
    objects = [
        DocumentChunk(
            document_id=document_id,
            content=c["content"],
            chunk_index=c["chunk_index"],
            embedding=c.get("embedding"),
        )
        for c in chunks
    ]

    db.add_all(objects)
    db.commit()

    return len(objects)
