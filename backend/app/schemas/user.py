from pydantic import BaseModel, EmailStr, ConfigDict
from datetime import datetime
from typing import Optional


class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None


class UserCreate(UserBase):
    password: str


class UserResponse(UserBase):
    id: int
    role: str
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class UserProfileUpdate(BaseModel):
    full_name: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AdminUserUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class UserListResponse(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[UserResponse]


class AdminRecentUser(BaseModel):
    id: int
    email: EmailStr
    full_name: Optional[str] = None
    role: str
    is_active: bool
    created_at: datetime


class AdminRecentDocument(BaseModel):
    id: int
    filename: str
    file_type: str
    status: str
    user_id: int
    owner_email: str
    owner_name: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class AdminRecentJob(BaseModel):
    id: int
    document_id: Optional[int] = None
    job_type: str
    status: str
    retry_count: int
    created_at: datetime
    last_started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None


class AdminOverviewKpis(BaseModel):
    total_users: int
    active_users: int
    inactive_users: int
    total_documents: int
    uploaded: int
    processing: int
    processed: int
    failed: int
    total_qa: int
    total_summaries: int
    total_jobs: int
    jobs_pending: int
    jobs_running: int
    jobs_done: int
    jobs_failed: int


class AdminBucketCount(BaseModel):
    value: str
    count: int


class AdminOverviewResponse(BaseModel):
    kpis: AdminOverviewKpis
    recent_users: list[AdminRecentUser]
    recent_documents: list[AdminRecentDocument]
    recent_jobs: list[AdminRecentJob]
    documents_by_type: list[AdminBucketCount]
