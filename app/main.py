import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from app.core.config import settings
from app.services.database import init_pool
from app.routers import auth, practice, chat, progress, subscription, payment, admin

log = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="CertMind API", version="1.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(practice.router)
app.include_router(chat.router)
app.include_router(progress.router)
app.include_router(subscription.router)
app.include_router(payment.router)
app.include_router(admin.router)


# ── Expiry reminder background task ──────────────────────────────────────────

async def _expiry_reminder_loop() -> None:
    """
    Runs every hour. Finds subscriptions expiring in 20–26 hours,
    sends a reminder email, and marks notified_expiry = TRUE.
    """
    from app.services.database import fetchall, execute
    from app.services.email import send_expiry_reminder_email

    while True:
        try:
            rows = fetchall(
                """
                SELECT s.id, s.user_id, s.exam_slug, s.expires_at, u.email
                FROM user_subscriptions s
                JOIN users u ON u.id = s.user_id
                WHERE s.status IN ('active', 'trial')
                  AND s.notified_expiry = FALSE
                  AND s.expires_at BETWEEN NOW() + INTERVAL '20 hours'
                                       AND NOW() + INTERVAL '26 hours'
                """,
                (),
            )
            for r in (rows or []):
                try:
                    expires_iso = (
                        r["expires_at"].isoformat()
                        if hasattr(r["expires_at"], "isoformat")
                        else str(r["expires_at"])
                    )
                    send_expiry_reminder_email(r["email"], r["exam_slug"], expires_iso)
                    execute(
                        "UPDATE user_subscriptions SET notified_expiry = TRUE WHERE id = %s",
                        (r["id"],),
                    )
                    log.info("Expiry reminder sent to %s for %s", r["email"], r["exam_slug"])
                except Exception as exc:
                    log.error("Failed expiry reminder for sub %s: %s", r["id"], exc)
        except Exception as exc:
            log.error("Expiry reminder loop error: %s", exc)

        await asyncio.sleep(3600)  # check every hour


@app.on_event("startup")
async def startup():
    init_pool()
    asyncio.create_task(_expiry_reminder_loop())


@app.get("/health")
async def health():
    return {"status": "ok"}
