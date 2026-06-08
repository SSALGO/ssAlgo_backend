import datetime
from contextlib import contextmanager
from uuid import uuid4

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from app.core.config import AppConfig
from app.core.database import get_database


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


@contextmanager
def jwt_app_context():
    yield


def create_compatible_access_token(username):
    now = datetime.datetime.utcnow()
    expires_delta = AppConfig.JWT_ACCESS_TOKEN_EXPIRES
    payload = {
        "fresh": False,
        "iat": now,
        "jti": str(uuid4()),
        "type": "access",
        "sub": username,
        "nbf": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, AppConfig.JWT_SECRET_KEY, algorithm="HS256")


def decode_compatible_access_token(access_token):
    return jwt.decode(access_token, AppConfig.JWT_SECRET_KEY, algorithms=["HS256"])


def verify_password(password, password_hash):
    if not password or not password_hash:
        return False
    return bcrypt.hashpw(password.encode("utf-8"), password_hash) == password_hash


def get_users_collection():
    return get_database()["users"]


def get_user_by_username_or_email(identifier):
    normalized = str(identifier or "").strip().lower()
    if not normalized:
        return None
    return get_users_collection().find_one({
        "$or": [{"username": normalized}, {"email": normalized}],
    })


def ensure_free_subscription(username, days=2):
    collection = get_database()["subscriptionperiod"]
    if collection.find_one({"user": username}):
        return
    today = datetime.datetime.now().date()
    end = today + datetime.timedelta(days=days)
    collection.insert_one({
        "user": username,
        "start": today.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
        "subtype": "free",
    })


async def get_current_user(request: Request, token: str = Depends(oauth2_scheme)):
    bearer_token = token
    if not bearer_token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            bearer_token = auth_header.split(" ", 1)[1].strip()
    if not bearer_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    try:
        claims = decode_compatible_access_token(bearer_token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token")
    username = claims.get("sub")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token")
    user = get_users_collection().find_one({"username": username})
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def require_admin(user=Depends(get_current_user)):
    if not user.get("admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
