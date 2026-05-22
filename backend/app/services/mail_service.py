import secrets

from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType

from app.core.config import settings


conf = ConnectionConfig(
    MAIL_USERNAME=settings.MAIL_USERNAME,
    MAIL_PASSWORD=settings.MAIL_PASSWORD,
    MAIL_FROM=settings.MAIL_FROM,
    MAIL_PORT=settings.MAIL_PORT,
    MAIL_SERVER=settings.MAIL_SERVER,
    MAIL_STARTTLS=settings.MAIL_STARTTLS,
    MAIL_SSL_TLS=settings.MAIL_SSL_TLS,
    USE_CREDENTIALS=True,
    VALIDATE_CERTS=True,
)


def generate_code() -> str:
    return f"{secrets.randbelow(1000000):06d}"


async def send_reset_code(email: str, code: str):
    if not settings.MAIL_USERNAME or not settings.MAIL_PASSWORD:
        raise RuntimeError("MAIL_USERNAME/MAIL_PASSWORD is not configured")

    message = MessageSchema(
        subject="[AI Paper System] Ma xac nhan dat lai mat khau",
        recipients=[email],
        body=(
            "<h3>Ma xac nhan cua ban la:</h3>"
            f"<h1 style='letter-spacing:8px;color:#5e7ce2'>{code}</h1>"
            f"<p>Ma co hieu luc trong <strong>{settings.OTP_EXPIRE_MINUTES} phut</strong>.</p>"
            "<p>Neu ban khong yeu cau, hay bo qua email nay.</p>"
        ),
        subtype=MessageType.html,
    )

    fm = FastMail(conf)
    await fm.send_message(message)
