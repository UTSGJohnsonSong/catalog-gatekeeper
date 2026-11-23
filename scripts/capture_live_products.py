"""Read one page of products from a real development store and save Shopify's raw answer as a fixture.

Read only: it sends the same `Products` query as the live adapter and never a mutation. It gets its token the same
way the adapter does, with the client credentials grant. The saved file holds the GraphQL `data` and `extensions`
as Shopify sent them, plus the API version and the time. It holds no token, and the shop domain is replaced in any
case or encoding, so the file can be committed and compared with the mock in `tests/integration/test_shopify_adapters.py`.
If the store's bare handle still appears anywhere, for example as a default vendor, nothing is saved.

Every product is also run through the adapter's mapper and the audit rules, and the script prints what it found.
It exits with status 1 when nothing could be saved, when the store returned no products, or when the mapper cannot
read a product (the raw answer is kept then, to see what Shopify sent).

Needs SHOPIFY_SHOP_DOMAIN, SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET in the environment. Optional:
SHOPIFY_API_VERSION (default: the version the app uses).

Usage:
  python scripts/capture_live_products.py                # first 10 products
  python scripts/capture_live_products.py --first 25
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx

from gatekeeper.config import ADMIN_API_VERSION, MYSHOPIFY_DOMAIN
from gatekeeper.rules.audit import audit_product, title_counts
from gatekeeper.shopify.live import QUERY_PRODUCTS, ClientCredentialsToken
from gatekeeper.shopify.mapper import product_from_node
from gatekeeper.shopify.port import ShopifyError

REPO_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_DIR = REPO_ROOT / "fixtures" / "shopify" / "live_capture"
REDACTED_DOMAIN = "dev-store.myshopify.com"
# What a mapper or a malformed answer can raise. Decimal errors are ArithmeticError, not ValueError.
MAPPING_ERRORS = (KeyError, TypeError, ValueError, AttributeError, ArithmeticError)


def redact(text: str, shop_domain: str) -> str:
    """Replace the shop's domain in any case, plain or with the dots percent-encoded."""
    handle = shop_domain.split(".", 1)[0]
    dot = r"(?:\.|%2e)"
    pattern = re.escape(handle) + dot + "myshopify" + dot + "com"
    return re.sub(pattern, REDACTED_DOMAIN, text, flags=re.IGNORECASE)


def handle_paths(value: Any, handle: str, path: str = "$") -> list[str]:
    """Where the store's handle still appears, case-insensitive and URL-decoded, as JSON paths."""
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in handle_paths(v, handle, f"{path}.{k}")]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in handle_paths(v, handle, f"{path}[{i}]")]
    # The replacement text is left out, so a handle like "dev" does not match "dev-store.myshopify.com".
    if isinstance(value, str) and handle.lower() in unquote(value).lower().replace(REDACTED_DOMAIN, ""):
        return [path]
    return []


def capture(
    *,
    shop_domain: str,
    client_id: str,
    client_secret: str,
    api_version: str,
    first: int,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    tokens = ClientCredentialsToken(
        shop_domain=shop_domain, client_id=client_id, client_secret=client_secret, transport=transport
    )
    with httpx.Client(
        base_url=f"https://{shop_domain}/admin/api/{api_version}", timeout=30.0, transport=transport
    ) as c:
        response = c.post(
            "/graphql.json",
            json={"query": QUERY_PRODUCTS, "variables": {"first": first}, "operationName": "Products"},
            headers={"X-Shopify-Access-Token": tokens.get(), "Content-Type": "application/json"},
        )
    if response.status_code != 200:
        # The body is not printed: an error page can echo the request.
        raise SystemExit(f"Admin API answered HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        raise SystemExit("Admin API answered 200 with a body that is not JSON") from None
    if not isinstance(body, dict):
        raise SystemExit("Admin API answered 200 with JSON that is not an object")
    if body.get("errors"):
        raise SystemExit(f"Admin API returned errors: {[e.get('message') for e in body['errors']]}")
    return {
        "captured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "api_version": api_version,
        "shop_domain": REDACTED_DOMAIN,
        "operation": "Products",
        "variables": {"first": first},
        "response": {"data": body.get("data"), "extensions": body.get("extensions")},
    }


def edges_of(saved: dict[str, Any]) -> list[dict[str, Any]]:
    return ((saved["response"]["data"] or {}).get("products") or {}).get("edges") or []


def report(saved: dict[str, Any]) -> list[str]:
    """Map every node and audit it. Returns one line per product; raises if a node cannot be mapped."""
    products = [product_from_node(edge["node"]) for edge in edges_of(saved)]
    titles = title_counts(products)
    lines = []
    for product in products:
        rules = sorted({finding.rule_id for finding in audit_product(product, titles)})
        lines.append(
            f"{product.handle}: {len(product.variants)} variants, {len(product.images)} media, "
            f"findings {', '.join(rules) or 'none'}"
        )
    return lines


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--first", type=int, default=10, help="products to read, at most 50")
    parser.add_argument("--out-dir", type=Path, default=CAPTURE_DIR)
    args = parser.parse_args(argv)
    if not 1 <= args.first <= 50:
        parser.error("--first must be between 1 and 50")

    missing = [
        k for k in ("SHOPIFY_SHOP_DOMAIN", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET") if not os.environ.get(k)
    ]
    if missing:
        parser.error(f"set {', '.join(missing)} first")
    shop_domain = os.environ["SHOPIFY_SHOP_DOMAIN"]
    if not MYSHOPIFY_DOMAIN.fullmatch(shop_domain):
        parser.error("SHOPIFY_SHOP_DOMAIN must be the store's myshopify.com domain")

    try:
        saved = capture(
            shop_domain=shop_domain,
            client_id=os.environ["SHOPIFY_CLIENT_ID"],
            client_secret=os.environ["SHOPIFY_CLIENT_SECRET"],
            api_version=os.environ.get("SHOPIFY_API_VERSION") or ADMIN_API_VERSION,
            first=args.first,
            transport=transport,
        )
    except (ShopifyError, httpx.HTTPError) as exc:
        # Transport errors can name the host, for example a TLS mismatch, so the message is redacted too.
        raise SystemExit(f"could not read the store: {type(exc).__name__}: {redact(str(exc), shop_domain)}") from None

    # The domain can also appear inside the data, for example in an online store URL.
    text = redact(json.dumps(saved, indent=1, ensure_ascii=False), shop_domain)
    redacted = json.loads(text)
    leftover = handle_paths(redacted, shop_domain.split(".", 1)[0])
    if leftover:
        # The bare handle may be an ordinary word, so it is not replaced blindly. A person decides.
        print(f"not saved: the store's handle still appears at {', '.join(leftover[:5])}", file=sys.stderr)
        print("edit those fields in the store admin, for example the vendor, and run again", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / f"products-{saved['captured_at'].replace(':', '')}.json"
    out.write_text(text + "\n", encoding="utf-8")

    edges = edges_of(redacted)
    if not edges:
        print(f"saved {out}, but it holds no products; import the CSV first", file=sys.stderr)
        return 1
    try:
        lines = report(redacted)
    except MAPPING_ERRORS as exc:
        print(f"saved {out}, but the mapper could not read a product: {exc!r}", file=sys.stderr)
        return 1
    print(f"saved {out}")
    for line in lines:
        print(f"  {line}")
    page = (redacted["response"]["data"].get("products") or {}).get("pageInfo") or {}
    if page.get("hasNextPage"):
        print(
            f"  more products exist past these {len(edges)}; the oldest come first, so use --first 50", file=sys.stderr
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
