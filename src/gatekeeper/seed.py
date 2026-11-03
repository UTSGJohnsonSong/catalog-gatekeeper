"""One-shot setup step: migrate, create roles, ingest policies, load the demo store, first sync."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from gatekeeper import jobs
from gatekeeper.config import Settings, get_settings
from gatekeeper.context import build_context, ensure_shop
from gatekeeper.db import make_pool, tx
from gatekeeper.migrate import bootstrap_roles, migrate
from gatekeeper.rag.embedder import HashingTfidfEmbedder
from gatekeeper.rag.store import ingest_directory
from gatekeeper.services.sync import sync_catalog
from gatekeeper.shopify.fixture import FixtureShopify
from gatekeeper.worker import configure_logging

log = logging.getLogger("gatekeeper.seed")
# Everything a visitor can change. TRUNCATE does not fire the audit log's row triggers, and only the owner role may
# run it: the app role has no TRUNCATE grant. There is no CASCADE, so a new table that references one of these fails
# the reset loudly instead of being emptied without anyone deciding to.
RESET_TABLES = (
    "jobs",
    "webhook_events",
    "writebacks",
    "reviews",
    "validation_results",
    "audit_log",
    "proposals",
    "run_retrievals",
    "llm_calls",
    "agent_runs",
    "findings",
    "products",
    "fixture.products",
)
# Kept, so a running API and worker never lose their footing: schema state, the shop row, the heartbeat, the allowed
# proposal transitions the status trigger reads, and the policies.
KEPT_TABLES = (
    "schema_migrations",
    "shops",
    "heartbeats",
    "proposal_transitions",
    "policy_docs",
    "policy_chunks",
    "embedder_state",
)
_RESET_LOCK = 727_002
LOCK_ATTEMPTS = 20
LOCK_RETRY_SECONDS = 0.25
LOAD_ATTEMPTS = 3
LOAD_RETRY_SECONDS = 2.0


class NotTheDemoDatabase(RuntimeError):
    """The database holds something a demo must not touch. Retrying cannot help."""


class DemoLeftEmpty(RuntimeError):
    """A reset emptied the tables and then could not load the demo again. Another attempt should come soon."""


def check_demo_database(conn: psycopg.Connection[Any], settings: Settings) -> None:
    """Refuses a database that has served anything but this demo. Only a database with no tables at all is fresh.

    Runs before setup touches anything, because setup migrates, resets role passwords and grants the app role every
    table in the schema."""
    rows = conn.execute(
        "SELECT CASE WHEN table_schema = 'public' THEN table_name ELSE table_schema || '.' || table_name END "
        "FROM information_schema.tables WHERE table_schema IN ('public', 'fixture') AND table_type = 'BASE TABLE'"
    ).fetchall()
    tables = {row[0] for row in rows}
    strangers = tables - set(RESET_TABLES) - set(KEPT_TABLES)
    if strangers:
        raise NotTheDemoDatabase(f"this database holds other tables, such as {min(strangers)}; refusing to use it")
    if "shops" not in tables:
        return  # Fresh, or this demo's own migrations stopped partway.
    foreign = conn.execute(
        "SELECT domain FROM shops WHERE domain <> %s LIMIT 1", (settings.shopify_shop_domain,)
    ).fetchone()
    if foreign:
        raise NotTheDemoDatabase(f"this database also serves {foreign[0]}; refusing to use it for the demo")
    if conn.execute("SELECT 1 FROM agent_runs WHERE mode = 'live' LIMIT 1").fetchone():
        raise NotTheDemoDatabase("this database holds runs of a real model; refusing to use it for the demo")
    # Independent of configuration: every product of the demo comes from its own fixture store.
    if "fixture.products" not in tables:
        stranger = conn.execute("SELECT 1 FROM products LIMIT 1").fetchone()
    else:
        stranger = conn.execute(
            "SELECT 1 FROM products p WHERE NOT EXISTS (SELECT 1 FROM fixture.products f WHERE f.gid = p.gid) LIMIT 1"
        ).fetchone()
    if stranger:
        raise NotTheDemoDatabase("this database holds products from outside the demo store; refusing to use it")


def prepare_database(settings: Settings) -> None:
    """Schema, roles, policies and the shop row. Safe to repeat on every boot."""
    log.info("migrations applied: %s", migrate(settings.database_url))
    bootstrap_roles(settings.database_url, settings)
    pool = make_pool(settings.database_url)
    try:
        chunks = ingest_directory(pool, settings.fixtures_dir / "policies", HashingTfidfEmbedder())
        log.info("policy chunks ingested: %d", chunks)
        ensure_shop(pool, settings.shopify_shop_domain, writeback_enabled=settings.demo_mode)
    finally:
        pool.close()


def load_store(settings: Settings) -> None:
    """Loads the demo catalog into an empty fixture store, syncs, and queues the demo's first agent runs."""
    ctx = build_context(settings)
    try:
        if settings.demo_mode and settings.shopify_adapter == "fixture":
            loaded = FixtureShopify(ctx.pool).load_file_if_empty(
                settings.fixtures_dir / "shopify" / "catalog_demo.json"
            )
            log.info("demo products loaded: %d", loaded)
        log.info("first sync: %s", sync_catalog(ctx, actor="seed"))
        if settings.demo_mode and settings.demo_autorun:
            with tx(ctx.pool) as conn:
                rows = conn.execute(
                    "SELECT DISTINCT product_id FROM findings WHERE status = 'open' AND fixable ORDER BY product_id LIMIT %s",
                    (settings.demo_autorun,),
                ).fetchall()
            for row in rows:
                jobs.enqueue(
                    ctx.pool, "agent_run", {"product_id": row["product_id"]}, dedupe_key=f"autorun:{row['product_id']}"
                )
            log.info("demo agent runs queued: %d", len(rows))
    finally:
        ctx.pool.close()


def reset_demo(settings: Settings) -> bool:
    """Puts a public demo back to its first state: the original catalog, no decisions, a fresh queue of agent runs.

    Returns False when another process holds the reset lock. Raises NotTheDemoDatabase for a database that has served
    anything else, because a wrong DATABASE_URL must not wipe an audit log that matters, and DemoLeftEmpty when the
    tables were emptied but the demo could not be loaded again. Records each finished reset as a heartbeat."""
    if not (settings.public_demo and settings.demo_mode and settings.shopify_adapter == "fixture"):
        raise NotTheDemoDatabase("reset_demo only runs for a public demo on the fixture store")
    catalog = json.loads((settings.fixtures_dir / "shopify" / "catalog_demo.json").read_text(encoding="utf-8"))
    with psycopg.connect(
        settings.database_url, autocommit=True, application_name="gatekeeper-reset", connect_timeout=10
    ) as conn:
        # A hung network or a stuck lock must end in an error the supervisor retries, never in a worker stopped forever.
        conn.execute("SET statement_timeout = '60s'")
        locked = conn.execute("SELECT pg_try_advisory_lock(%s)", (_RESET_LOCK,)).fetchone()
        if not (locked and locked[0]):
            log.warning("another process holds the demo reset lock; this reset is skipped")
            return False
        try:
            check_demo_database(conn, settings)
            _empty_tables(conn, settings, catalog)
            _load_again(settings)
            conn.execute(
                "INSERT INTO heartbeats (name, at) VALUES ('demo_reset', now()) "
                "ON CONFLICT (name) DO UPDATE SET at = now()"
            )
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_RESET_LOCK,))
    log.info("demo reset")
    return True


def _empty_tables(conn: psycopg.Connection[Any], settings: Settings, catalog: list[dict[str, Any]]) -> None:
    """Takes every table lock at once without waiting, truncates, and puts the demo catalog back, in one transaction.

    Waiting for one lock while holding others could deadlock with a visitor's approval, which locks the same tables in
    another order, so NOWAIT gives up at once and the whole set is tried again a moment later. The catalog goes back
    in the same transaction, so a committed reset never leaves the fixture store empty, even if loading the rest
    fails. Identities are not restarted: a job that was running when the reset came must not find a new job under
    its old id."""
    tables = ", ".join(RESET_TABLES)
    for attempt in range(1, LOCK_ATTEMPTS + 1):
        try:
            with conn.transaction():
                # The shop row below is not covered by the table locks. An owner flipping the kill switch locks it
                # in the other order, so this wait is short and ends in a retry, not a deadlock.
                conn.execute("SET LOCAL lock_timeout = '200ms'")
                conn.execute(f"LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE NOWAIT")
                conn.execute(f"TRUNCATE {tables}")
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO fixture.products (gid, data) VALUES (%s, %s)",
                        [(product["gid"], Jsonb(product)) for product in catalog],
                    )
                # A visitor cannot reach the kill switch, but an owner's admin token can; it must not outlive a reset.
                conn.execute(
                    "UPDATE shops SET writeback_enabled = true WHERE domain = %s", (settings.shopify_shop_domain,)
                )
            log.info("demo tables emptied and catalog restored: %d products", len(catalog))
            return
        except psycopg.errors.LockNotAvailable:
            if attempt == LOCK_ATTEMPTS:
                raise
            time.sleep(LOCK_RETRY_SECONDS)


def _load_again(settings: Settings) -> None:
    for attempt in range(1, LOAD_ATTEMPTS + 1):
        try:
            load_store(settings)
            return
        except Exception as exc:  # noqa: BLE001 - retried, then reported as the empty demo it leaves behind
            if attempt == LOAD_ATTEMPTS:
                raise DemoLeftEmpty("the demo was emptied and could not be loaded again") from exc
            log.warning("loading the demo after a reset failed, attempt %d; retrying", attempt, exc_info=True)
            time.sleep(LOAD_RETRY_SECONDS)


def main() -> None:
    configure_logging()
    settings = get_settings()
    prepare_database(settings)
    load_store(settings)


if __name__ == "__main__":
    main()
