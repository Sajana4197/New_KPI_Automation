"""PostgreSQL access: one shared connection pool and a few small helpers."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .config import APP_DIR, config

_pool: ConnectionPool | None = None

DEFAULT_SETTINGS: dict[str, Any] = {
    # Same rules as the original IPPM notebook.
    "thresholds": {"pd_pct": 0.5, "jitter_ms": 7, "rtt_ms": 20, "min_hours": 3},
}


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            config.conninfo, min_size=1, max_size=8, open=True,
            kwargs={"row_factory": dict_row},
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def tx() -> Iterator[psycopg.Connection]:
    """A connection inside one transaction: commits on success, rolls back on error."""
    with pool().connection() as conn:
        yield conn


def query(sql: str, params: Any = None) -> list[dict]:
    with tx() as conn:
        return conn.execute(sql, params).fetchall()


def query_one(sql: str, params: Any = None) -> dict | None:
    with tx() as conn:
        return conn.execute(sql, params).fetchone()


def scalar(sql: str, params: Any = None) -> Any:
    row = query_one(sql, params)
    return None if row is None else next(iter(row.values()))


def execute(sql: str, params: Any = None) -> int:
    with tx() as conn:
        return conn.execute(sql, params).rowcount


def init_schema() -> None:
    schema = (APP_DIR / "schema.sql").read_text(encoding="utf-8")
    with tx() as conn:
        conn.execute(schema)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
                (key, Jsonb(value)),
            )


def get_setting(key: str, conn: psycopg.Connection | None = None) -> Any:
    sql = "SELECT value FROM settings WHERE key = %s"
    row = conn.execute(sql, (key,)).fetchone() if conn else query_one(sql, (key,))
    return row["value"] if row else DEFAULT_SETTINGS.get(key)


def set_setting(key: str, value: Any, user_id: int | None = None) -> None:
    execute(
        """INSERT INTO settings (key, value, updated_by) VALUES (%s, %s, %s)
           ON CONFLICT (key) DO UPDATE
           SET value = EXCLUDED.value, updated_at = now(), updated_by = EXCLUDED.updated_by""",
        (key, Jsonb(value), user_id),
    )


def log_activity(user: dict | None, action: str, detail: str = "") -> None:
    execute(
        "INSERT INTO activity_log (user_id, username, action, detail) VALUES (%s, %s, %s, %s)",
        (user["id"] if user else None, user["username"] if user else "system", action, detail),
    )
