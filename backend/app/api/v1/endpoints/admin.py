from fastapi import APIRouter, Depends, HTTPException, Security
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.document import Document
from app.models.document_job import DocumentJob
from app.models.document_summary import DocumentSummary
from app.models.qa_history import QAHistory
from app.models.user import User
from app.schemas.user import (
    AdminOverviewResponse,
    AdminUserUpdate,
    UserListResponse,
    UserResponse,
)

router = APIRouter()

VALID_ROLES = {"user", "admin"}

@router.get(
    "/dashboard",
    dependencies=[Security(get_current_user, scopes=["admin:read"])]
)
def admin_dashboard(
    admin: User = Security(get_current_user, scopes=["admin:read"])
):
    return {
        "message": "Admin dashboard",
        "email": admin.email
    }


@router.get(
    "/overview",
    response_model=AdminOverviewResponse,
    dependencies=[Security(get_current_user, scopes=["admin:read"])],
)
def admin_overview(
    db: Session = Depends(get_db),
):
    users_query = db.query(User)
    documents_query = db.query(Document).filter(Document.is_deleted.is_(False))

    recent_users = (
        users_query
        .order_by(User.created_at.desc())
        .limit(5)
        .all()
    )
    recent_documents = (
        documents_query
        .order_by(Document.created_at.desc())
        .limit(5)
        .all()
    )
    recent_jobs = (
        db.query(DocumentJob)
        .order_by(DocumentJob.created_at.desc())
        .limit(8)
        .all()
    )
    documents_by_type_rows = (
        db.query(Document.file_type, Document.id)
        .filter(Document.is_deleted.is_(False))
        .all()
    )
    file_type_count: dict[str, int] = {}
    for file_type, _ in documents_by_type_rows:
        key = (file_type or "unknown").lower()
        file_type_count[key] = file_type_count.get(key, 0) + 1
    documents_by_type = [
        {"value": key, "count": value}
        for key, value in sorted(file_type_count.items(), key=lambda item: item[1], reverse=True)
    ]

    jobs_query = db.query(DocumentJob)

    return {
        "kpis": {
            "total_users": users_query.count(),
            "active_users": users_query.filter(User.is_active.is_(True)).count(),
            "inactive_users": users_query.filter(User.is_active.is_(False)).count(),
            "total_documents": documents_query.count(),
            "uploaded": documents_query.filter(Document.status == "uploaded").count(),
            "processing": documents_query.filter(Document.status == "processing").count(),
            "processed": documents_query.filter(Document.status == "processed").count(),
            "failed": documents_query.filter(Document.status == "failed").count(),
            "total_qa": db.query(QAHistory).count(),
            "total_summaries": db.query(DocumentSummary).count(),
            "total_jobs": jobs_query.count(),
            "jobs_pending": jobs_query.filter(DocumentJob.status == "pending").count(),
            "jobs_running": jobs_query.filter(DocumentJob.status == "running").count(),
            "jobs_done": jobs_query.filter(DocumentJob.status == "done").count(),
            "jobs_failed": jobs_query.filter(DocumentJob.status == "failed").count(),
        },
        "recent_users": recent_users,
        "recent_documents": [
            {
                "id": doc.id,
                "filename": doc.filename,
                "file_type": doc.file_type,
                "status": doc.status,
                "user_id": doc.user_id,
                "owner_email": doc.user.email if doc.user else "",
                "owner_name": doc.user.full_name if doc.user else None,
                "created_at": doc.created_at,
                "updated_at": getattr(doc, "updated_at", None),
            }
            for doc in recent_documents
        ],
        "recent_jobs": recent_jobs,
        "documents_by_type": documents_by_type,
    }


@router.get(
    "/users",
    response_model=UserListResponse,
    dependencies=[Security(get_current_user, scopes=["admin:read"])],
)
def list_users(
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)

    query = db.query(User)
    if q:
        pattern = f"%{q}%"
        query = query.filter(
            (User.email.ilike(pattern)) |
            (User.full_name.ilike(pattern))
        )
    if role:
        query = query.filter(User.role == role)
    if is_active is not None:
        query = query.filter(User.is_active == is_active)

    total = query.count()
    users = (
        query.order_by(User.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": users,
    }


@router.patch(
    "/users/{user_id}",
    response_model=UserResponse,
    dependencies=[Security(get_current_user, scopes=["admin:write"])],
)
def update_user_by_admin(
    user_id: int,
    payload: AdminUserUpdate,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    data = payload.model_dump(exclude_unset=True)
    if "role" in data and data["role"] not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")

    for field, value in data.items():
        setattr(user, field, value)

    db.commit()
    db.refresh(user)
    return user


@router.post(
    "/users/{user_id}/activate",
    response_model=UserResponse,
    dependencies=[Security(get_current_user, scopes=["admin:write"])],
)
def activate_user(
    user_id: int,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = True
    db.commit()
    db.refresh(user)
    return user


@router.post(
    "/users/{user_id}/deactivate",
    response_model=UserResponse,
    dependencies=[Security(get_current_user, scopes=["admin:write"])],
)
def deactivate_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Security(get_current_user, scopes=["admin:write"]),
):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Admin cannot deactivate own account")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = False
    db.commit()
    db.refresh(user)
    return user


@router.get(
    "/documents",
    dependencies=[Security(get_current_user, scopes=["admin:read"])],
)
def list_documents_admin(
    page: int = 1,
    page_size: int = 10,
    q: str | None = None,
    status: str | None = None,
    file_type: str | None = None,
    owner: str | None = None,
    db: Session = Depends(get_db),
):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)

    query = db.query(Document).filter(Document.is_deleted.is_(False))
    if q:
        pattern = f"%{q}%"
        query = query.filter(Document.filename.ilike(pattern))
    if status:
        query = query.filter(Document.status == status)
    if file_type:
        query = query.filter(Document.file_type.ilike(file_type))
    if owner:
        pattern = f"%{owner}%"
        query = query.join(User, User.id == Document.user_id).filter(
            (User.email.ilike(pattern)) | (User.full_name.ilike(pattern))
        )

    total = query.count()
    documents = (
        query
        .order_by(Document.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    doc_ids = [doc.id for doc in documents]
    summary_rows = (
        db.query(DocumentSummary.document_id, func.count(DocumentSummary.id))
        .filter(DocumentSummary.document_id.in_(doc_ids))
        .group_by(DocumentSummary.document_id)
        .all()
        if doc_ids else []
    )
    qa_rows = (
        db.query(QAHistory.document_id, func.count(QAHistory.id))
        .filter(QAHistory.document_id.in_(doc_ids))
        .group_by(QAHistory.document_id)
        .all()
        if doc_ids else []
    )
    summary_map = {doc_id: count for doc_id, count in summary_rows}
    qa_map = {doc_id: count for doc_id, count in qa_rows}

    items = []
    for doc in documents:
        items.append({
            "id": doc.id,
            "filename": doc.filename,
            "file_type": doc.file_type,
            "status": doc.status,
            "user_id": doc.user_id,
            "owner_email": doc.user.email if doc.user else "",
            "owner_name": doc.user.full_name if doc.user else None,
            "created_at": doc.created_at,
            "chunk_count": 0,
            "summary_count": int(summary_map.get(doc.id, 0)),
            "qa_count": int(qa_map.get(doc.id, 0)),
        })

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": items,
    }
