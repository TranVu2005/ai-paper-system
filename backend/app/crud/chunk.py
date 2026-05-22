from sqlalchemy.orm import Session
from app.models.document_chunk import DocumentChunk


# =========================
# CREATE
# =========================
def create_chunk(
    db: Session,
    document_id: int,
    content: str,
    chunk_index: int,
    embedding: list[float] | None = None,
):
    chunk = DocumentChunk(
        document_id=document_id,
        content=content,
        chunk_index=chunk_index,
        embedding=embedding,
    )
    db.add(chunk)
    db.commit()
    db.refresh(chunk)
    return chunk


# =========================
# BULK INSERT (QUAN TRỌNG)
# =========================
def create_chunks_bulk(
    db: Session,
    document_id: int,
    chunks: list[dict],
):
    """
    chunks = [
        {"content": "...", "chunk_index": 0, "embedding": [...]},
        ...
    ]
    """
    objects = [
        DocumentChunk(
            document_id=document_id,
            content=c["content"],
            chunk_index=c["chunk_index"],
            embedding=c.get("embedding"),
        )
        for c in chunks
    ]

    db.bulk_save_objects(objects)
    db.commit()

    return len(objects)


# =========================
# READ
# =========================
def get_chunks_by_document(
    db: Session,
    document_id: int,
):
    return (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
        .all()
    )


# =========================
# DELETE (khi re-process)
# =========================
def delete_chunks_by_document(
    db: Session,
    document_id: int,
):
    return (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .delete()
    )


# =========================
# COUNT (analytics)
# =========================
def count_chunks(
    db: Session,
    document_id: int,
):
    return (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .count()
    )