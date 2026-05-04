from __future__ import annotations

from typing import Optional

from fastapi import Cookie, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import get_settings

SESSION_COOKIE = "dca_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="dca-auth")


def make_session_token() -> str:
    return _serializer().dumps({"u": "owner"})


def verify_session_token(token: str) -> bool:
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return False
    return isinstance(data, dict) and data.get("u") == "owner"


def is_authenticated(token: Optional[str]) -> bool:
    if not token:
        return False
    return verify_session_token(token)


def require_auth(
    request: Request,
    dca_session: Optional[str] = Cookie(default=None),
) -> bool:
    """FastAPI dependency that guards JSON endpoints — returns 401 JSON when not logged in."""
    if not is_authenticated(dca_session):
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"Location": "/login"},
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return True


def check_password(submitted: str) -> bool:
    expected = get_settings().site_password
    return bool(expected) and submitted == expected
