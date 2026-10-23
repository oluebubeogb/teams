"""PostgreSQL access: one small psycopg 3 pool, dict rows, idempotent migrations on boot."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Optional

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import DATABASE_URL, DB_POOL_MIN, DB_POOL_MAX

log = logging.getLogger("teams.db")
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_pool: Optional[ConnectionPool] = None


def _url() -> str:
    # Accept SQLAlchemy-style URLs too (postgresql+psycopg://, postgres://)
    url = DATABASE_URL
    for p in ("postgresql+psycopg://", "postgresql+psycopg2://", "postgres://"):
        if url.startswith(p):
            url = "postgresql://" + url[len(p):]
    return url


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            _url(), min_size=DB_POOL_MIN, max_size=DB_POOL_MAX,
            kwargs={"row_factory": dict_row, "autocommit": False},
            open=True, timeout=15,
        )
    return _pool


def close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def tx():
    """Transaction-scoped connection. Commits on success, rolls back on error."""
    with pool().connection() as conn:
        yield conn


def q(sql: str, params: Any = None) -> list[dict]:
    with tx() as c:
        return c.execute(sql, params).fetchall()


def q1(sql: str, params: Any = None) -> Optional[dict]:
    with tx() as c:
        return c.execute(sql, params).fetchone()


def ex(sql: str, params: Any = None) -> int:
    with tx() as c:
        cur = c.execute(sql, params)
        return cur.rowcount


def val(sql: str, params: Any = None, default=None):
    row = q1(sql, params)
    if not row:
        return default
    return next(iter(row.values()))


def run_migrations():
    """Apply app/migrations/*.sql in order, once each. Files are also written to be re-runnable."""
    with tx() as c:
        c.execute("SELECT pg_advisory_lock(727001)")
        try:
            c.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
            c.commit()
            done = {r["name"] for r in c.execute("SELECT name FROM schema_migrations").fetchall()}
            for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if f.name in done:
                    continue
                log.info("applying migration %s", f.name)
                c.execute(f.read_text())
                c.execute("INSERT INTO schema_migrations(name) VALUES (%s)", (f.name,))
                c.commit()
        finally:
            c.execute("SELECT pg_advisory_unlock(727001)")
            c.commit()
