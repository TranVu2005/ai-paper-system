import time
from jose import jwt, JWTError
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
class AuthLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        # DEFAULT
        user = "anonymous"
        role = "anonymous"
        scopes = []

        # READ TOKEN
        auth_header = request.headers.get("Authorization") #lấy token
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.replace("Bearer ", "")

            try:
                # kiểm tra token hợp lệ
                payload = jwt.decode(
                    token,
                    settings.SECRET_KEY,
                    algorithms=[settings.ALGORITHM]
                )
                # lấy thông tin user
                user = payload.get("sub", "unknown")
                role = payload.get("role", "unknown")
                scopes = payload.get("scopes", [])
            except JWTError:
                user = "invalid-token"

        # gọi API
        response = await call_next(request)

        process_time = int((time.time() - start_time) * 1000)

        print(
            f"[AUTH] user={user} role={role} scopes={scopes} | "
            f"{request.method} {request.url.path} | "
            f"{response.status_code} | {process_time}ms"
        )

        return response
