from datetime import datetime

from pydantic import BaseModel, Field


class SummaryRequest(BaseModel):
    level: str = Field(default="medium", pattern="^(short|medium|long)$")
    summary_style: str = Field(default="academic", pattern="^(academic|semantic|executive)$")


class SummaryResponse(BaseModel):
    id: int | None = None
    document_id: int
    summary_type: str | None = None
    summary: str | None = None
    created_at: datetime | None = None


class QARequest(BaseModel):
    question: str


class QARequestResponse(BaseModel):
    document_id: int
    question: str
    status: str
    message: str
    job_id: int | None = None
    summary_style: str | None = None
    summary_text: str | None = None


class SearchRequest(BaseModel):
    query: str
    limit: int = Field(default=5, ge=1, le=20)


class SearchResult(BaseModel):
    chunk_id: int | None = None
    document_id: int
    chunk_index: int
    content: str
    score: float | None = None
    embedding_available: bool = False


class SearchResponse(BaseModel):
    query: str
    items: list[SearchResult]


class SearchRequestResponse(BaseModel):
    query: str
    status: str
    message: str
    job_id: int
    job_type: str = "search"


class JobRequestResponse(BaseModel):
    document_id: int
    job_id: int
    job_type: str
    status: str
    message: str


class JobStatusResponse(BaseModel):
    id: int
    document_id: int
    job_type: str
    status: str
    payload: dict | None = None
    result: dict | None = None
    error_message: str | None = None
    retry_count: int = 0
    created_at: datetime | None = None
    last_started_at: datetime | None = None
    completed_at: datetime | None = None


class GraphPayload(BaseModel):
    nodes: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)


class GraphResponse(GraphPayload):
    document_id: int
    updated_at: datetime | None = None


class RecommendationItem(BaseModel):
    id: int | None = None
    recommended_document_id: int | None = None
    recommendation_type: str = "method"
    title: str
    reason: str | None = None
    score: float | None = None
    source: str | None = None
    external_url: str | None = None


class RecommendationPayload(BaseModel):
    items: list[RecommendationItem] = Field(default_factory=list)


class RecommendationResponse(BaseModel):
    document_id: int
    items: list[RecommendationItem]
