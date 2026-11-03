"""Token handling of the live adapter: the client credentials grant, renewal, and one retry after a 401."""

from __future__ import annotations

import logging
import threading
import time
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import ValidationError

from gatekeeper.config import Settings
from gatekeeper.context import build_shopify
from gatekeeper.shopify.live import ClientCredentialsToken, LiveShopify, StaticToken
from gatekeeper.shopify.port import ShopifyAuthError, ShopifyThrottled, ShopifyTransient

SHOP = "demo.myshopify.com"


class FakeShop:
    """Answers the token endpoint and a Product query. Rejects any token not in `valid`."""

    def __init__(self) -> None:
        self.issued = 0
        self.token_requests: list[dict[str, list[str]]] = []
        self.graphql_tokens: list[str] = []
        self.valid: set[str] = set()
        self.token_response: httpx.Response | None = None
        self.always_401 = False
        self.token_delay = 0.0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/oauth/access_token":
            self.token_requests.append(parse_qs(request.content.decode()))
            time.sleep(self.token_delay)
            if self.token_response is not None:
                return self.token_response
            self.issued += 1
            token = f"tok-{self.issued}"
            self.valid.add(token)
            return httpx.Response(200, json={"access_token": token, "scope": "read_products", "expires_in": 86399})
        token = request.headers.get("x-shopify-access-token", "")
        self.graphql_tokens.append(token)
        if self.always_401 or token not in self.valid:
            return httpx.Response(401, json={"errors": "Invalid API key or access token"})
        return httpx.Response(200, json={"data": {"product": None}})


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def shop() -> FakeShop:
    return FakeShop()


@pytest.fixture
def clock() -> Clock:
    return Clock()


def make(shop: FakeShop, clock: Clock) -> tuple[LiveShopify, ClientCredentialsToken]:
    transport = httpx.MockTransport(shop)
    tokens = ClientCredentialsToken(
        shop_domain=SHOP, client_id="client-1", client_secret="secret-1", transport=transport, clock=clock
    )
    return LiveShopify(shop_domain=SHOP, api_version="2026-10", token_source=tokens, transport=transport), tokens


def test_first_call_fetches_a_token_with_the_client_credentials_grant(shop, clock):
    live, _ = make(shop, clock)
    assert live.get_product("gid://shopify/Product/1") is None
    assert shop.token_requests == [
        {"grant_type": ["client_credentials"], "client_id": ["client-1"], "client_secret": ["secret-1"]}
    ]
    assert shop.graphql_tokens == ["tok-1"]


def test_token_is_reused_until_shortly_before_it_expires(shop, clock):
    live, _ = make(shop, clock)
    live.get_product("gid://shopify/Product/1")
    clock.now += 86399 - 301
    live.get_product("gid://shopify/Product/1")
    assert len(shop.token_requests) == 1
    clock.now += 2
    live.get_product("gid://shopify/Product/1")
    assert len(shop.token_requests) == 2
    assert shop.graphql_tokens == ["tok-1", "tok-1", "tok-2"]


def test_a_401_renews_the_token_and_retries_once(shop, clock):
    live, _ = make(shop, clock)
    live.get_product("gid://shopify/Product/1")
    shop.valid.clear()  # The store revoked tok-1 early.
    assert live.get_product("gid://shopify/Product/1") is None
    assert shop.graphql_tokens == ["tok-1", "tok-1", "tok-2"]


def test_a_second_401_is_final(shop, clock):
    live, _ = make(shop, clock)
    shop.always_401 = True
    with pytest.raises(ShopifyAuthError):
        live.get_product("gid://shopify/Product/1")
    assert len(shop.graphql_tokens) == 2
    assert len(shop.token_requests) == 2


def test_a_static_token_is_not_retried():
    shop = FakeShop()
    live = LiveShopify(
        shop_domain=SHOP, api_version="2026-10", access_token="set-by-hand", transport=httpx.MockTransport(shop)
    )
    with pytest.raises(ShopifyAuthError):
        live.get_product("gid://shopify/Product/1")
    assert shop.graphql_tokens == ["set-by-hand"]
    assert shop.token_requests == []


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(400, json={"error": "invalid_client"}), ShopifyAuthError),
        (httpx.Response(401, text="no"), ShopifyAuthError),
        (httpx.Response(503, text="down"), ShopifyTransient),
        (httpx.Response(429, headers={"Retry-After": "3"}), ShopifyThrottled),
        (httpx.Response(200, text="<html>"), ShopifyTransient),
        (httpx.Response(200, json={"scope": "read_products"}), ShopifyAuthError),
    ],
)
def test_token_endpoint_failures_map_to_store_errors(shop, clock, response, error):
    live, _ = make(shop, clock)
    shop.token_response = response
    with pytest.raises(error):
        live.get_product("gid://shopify/Product/1")
    assert shop.graphql_tokens == []


@pytest.mark.parametrize(
    "body",
    [
        [{"access_token": "tok"}],
        {"access_token": "tok", "expires_in": "soon"},
        {"access_token": "tok", "expires_in": 0},
        {"access_token": "tok", "expires_in": True},
    ],
)
def test_odd_token_responses_are_refused(shop, clock, body):
    live, _ = make(shop, clock)
    shop.token_response = httpx.Response(200, json=body)
    with pytest.raises(ShopifyAuthError):
        live.get_product("gid://shopify/Product/1")


def test_an_early_renewal_that_hits_an_outage_keeps_the_valid_token(shop, clock):
    live, _ = make(shop, clock)
    live.get_product("gid://shopify/Product/1")
    clock.now += 86399 - 100  # Inside the renewal margin, still before expiry.
    shop.token_response = httpx.Response(503, text="down")
    assert live.get_product("gid://shopify/Product/1") is None
    assert shop.graphql_tokens[-1] == "tok-1"
    clock.now += 200  # Now expired: the outage can no longer be ridden out.
    with pytest.raises(ShopifyTransient):
        live.get_product("gid://shopify/Product/1")


def test_a_failed_early_renewal_backs_off_and_says_so(shop, clock, caplog):
    live, _ = make(shop, clock)
    live.get_product("gid://shopify/Product/1")
    clock.now += 86399 - 100
    shop.token_response = httpx.Response(503, text="down")
    with caplog.at_level(logging.WARNING, logger="gatekeeper.shopify"):
        for _ in range(5):
            live.get_product("gid://shopify/Product/1")
    assert len(shop.token_requests) == 2  # The first fetch, then one renewal attempt; the next calls back off.
    assert "could not renew the store token" in caplog.text
    clock.now += 31
    shop.token_response = None
    live.get_product("gid://shopify/Product/1")
    assert shop.graphql_tokens[-1] == "tok-2"


def test_settings_with_client_credentials_wire_the_renewing_token(shop):
    settings = Settings(
        shopify_adapter="live", shopify_shop_domain=SHOP, shopify_client_id="client-1", shopify_client_secret="secret-1"
    )
    live = build_shopify(settings, None, transport=httpx.MockTransport(shop))  # type: ignore[arg-type]
    assert live.get_product("gid://shopify/Product/1") is None
    assert shop.token_requests == [
        {"grant_type": ["client_credentials"], "client_id": ["client-1"], "client_secret": ["secret-1"]}
    ]
    assert shop.graphql_tokens == ["tok-1"]


def test_settings_with_a_static_token_never_ask_for_one(shop):
    settings = Settings(shopify_adapter="live", shopify_shop_domain=SHOP, shopify_access_token="shpat-static")
    live = build_shopify(settings, None, transport=httpx.MockTransport(shop))  # type: ignore[arg-type]
    with pytest.raises(ShopifyAuthError):
        live.get_product("gid://shopify/Product/1")
    assert shop.token_requests == []
    assert shop.graphql_tokens == ["shpat-static"]


def test_a_short_lived_token_does_not_cause_a_token_request_per_call(shop, clock):
    _, tokens = make(shop, clock)
    shop.token_response = httpx.Response(200, json={"access_token": "brief", "expires_in": 120})
    tokens.get()
    clock.now += 50
    tokens.get()
    assert len(shop.token_requests) == 1


def test_the_secret_is_only_ever_sent_to_a_myshopify_domain(clock):
    with pytest.raises(ValueError, match="myshopify.com"):
        ClientCredentialsToken(shop_domain="store.example.com", client_id="c", client_secret="s", clock=clock)


def test_token_endpoint_network_failure_is_transient(clock):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    tokens = ClientCredentialsToken(
        shop_domain=SHOP, client_id="c", client_secret="s", transport=httpx.MockTransport(boom), clock=clock
    )
    with pytest.raises(ShopifyTransient):
        tokens.get()


def test_concurrent_callers_share_one_token_request(shop, clock):
    _, tokens = make(shop, clock)
    shop.token_delay = 0.05
    results: list[str] = []
    threads = [threading.Thread(target=lambda: results.append(tokens.get())) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == ["tok-1"] * 8
    assert len(shop.token_requests) == 1


def test_invalidate_keeps_a_newer_token(shop, clock):
    _, tokens = make(shop, clock)
    first = tokens.get()
    tokens.invalidate(first)
    second = tokens.get()
    tokens.invalidate(first)  # A late caller reporting the old token must not throw away the new one.
    assert tokens.get() == second == "tok-2"


def test_secrets_stay_out_of_repr(clock):
    tokens = ClientCredentialsToken(shop_domain=SHOP, client_id="c", client_secret="very-secret", clock=clock)
    assert "very-secret" not in repr(tokens)
    assert "set-by-hand" not in repr(StaticToken("set-by-hand"))


def test_adapter_needs_exactly_one_token_source():
    with pytest.raises(ValueError):
        LiveShopify(shop_domain=SHOP, api_version="2026-10")
    with pytest.raises(ValueError):
        LiveShopify(shop_domain=SHOP, api_version="2026-10", access_token="a", token_source=StaticToken("b"))


class TestSettings:
    def test_client_credentials_must_come_as_a_pair(self):
        with pytest.raises(ValidationError, match="must be set together"):
            Settings(shopify_client_id="c")

    def test_static_token_and_client_credentials_are_exclusive(self):
        with pytest.raises(ValidationError, match="not both"):
            Settings(shopify_client_id="c", shopify_client_secret="s", shopify_access_token="t")

    def test_production_accepts_client_credentials_instead_of_a_static_token(self):
        Settings(
            env="production",
            shopify_adapter="live",
            shopify_shop_domain=SHOP,
            shopify_client_id="c",
            shopify_client_secret="s",
            reviewer_tokens="a:admin:" + "0" * 64,
            shopify_webhook_secret="w",
            llm_provider="anthropic",
            anthropic_api_key="k",
        )

    def test_production_live_adapter_still_needs_some_credential(self):
        with pytest.raises(ValidationError, match="needs SHOPIFY_ACCESS_TOKEN"):
            Settings(
                env="production",
                shopify_adapter="live",
                shopify_shop_domain=SHOP,
                reviewer_tokens="a:admin:" + "0" * 64,
                shopify_webhook_secret="w",
                llm_provider="anthropic",
                anthropic_api_key="k",
            )
