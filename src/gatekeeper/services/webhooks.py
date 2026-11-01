"""A webhook is only a signal: the payload names a product, and the store is refetched for its current state.
Delivery order therefore does not matter, and a forged or replayed payload cannot plant data."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from psycopg.types.json import Jsonb

from gatekeeper.context import AppContext
from gatekeeper.db import tx
from gatekeeper.domain.product import content_hash
from gatekeeper.services.catalog import (
    active_title_counts,
    refresh_findings,
    stale_open_proposals,
    upsert_product,
)

HANDLED_TOPICS = {"products/update", "products/create", "products/delete"}
ACTOR = "webhook"


def record_event(
    pool: Any,
    *,
    webhook_id: str,
    event_id: str | None,
    shop_domain: str,
    topic: str,
    raw_body: bytes,
    triggered_at: str | None,
) -> str:
    """The whole synchronous path: one insert and one enqueue, which fits Shopify's 5 second limit."""
    try:
        payload = json.loads(raw_body)
    except ValueError:
        payload = {"unparseable": True}
    status = "received" if topic in HANDLED_TOPICS else "ignored"
    with tx(pool, ACTOR) as conn:
        row = conn.execute(
            "INSERT INTO webhook_events (webhook_id, event_id, shop_domain, topic, triggered_at, payload, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (webhook_id) DO NOTHING RETURNING id",
            (webhook_id, event_id, shop_domain, topic, triggered_at, Jsonb(payload), status),
        ).fetchone()
        if row is None:
            return "duplicate"
        if status == "ignored":
            return "ignored"
        conn.execute(
            "INSERT INTO jobs (kind, payload, dedupe_key) VALUES ('process_webhook', %s, %s) ON CONFLICT (dedupe_key) DO NOTHING",
            (Jsonb({"event_row_id": row["id"]}), f"wh:{webhook_id}"),
        )
    return "accepted"


def _gid_from_payload(payload: dict[str, Any]) -> str | None:
    gid = payload.get("admin_graphql_api_id")
    if isinstance(gid, str) and gid.startswith("gid://shopify/Product/"):
        return gid
    numeric = payload.get("id")
    if isinstance(numeric, int) and not isinstance(numeric, bool):
        return f"gid://shopify/Product/{numeric}"
    return None


def process_event(ctx: AppContext, event_row_id: int) -> str:
    with tx(ctx.pool, ACTOR) as conn:
        event = conn.execute("SELECT topic, payload FROM webhook_events WHERE id = %s", (event_row_id,)).fetchone()
    if event is None:
        raise KeyError(event_row_id)
    gid = _gid_from_payload(event["payload"])
    if gid is None:
        _mark(ctx, event_row_id, "failed", "payload has no product id")
        return "failed"
    current = ctx.shopify.get_product(gid)
    with tx(ctx.pool, ACTOR) as conn:
        if current is None:
            conn.execute("UPDATE products SET deleted_at = now() WHERE shop_id = %s AND gid = %s", (ctx.shop_id, gid))
            row = conn.execute("SELECT id FROM products WHERE shop_id = %s AND gid = %s", (ctx.shop_id, gid)).fetchone()
            if row:
                stale_open_proposals(conn, row["id"], current_hash=None)
        else:
            result = upsert_product(conn, ctx.shop_id, current)
            if result.product_id is not None and result.changed:
                stale_open_proposals(conn, result.product_id, current_hash=content_hash(current))
                refresh_findings(conn, ctx.shop_id, result.product_id, current, active_title_counts(conn, ctx.shop_id))
        conn.execute(
            "UPDATE webhook_events SET status = 'processed', processed_at = %s WHERE id = %s",
            (datetime.now(UTC), event_row_id),
        )
    return "processed"


def _mark(ctx: AppContext, event_row_id: int, status: str, error: str) -> None:
    with tx(ctx.pool, ACTOR) as conn:
        conn.execute(
            "UPDATE webhook_events SET status = %s, error = %s, processed_at = now() WHERE id = %s",
            (status, error, event_row_id),
        )
