"""Full and incremental sync from the store into the local catalog, then audit."""

from __future__ import annotations

import logging
from datetime import datetime

from gatekeeper.context import AppContext
from gatekeeper.db import tx
from gatekeeper.domain.product import content_hash
from gatekeeper.services.catalog import (
    active_title_counts,
    mark_deleted,
    product_from_row,
    refresh_findings,
    stale_open_proposals,
    upsert_product,
    write_audit,
)

log = logging.getLogger("gatekeeper.sync")
PAGE_SIZE = 10


def sync_catalog(
    ctx: AppContext, *, updated_since: datetime | None = None, actor: str = "worker:sync"
) -> dict[str, int]:
    cursor: str | None = None
    seen = changed = staled = deleted = 0
    gids: set[str] = set()
    while True:
        page = ctx.shopify.list_products(cursor=cursor, first=PAGE_SIZE, updated_since=updated_since)
        with tx(ctx.pool, actor) as conn:
            for product in page.products:
                seen += 1
                gids.add(product.gid)
                result = upsert_product(conn, ctx.shop_id, product)
                if result.product_id is not None and result.changed:
                    changed += 1
                    staled += stale_open_proposals(conn, result.product_id, current_hash=content_hash(product))
        cursor = page.next_cursor
        if cursor is None:
            break
    if updated_since is None:
        deleted = _tombstone_missing(ctx, gids, actor)
    audited = audit_all(ctx, actor)
    with tx(ctx.pool, actor) as conn:
        write_audit(
            conn,
            actor,
            "sync.finished",
            "shop",
            str(ctx.shop_id),
            ctx.shop_id,
            {"seen": seen, "changed": changed, "staled": staled, "deleted": deleted},
        )
    return {"seen": seen, "changed": changed, "staled": staled, "deleted": deleted, "findings": audited}


def _tombstone_missing(ctx: AppContext, gids: set[str], actor: str) -> int:
    """A full sync that saw every page knows what no longer exists. An empty result is treated as a fault in the
    store connection, not as a shop with no products, so it never wipes the catalog."""
    with tx(ctx.pool, actor) as conn:
        rows = conn.execute(
            "SELECT id, gid FROM products WHERE shop_id = %s AND deleted_at IS NULL", (ctx.shop_id,)
        ).fetchall()
        if not gids:
            if rows:
                log.error("full sync returned no products while %d are stored; skipping deletion", len(rows))
            return 0
        missing = [r["id"] for r in rows if r["gid"] not in gids]
        for product_id in missing:
            mark_deleted(conn, product_id)
    return len(missing)


def audit_all(ctx: AppContext, actor: str = "worker:audit") -> int:
    with tx(ctx.pool, actor) as conn:
        titles = active_title_counts(conn, ctx.shop_id)
        rows = conn.execute(
            "SELECT * FROM products WHERE shop_id = %s AND deleted_at IS NULL", (ctx.shop_id,)
        ).fetchall()
        return sum(refresh_findings(conn, ctx.shop_id, r["id"], product_from_row(r), titles) for r in rows)
