"""One contract, two adapters. The live adapter runs against a mocked GraphQL server here, never a real store."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from gatekeeper.domain.product import content_hash
from gatekeeper.shopify.fixture import FixtureShopify
from gatekeeper.shopify.live import LiveShopify
from gatekeeper.shopify.mapper import money, product_from_node, update_input
from gatekeeper.shopify.port import (
    ShopifyAuthError,
    ShopifyGraphQLError,
    ShopifyThrottled,
    ShopifyTransient,
    UpdateResult,
)
from gatekeeper.shopify.webhook_hmac import sign, verify
from tests.conftest import make_product


def to_node(p) -> dict[str, Any]:
    return {
        "id": p.gid,
        "handle": p.handle,
        "title": p.title,
        "status": p.status,
        "vendor": p.vendor,
        "productType": p.product_type,
        "tags": p.tags,
        "descriptionHtml": p.description_html or None,
        "updatedAt": p.updated_at,
        "seo": {"title": p.seo_title or None, "description": p.seo_description or None},
        "variants": {
            "edges": [
                {
                    "node": {
                        "id": v["gid"],
                        "sku": v["sku"],
                        "price": v["price"],
                        "selectedOptions": [{"value": o} for o in v["options"]],
                    }
                }
                for v in p.variants
            ]
        },
        "media": {"edges": [{"node": {"id": i["id"], "alt": i["alt"]}} for i in p.images]},
    }


class FakeAdminGraphQL:
    """Dispatches on operationName and delegates to a FixtureShopify, so both adapters see the same store."""

    def __init__(self, store: FixtureShopify):
        self.store = store
        self.requests: list[dict[str, Any]] = []
        self.next_responses: list[httpx.Response] = []
        self.user_errors: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append({"headers": dict(request.headers), "url": str(request.url), **body})
        if self.next_responses:
            return self.next_responses.pop(0)
        op, variables = body["operationName"], body["variables"]
        if op == "Products":
            page = self.store.list_products(cursor=variables.get("after"), first=variables["first"])
            data = {
                "products": {
                    "pageInfo": {"hasNextPage": page.next_cursor is not None, "endCursor": page.next_cursor},
                    "edges": [{"node": to_node(p)} for p in page.products],
                }
            }
        elif op == "Product":
            found = self.store.get_product(variables["id"])
            data = {"product": to_node(found) if found else None}
        elif op == "ProductUpdate":
            data = {"productUpdate": self._update(variables["product"])}
        else:
            return httpx.Response(400, json={"errors": [{"message": "unknown op"}]})
        return httpx.Response(200, json={"data": data})

    def _update(self, product: dict[str, Any]) -> dict[str, Any]:
        if self.user_errors:
            return {"product": None, "userErrors": self.user_errors}
        fields: dict[str, str] = {}
        if "descriptionHtml" in product:
            fields["description_html"] = product["descriptionHtml"]
        seo = product.get("seo") or {}
        if "title" in seo:
            fields["seo_title"] = seo["title"]
        if "description" in seo:
            fields["seo_description"] = seo["description"]
        result = self.store.update_product(product["id"], fields)
        if result.user_errors:
            return {
                "product": None,
                "userErrors": [{"field": e.field, "message": e.message} for e in result.user_errors],
            }
        return {"product": to_node(result.product), "userErrors": []}


@pytest.fixture
def store(ctx) -> FixtureShopify:
    return ctx.shopify


@pytest.fixture
def fake(store) -> FakeAdminGraphQL:
    return FakeAdminGraphQL(store)


def make_live(fake, **kwargs) -> LiveShopify:
    return LiveShopify(
        shop_domain="demo.myshopify.com",
        access_token="test-token",
        api_version="2026-10",
        writes_enabled=kwargs.pop("writes_enabled", True),
        transport=httpx.MockTransport(fake),
        **kwargs,
    )


@pytest.fixture(params=["fixture", "live"])
def adapter(request, store, fake):
    return store if request.param == "fixture" else make_live(fake)


class TestContract:
    def test_pagination_visits_every_product_once(self, store, adapter):
        store.load_catalog([make_product(f"gid://shopify/Product/{i}", handle=f"p{i}").to_dict() for i in range(5)])
        seen, cursor = [], None
        while True:
            page = adapter.list_products(cursor=cursor, first=2)
            seen += [p.gid for p in page.products]
            cursor = page.next_cursor
            if cursor is None:
                break
        assert len(seen) == 5 and len(set(seen)) == 5

    def test_get_missing_returns_none(self, adapter):
        assert adapter.get_product("gid://shopify/Product/404") is None

    def test_update_changes_content_hash_and_returns_product(self, store, adapter):
        store.load_catalog([make_product("gid://shopify/Product/1").to_dict()])
        before = content_hash(adapter.get_product("gid://shopify/Product/1"))
        result = adapter.update_product("gid://shopify/Product/1", {"seo_title": "Better title"})
        assert isinstance(result, UpdateResult) and not result.user_errors
        assert result.product.seo_title == "Better title"
        assert content_hash(adapter.get_product("gid://shopify/Product/1")) != before

    def test_update_unknown_product_gives_user_errors(self, adapter):
        result = adapter.update_product("gid://shopify/Product/404", {"seo_title": "x"})
        assert result.user_errors and result.product is None

    def test_hash_is_identical_through_both_adapters(self, store, fake):
        product = make_product("gid://shopify/Product/1", description_html="", seo_title="")
        store.load_catalog([product.to_dict()])
        direct = store.get_product(product.gid)
        via_live = make_live(fake).get_product(product.gid)
        assert content_hash(direct) == content_hash(via_live), "null-to-empty mapping must not change the hash"


class TestLiveOnly:
    def test_sends_token_version_and_operation(self, fake):
        make_live(fake).get_product("gid://shopify/Product/1")
        request = fake.requests[0]
        assert request["headers"]["x-shopify-access-token"] == "test-token"
        assert "/admin/api/2026-10/graphql.json" in request["url"]
        assert request["operationName"] == "Product"

    def test_update_sends_absolute_values_only_for_allowed_fields(self):
        assert update_input("g", {"description_html": "<p>x</p>", "seo_title": "t"}) == {
            "id": "g",
            "descriptionHtml": "<p>x</p>",
            "seo": {"title": "t"},
        }

    def test_writes_are_refused_when_disabled(self, fake):
        with pytest.raises(ShopifyAuthError):
            make_live(fake, writes_enabled=False).update_product("g", {"seo_title": "x"})
        assert fake.requests == []

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_errors(self, fake, status):
        fake.next_responses = [httpx.Response(status, json={})]
        with pytest.raises(ShopifyAuthError):
            make_live(fake).get_product("g")

    def test_429_honours_retry_after(self, fake):
        fake.next_responses = [httpx.Response(429, headers={"Retry-After": "7"}, json={})]
        with pytest.raises(ShopifyThrottled) as caught:
            make_live(fake).get_product("g")
        assert caught.value.retry_after == 7.0

    def test_graphql_throttled_error_uses_restore_rate(self, fake):
        fake.next_responses = [
            httpx.Response(
                200,
                json={
                    "errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
                    "extensions": {
                        "cost": {
                            "requestedQueryCost": 100,
                            "throttleStatus": {"maximumAvailable": 1000, "currentlyAvailable": 50, "restoreRate": 50},
                        }
                    },
                },
            )
        ]
        with pytest.raises(ShopifyThrottled) as caught:
            make_live(fake).get_product("g")
        assert caught.value.retry_after == 1.0

    @pytest.mark.parametrize(
        "response", [httpx.Response(503, json={}), httpx.Response(200, text="<html>bad gateway</html>")]
    )
    def test_transient_errors(self, fake, response):
        fake.next_responses = [response]
        with pytest.raises(ShopifyTransient):
            make_live(fake).get_product("g")

    def test_network_failure_is_transient(self):
        def boom(request):
            raise httpx.ConnectError("down")

        live = LiveShopify(
            shop_domain="d", access_token="t", api_version="2026-10", transport=httpx.MockTransport(boom)
        )
        with pytest.raises(ShopifyTransient):
            live.get_product("g")

    def test_other_graphql_errors_raise(self, fake):
        fake.next_responses = [httpx.Response(200, json={"errors": [{"message": "Field 'x' doesn't exist"}]})]
        with pytest.raises(ShopifyGraphQLError):
            make_live(fake).get_product("g")

    def test_user_errors_are_returned_not_raised(self, fake):
        fake.user_errors = [{"field": ["seo", "title"], "message": "too long"}]
        result = make_live(fake).update_product("g", {"seo_title": "x"})
        assert result.user_errors[0].message == "too long" and result.user_errors[0].code is None

    def test_paces_when_the_bucket_is_low(self, fake):
        sleeps: list[float] = []
        fake.next_responses = [
            httpx.Response(
                200,
                json={
                    "data": {"product": None},
                    "extensions": {
                        "cost": {
                            "requestedQueryCost": 100,
                            "throttleStatus": {"maximumAvailable": 1000, "currentlyAvailable": 0, "restoreRate": 50},
                        }
                    },
                },
            )
        ]
        make_live(fake, sleep=sleeps.append).get_product("g")
        assert 2.0 <= sleeps[0] <= 2.25

    def test_mapper_null_handling_and_money(self):
        product = product_from_node({"id": "g", "seo": None, "descriptionHtml": None, "variants": None, "media": None})
        assert product.description_html == "" and product.seo_title == ""
        assert money("12.5") == "12.50" and money(None) == "0.00"


class TestHmac:
    def test_verify_round_trip_and_rejections(self):
        raw = b'{"id":1}'
        assert verify("s", raw, sign("s", raw))
        assert not verify("s", raw, sign("t", raw))
        assert not verify("s", raw + b" ", sign("s", raw))
        assert not verify("s", raw, None)
        assert not verify("s", raw, "")
        assert not verify("s", raw, "éé")

    def test_known_vector(self):
        # Computed independently: echo -n '{"id":1}' | openssl dgst -sha256 -hmac secret -binary | base64
        assert sign("secret", b'{"id":1}') == "A971iWIMgT8Zj9A9eWfikrFj7wQ16/Qwcc4OlRl2PLc="
