"""A store simulator backed by the fixture schema.

It is stricter than real Shopify in one way: `expected_content_hash` is an atomic precondition here,
which Shopify's productUpdate does not document. That lets tests exercise the race; it does not prove
the live adapter closes it. See docs/adr/0006.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from gatekeeper.db import Conn
from gatekeeper.domain.product import ALLOWED_FIELDS, Product, content_hash
from gatekeeper.shopify.port import ProductPage, ShopifyThrottled, ShopifyTransient, UpdateResult, UserError

# Fixture-defined limits, not verified against Shopify.
SEO_TITLE_MAX = 255
SEO_DESCRIPTION_MAX = 512


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class FixtureShopify:
    def __init__(self, pool: ConnectionPool[Conn]):
        self.pool = pool
        self.update_calls = 0
        self._fail_next: list[str] = []
        self.before_update: Callable[[str], None] | None = None

    # Test hooks ---------------------------------------------------------------------------------
    def fail_next(self, count: int, kind: str) -> None:
        self._fail_next = [kind] * count

    def _maybe_fail(self) -> None:
        if self._fail_next:
            kind = self._fail_next.pop(0)
            if kind == "throttle":
                raise ShopifyThrottled(0.0)
            raise ShopifyTransient("injected 5xx")

    def external_edit(self, gid: str, **fields: object) -> None:
        """Simulate a merchant editing the product in the Shopify admin."""
        with self.pool.connection() as conn:
            row = conn.execute("SELECT data FROM fixture.products WHERE gid = %s FOR UPDATE", (gid,)).fetchone()
            if row is None:
                raise KeyError(gid)
            data = dict(row["data"])
            data.update(fields)
            data["updated_at"] = _now_iso()
            conn.execute(
                "UPDATE fixture.products SET data = %s, version = version + 1, updated_at = now() WHERE gid = %s",
                (Jsonb(data), gid),
            )

    # Loading ------------------------------------------------------------------------------------
    def load_catalog(self, products: list[dict]) -> int:
        with self.pool.connection() as conn:
            for data in products:
                conn.execute(
                    "INSERT INTO fixture.products (gid, data) VALUES (%s, %s) ON CONFLICT (gid) DO NOTHING",
                    (data["gid"], Jsonb(data)),
                )
        return len(products)

    def load_file_if_empty(self, path: Path) -> int:
        with self.pool.connection() as conn:
            count = conn.execute("SELECT count(*) AS n FROM fixture.products").fetchone()["n"]
        if count:
            return 0
        return self.load_catalog(json.loads(path.read_text(encoding="utf-8")))

    # Port ---------------------------------------------------------------------------------------
    def list_products(
        self, *, cursor: str | None = None, first: int = 50, updated_since: datetime | None = None
    ) -> ProductPage:
        self._maybe_fail()
        after = base64.b64decode(cursor).decode() if cursor else ""
        params: list[object] = [after]
        where = "gid > %s"
        if updated_since is not None:
            where += " AND updated_at >= %s"
            params.append(updated_since)
        params.append(first + 1)
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"SELECT gid, data FROM fixture.products WHERE {where} ORDER BY gid LIMIT %s", params
            ).fetchall()
        page = rows[:first]
        next_cursor = base64.b64encode(page[-1]["gid"].encode()).decode() if len(rows) > first else None
        return ProductPage([Product.from_dict(r["data"]) for r in page], next_cursor)

    def get_product(self, gid: str) -> Product | None:
        self._maybe_fail()
        with self.pool.connection() as conn:
            row = conn.execute("SELECT data FROM fixture.products WHERE gid = %s", (gid,)).fetchone()
        return Product.from_dict(row["data"]) if row else None

    def ensure_writable(self) -> None:
        return None  # The simulated store takes every write.

    def update_product(
        self, gid: str, fields: Mapping[str, str], *, expected_content_hash: str | None = None
    ) -> UpdateResult:
        self.update_calls += 1
        self._maybe_fail()
        if self.before_update:
            self.before_update(gid)
        errors: list[UserError] = []
        for name in fields:
            if name not in ALLOWED_FIELDS:
                errors.append(UserError([name], "Field is not writable by this app", "INVALID"))
        if len(fields.get("seo_title", "")) > SEO_TITLE_MAX:
            errors.append(UserError(["seo", "title"], "SEO title is too long", "TOO_LONG"))
        if len(fields.get("seo_description", "")) > SEO_DESCRIPTION_MAX:
            errors.append(UserError(["seo", "description"], "SEO description is too long", "TOO_LONG"))
        if errors:
            return UpdateResult(None, errors)
        with self.pool.connection() as conn:
            return self._apply(conn, gid, fields, expected_content_hash)

    @staticmethod
    def _apply(conn: Conn, gid: str, fields: Mapping[str, str], expected: str | None) -> UpdateResult:
        row = conn.execute("SELECT data FROM fixture.products WHERE gid = %s FOR UPDATE", (gid,)).fetchone()
        if row is None:
            return UpdateResult(None, [UserError(["id"], "Product does not exist", "PRODUCT_DOES_NOT_EXIST")])
        current = Product.from_dict(row["data"])
        if expected is not None and content_hash(current) != expected:
            return UpdateResult(None, [UserError(["id"], "Product was modified", "STALE_OBJECT")])
        data = dict(row["data"])
        data.update(fields)
        data["updated_at"] = _now_iso()
        conn.execute(
            "UPDATE fixture.products SET data = %s, version = version + 1, updated_at = now() WHERE gid = %s",
            (Jsonb(data), gid),
        )
        return UpdateResult(Product.from_dict(data))
