import uuid
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from app.core.config import settings


def _normalize(row: dict | None) -> dict | None:
    """Convert UUID objects → str so callers never touch uuid.UUID directly."""
    if row is None:
        return None
    return {k: str(v) if isinstance(v, uuid.UUID) else v for k, v in row.items()}

_pool: ConnectionPool | None = None


def init_pool() -> None:
    global _pool
    _pool = ConnectionPool(conninfo=settings.database_url, min_size=1, max_size=10)


def _pool_conn():
    assert _pool is not None, "DB pool not initialised — call init_pool() first"
    return _pool.connection()


def fetchone(sql: str, params=()) -> dict | None:
    with _pool_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return _normalize(cur.fetchone())


def fetchall(sql: str, params=()) -> list[dict]:
    with _pool_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return [_normalize(row) for row in cur.fetchall()]


def execute(sql: str, params=()) -> dict | None:
    """Execute INSERT/UPDATE/DELETE. Returns first row when RETURNING is used."""
    with _pool_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            try:
                return _normalize(cur.fetchone())
            except psycopg.ProgrammingError:
                return None
