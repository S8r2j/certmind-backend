"""
Admin-configurable platform settings backed by the `platform_settings` DB table.

Values are cached in-process for CACHE_TTL seconds so hot paths (practice, payment)
don't hit the DB on every request. Cache is invalidated immediately on write.
"""
import time
from typing import Any

from app.services.database import fetchone, execute

CACHE_TTL = 60  # seconds

_cache: dict[str, tuple[Any, float]] = {}  # key → (value, expiry_ts)


def _now() -> float:
    return time.monotonic()


def get_setting(key: str, default: Any = None) -> str | None:
    cached = _cache.get(key)
    if cached and cached[1] > _now():
        return cached[0]

    row = fetchone("SELECT value FROM platform_settings WHERE key = %s", (key,))
    value = row["value"] if row else default
    _cache[key] = (value, _now() + CACHE_TTL)
    return value


def get_int(key: str, default: int) -> int:
    val = get_setting(key)
    try:
        return int(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def set_setting(key: str, value: str) -> None:
    execute(
        "UPDATE platform_settings SET value = %s, updated_at = NOW() WHERE key = %s",
        (value, key),
    )
    # Invalidate cache immediately
    _cache.pop(key, None)


def get_all_settings() -> list[dict]:
    from app.services.database import fetchall
    rows = fetchall("SELECT key, value, description, updated_at FROM platform_settings ORDER BY key", ())
    return [
        {
            "key": r["key"],
            "value": r["value"],
            "description": r["description"],
            "updated_at": r["updated_at"].isoformat() if hasattr(r["updated_at"], "isoformat") else str(r["updated_at"]),
        }
        for r in (rows or [])
    ]
