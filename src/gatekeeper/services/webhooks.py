"""A webhook is only a signal: the payload names a product, and the store is refetched for its current state.
Delivery order therefore does not matter, and a forged or replayed payload cannot plant data."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from psycopg.types.json import Jsonb

from gatekeeper.context import AppContext
from gatekeeper.db import tx
from gatekeeper.domain.product import content_hash
from gatekeeper.services import shop_lifecycle
from gatekeeper.services.catalog import (
    active_title_counts,
    mark_deleted,
    refresh_findings,
    stale_open_proposals,
    upsert_product,
)

log = logging.getLogger("gatekeeper.webhooks")
PRODUCT_TOPICS = {"products/update", "products/create", "products/delete"}
# The three privacy webhooks every App Store app must answer, and the uninstall notice.
CUSTOMER_TOPICS = {"customers/data_request", "customers/redact"}
LIFECYCLE_TOPICS = {"app/uninstalled", "shop/redact"} | CUSTOMER_TOPICS
HANDLED_TOPICS = PRODUCT_TOPICS | LIFECYCLE_TOPICS
ACTOR = "webhook"


def _parse_time(value: str | None) -> datetime | None:
    """The header is outside the signature and is informational, so a bad value is dropped, not a 500."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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
    if topic in LIFECYCLE_TOPICS:
        # Not kept: customer requests name the customer, and the uninstall notice carries the merchant's contact
        # details. The topic alone says what to do.
        payload: Any = {"not_stored": True}
    else:
        try:
            payload = json.loads(raw_body)
        except ValueError:
            payload = {"unparseable": True}
    status = "received" if topic in HANDLED_TOPICS else "ignored"
    with tx(pool, ACTOR) as conn:
        row = conn.execute(
            "INSERT INTO webhook_events (webhook_id, event_id, shop_domain, topic, triggered_at, payload, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (webhook_id) DO NOTHING RETURNING id",
            (webhook_id, event_id, shop_domain, topic, _parse_time(triggered_at), Jsonb(payload), status),
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


def _gid_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
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
    if event["topic"] in LIFECYCLE_TOPICS:
        _handle_lifecycle(ctx, event["topic"])
        # shop/redact deletes this row with the rest of the store's events, so the update may match nothing.
        _mark(ctx, event_row_id, "processed", None)
        return "processed"
    gid = _gid_from_payload(event["payload"])
    if gid is None:
        log.warning("signed webhook names no product", extra={"webhook_id": event_row_id})
        _mark(ctx, event_row_id, "failed", "payload has no product id")
        return "failed"
    current = ctx.shopify.get_product(gid)
    with tx(ctx.pool, ACTOR) as conn:
        if current is None:
            row = conn.execute("SELECT id FROM products WHERE shop_id = %s AND gid = %s", (ctx.shop_id, gid)).fetchone()
            if row:
                mark_deleted(conn, row["id"])
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


def _handle_lifecycle(ctx: AppContext, topic: str) -> None:
    if topic == "app/uninstalled":
        shop_lifecycle.mark_uninstalled(ctx)
    elif topic == "shop/redact":
        counts = shop_lifecycle.erase_shop_data(ctx)
        log.info("store data erased after shop/redact: %s", counts)
    else:
        shop_lifecycle.acknowledge_customer_request(ctx, topic)


def _mark(ctx: AppContext, event_row_id: int, status: str, error: str | None) -> None:
    with tx(ctx.pool, ACTOR) as conn:
        conn.execute(
            "UPDATE webhook_events SET status = %s, error = %s, processed_at = now() WHERE id = %s",
            (status, error, event_row_id),
        )
