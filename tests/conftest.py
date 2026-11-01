"""Tests run against real Postgres with pgvector. `docker compose up -d db` provides it locally."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg_pool import ConnectionPool

from gatekeeper.config import Settings
from gatekeeper.context import AppContext, ensure_shop
from gatekeeper.db import make_pool
from gatekeeper.domain.product import Product
from gatekeeper.llm.heuristic import HeuristicProvider
from gatekeeper.migrate import migrate
from gatekeeper.rag.embedder import HashingTfidfEmbedder
from gatekeeper.rag.store import ingest_directory
from gatekeeper.shopify.fixture import FixtureShopify

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get(
    "TEST_ADMIN_DATABASE_URL", "postgresql://gatekeeper:local-demo-owner@127.0.0.1:54329/postgres"
)
TEST_DB = "gatekeeper_test"
TEST_URL = ADMIN_URL.rsplit("/", 1)[0] + "/" + TEST_DB
TRUNCATE = (
    "TRUNCATE jobs, webhook_events, writebacks, reviews, validation_results, audit_log, proposals, run_retrievals, llm_calls, "
    "agent_runs, findings, products, shops, heartbeats, fixture.products RESTART IDENTITY CASCADE"
)


@pytest.fixture(scope="session")
def database_url() -> str:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(TEST_DB)))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB)))
    migrate(TEST_URL)
    return TEST_URL


@pytest.fixture(scope="session")
def session_pool(database_url: str) -> Iterator[ConnectionPool]:
    pool = make_pool(database_url, min_size=2, max_size=12)
    embedder = HashingTfidfEmbedder()
    ingest_directory(pool, ROOT / "fixtures" / "policies", embedder)
    pool.embedder = embedder  # type: ignore[attr-defined]
    yield pool
    pool.close()


@pytest.fixture
def pool(session_pool: ConnectionPool) -> ConnectionPool:
    with session_pool.connection() as conn:
        conn.execute(TRUNCATE)
    return session_pool


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(
        env="test",
        database_url=database_url,
        writeback_enabled=True,
        shopify_webhook_secret="test-secret",
        demo_mode=True,
    )


@pytest.fixture
def ctx(pool: ConnectionPool, settings: Settings) -> AppContext:
    shop_id = ensure_shop(pool, settings.shopify_shop_domain, writeback_enabled=True)
    return AppContext(settings, pool, FixtureShopify(pool), HeuristicProvider(), None, pool.embedder, shop_id)  # type: ignore[attr-defined,arg-type]


def make_product(gid: str = "gid://shopify/Product/1", **overrides: object) -> Product:
    base: dict = {
        "gid": gid,
        "handle": gid.rsplit("/", 1)[-1],
        "title": "Summit Bottle 750ml",
        "status": "ACTIVE",
        "vendor": "Cedar Peak",
        "product_type": "Drinkware",
        "tags": ["drinkware"],
        "description_html": "<p>Summit Bottle 750ml keeps drinks cold on long days out, with a steel body and a tidy lid.</p>",
        "seo_title": "Summit Bottle 750ml | Cedar Peak",
        "seo_description": "Summit Bottle 750ml from Cedar Peak.",
        "variants": [{"gid": f"{gid}/v1", "sku": "SB-1", "price": "32.00", "options": ["Steel"]}],
        "images": [{"id": f"{gid}/img1", "alt": "A steel bottle"}],
    }
    base.update(overrides)
    return Product.from_dict(base)


def seed_product(ctx: AppContext, product: Product | None = None) -> int:
    """Put a product in the store and the local catalog, audited, as a sync would."""
    from gatekeeper.services.sync import sync_catalog

    product = product or make_product()
    ctx.shopify.load_catalog([product.to_dict()])  # type: ignore[attr-defined]
    sync_catalog(ctx, actor="test")
    with ctx.pool.connection() as conn:
        return conn.execute("SELECT id FROM products WHERE gid = %s", (product.gid,)).fetchone()["id"]
