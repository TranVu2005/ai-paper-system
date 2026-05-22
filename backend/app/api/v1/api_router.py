from fastapi import APIRouter

# ===== Core endpoints =====
from app.api.v1.endpoints import auth, users, admin

# ===== CMS (user-facing features) =====
from app.api.v1.cms import ai_features, analytics, dashboard, documents, workspaces

# ===== Internal (AI worker, system) =====
from app.api.v1.internal import chunks
from app.api.v1.ai import internal as ai_internal

api_router = APIRouter()

# =========================
# AUTH & USER
# =========================
api_router.include_router(
    auth.router,
    prefix="/auth",
    tags=["auth"]
)

api_router.include_router(
    users.router,
    prefix="/users",
    tags=["users"]
)

api_router.include_router(
    admin.router,
    prefix="/admin",
    tags=["admin"]
)

# =========================
# CMS MODULE
# =========================
api_router.include_router(
    documents.router,
    prefix="/documents",
    tags=["documents"]
)

api_router.include_router(
    workspaces.router,
    prefix="/workspaces",
    tags=["workspaces"]
)

api_router.include_router(
    dashboard.router,
    prefix="/cms/dashboard",
    tags=["cms-dashboard"]
)

api_router.include_router(
    ai_features.router,
    prefix="/cms",
    tags=["cms-ai"]
)

api_router.include_router(
    analytics.router,
    prefix="/cms/analytics",
    tags=["cms-analytics"]
)

# =========================
# INTERNAL (AI PIPELINE)
# =========================
api_router.include_router(
    chunks.router,
    prefix="/internal/chunks",
    tags=["internal"]
)

api_router.include_router(
    ai_internal.router,
    prefix="/internal/ai",
    tags=["internal-ai"]
)

# Backward-compatible alias for old internal demo scripts:
# /api/v1/internal/demo/* (without /ai segment)
api_router.include_router(
    ai_internal.router,
    prefix="/internal",
    tags=["internal-ai-compat"],
    include_in_schema=False,
)
