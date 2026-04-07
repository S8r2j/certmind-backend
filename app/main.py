from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from app.core.config import settings
from app.services.database import init_pool
from app.routers import auth, practice, chat, progress, subscription, payment

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


@app.on_event("startup")
def startup():
    init_pool()


@app.get("/health")
async def health():
    return {"status": "ok"}
