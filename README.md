# CertMind — Backend

FastAPI backend for CertMind, an AI-powered AWS certification exam prep platform. Deployed on Railway.

## Features

- **Adaptive practice tests** — domain-weighted question selection, shared 50-question sets, session-based progress tracking (resume from where you left off)
- **AI question generation** — falls back to AI (Groq / Gemini / Anthropic) only when a set isn't full yet; generated questions are stored and shared with all users
- **AI chat tutor** — streaming SSE responses scoped to the exam, multi-session history, markdown output, no hallucinated URLs
- **Access & refresh token auth** — 2-minute access tokens, 7-day refresh tokens, refresh token blacklisting on logout via Redis
- **Single active session enforcement** — new login invalidates previous device's session
- **One exam at a time** — users can only hold one active subscription or trial at a time
- **Free 1-week trial** — auto-granted on first practice attempt, locked to that exam
- **Stripe payments** — one-time $15 payment, 14-day access, webhook-verified activation
- **Redis caching** — shared question pool cache per exam+domain+set (10 min TTL), per-user prefetch cache (5 min TTL), refresh token blacklist
- **Rate limiting** — per-IP and per-user limits via SlowAPI + Upstash Redis

## Tech Stack

| Layer | Tool |
|---|---|
| Framework | FastAPI + Uvicorn |
| Database | PostgreSQL via psycopg3 + psycopg-pool |
| Migrations | Alembic |
| Auth | python-jose (JWT HS256) + Argon2 password hashing |
| AI | Groq / Google Gemini / Anthropic (configurable via `AI_MODEL`) |
| Payments | Stripe Python SDK |
| Caching | Redis (Upstash) |
| Rate limiting | SlowAPI |
| Deployment | Docker → Railway |

## Project Structure

```
app/
├── core/
│   └── config.py          # Pydantic Settings — all env vars
├── middleware/
│   ├── auth.py            # JWT validation (access token only)
│   └── session.py         # Single active session enforcement
├── routers/
│   ├── auth.py            # Register, login, token refresh, logout
│   ├── practice.py        # Adaptive questions + answer submission
│   ├── chat.py            # Streaming AI tutor (SSE)
│   ├── progress.py        # Domain score breakdowns
│   ├── subscription.py    # Subscription status
│   └── payment.py         # Stripe checkout + webhook
├── services/
│   ├── database.py        # psycopg3 connection pool helpers
│   ├── ai.py              # Multi-provider AI client (Groq/Gemini/Anthropic)
│   ├── redis_client.py    # Question pool cache, prefetch, token blacklist
│   └── sanitize.py        # Prompt injection sanitizer
└── schemas/
    └── models.py          # Pydantic request/response models
alembic/
└── versions/
    ├── 0001_initial_schema.py
    ├── 0002_question_sets.py
    ├── 0003_user_trial.py
    └── 0004_practice_sessions.py
```

## Local Setup

### 1. Clone and install

```bash
git clone https://github.com/S8r2j/certmind-backend.git
cd certmind-backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
DATABASE_URL=postgresql://user:password@localhost:5432/certmind

# Auth
JWT_SECRET=your-long-random-secret-here
JWT_ALGORITHM=HS256
JWT_ACCESS_EXPIRE_MINUTES=2
JWT_REFRESH_EXPIRE_DAYS=7

# AI provider — prefix determines which provider is used:
#   llama-* / mixtral-* → Groq (free tier)
#   gemini-*            → Google Gemini (free tier)
#   claude-*            → Anthropic
AI_MODEL=gemini-2.0-flash
GROQ_API_KEY=
GOOGLE_API_KEY=
ANTHROPIC_API_KEY=

# Stripe
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_EXAM_PRICE_ID=price_...

# Upstash Redis (rate limiting + caching + token blacklist)
UPSTASH_REDIS_REST_URL=https://...upstash.io
UPSTASH_REDIS_REST_TOKEN=

# CORS
FRONTEND_URL=http://localhost:3000

# Set to true to skip payment gates during local development
BYPASS_SUBSCRIPTION=true
```

### 3. Run migrations and start

```bash
alembic upgrade head
uvicorn app.main:app --reload
```

API docs available at `http://localhost:8000/docs`.

## API Overview

| Method | Path | Description |
|---|---|---|
| POST | `/auth/register` | Create account → returns access + refresh + session tokens |
| POST | `/auth/login` | Login → returns access + refresh + session tokens |
| POST | `/auth/token/refresh` | Exchange refresh token for new access token |
| POST | `/auth/logout` | Blacklist refresh token + invalidate session |
| POST | `/practice/question` | Get next adaptive question for current session |
| POST | `/practice/answer` | Submit answer, update progress, advance session |
| GET | `/chat/sessions` | List chat sessions for an exam |
| GET | `/chat/history` | Get messages for a session |
| POST | `/chat/message` | Send message → streaming SSE response |
| GET | `/progress/{exam_slug}` | Get domain score breakdown |
| GET | `/subscription/status` | Get active subscription/trial info |
| POST | `/payment/create-checkout` | Create Stripe checkout session |
| POST | `/payment/webhook` | Stripe webhook handler |
| GET | `/health` | Keep-alive ping |

## Practice Session Logic

- One session = one set of 50 questions
- Set 1 is shared across all users for the same exam — questions generated by User A are served to User B from the pool
- AI generation only triggers when the current set has fewer than 50 questions and no unseen DB questions are available
- If a user exits mid-session, their next `/practice/question` call resumes from their position in the same set
- When 50 questions are answered, the session is marked complete and the frontend shows a summary screen

## Token Flow

```
Login → { access_token (2min), refresh_token (7 days), session_token (permanent) }

Every API request:  Authorization: Bearer <access_token>
                    X-Session-Token: <session_token>

Access token expires → POST /auth/token/refresh → new access_token
Logout → POST /auth/logout → refresh_token blacklisted in Redis, session invalidated
```

## Deployment (Railway)

The `Dockerfile` runs `alembic upgrade head` before starting Uvicorn, so migrations apply automatically on deploy:

```dockerfile
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```

Set all env vars in Railway's Variables tab. Set `BYPASS_SUBSCRIPTION=false` in production.
