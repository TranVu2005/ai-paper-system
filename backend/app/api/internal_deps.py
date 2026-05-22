from fastapi import Header, HTTPException
from app.core.config import settings


def verify_internal_token(
    x_internal_token: str = Header(None)
):
    if x_internal_token != settings.INTERNAL_API_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Invalid internal token"
        )
