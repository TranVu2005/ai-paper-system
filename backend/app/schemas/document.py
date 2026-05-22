from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentStatusUpdate(BaseModel):
    status: str


class DocumentMetadataBase(BaseModel):
    title: str | None = None
    abstract: str | None = None
    publication_year: int | None = Field(default=None, ge=1000, le=3000)
    language: str = "vi"
    authors: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    doi: str | None = None


class DocumentMetadataUpdate(DocumentMetadataBase):
    pass


class DocumentMetadataResponse(DocumentMetadataBase):
    id: int
    document_id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class DocumentResponse(BaseModel):
    id: int
    filename: str
    file_type: str
    status: str
    user_id: int
    workspace_id: int | None = None
    created_at: datetime | None = None
    metadata: DocumentMetadataResponse | None = None


class DocumentListResponse(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[DocumentResponse]


class DocumentUploadResponse(BaseModel):
    message: str
    document_id: int
    item: DocumentResponse


class DocumentDashboardResponse(BaseModel):
    total_documents: int
    uploaded: int
    processing: int
    processed: int
    failed: int
    recent_documents: list[DocumentResponse]
