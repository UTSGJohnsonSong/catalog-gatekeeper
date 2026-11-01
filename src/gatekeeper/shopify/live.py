"""Admin GraphQL adapter. Tested only against mocked HTTP; never called against a real store from this repo.

Shopify documents no precondition on productUpdate, so `expected_content_hash` is ignored here and the
write-back service does refetch, compare, write, read back. The residual race is about one round trip.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

import httpx

from gatekeeper.domain.product import Product
from gatekeeper.shopify.mapper import product_from_node, update_input
from gatekeeper.shopify.port import (
    ProductPage,
    ShopifyAuthError,
    ShopifyGraphQLError,
    ShopifyThrottled,
    ShopifyTransient,
    UpdateResult,
    UserError,
)

_PRODUCT_FIELDS = """
  id handle title status vendor productType tags descriptionHtml updatedAt
  seo { title description }
  variants(first: 100) { edges { node { id sku price selectedOptions { value } } } }
  media(first: 50) { edges { node { id alt } } }
"""

QUERY_PRODUCTS = (
    "query Products($first: Int!, $after: String, $query: String) {"
    " products(first: $first, after: $after, query: $query, sortKey: UPDATED_AT) {"
    " pageInfo { hasNextPage endCursor } edges { node {" + _PRODUCT_FIELDS + "} } } }"
)
QUERY_PRODUCT = "query Product($id: ID!) { product(id: $id) {" + _PRODUCT_FIELDS + "} }"
MUTATION_UPDATE = (
    "mutation ProductUpdate($product: ProductUpdateInput!) {"
    " productUpdate(product: $product) { product {" + _PRODUCT_FIELDS + "} userErrors { field message } } }"
)


class LiveShopify:
    def __init__(
        self,
        *,
        shop_domain: str,
        access_token: str,
        api_version: str,
        writes_enabled: bool = False,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._writes_enabled = writes_enabled
        self._sleep = sleep
        self._client = httpx.Client(
            base_url=f"https://{shop_domain}/admin/api/{api_version}",
            headers={"X-Shopify-Access-Token": access_token, "Content-Type": "application/json"},
            timeout=30.0,
            transport=transport,
        )

    def _post(self, operation: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(
                "/graphql.json", json={"query": query, "variables": variables, "operationName": operation}
            )
        except httpx.TimeoutException as exc:
            raise ShopifyTransient(f"timeout calling {operation}") from exc
        except httpx.TransportError as exc:
            raise ShopifyTransient(f"transport error calling {operation}: {exc}") from exc
        if response.status_code in (401, 403):
            raise ShopifyAuthError(f"{response.status_code} from Admin API")
        if response.status_code == 429:
            raise ShopifyThrottled(float(response.headers.get("Retry-After", "2")))
        if response.status_code >= 500:
            raise ShopifyTransient(f"{response.status_code} from Admin API")
        if response.status_code != 200:
            raise ShopifyGraphQLError(f"unexpected HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise ShopifyTransient("Admin API returned a non-JSON body") from exc
        for error in body.get("errors") or []:
            code = (error.get("extensions") or {}).get("code")
            if code == "THROTTLED":
                raise ShopifyThrottled(self._restore_delay(body, default=2.0))
            if code == "INTERNAL_SERVER_ERROR":
                raise ShopifyTransient("Admin API internal error")
            raise ShopifyGraphQLError(str(error.get("message", "GraphQL error")))
        self._pace(body)
        return body.get("data") or {}

    @staticmethod
    def _restore_delay(body: dict[str, Any], default: float) -> float:
        cost = (body.get("extensions") or {}).get("cost") or {}
        status = cost.get("throttleStatus") or {}
        rate = status.get("restoreRate") or 0
        if not rate:
            return default
        needed = (cost.get("requestedQueryCost") or 0) - (status.get("currentlyAvailable") or 0)
        return max(0.0, needed / rate)

    def _pace(self, body: dict[str, Any]) -> None:
        """Sleep before the bucket runs dry, so the next call is not throttled."""
        cost = (body.get("extensions") or {}).get("cost") or {}
        status = cost.get("throttleStatus") or {}
        available, rate = status.get("currentlyAvailable"), status.get("restoreRate")
        requested = cost.get("requestedQueryCost")
        if available is None or not rate or requested is None or available >= requested:
            return
        self._sleep((requested - available) / rate + random.uniform(0, 0.25))

    def list_products(
        self, *, cursor: str | None = None, first: int = 50, updated_since: datetime | None = None
    ) -> ProductPage:
        query = f"updated_at:>={updated_since.strftime('%Y-%m-%dT%H:%M:%SZ')}" if updated_since else None
        data = self._post("Products", QUERY_PRODUCTS, {"first": first, "after": cursor, "query": query})
        connection = data["products"]
        info = connection["pageInfo"]
        return ProductPage(
            [product_from_node(edge["node"]) for edge in connection["edges"]],
            info["endCursor"] if info["hasNextPage"] else None,
        )

    def get_product(self, gid: str) -> Product | None:
        node = self._post("Product", QUERY_PRODUCT, {"id": gid}).get("product")
        return product_from_node(node) if node else None

    def update_product(
        self, gid: str, fields: Mapping[str, str], *, expected_content_hash: str | None = None
    ) -> UpdateResult:
        if not self._writes_enabled:
            raise ShopifyAuthError("live writes are disabled (WRITEBACK_ENABLED is false)")
        data = self._post("ProductUpdate", MUTATION_UPDATE, {"product": update_input(gid, dict(fields))})
        payload = data.get("productUpdate") or {}
        errors = [
            UserError(list(e.get("field") or []), e.get("message", ""), e.get("code"))
            for e in payload.get("userErrors") or []
        ]
        node = payload.get("product")
        return UpdateResult(product_from_node(node) if node and not errors else None, errors)
