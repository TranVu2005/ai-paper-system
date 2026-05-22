import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import httpx

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from fastapi.security import OAuth2PasswordRequestForm
from jose import jwt, JWTError
from starlette.requests import Request
from fastapi.responses import RedirectResponse

try:
    from authlib.integrations.starlette_client import OAuth
except ImportError:  # pragma: no cover
    OAuth = None

from app.db.session import get_db
from app.schemas.user import UserCreate, UserResponse
from app.schemas.auth import (
    LoginRequest,
    TokenResponse,
    RefreshTokenRequest,
    GoogleLoginRequest,
    ForgotPasswordSendCodeRequest,
    ForgotPasswordVerifyCodeRequest,
    ForgotPasswordResetRequest,
)
from app.crud.user import create_user, get_user_by_email, authenticate_user
from app.crud.refresh_token import (
    create_refresh_token as save_refresh_token,
    revoke_refresh_token,
    is_refresh_token_revoked,
    rotate_refresh_token
)
from app.core.security import create_access_token, create_refresh_token, get_password_hash
from app.core.config import settings
from app.services.mail_service import generate_code, send_reset_code
from app.models.password_reset_code import PasswordResetCode


router = APIRouter()

OTP_RESEND_SECONDS = 60
OTP_MAX_PER_HOUR = 5


def _latest_active_otp_record(db: Session, email: str):
    return (
        db.query(PasswordResetCode)
        .filter(PasswordResetCode.email == email, PasswordResetCode.used_at.is_(None))
        .order_by(PasswordResetCode.created_at.desc())
        .first()
    )


@router.get("/google/config")
def get_google_auth_config():
    enabled = bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET and OAuth)
    return {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "enabled": enabled,
        "redirect_enabled": enabled,
        "provider": "google-oauth",
    }


def get_scopes_for_role(role: str) -> list[str]:
    return (
        ["admin:read", "admin:write", "user:read", "user:write"]
        if role == "admin"
        else ["user:read", "user:write"]
    )


def build_token_response(db: Session, user, device_id: str | None = None):
    scopes = get_scopes_for_role(user.role)

    access_token = create_access_token({
        "sub": user.email,
        "role": user.role,
        "scopes": scopes
    })

    refresh_token = create_refresh_token({
        "sub": user.email,
        "type": "refresh"
    })

    save_refresh_token(
        db=db,
        token=refresh_token,
        user_email=user.email,
        device_id=device_id or "web",
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": user,
    }


@router.post("/forgot-password/send-code")
@router.post("/forgot-password", include_in_schema=False)
async def send_forgot_password_code(payload: ForgotPasswordSendCodeRequest, db: Session = Depends(get_db)):
    try:
        email = str(payload.email).lower().strip()
        user = get_user_by_email(db, email)

        # Return success regardless to avoid account enumeration.
        if not user:
            return {"message": "Nếu email tồn tại, mã xác nhận đã được gửi."}

        code = generate_code()
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        recent_count = (
            db.query(PasswordResetCode)
            .filter(
                PasswordResetCode.email == email,
                PasswordResetCode.created_at >= now - timedelta(hours=1),
            )
            .count()
        )
        if recent_count >= OTP_MAX_PER_HOUR:
            raise HTTPException(status_code=429, detail="Bạn đã gửi quá nhiều mã. Vui lòng thử lại sau.")

        latest = _latest_active_otp_record(db, email)
        if latest and latest.created_at and (now - latest.created_at).total_seconds() < OTP_RESEND_SECONDS:
            raise HTTPException(status_code=429, detail="Vui lòng chờ 60 giây trước khi gửi lại mã.")
        active_codes = db.query(PasswordResetCode).filter(
            PasswordResetCode.email == email,
            PasswordResetCode.used_at.is_(None),
        ).all()
        for item in active_codes:
            item.used_at = now

        db.add(
            PasswordResetCode(
                email=email,
                code_hash=code_hash,
                expires_at=now + timedelta(minutes=settings.OTP_EXPIRE_MINUTES),
            )
        )
        db.commit()
        await send_reset_code(email, code)
        return {"message": "Nếu email tồn tại, mã xác nhận đã được gửi."}
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Không gửi được mã xác nhận: {exc}",
        ) from exc


@router.post("/forgot-password/verify-code")
@router.post("/verify-reset-code", include_in_schema=False)
def verify_forgot_password_code(payload: ForgotPasswordVerifyCodeRequest, db: Session = Depends(get_db)):
    email = str(payload.email).lower().strip()
    user = get_user_by_email(db, email)
    if not user:
        raise HTTPException(status_code=400, detail="Mã xác nhận không hợp lệ")

    now = datetime.now(timezone.utc)
    record = _latest_active_otp_record(db, email)

    if not record or record.expires_at < now:
        raise HTTPException(status_code=400, detail="Mã xác nhận đã hết hạn hoặc không hợp lệ")

    code_hash = hashlib.sha256(payload.code.strip().encode("utf-8")).hexdigest()
    if code_hash != record.code_hash:
        record.attempts += 1
        if record.attempts >= 5:
            record.used_at = now
        db.commit()
        raise HTTPException(status_code=400, detail="Mã xác nhận đã hết hạn hoặc không hợp lệ")

    return {"message": "Xác nhận mã thành công"}


@router.post("/forgot-password/reset-password")
@router.post("/reset-password", include_in_schema=False)
def reset_password_with_code(payload: ForgotPasswordResetRequest, db: Session = Depends(get_db)):
    email = str(payload.email).lower().strip()
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu mới phải có ít nhất 6 ký tự")

    user = get_user_by_email(db, email)
    if not user:
        raise HTTPException(status_code=400, detail="Yêu cầu không hợp lệ")

    now = datetime.now(timezone.utc)
    record = _latest_active_otp_record(db, email)
    if not record or record.expires_at < now:
        raise HTTPException(status_code=400, detail="Mã xác nhận đã hết hạn hoặc không hợp lệ")

    code_hash = hashlib.sha256(payload.code.strip().encode("utf-8")).hexdigest()
    if code_hash != record.code_hash:
        record.attempts += 1
        if record.attempts >= 5:
            record.used_at = now
        db.commit()
        raise HTTPException(status_code=400, detail="Mã xác nhận đã hết hạn hoặc không hợp lệ")

    user.hashed_password = get_password_hash(payload.new_password)
    record.used_at = now
    db.add(user)
    db.add(record)
    db.commit()
    return {"message": "Đổi mật khẩu thành công"}


def get_google_oauth_client():
    if not OAuth:
        raise HTTPException(status_code=503, detail="Authlib is not installed on the backend")
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="Google login is not configured")

    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth.google


def get_google_redirect_uri() -> str:
    return settings.GOOGLE_OAUTH_REDIRECT_URI or "https://triumphant-charisma-production-f2a9.up.railway.app/api/v1/auth/google/callback"


def build_frontend_callback_url(fragment_params: dict[str, str]) -> str:
    fragment = urlencode(fragment_params)
    return f"{settings.FRONTEND_APP_URL.rstrip('/')}/auth/google/callback#{fragment}"

def resolve_frontend_origin(frontend_origin: str | None) -> str:
    allowed_prefixes = (
        "https://ai-paper-system.vercel.app",
    )
    candidate = (frontend_origin or "").strip()
    if any(candidate.startswith(prefix) for prefix in allowed_prefixes):
        return candidate.rstrip("/")
    return settings.FRONTEND_APP_URL.rstrip("/")


def get_or_create_google_user(db: Session, userinfo: dict):
    email = str(userinfo.get("email", "")).lower().strip()
    if not email:
        raise HTTPException(status_code=401, detail="Google account information is incomplete")

    user = get_user_by_email(db, email)
    if user:
        return user

    return create_user(
        db,
        UserCreate(
            email=email,
            full_name=userinfo.get("name"),
            password=secrets.token_urlsafe(32),
        ),
    )

# =========================
# REGISTER
# =========================
@router.post("/register", response_model=UserResponse)
def register(user_in: UserCreate, db: Session = Depends(get_db)):
    # nếu email đã tồn tại thì sẽ báo lỗi còn ko sẽ gọi đến create_user ở crud/user.py
    if get_user_by_email(db, str(user_in.email)):
        raise HTTPException(status_code=400, detail="Email already registered")
    return create_user(db, user_in)

# =========================
# LOGIN
# =========================
@router.post("/login", response_model=TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    # so sánh xem username và password đã có trong database chưa 
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is inactive")

    return build_token_response(db=db, user=user, device_id="oauth2-form")


@router.post("/login/email", response_model=TokenResponse)
def login_with_email(
    payload: LoginRequest,
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, str(payload.email), payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is inactive")

    return build_token_response(
        db=db,
        user=user,
        device_id=payload.device_id,
    )


@router.post("/login/google", response_model=TokenResponse)
async def login_with_google(
    payload: GoogleLoginRequest,
    db: Session = Depends(get_db),
):
    id_token = payload.id_token.strip()
    if not id_token:
        raise HTTPException(status_code=400, detail="Google id_token is required")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": id_token},
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Google verification failed: {exc}") from exc

    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Google token is invalid")

    token_info = resp.json()
    aud = str(token_info.get("aud", "")).strip()
    email = str(token_info.get("email", "")).lower().strip()
    email_verified = str(token_info.get("email_verified", "")).lower() == "true"

    if not aud or aud != (settings.GOOGLE_CLIENT_ID or "").strip():
        raise HTTPException(status_code=401, detail="Google token audience mismatch")

    if not email:
        raise HTTPException(status_code=401, detail="Google account email is missing")

    if not email_verified:
        raise HTTPException(status_code=401, detail="Google email is not verified")

    user = get_user_by_email(db, email)
    if not user:
        user = create_user(
            db,
            UserCreate(
                email=email,
                full_name=token_info.get("name"),
                password=secrets.token_urlsafe(32),
            ),
        )

    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is inactive")

    return build_token_response(
        db=db,
        user=user,
        device_id=payload.device_id or "google-mobile",
    )


@router.get("/google/login")
async def start_google_login(
    request: Request,
    frontend_origin: str | None = Query(default=None),
):
    google_client = get_google_oauth_client()
    request.session.pop("oauth_retry_done", None)
    request.session["frontend_origin"] = resolve_frontend_origin(frontend_origin)
    return await google_client.authorize_redirect(request, get_google_redirect_uri())


@router.get("/google/start", include_in_schema=False)
async def start_google_login_legacy(request: Request):
    return await start_google_login(request)


@router.get("/google/callback")
async def google_login_callback(
    request: Request,
    db: Session = Depends(get_db),
    error: str | None = Query(default=None),
):
    frontend_origin = resolve_frontend_origin(request.session.get("frontend_origin"))
    request.session.pop("frontend_origin", None)

    def callback_url(params: dict[str, str]) -> str:
        fragment = urlencode(params)
        return f"{frontend_origin}/auth/google/callback#{fragment}"

    if error:
        return RedirectResponse(
            url=callback_url({"error": "google_access_denied"}),
            status_code=302,
        )

    try:
        google_client = get_google_oauth_client()
        token = await google_client.authorize_access_token(request)
        userinfo = token.get("userinfo")
        if not userinfo:
            userinfo = await google_client.userinfo(token=token)
    except HTTPException as exc:
        request.session.pop("oauth_retry_done", None)
        return RedirectResponse(
            url=callback_url(
                {
                    "error": exc.detail.replace(" ", "_").lower(),
                    "error_description": str(exc.detail),
                }
            ),
            status_code=302,
        )
    except Exception as exc:
        error_text = str(exc)
        if "mismatching_state" in error_text.lower() and not request.session.get("oauth_retry_done"):
            request.session["oauth_retry_done"] = True
            retry_origin = resolve_frontend_origin(request.session.get("frontend_origin"))
            retry_query = urlencode({"frontend_origin": retry_origin})
            return RedirectResponse(
                url=f"/api/v1/auth/google/login?{retry_query}",
                status_code=302,
            )

        request.session.pop("oauth_retry_done", None)
        return RedirectResponse(
            url=callback_url(
                {
                    "error": "google_callback_invalid",
                    "error_description": error_text,
                }
            ),
            status_code=302,
        )

    email_verified = userinfo.get("email_verified")
    if email_verified is False or str(email_verified).lower() == "false":
        request.session.pop("oauth_retry_done", None)
        return RedirectResponse(
            url=callback_url({"error": "google_email_not_verified"}),
            status_code=302,
        )

    user = get_or_create_google_user(db, userinfo)
    if not user.is_active:
        request.session.pop("oauth_retry_done", None)
        return RedirectResponse(
            url=callback_url({"error": "user_inactive"}),
            status_code=302,
        )

    request.session.pop("oauth_retry_done", None)
    token_response = build_token_response(db=db, user=user, device_id="google-oauth-redirect")
    return RedirectResponse(
        url=callback_url(
            {
                "access_token": token_response["access_token"],
                "refresh_token": token_response["refresh_token"],
                "token_type": token_response["token_type"],
            }
        ),
        status_code=302,
    )

# =========================
# REFRESH TOKEN
# =========================
# khi access_token(15p) thì người dùng sử dụng referesh token(7 ngày) để server cấp access_token mới
@router.post("/refresh", response_model=TokenResponse)
def refresh(data: RefreshTokenRequest, db: Session = Depends(get_db)):
    try: 
        #kiểm tra token có hợp lệ không
        payload = jwt.decode(
            data.refresh_token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )

        # ktra xem có sử dụng refresh token không ( sử dụng access token thì không được)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")

        #lấy email người dùng
        email = payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="Invalid token")

        user = get_user_by_email(db, email)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid token")

        #  token cũ đã revoke
        if is_refresh_token_revoked(db, data.refresh_token):
            raise HTTPException(status_code=401, detail="Refresh token revoked")

        # tạo access token mới
        new_access = create_access_token({
            "sub": email,
            "role": user.role,
            "scopes": get_scopes_for_role(user.role)
        })
        
        # tạo refresh token mới
        new_refresh = create_refresh_token({
            "sub": email,
            "type": "refresh"
        })

        # thay đổi refresh token mới (khóa lại token cũ)
        success = rotate_refresh_token(
            db=db,
            old_token=data.refresh_token,
            new_token=new_refresh,
            user_email=email,
            device_id=data.device_id or "same-device"
        )

        if not success:
            raise HTTPException(status_code=401, detail="Invalid refresh token")

        return {
            "access_token": new_access,
            "refresh_token": new_refresh,
            "token_type": "bearer",
            "user": user,
        }

    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

# =========================
# LOGOUT
# =========================
@router.post("/logout")
def logout(data: RefreshTokenRequest, db: Session = Depends(get_db)):
    revoked = revoke_refresh_token(db, data.refresh_token)
    if not revoked:
        raise HTTPException(status_code=400, detail="Invalid refresh token")
    return {"message": "Logged out successfully"}
