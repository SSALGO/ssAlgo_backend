import smtplib
from email.message import EmailMessage

from app.core.config import AppConfig


def mail_is_configured():
    return bool(AppConfig.MAIL_USERNAME and AppConfig.MAIL_PASSWORD)


def send_security_email(to_email: str, subject: str, body: str):
    if not mail_is_configured():
        return False
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = AppConfig.MAIL_USERNAME
    message["To"] = to_email
    message.set_content(body)

    try:
        if AppConfig.MAIL_USE_SSL:
            with smtplib.SMTP_SSL(AppConfig.MAIL_SERVER, AppConfig.MAIL_PORT, timeout=10) as smtp:
                smtp.login(AppConfig.MAIL_USERNAME, AppConfig.MAIL_PASSWORD)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(AppConfig.MAIL_SERVER, AppConfig.MAIL_PORT, timeout=10) as smtp:
                smtp.starttls()
                smtp.login(AppConfig.MAIL_USERNAME, AppConfig.MAIL_PASSWORD)
                smtp.send_message(message)
        return True
    except Exception:
        return False
