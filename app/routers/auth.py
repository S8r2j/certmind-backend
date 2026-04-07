import secrets
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Request, Depends
from jose import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from app.core.config import settings
from app.middleware.auth import get_current_user
from app.services.database import fetchone, execute
from app.schemas.models import RegisterRequest, LoginRequest, AuthResponse

router = APIRouter(prefix="/auth", tags=["auth"])
ph = PasswordHasher()


def _make_jwt(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.jwt_expire_days)
    return jwt.encode(
        {"sub": user_id, "exp": expire},
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
    access_token = _make_jwt(user_id)
    session_token = _make_session(user_id)
    return AuthResponse(access_token=access_token, session_token=session_token, email=body.email)


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest):
    user = fetchone("SELECT id, email, password_hash FROM users WHERE email = %s", (body.email,))
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    try:
        ph.verify(user["password_hash"], body.password)
    except VerifyMismatchError:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    access_token = _make_jwt(user["id"])
    session_token = _make_session(user["id"])
    return AuthResponse(access_token=access_token, session_token=session_token, email=user["email"])


@router.post("/refresh")
async def refresh_token(user_id: str = Depends(get_current_user)):
    """Issue a fresh JWT without re-authenticating (called on app load if token near expiry)."""
    access_token = _make_jwt(user_id)
    return {"access_token": access_token}
