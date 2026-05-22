import os
from pathlib import Path
from dotenv import load_dotenv

# ✅ Luôn load .env theo file này, KHÔNG theo cwd
BASE_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BASE_DIR / ".env"


load_dotenv(dotenv_path=ENV_PATH)


def _parse_csv_env(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings:
    APP_NAME: str = os.getenv("APP_NAME", "ai-paper-system")
    ENV: str = os.getenv("ENV", "development")

    SECRET_KEY: str | None = os.getenv("SECRET_KEY")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    INTERNAL_API_TOKEN: str | None = os.getenv("INTERNAL_API_TOKEN")
    GOOGLE_CLIENT_ID: str | None = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET: str | None = os.getenv("GOOGLE_CLIENT_SECRET")
    GOOGLE_OAUTH_REDIRECT_URI: str | None = os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
    FRONTEND_APP_URL: str = os.getenv("FRONTEND_APP_URL", "https://ai-paper-system.vercel.app")
    SMTP_HOST: str | None = os.getenv("SMTP_HOST")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str | None = os.getenv("SMTP_USER")
    SMTP_PASSWORD: str | None = os.getenv("SMTP_PASSWORD")
    SMTP_FROM_EMAIL: str = os.getenv("SMTP_FROM_EMAIL", "no-reply@ai-paper.local")
    SMTP_USE_TLS: bool = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
    OTP_EXPIRE_MINUTES: int = int(os.getenv("OTP_EXPIRE_MINUTES", "10"))
    MAIL_USERNAME: str | None = os.getenv("MAIL_USERNAME", os.getenv("SMTP_USER"))
    MAIL_PASSWORD: str | None = os.getenv("MAIL_PASSWORD", os.getenv("SMTP_PASSWORD"))
    MAIL_FROM: str = os.getenv("MAIL_FROM", os.getenv("SMTP_FROM_EMAIL", "no-reply@ai-paper.local"))
    MAIL_SERVER: str = os.getenv("MAIL_SERVER", os.getenv("SMTP_HOST", "smtp.gmail.com"))
    MAIL_PORT: int = int(os.getenv("MAIL_PORT", os.getenv("SMTP_PORT", "587")))
    MAIL_STARTTLS: bool = os.getenv("MAIL_STARTTLS", os.getenv("SMTP_USE_TLS", "true")).lower() == "true"
    MAIL_SSL_TLS: bool = os.getenv("MAIL_SSL_TLS", "false").lower() == "true"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
        os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60")
    )
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(
        os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7")
    )

    DATABASE_URL: str | None = os.getenv("DATABASE_URL")
    CORS_ALLOWED_ORIGINS: list[str] = _parse_csv_env(
        os.getenv(
            "CORS_ALLOWED_ORIGINS",
            "https://ai-paper-system.vercel.app,https://triumphant-charisma-production-f2a9.up.railway.app",
        )
    )

    def __init__(self):
        if not isinstance(self.SECRET_KEY, str):
            raise RuntimeError("❌ SECRET_KEY is missing or not a string")

settings = Settings()
