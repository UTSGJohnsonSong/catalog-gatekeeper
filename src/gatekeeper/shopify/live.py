"""Admin GraphQL adapter. Tested only against mocked HTTP; never called against a real store from this repo.

Shopify documents no precondition on productUpdate, so `expected_content_hash` is ignored here and the
write-back service does refetch, compare, write, read back. The residual race is about one round trip.

Only the first 25 variants and 10 media of a product are read, to stay under the query cost limit. A change beyond
those does not change the content hash, and defects there are not found. See docs/adr/0006.

Tokens: either one token set by hand, or the client credentials grant, which Shopify answers with a token that lasts
24 hours and no refresh token. Asking again is the refresh, so `ClientCredentialsToken` asks shortly before expiry
and once more after a 401.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol

import httpx

from gatekeeper.config import MYSHOPIFY_DOMAIN
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

log = logging.getLogger("gatekeeper.shopify")

_PRODUCT_FIELDS = """
  id handle title status vendor productType tags descriptionHtml updatedAt
  seo { title description }
  variants(first: 25) { edges { node { id sku price selectedOptions { value } } } }
  media(first: 10) { edges { node { id alt } } }
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
# Shopify documents expires_in as 86399 for this grant. A shorter real lifetime still ends in a 401 and a refresh.
DEFAULT_TOKEN_SECONDS = 86_399
REFRESH_MARGIN_SECONDS = 300.0
# After a failed early renewal, the next attempt waits this long, or as long as the endpoint's Retry-After says.
RENEWAL_BACKOFF_SECONDS = 30.0


class TokenSource(Protocol):
    refreshable: bool

    def get(self) -> str: ...

    def invalidate(self, token: str) -> None: ...


class StaticToken:
    """A token set by hand. Nothing can renew it, so a 401 is final."""

    refreshable = False

    def __init__(self, token: str):
        self._token = token

    def __repr__(self) -> str:
        return "StaticToken(***)"

    def get(self) -> str:
        return self._token

    def invalidate(self, token: str) -> None:
        return None


class ClientCredentialsToken:
    """Gets Admin API tokens with the client credentials grant and renews them before they expire.

    One instance is shared by every thread of a process, so the lock makes concurrent callers wait for one request.
    """

    refreshable = True

    def __init__(
        self,
        *,
        shop_domain: str,
        client_id: str,
        client_secret: str,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        refresh_margin_seconds: float = REFRESH_MARGIN_SECONDS,
    ):
        if not MYSHOPIFY_DOMAIN.fullmatch(shop_domain):
            # The client secret is posted to this host, so it must be a store's own Shopify domain.
            raise ValueError("shop_domain must be the store's myshopify.com domain")
        self._url = f"https://{shop_domain}/admin/oauth/access_token"
        self._client_id = client_id
        self._client_secret = client_secret
        self._clock = clock
        self._margin = refresh_margin_seconds
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0
        self._renew_after = 0.0
        self._http = httpx.Client(timeout=30.0, transport=transport)

    def __repr__(self) -> str:
        return f"ClientCredentialsToken(url={self._url!r}, client_id={self._client_id!r}, secret=***)"

    def get(self) -> str:
        with self._lock:
            now = self._clock()
            if self._token is not None and now < self._expires_at - self._margin:
                return self._token
            if self._token is not None and now < min(self._renew_after, self._expires_at):
                return self._token  # Backing off after a failed renewal, while the token still works.
            try:
                token, lifetime = self._request()
            except (ShopifyTransient, ShopifyThrottled) as exc:
                # Renewing early is a courtesy. While the old token is still valid, a hiccup at the token endpoint
                # must not fail a call that would have worked, and it must not be asked again on every call.
                left = self._expires_at - self._clock()
                if self._token is not None and left > 0:
                    pause = exc.retry_after if isinstance(exc, ShopifyThrottled) else RENEWAL_BACKOFF_SECONDS
                    self._renew_after = self._clock() + max(1.0, pause)
                    log.warning("could not renew the store token (%s); the current one works %.0f s more", exc, left)
                    return self._token
                raise
            # Timed from before the request, and the margin never exceeds half a lifetime, so a short-lived token
            # cannot turn every call into a token request.
            self._token, self._expires_at = token, now + lifetime
            self._margin = min(self._margin, lifetime / 2)
            return token

    def invalidate(self, token: str) -> None:
        """Drop a token the store rejected. A newer token that another thread already fetched is kept."""
        with self._lock:
            if self._token == token:
                self._token = None

    def _request(self) -> tuple[str, float]:
        try:
            response = self._http.post(
                self._url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
        except httpx.TimeoutException as exc:
            raise ShopifyTransient("timeout requesting an access token") from exc
        except httpx.TransportError as exc:
            raise ShopifyTransient(f"transport error requesting an access token: {exc}") from exc
        # Error bodies are not logged or raised: they can echo the request.
        if response.status_code == 429:
            raise ShopifyThrottled(float(response.headers.get("Retry-After", "2")))
        if response.status_code >= 500:
            raise ShopifyTransient(f"{response.status_code} from the token endpoint")
        if response.status_code != 200:
            raise ShopifyAuthError(f"token request rejected with HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise ShopifyTransient("token endpoint returned a non-JSON body") from exc
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise ShopifyAuthError("token response had no access_token")
        expires_in = body.get("expires_in", DEFAULT_TOKEN_SECONDS)
        if isinstance(expires_in, bool) or not isinstance(expires_in, int | float) or expires_in <= 0:
            raise ShopifyAuthError("token response had an unusable expires_in")
        return token, float(expires_in)


class LiveShopify:
    def __init__(
        self,
        *,
        shop_domain: str,
        api_version: str,
        access_token: str | None = None,
        token_source: TokenSource | None = None,
        writes_enabled: bool = False,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if (access_token is None) == (token_source is None):
            raise ValueError("pass exactly one of access_token and token_source")
        self._tokens: TokenSource = token_source or StaticToken(access_token or "")
        self._writes_enabled = writes_enabled
        self._sleep = sleep
        self._client = httpx.Client(
            base_url=f"https://{shop_domain}/admin/api/{api_version}",
            headers={"Content-Type": "application/json"},
            timeout=30.0,
            transport=transport,
        )

    def _send(self, operation: str, query: str, variables: dict[str, Any]) -> httpx.Response:
        token = self._tokens.get()
        try:
            response = self._client.post(
                "/graphql.json",
                json={"query": query, "variables": variables, "operationName": operation},
                headers={"X-Shopify-Access-Token": token},
            )
        except httpx.TimeoutException as exc:
            raise ShopifyTransient(f"timeout calling {operation}") from exc
        except httpx.TransportError as exc:
            raise ShopifyTransient(f"transport error calling {operation}: {exc}") from exc
        if response.status_code == 401:
            self._tokens.invalidate(token)
        return response

    def _post(self, operation: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = self._send(operation, query, variables)
        retried = False
        if response.status_code == 401 and self._tokens.refreshable:
            # One retry with a fresh token. A second 401 means the app lost access, which a person has to fix.
            response, retried = self._send(operation, query, variables), True
        if response.status_code in (401, 403):
            raise ShopifyAuthError(
                f"{response.status_code} from Admin API" + (" even with a fresh token" if retried else "")
            )
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
        self._sleep((requested - available) / rate + random.uniform(0, 0.25))  # noqa: S311 - jitter, not security

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
