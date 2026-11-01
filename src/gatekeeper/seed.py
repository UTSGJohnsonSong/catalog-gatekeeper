"""One-shot setup step: migrate, create roles, ingest policies, load the demo store, first sync."""

from __future__ import annotations

import logging

from gatekeeper import jobs
from gatekeeper.config import get_settings
from gatekeeper.context import build_context, ensure_shop
from gatekeeper.db import make_pool, tx
from gatekeeper.migrate import bootstrap_roles, migrate
from gatekeeper.rag.embedder import HashingTfidfEmbedder
from gatekeeper.rag.store import ingest_directory
from gatekeeper.services.sync import sync_catalog
from gatekeeper.shopify.fixture import FixtureShopify
from gatekeeper.worker import configure_logging

log = logging.getLogger("gatekeeper.seed")


def main() -> None:
    configure_logging()
    settings = get_settings()
    log.info("migrations applied: %s", migrate(settings.database_url))
    bootstrap_roles(settings.database_url, settings)
    pool = make_pool(settings.database_url)
    chunks = ingest_directory(pool, settings.fixtures_dir / "policies", HashingTfidfEmbedder())
    log.info("policy chunks ingested: %d", chunks)
    ensure_shop(pool, settings.shopify_shop_domain, writeback_enabled=settings.demo_mode)
    if settings.demo_mode and settings.shopify_adapter == "fixture":
        loaded = FixtureShopify(pool).load_file_if_empty(settings.fixtures_dir / "shopify" / "catalog_demo.json")
        log.info("demo products loaded: %d", loaded)
    pool.close()
    ctx = build_context(settings)
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
    ctx.pool.close()


if __name__ == "__main__":
    main()
