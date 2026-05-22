from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.v1.cms.documents import serialize_document
from app.db.session import get_db
from app.models.document import Document
from app.models.workspace import Workspace
from app.schemas.workspace import (
    WorkspaceCreate,
    WorkspaceListResponse,
    WorkspaceResponse,
    WorkspaceUpdate,
)


router = APIRouter()


def get_owned_workspace(db: Session, workspace_id: int, user_id: int) -> Workspace:
    workspace = (
        db.query(Workspace)
        .filter(
            Workspace.id == workspace_id,
            Workspace.user_id == user_id,
            Workspace.is_deleted == False,
        )
        .first()
    )

    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    return workspace


def serialize_workspace(workspace: Workspace, documents: list[Document] | None = None):
    live_documents = [
        document
        for document in (documents if documents is not None else workspace.documents)
        if not document.is_deleted
    ]

    return WorkspaceResponse(
        id=workspace.id,
        title=workspace.title,
        user_id=workspace.user_id,
        document_count=len(live_documents),
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
        documents=[serialize_document(document) for document in live_documents],
    )


@router.post("/", response_model=WorkspaceResponse)
def create_workspace(
    payload: WorkspaceCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    workspace = Workspace(title=payload.title or "Untitled notebook", user_id=current_user.id)
    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    return serialize_workspace(workspace, [])


@router.get("/", response_model=WorkspaceListResponse)
def list_workspaces(
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if page < 1:
        page = 1
    page_size = min(max(page_size, 1), 50)
    offset = (page - 1) * page_size

    base_query = db.query(Workspace).filter(
        Workspace.user_id == current_user.id,
        Workspace.is_deleted == False,
    )
    items = (
        base_query
        .order_by(Workspace.updated_at.desc(), Workspace.created_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    return {
        "page": page,
        "page_size": page_size,
        "total": base_query.count(),
        "items": [serialize_workspace(item) for item in items],
    }


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
def get_workspace(
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    workspace = get_owned_workspace(db, workspace_id, current_user.id)
    documents = (
        db.query(Document)
        .filter(
            Document.workspace_id == workspace.id,
            Document.user_id == current_user.id,
            Document.is_deleted == False,
        )
        .order_by(Document.created_at.desc())
        .all()
    )
    return serialize_workspace(workspace, documents)


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
def update_workspace(
    workspace_id: int,
    payload: WorkspaceUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    workspace = get_owned_workspace(db, workspace_id, current_user.id)
    if payload.title is not None:
        workspace.title = payload.title
    db.commit()
    db.refresh(workspace)
    return serialize_workspace(workspace)


@router.delete("/{workspace_id}")
def delete_workspace(
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    workspace = get_owned_workspace(db, workspace_id, current_user.id)
    workspace.is_deleted = True
    for document in workspace.documents:
        document.is_deleted = True
    db.commit()
    return {"message": "Workspace deleted successfully"}
