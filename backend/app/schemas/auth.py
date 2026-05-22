from pydantic import BaseModel, EmailStr

from app.schemas.user import UserResponse


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    device_id: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse

class TokenPayload(BaseModel):
    sub: str | None = None
    
class RefreshTokenRequest(BaseModel):
    refresh_token: str
    device_id: str | None = None


class GoogleLoginRequest(BaseModel):
    id_token: str
    device_id: str | None = None


class ForgotPasswordSendCodeRequest(BaseModel):
    email: EmailStr


class ForgotPasswordVerifyCodeRequest(BaseModel):
    email: EmailStr
    code: str


class ForgotPasswordResetRequest(BaseModel):
    email: EmailStr
    code: str
    new_password: str
