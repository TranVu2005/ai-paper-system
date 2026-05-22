from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.document import Document
from app.models.document_metadata import DocumentMetadata


router = APIRouter()

VALID_GROUPS = {"year", "author", "topic"}


def base_document_query(db: Session, current_user):
    query = db.query(Document).filter(Document.is_deleted == False)
    if current_user.role != "admin":
        query = query.filter(Document.user_id == current_user.id)
    return query


def count_metadata_list(rows, field: str) -> list[dict]:
    counter: Counter[str] = Counter()

    for row in rows:
        values = getattr(row, field) or []
        if isinstance(values, str):
            values = [values]
        for value in values:
            if value:
                counter[str(value)] += 1

    return [
        {"value": value, "count": count}
        for value, count in counter.most_common()
    ]


@router.get("/overview")
def get_analytics_overview(
    group_by: str = "year",
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if group_by not in VALID_GROUPS:
        raise HTTPException(
            status_code=400,
            detail="group_by must be one of: year, author, topic",
        )

    document_query = base_document_query(db, current_user)
    document_ids = [row.id for row in document_query.with_entities(Document.id).all()]

    metadata_query = db.query(DocumentMetadata)
    if document_ids:
        metadata_query = metadata_query.filter(DocumentMetadata.document_id.in_(document_ids))
    else:
        metadata_query = metadata_query.filter(False)

    metadata_rows = metadata_query.all()

    status_counts = {
        "uploaded": document_query.filter(Document.status == "uploaded").count(),
        "processing": document_query.filter(Document.status == "processing").count(),
        "processed": document_query.filter(Document.status == "processed").count(),
        "failed": document_query.filter(Document.status == "failed").count(),
    }

    years = Counter(
        row.publication_year
        for row in metadata_rows
        if row.publication_year is not None
    )
    by_year = [
        {"value": year, "count": count}
        for year, count in sorted(years.items(), reverse=True)
    ]

    grouped = {
        "year": by_year,
        "author": count_metadata_list(metadata_rows, "authors"),
        "topic": count_metadata_list(metadata_rows, "topics"),
    }

    return {
        "scope": "all" if current_user.role == "admin" else "mine",
        "total_documents": document_query.count(),
        "status": status_counts,
        "group_by": group_by,
        "groups": grouped[group_by],
        "by_year": by_year,
        "top_authors": grouped["author"][:10],
        "top_topics": grouped["topic"][:10],
    }
