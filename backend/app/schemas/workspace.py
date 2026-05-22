from datetime import datetime

from pydantic import BaseModel

from app.schemas.document import DocumentResponse


class WorkspaceCreate(BaseModel):
    title: str = "Untitled notebook"


class WorkspaceUpdate(BaseModel):
    title: str | None = None


class WorkspaceResponse(BaseModel):
    id: int
    title: str
    user_id: int
    document_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    documents: list[DocumentResponse] = []


class WorkspaceListResponse(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[WorkspaceResponse]
