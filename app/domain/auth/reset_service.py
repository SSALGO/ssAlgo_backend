import datetime
import hashlib
import hmac
import secrets
from typing import Any

import bcrypt


RESET_TOKEN_TTL_MINUTES = 30
OTP_TTL_MINUTES = 10
MAX_OTP_ATTEMPTS = 5


def hash_token(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def validate_password(password: str):
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters long.")
    if password.isdigit() or password.isalpha():
        raise ValueError("Password must contain a mix of letters and non-letters.")


def create_reset_token():
    token = secrets.token_urlsafe(32)
    expiration = datetime.datetime.utcnow() + datetime.timedelta(minutes=RESET_TOKEN_TTL_MINUTES)
    return token, hash_token(token), expiration


def create_otp():
    otp = str(secrets.randbelow(900000) + 100000)
    expiration = datetime.datetime.utcnow() + datetime.timedelta(minutes=OTP_TTL_MINUTES)
    return otp, hash_token(otp), expiration


def verify_reset_token(user: dict | None, reset_token: str):
    if not user or not reset_token:
        return False
    stored_hash = user.get("reset_token_hash")
    expires_at = user.get("reset_token_expiration")
    if not stored_hash or user.get("reset_token_used"):
        return False
    if expires_at and expires_at < datetime.datetime.utcnow():
        return False
    return hmac.compare_digest(str(stored_hash), hash_token(reset_token))


def verify_otp_hash(user: dict | None, otp: str):
    if not user or not otp:
        return False, "Invalid OTP."
    if int(user.get("otp_attempts") or 0) >= MAX_OTP_ATTEMPTS:
        return False, "OTP attempt limit reached."
    expires_at = user.get("otp_expiration")
    if expires_at and expires_at < datetime.datetime.utcnow():
        return False, "OTP expired."
    if not hmac.compare_digest(str(user.get("otp_hash") or ""), hash_token(otp)):
        return False, "Invalid OTP."
    return True, ""


def hash_password(password: str):
    validate_password(password)
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
