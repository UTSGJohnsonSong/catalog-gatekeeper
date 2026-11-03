"""Wiring: what the services need, built once per process."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import httpx
from psycopg_pool import ConnectionPool

from gatekeeper.config import Settings
from gatekeeper.db import Conn, make_pool
from gatekeeper.llm.anthropic_provider import AnthropicProvider
from gatekeeper.llm.heuristic import HeuristicProvider
from gatekeeper.llm.port import LLMProvider
from gatekeeper.rag.embedder import Embedder
from gatekeeper.rag.store import load_embedder
from gatekeeper.shopify.fixture import FixtureShopify
from gatekeeper.shopify.live import ClientCredentialsToken, LiveShopify, StaticToken, TokenSource
from gatekeeper.shopify.port import ShopifyPort


@dataclass
class AppContext:
    settings: Settings
    pool: ConnectionPool[Conn]
    shopify: ShopifyPort
    provider: LLMProvider
    judge: LLMProvider | None
    embedder: Embedder
    shop_id: int


def build_provider(settings: Settings) -> tuple[LLMProvider, LLMProvider | None]:
    """Picks the model. The demo model never gets a judge: a template cannot assess anything."""
    choice = settings.llm_provider
    if choice == "auto":
        choice = "anthropic" if settings.anthropic_api_key else "heuristic"
    if choice == "heuristic":
        return HeuristicProvider(), None
    if not settings.anthropic_api_key:
        raise ValueError("LLM_PROVIDER=anthropic needs ANTHROPIC_API_KEY")
    key = settings.anthropic_api_key.get_secret_value()
    agent = AnthropicProvider(api_key=key, model=settings.llm_model)
    judge = AnthropicProvider(api_key=key, model=settings.llm_judge_model or settings.llm_model)
    return agent, judge


def build_shopify(
    settings: Settings, pool: ConnectionPool[Conn], *, transport: httpx.BaseTransport | None = None
) -> ShopifyPort:
    """`transport` replaces the network, for tests of this wiring."""
    if settings.shopify_adapter == "fixture":
        return cast(ShopifyPort, FixtureShopify(pool))
    tokens: TokenSource
    if settings.shopify_client_id and settings.shopify_client_secret:
        tokens = ClientCredentialsToken(
            shop_domain=settings.shopify_shop_domain,
            client_id=settings.shopify_client_id,
            client_secret=settings.shopify_client_secret.get_secret_value(),
            transport=transport,
        )
    elif settings.shopify_access_token:
        tokens = StaticToken(settings.shopify_access_token.get_secret_value())
    else:
        raise ValueError(
            "SHOPIFY_ADAPTER=live needs SHOPIFY_ACCESS_TOKEN, or SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET"
        )
    return cast(
        ShopifyPort,
        LiveShopify(
            shop_domain=settings.shopify_shop_domain,
            token_source=tokens,
            api_version=settings.shopify_api_version,
            writes_enabled=settings.writeback_enabled,
            transport=transport,
        ),
    )


def ensure_shop(pool: ConnectionPool[Conn], domain: str, *, writeback_enabled: bool = False) -> int:
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO shops (domain, writeback_enabled) VALUES (%s, %s) "
            "ON CONFLICT (domain) DO UPDATE SET domain = EXCLUDED.domain RETURNING id",
            (domain, writeback_enabled),
        ).fetchone()
    return row["id"]


def build_context(settings: Settings) -> AppContext:
    pool = make_pool(settings.database_url)
    provider, judge = build_provider(settings)
    shop_id = ensure_shop(pool, settings.shopify_shop_domain, writeback_enabled=settings.demo_mode)
    return AppContext(settings, pool, build_shopify(settings, pool), provider, judge, load_embedder(pool), shop_id)
