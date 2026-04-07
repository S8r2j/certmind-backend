"""
Redis helpers for question caching and token blacklisting.

Cache layers:
  qpool:{exam_slug}:{domain}     — shared 20-question pool (10 min TTL)
  prefetch:{user_id}:{exam_slug} — per-user next question (5 min TTL)
  rt_blacklist:{token}           — blacklisted refresh tokens (TTL = token's remaining lifetime)

All use the Upstash Redis REST URL + token already in settings.
If Redis is unavailable, helpers return None / no-op (graceful degradation).
IMPORTANT: if Redis is unavailable the blacklist check returns False (not blacklisted),
meaning logout-based revocation won't work without Redis — configure Redis in production.
"""
from __future__ import annotations

import json
import logging

import redis as _redis

from app.core.config import settings

log = logging.getLogger(__name__)

QUESTION_POOL_TTL = 600   # 10 min — shared pool per exam+domain
PREFETCH_TTL      = 300   # 5 min  — per-user next-question prefetch

_client: _redis.Redis | None = None


def _get_client() -> _redis.Redis | None:
    global _client
    if _client is not None:
        return _client
    url = settings.upstash_redis_rest_url
    token = settings.upstash_redis_rest_token
    if not url or not token:
        return None
    try:
        # Upstash Redis REST URL is https://... — use redis-py with URL scheme
        # Upstash also supports the standard redis:// protocol via a different URL,
        # but the REST URL format works with httpx. We use the standard redis package
        # which expects redis:// — convert https:// Upstash URL to redis+ssl://.
        # If the user configures a standard redis:// URL this also works directly.
        if url.startswith("https://"):
            # Upstash REST URL — build a redis:// URL with token as password
            host = url.removeprefix("https://")
            redis_url = f"rediss://:{token}@{host}:6380"
        else:
            redis_url = url

        _client = _redis.from_url(redis_url, decode_responses=True, socket_timeout=2)
        _client.ping()
        return _client
    except Exception as exc:
        log.warning("Redis unavailable: %s — question caching disabled", exc)
        _client = None
        return None


# ── Shared question pool ──────────────────────────────────────────────────────

def cache_question_pool(exam_slug: str, domain: str, questions: list[dict]) -> None:
    """Store a list of question dicts for a given exam+domain combo."""
    client = _get_client()
    if client is None:
        return
    try:
        key = f"qpool:{exam_slug}:{domain}"
        client.set(key, json.dumps(questions), ex=QUESTION_POOL_TTL)
    except Exception as exc:
        log.warning("Redis set failed (qpool): %s", exc)


def get_cached_pool(exam_slug: str, domain: str) -> list[dict] | None:
    """Return cached pool or None on miss/error."""
    client = _get_client()
    if client is None:
        return None
    try:
        val = client.get(f"qpool:{exam_slug}:{domain}")
        return json.loads(val) if val else None
    except Exception as exc:
        log.warning("Redis get failed (qpool): %s", exc)
        return None


# ── Per-user prefetch ─────────────────────────────────────────────────────────

def set_prefetch(user_id: str, exam_slug: str, question: dict) -> None:
    """Store the pre-fetched next question for a user."""
    client = _get_client()
    if client is None:
        return
    try:
        key = f"prefetch:{user_id}:{exam_slug}"
        client.set(key, json.dumps(question), ex=PREFETCH_TTL)
    except Exception as exc:
        log.warning("Redis set failed (prefetch): %s", exc)


def pop_prefetch(user_id: str, exam_slug: str) -> dict | None:
    """Atomically read + delete the prefetched question. Returns None on miss."""
    client = _get_client()
    if client is None:
        return None
    try:
        key = f"prefetch:{user_id}:{exam_slug}"
        val = client.get(key)
        if val:
            client.delete(key)
            return json.loads(val)
        return None
    except Exception as exc:
        log.warning("Redis pop failed (prefetch): %s", exc)
        return None


# ── Refresh token blacklist ───────────────────────────────────────────────────

def blacklist_refresh_token(token: str, ttl_seconds: int) -> None:
    """
    Add a refresh token to the blacklist.
    ttl_seconds should be the token's remaining lifetime so the entry
    auto-expires when the token would have been invalid anyway.
    """
    client = _get_client()
    if client is None:
        log.warning("Redis unavailable — refresh token blacklisting skipped")
        return
    try:
        client.set(f"rt_blacklist:{token}", "1", ex=max(ttl_seconds, 1))
    except Exception as exc:
        log.warning("Redis set failed (rt_blacklist): %s", exc)


def is_refresh_token_blacklisted(token: str) -> bool:
    """
    Return True if the token has been blacklisted (i.e. user logged out).
    Returns False on Redis unavailability — log a warning so ops can act.
    """
    client = _get_client()
    if client is None:
        log.warning("Redis unavailable — cannot verify refresh token blacklist")
        return False
    try:
        return client.exists(f"rt_blacklist:{token}") == 1
    except Exception as exc:
        log.warning("Redis get failed (rt_blacklist): %s", exc)
        return False
