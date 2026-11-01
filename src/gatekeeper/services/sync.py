"""Full and incremental sync from the store into the local catalog, then audit."""

from __future__ import annotations

from datetime import datetime

from gatekeeper.context import AppContext
from gatekeeper.db import tx
from gatekeeper.domain.product import content_hash
from gatekeeper.services.catalog import (
    active_title_counts,
    product_from_row,
    refresh_findings,
    stale_open_proposals,
    upsert_product,
    write_audit,
)


def sync_catalog(
    ctx: AppContext, *, updated_since: datetime | None = None, actor: str = "worker:sync"
) -> dict[str, int]:
    cursor: str | None = None
    seen = changed = staled = 0
    while True:
        page = ctx.shopify.list_products(cursor=cursor, first=50, updated_since=updated_since)
        with tx(ctx.pool, actor) as conn:
            for product in page.products:
                seen += 1
                result = upsert_product(conn, ctx.shop_id, product)
                if result.product_id is not None and result.changed:
                    changed += 1
                    staled += stale_open_proposals(conn, result.product_id, current_hash=content_hash(product))
        cursor = page.next_cursor
        if cursor is None:
            break
    audited = audit_all(ctx, actor)
    with tx(ctx.pool, actor) as conn:
        write_audit(
            conn,
            actor,
            "sync.finished",
            "shop",
            str(ctx.shop_id),
            ctx.shop_id,
            {"seen": seen, "changed": changed, "staled": staled},
        )
    return {"seen": seen, "changed": changed, "staled": staled, "findings": audited}


def audit_all(ctx: AppContext, actor: str = "worker:audit") -> int:
    with tx(ctx.pool, actor) as conn:
        titles = active_title_counts(conn, ctx.shop_id)
        rows = conn.execute(
            "SELECT * FROM products WHERE shop_id = %s AND deleted_at IS NULL", (ctx.shop_id,)
        ).fetchall()
        return sum(refresh_findings(conn, ctx.shop_id, r["id"], product_from_row(r), titles) for r in rows)
