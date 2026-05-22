from fastapi import APIRouter, Security, HTTPException
from app.api.deps import get_current_user
from app.models.user import User

from sqlalchemy.orm import Session
from fastapi import Depends

from app.db.session import get_db
from app.models.document import Document
from app.models.document_summary import DocumentSummary
from app.models.qa_history import QAHistory
from app.schemas.user import UserResponse, UserProfileUpdate, ChangePasswordRequest
from app.core.security import get_password_hash, verify_password

router = APIRouter()


@router.get(
    "/me",
    response_model=UserResponse,
    dependencies=[Security(get_current_user, scopes=["user:read"])]
)
def read_me(current_user: User = Security(get_current_user, scopes=["user:read"])):
    return current_user


@router.patch(
    "/me",
    response_model=UserResponse,
    dependencies=[Security(get_current_user, scopes=["user:write"])]
)
def update_me(
    payload: UserProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Security(get_current_user, scopes=["user:write"]),
):
    current_user.full_name = payload.full_name.strip() if payload.full_name is not None else current_user.full_name
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user


@router.post(
    "/me/change-password",
    dependencies=[Security(get_current_user, scopes=["user:write"])]
)
def change_my_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Security(get_current_user, scopes=["user:write"]),
):
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu mới phải có ít nhất 6 ký tự")

    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Mật khẩu hiện tại không đúng")

    current_user.hashed_password = get_password_hash(payload.new_password)
    db.add(current_user)
    db.commit()

    return {"detail": "Cập nhật mật khẩu thành công"}


@router.get("/me/dashboard")
def get_user_dashboard(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    # Total docs
    total_documents = (
        db.query(Document)
        .filter(Document.user_id == current_user.id)
        .count()
    )

    # Count by status
    processing = (
        db.query(Document)
        .filter(
            Document.user_id == current_user.id,
            Document.status == "processing"
        )
        .count()
    )

    processed = (
        db.query(Document)
        .filter(
            Document.user_id == current_user.id,
            Document.status == "processed"
        )
        .count()
    )

    uploaded = (
        db.query(Document)
        .filter(
            Document.user_id == current_user.id,
            Document.status == "uploaded"
        )
        .count()
    )

    # Recent docs
    recent_documents = (
        db.query(Document)
        .filter(Document.user_id == current_user.id)
        .order_by(Document.created_at.desc())
        .limit(5)
        .all()
    )

    total_qa = (
        db.query(QAHistory)
        .filter(QAHistory.user_id == current_user.id)
        .count()
    )

    total_summaries = (
        db.query(DocumentSummary)
        .join(Document, Document.id == DocumentSummary.document_id)
        .filter(Document.user_id == current_user.id)
        .count()
    )

    return {
        "total_documents": total_documents,
        "processing": processing,
        "processed": processed,
        "uploaded": uploaded,
        "total_qa": total_qa,
        "total_summaries": total_summaries,
        "recent_documents": recent_documents,
    }
