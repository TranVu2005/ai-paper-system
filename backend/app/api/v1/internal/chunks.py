from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.internal_deps import verify_internal_token
from app.models.document import Document
from app.models.document_chunk import DocumentChunk

# 🔥 chuyển sang dùng DOMAIN (không dùng CRUD nữa)
from app.domain.document.service import add_chunks

router = APIRouter()


class ChunkPayload(BaseModel):
    chunk_index: int
    content: str
    embedding: list[float] | None = None


class ChunkEmbeddingPayload(BaseModel):
    embedding: list[float] = Field(min_length=1)


class ChunkEmbeddingItem(BaseModel):
    chunk_id: int | None = None
    chunk_index: int | None = None
    embedding: list[float] = Field(min_length=1)


class BulkEmbeddingsPayload(BaseModel):
    items: list[ChunkEmbeddingItem] = Field(default_factory=list)


@router.post("/")
def receive_chunks(
    document_id: int,
    chunks: list[ChunkPayload],
    db: Session = Depends(get_db),
    _=Depends(verify_internal_token),
):
    """
    AI worker gửi chunk về backend
    """
    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.is_deleted == False)
        .first()
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # 🔥 gọi domain layer
    count = add_chunks(
        db=db,
        document_id=document_id,
        chunks=[chunk.model_dump() for chunk in chunks],
    )

    return {
        "message": "Chunks received",
        "count": count
    }


@router.patch("/{chunk_id}/embedding")
def update_chunk_embedding(
    chunk_id: int,
    payload: ChunkEmbeddingPayload,
    db: Session = Depends(get_db),
    _=Depends(verify_internal_token),
):
    chunk = db.query(DocumentChunk).filter(DocumentChunk.id == chunk_id).first()
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")

    chunk.embedding = payload.embedding
    db.commit()

    return {"message": "Embedding updated", "chunk_id": chunk.id}


@router.post("/documents/{document_id}/embeddings")
def update_document_chunk_embeddings(
    document_id: int,
    payload: BulkEmbeddingsPayload,
    db: Session = Depends(get_db),
    _=Depends(verify_internal_token),
):
    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.is_deleted == False)
        .first()
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    updated = 0
    missing: list[dict] = []

    for item in payload.items:
        query = db.query(DocumentChunk).filter(DocumentChunk.document_id == document_id)
        if item.chunk_id is not None:
            query = query.filter(DocumentChunk.id == item.chunk_id)
        elif item.chunk_index is not None:
            query = query.filter(DocumentChunk.chunk_index == item.chunk_index)
        else:
            missing.append({"reason": "chunk_id_or_chunk_index_required"})
            continue

        chunk = query.first()
        if not chunk:
            missing.append({
                "chunk_id": item.chunk_id,
                "chunk_index": item.chunk_index,
                "reason": "not_found",
            })
            continue

        chunk.embedding = item.embedding
        updated += 1

    db.commit()

    return {
        "message": "Embeddings updated",
        "document_id": document_id,
        "updated": updated,
        "missing": missing,
    }
