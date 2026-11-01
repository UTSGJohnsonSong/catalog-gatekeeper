"""Connection pool helpers. SQL is hand-written; there is no ORM."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

Row = dict[str, Any]
Conn = psycopg.Connection[Row]


def _configure(conn: Conn) -> None:
    register_vector(conn)
    conn.commit()


def make_pool(database_url: str, *, min_size: int = 1, max_size: int = 8) -> ConnectionPool[Conn]:
    pool = ConnectionPool[Conn](
        database_url,
        min_size=min_size,
        max_size=max_size,
        configure=_configure,
        kwargs={"row_factory": dict_row},
        open=False,
    )
    pool.open(wait=True, timeout=30)
    return pool


def set_actor(conn: Conn, actor: str) -> None:
    """Attribute status changes in this transaction; the proposals trigger writes the audit row."""
    conn.execute("SELECT set_config('gatekeeper.actor', %s, true)", (actor,))


@contextmanager
def tx(pool: ConnectionPool[Conn], actor: str | None = None) -> Iterator[Conn]:
    """One transaction: commits on clean exit, rolls back on exception."""
    with pool.connection() as conn:
        if actor:
            set_actor(conn, actor)
        yield conn
