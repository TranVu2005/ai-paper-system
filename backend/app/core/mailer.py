import smtplib
from email.message import EmailMessage

from app.core.config import settings


def send_otp_email(to_email: str, code: str) -> None:
    if not settings.SMTP_HOST:
        # Dev fallback: still show code in logs if SMTP is not set.
        print(f"[OTP] SMTP not configured. OTP for {to_email}: {code}")
        return

    message = EmailMessage()
    message["Subject"] = "Ma xac nhan 6 so"
    message["From"] = settings.SMTP_FROM_EMAIL
    message["To"] = to_email
    message.set_content(
        f"Ma xac nhan cua ban la: {code}\n"
        f"Ma het han sau {settings.OTP_EXPIRE_MINUTES} phut."
    )

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
            if settings.SMTP_USE_TLS:
                smtp.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError("SMTP auth failed. Use Gmail App Password, not your normal password.") from exc
    except smtplib.SMTPException as exc:
        raise RuntimeError(f"SMTP send failed: {type(exc).__name__}") from exc
