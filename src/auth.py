"""Password hashing and session token management."""
from __future__ import annotations

import os

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_SESSION_MAX_AGE = 8 * 3600  # 8 hours
_SECRET: str | None = None


def _get_secret() -> str:
    key = os.environ.get("SECRET_KEY", "").strip()
    if not key:
        import secrets as _sec
        key = _sec.token_hex(32)
        print(
            "[auth] WARNING: SECRET_KEY not set — sessions will not persist across "
            "restarts. Add SECRET_KEY to /opt/cyber-ai-news/.env"
        )
    return key


def _serializer() -> URLSafeTimedSerializer:
    global _SECRET
    if _SECRET is None:
        _SECRET = _get_secret()
    return URLSafeTimedSerializer(_SECRET, salt="session")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


def create_session_token(user_id: int) -> str:
    return _serializer().dumps(user_id)


def verify_session_token(token: str) -> int | None:
    try:
        return _serializer().loads(token, max_age=_SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
