import secrets
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import RedirectResponse
from jose import jwt, JWTError
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from app.core.config import settings
from app.middleware.auth import get_current_user
from app.middleware.session import validate_session
from app.services.database import fetchone, execute
from app.services.email import send_verification_email, send_password_reset_email
from app.services.redis_client import blacklist_refresh_token, is_refresh_token_blacklisted
from app.schemas.models import (
    RegisterRequest, LoginRequest, AuthResponse, RefreshRequest,
    ForgotPasswordRequest, ResetPasswordRequest, ResendVerificationRequest,
    ProfileResponse, UpdateProfileRequest, ChangePasswordRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])
ph = PasswordHasher()

VERIFY_TOKEN_TTL_HOURS = 24
RESET_TOKEN_TTL_HOURS = 1


# ── JWT helpers ───────────────────────────────────────────────────────────────

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
    execute("UPDATE user_sessions SET is_active = FALSE WHERE user_id = %s", (user_id,))
    token = secrets.token_urlsafe(32)
    execute(
        "INSERT INTO user_sessions (id, user_id, session_token) VALUES (%s, %s, %s)",
        (str(uuid.uuid4()), user_id, token),
    )
    return token


# ── Register ──────────────────────────────────────────────────────────────────

@router.post("/register", response_model=AuthResponse)
async def register(body: RegisterRequest):
    if fetchone("SELECT id FROM users WHERE email = %s", (body.email,)):
        raise HTTPException(status_code=409, detail="Email already registered")

    user_id = str(uuid.uuid4())
    hashed = ph.hash(body.password)
    verify_token = secrets.token_urlsafe(32)
    verify_expires = datetime.now(timezone.utc) + timedelta(hours=VERIFY_TOKEN_TTL_HOURS)

    execute(
        "INSERT INTO users (id, email, password_hash, email_verified, email_verify_token, "
        "email_verify_token_expires_at, first_name, middle_name, last_name, gender, "
        "date_of_birth, employment_details, goals) "
        "VALUES (%s, %s, %s, FALSE, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            user_id, body.email, hashed, verify_token, verify_expires,
            body.first_name, body.middle_name, body.last_name, body.gender,
            body.date_of_birth, body.employment_details, body.goals,
        ),
    )

    try:
        send_verification_email(body.email, verify_token)
    except Exception:
        pass  # don't block registration if email fails — user can resend

    access_token = _make_access_token(user_id)
    refresh_token = _make_refresh_token(user_id)
    session_token = _make_session(user_id)
    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        session_token=session_token,
        email=body.email,
        first_name=body.first_name,
        last_name=body.last_name,
    )


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest):
    user = fetchone(
        "SELECT id, email, password_hash, email_verified, first_name, last_name FROM users WHERE email = %s",
        (body.email,),
    )
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    try:
        ph.verify(user["password_hash"], body.password)
    except VerifyMismatchError:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user["email_verified"]:
        raise HTTPException(status_code=403, detail="EMAIL_NOT_VERIFIED")

    access_token = _make_access_token(user["id"])
    refresh_token = _make_refresh_token(user["id"])
    session_token = _make_session(user["id"])
    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        session_token=session_token,
        email=user["email"],
        first_name=user.get("first_name"),
        last_name=user.get("last_name"),
    )


# ── Email verification ────────────────────────────────────────────────────────

@router.get("/verify-email")
async def verify_email(token: str):
    user = fetchone(
        "SELECT id, email_verified, email_verify_token_expires_at FROM users "
        "WHERE email_verify_token = %s",
        (token,),
    )
    if not user:
        return RedirectResponse(f"{settings.frontend_url}/verify-email?status=invalid")

    if user["email_verified"]:
        return RedirectResponse(f"{settings.frontend_url}/verify-email?status=already_verified")

    expires = user["email_verify_token_expires_at"]
    if isinstance(expires, str):
        expires = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        return RedirectResponse(f"{settings.frontend_url}/verify-email?status=expired")

    execute(
        "UPDATE users SET email_verified = TRUE, email_verify_token = NULL, "
        "email_verify_token_expires_at = NULL WHERE email_verify_token = %s",
        (token,),
    )
    return RedirectResponse(f"{settings.frontend_url}/verify-email?status=success")


@router.post("/resend-verification")
async def resend_verification(body: ResendVerificationRequest):
    user = fetchone(
        "SELECT id, email_verified FROM users WHERE email = %s", (body.email,)
    )
    # Always return 200 to avoid email enumeration
    if not user or user["email_verified"]:
        return {"ok": True}

    verify_token = secrets.token_urlsafe(32)
    verify_expires = datetime.now(timezone.utc) + timedelta(hours=VERIFY_TOKEN_TTL_HOURS)
    execute(
        "UPDATE users SET email_verify_token = %s, email_verify_token_expires_at = %s WHERE id = %s",
        (verify_token, verify_expires, user["id"]),
    )
    try:
        send_verification_email(body.email, verify_token)
    except Exception:
        pass
    return {"ok": True}


# ── Forgot / reset password ───────────────────────────────────────────────────

@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest):
    user = fetchone("SELECT id FROM users WHERE email = %s", (body.email,))
    # Always return 200 to avoid email enumeration
    if not user:
        return {"ok": True}

    reset_token = secrets.token_urlsafe(32)
    reset_expires = datetime.now(timezone.utc) + timedelta(hours=RESET_TOKEN_TTL_HOURS)
    execute(
        "UPDATE users SET reset_token = %s, reset_token_expires_at = %s WHERE id = %s",
        (reset_token, reset_expires, user["id"]),
    )
    try:
        send_password_reset_email(body.email, reset_token)
    except Exception:
        pass
    return {"ok": True}


@router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest):
    user = fetchone(
        "SELECT id, reset_token_expires_at FROM users WHERE reset_token = %s",
        (body.token,),
    )
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")

    expires = user["reset_token_expires_at"]
    if isinstance(expires, str):
        expires = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Reset link has expired. Please request a new one.")

    new_hash = ph.hash(body.new_password)
    execute(
        "UPDATE users SET password_hash = %s, reset_token = NULL, reset_token_expires_at = NULL "
        "WHERE id = %s",
        (new_hash, user["id"]),
    )
    # Invalidate all active sessions so the old password can't be used
    execute("UPDATE user_sessions SET is_active = FALSE WHERE user_id = %s", (user["id"],))
    return {"ok": True}


# ── Token refresh + logout ────────────────────────────────────────────────────

@router.post("/token/refresh")
async def refresh_token_endpoint(body: RefreshRequest, request: Request):
    try:
        payload = jwt.decode(body.refresh_token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Refresh token expired or invalid")

    if is_refresh_token_blacklisted(body.refresh_token):
        raise HTTPException(status_code=401, detail="Refresh token has been revoked")

    await validate_session(request, user_id)
    return {"access_token": _make_access_token(user_id)}


@router.post("/logout")
async def logout(body: RefreshRequest, request: Request, user_id: str = Depends(get_current_user)):
    await validate_session(request, user_id)

    try:
        payload = jwt.decode(body.refresh_token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        exp = payload.get("exp", 0)
        ttl = max(int(exp - datetime.now(timezone.utc).timestamp()), 1)
    except JWTError:
        ttl = settings.jwt_refresh_expire_days * 86400

    blacklist_refresh_token(body.refresh_token, ttl)

    session_token = request.headers.get("X-Session-Token")
    if session_token:
        execute("UPDATE user_sessions SET is_active = FALSE WHERE session_token = %s", (session_token,))

    return {"ok": True}


# ── Profile ───────────────────────────────────────────────────────────────────

@router.get("/profile", response_model=ProfileResponse)
async def get_profile(request: Request, user_id: str = Depends(get_current_user)):
    await validate_session(request, user_id)
    user = fetchone(
        "SELECT email, first_name, middle_name, last_name, gender, date_of_birth, "
        "employment_details, goals FROM users WHERE id = %s",
        (user_id,),
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    dob = user.get("date_of_birth")
    return ProfileResponse(
        email=user["email"],
        first_name=user.get("first_name"),
        middle_name=user.get("middle_name"),
        last_name=user.get("last_name"),
        gender=user.get("gender"),
        date_of_birth=str(dob) if dob else None,
        employment_details=user.get("employment_details"),
        goals=user.get("goals"),
    )


@router.put("/profile", response_model=ProfileResponse)
async def update_profile(body: UpdateProfileRequest, request: Request, user_id: str = Depends(get_current_user)):
    await validate_session(request, user_id)
    execute(
        "UPDATE users SET first_name = COALESCE(%s, first_name), middle_name = %s, "
        "last_name = COALESCE(%s, last_name), gender = %s, date_of_birth = %s, "
        "employment_details = %s, goals = %s WHERE id = %s",
        (
            body.first_name, body.middle_name, body.last_name, body.gender,
            body.date_of_birth, body.employment_details, body.goals, user_id,
        ),
    )
    return await get_profile(request, user_id)


@router.post("/change-password")
async def change_password(body: ChangePasswordRequest, request: Request, user_id: str = Depends(get_current_user)):
    await validate_session(request, user_id)
    user = fetchone("SELECT password_hash FROM users WHERE id = %s", (user_id,))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        ph.verify(user["password_hash"], body.current_password)
    except VerifyMismatchError:
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    new_hash = ph.hash(body.new_password)
    execute("UPDATE users SET password_hash = %s WHERE id = %s", (new_hash, user_id))
    return {"ok": True}
