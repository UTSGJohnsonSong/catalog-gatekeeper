"""Numbered .sql migrations, guarded by an advisory lock so concurrent boots are safe."""

from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg import sql

from gatekeeper.config import Settings

MIGRATIONS = Path(__file__).parent / "migrations"
_LOCK_ID = 727_001


def latest_version() -> str:
    return sorted(p.name.split("_")[0] for p in MIGRATIONS.glob("*.sql") if ".down." not in p.name)[-1]


def migrate(database_url: str) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_ID,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
            files = sorted(p for p in MIGRATIONS.glob("*.sql") if ".down." not in p.name)
            for path in files:
                version = path.name.split("_")[0]
                if version in done:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8").encode())
                    conn.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (version,))
                applied.append(version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))
    return applied


def bootstrap_roles(database_url: str, settings: Settings) -> None:
    """Least-privilege roles. Passwords go through sql.Literal, never string formatting."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        dbname = conn.info.dbname
        if settings.app_db_password:
            _ensure_role(conn, "gatekeeper_app", settings.app_db_password.get_secret_value())
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO gatekeeper_app").format(sql.Identifier(dbname)))
            for schema in ("public", "fixture"):
                schema_id = sql.Identifier(schema)
                conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO gatekeeper_app").format(schema_id))
                conn.execute(
                    sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO gatekeeper_app").format(
                        schema_id
                    )
                )
                conn.execute(
                    sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO gatekeeper_app").format(schema_id)
                )
            conn.execute("REVOKE UPDATE, DELETE ON audit_log FROM gatekeeper_app")
        if settings.mcp_db_password:
            _ensure_role(conn, "gatekeeper_mcp_ro", settings.mcp_db_password.get_secret_value())
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO gatekeeper_mcp_ro").format(sql.Identifier(dbname)))
            conn.execute("GRANT USAGE ON SCHEMA public TO gatekeeper_mcp_ro")
            conn.execute(
                "GRANT SELECT ON v_products, v_findings, v_proposals, v_validation, v_audit_log, v_policy_chunks, "
                "v_policy_search, v_embedder_state TO gatekeeper_mcp_ro"
            )
            conn.execute("ALTER ROLE gatekeeper_mcp_ro SET default_transaction_read_only = on")


def _ensure_role(conn: psycopg.Connection, role: str, password: str) -> None:
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    verb = "ALTER" if exists else "CREATE"
    conn.execute(sql.SQL(verb + " ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
