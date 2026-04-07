import secrets
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Request, Depends
from jose import jwt, JWTError
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from app.core.config import settings
from app.middleware.auth import get_current_user
from app.middleware.session import validate_session
from app.services.database import fetchone, execute
from app.schemas.models import RegisterRequest, LoginRequest, AuthResponse, RefreshRequest
from app.services.redis_client import blacklist_refresh_token, is_refresh_token_blacklisted

router = APIRouter(prefix="/auth", tags=["auth"])
ph = PasswordHasher()


def _make_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_access_expire_minutes)
    return jwt.encode(
        {"sub": user_id, "type": "access", "exp": expire},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def _make_refresh_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_expire_days)
    return jwt.encode(
        {"sub": user_id, "type": "refresh", "exp": expire},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def _make_session(user_id: str) -> str:
    # Invalidate all existing sessions
    execute("UPDATE user_sessions SET is_active = FALSE WHERE user_id = %s", (user_id,))
    token = secrets.token_urlsafe(32)
    execute(
        "INSERT INTO user_sessions (id, user_id, session_token) VALUES (%s, %s, %s)",
        (str(uuid.uuid4()), user_id, token),
    )
    return token


@router.post("/register", response_model=AuthResponse)
async def register(body: RegisterRequest):
    existing = fetchone("SELECT id FROM users WHERE email = %s", (body.email,))
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    user_id = str(uuid.uuid4())
    hashed = ph.hash(body.password)
    execute(
        "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)",
        (user_id, body.email, hashed),
    )
    access_token = _make_access_token(user_id)
    refresh_token = _make_refresh_token(user_id)
    session_token = _make_session(user_id)
    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        session_token=session_token,
        email=body.email,
    )


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest):
    user = fetchone("SELECT id, email, password_hash FROM users WHERE email = %s", (body.email,))
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    try:
        ph.verify(user["password_hash"], body.password)
    except VerifyMismatchError:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    access_token = _make_access_token(user["id"])
    refresh_token = _make_refresh_token(user["id"])
    session_token = _make_session(user["id"])
    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        session_token=session_token,
        email=user["email"],
    )


@router.post("/token/refresh")
async def refresh_token_endpoint(body: RefreshRequest, request: Request):
    """
    Exchange a valid refresh token for a new access token.
    Checks: signature, expiry, type, blacklist, and active session.
    """
    try:
        payload = jwt.decode(body.refresh_token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Refresh token expired or invalid")

    # Reject blacklisted tokens (user already logged out)
    if is_refresh_token_blacklisted(body.refresh_token):
        raise HTTPException(status_code=401, detail="Refresh token has been revoked")

    # Validate session is still active (catches forced logouts from new device)
    await validate_session(request, user_id)

    return {"access_token": _make_access_token(user_id)}


@router.post("/logout")
async def logout(body: RefreshRequest, request: Request, user_id: str = Depends(get_current_user)):
    """
    Blacklist the refresh token and invalidate the session.
    The access token is short-lived (2 min) so no need to blacklist it.
    """
    await validate_session(request, user_id)

    # Compute remaining TTL from the token's exp claim so Redis auto-cleans it
    try:
        payload = jwt.decode(body.refresh_token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        exp = payload.get("exp", 0)
        ttl = max(int(exp - datetime.now(timezone.utc).timestamp()), 1)
    except JWTError:
        ttl = settings.jwt_refresh_expire_days * 86400  # fallback: full 7 days

    blacklist_refresh_token(body.refresh_token, ttl)

    # Invalidate the session so this device is fully signed out
    session_token = request.headers.get("X-Session-Token")
    if session_token:
        execute("UPDATE user_sessions SET is_active = FALSE WHERE session_token = %s", (session_token,))

    return {"ok": True}
