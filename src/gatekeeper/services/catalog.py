"""The app's local copy of the catalog, and the findings derived from it."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb

from gatekeeper.db import Conn
from gatekeeper.domain.product import Product, content_hash
from gatekeeper.rules.audit import audit_product
from gatekeeper.rules.spec import RULE_VERSION
from gatekeeper.rules.text import norm


def product_from_row(row: dict[str, Any]) -> Product:
    return Product(
        gid=row["gid"],
        handle=row["handle"],
        title=row["title"],
        status=row["status"],
        vendor=row["vendor"],
        product_type=row["product_type"],
        tags=list(row["tags"]),
        description_html=row["description_html"],
        seo_title=row["seo_title"],
        seo_description=row["seo_description"],
        variants=row["variants"],
        images=row["images"],
        updated_at=row["shopify_updated_at"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        metafields=row.get("metafields") or {},
    )


@dataclass(frozen=True)
class UpsertResult:
    product_id: int | None  # None when an older delivery lost to the >= guard
    changed: bool


def upsert_product(conn: Conn, shop_id: int, p: Product) -> UpsertResult:
    """The >= guard means an out-of-order refetch can never overwrite newer data with older data."""
    new_hash = content_hash(p)
    previous = conn.execute(
        "SELECT content_hash FROM products WHERE shop_id = %s AND gid = %s", (shop_id, p.gid)
    ).fetchone()
    row = conn.execute(
        """
        INSERT INTO products (shop_id, gid, handle, title, vendor, product_type, status, tags, description_html,
                              seo_title, seo_description, variants, images, metafields, shopify_updated_at, content_hash)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (shop_id, gid) DO UPDATE SET
          handle = EXCLUDED.handle, title = EXCLUDED.title, vendor = EXCLUDED.vendor,
          product_type = EXCLUDED.product_type, status = EXCLUDED.status, tags = EXCLUDED.tags,
          description_html = EXCLUDED.description_html, seo_title = EXCLUDED.seo_title,
          seo_description = EXCLUDED.seo_description, variants = EXCLUDED.variants, images = EXCLUDED.images,
          metafields = EXCLUDED.metafields, shopify_updated_at = EXCLUDED.shopify_updated_at,
          content_hash = EXCLUDED.content_hash, synced_at = now(), deleted_at = NULL
        WHERE EXCLUDED.shopify_updated_at >= products.shopify_updated_at
        RETURNING id
        """,
        (
            shop_id,
            p.gid,
            p.handle,
            p.title,
            p.vendor,
            p.product_type,
            p.status,
            p.tags,
            p.description_html,
            p.seo_title,
            p.seo_description,
            Jsonb(p.variants),
            Jsonb(p.images),
            Jsonb(p.metafields),
            p.updated_at,
            new_hash,
        ),
    ).fetchone()
    if row is None:
        return UpsertResult(None, False)
    return UpsertResult(row["id"], previous is None or previous["content_hash"] != new_hash)


def stale_open_proposals(conn: Conn, product_id: int, *, current_hash: str | None) -> int:
    """A proposal built on an old base can no longer be approved. 'applying' belongs to the writer."""
    cursor = conn.execute(
        "UPDATE proposals SET status = 'stale' WHERE product_id = %s "
        "AND status IN ('pending_review','approved') AND base_content_hash IS DISTINCT FROM %s",
        (product_id, current_hash),
    )
    return cursor.rowcount


def active_title_counts(conn: Conn, shop_id: int) -> Counter[str]:
    rows = conn.execute(
        "SELECT title FROM products WHERE shop_id = %s AND status = 'ACTIVE' AND deleted_at IS NULL", (shop_id,)
    ).fetchall()
    return Counter(norm(r["title"]) for r in rows)


def refresh_findings(conn: Conn, shop_id: int, product_id: int, p: Product, titles: Counter[str]) -> int:
    findings = audit_product(p, titles)
    hash_now = content_hash(p)
    keep: list[tuple[str, str]] = []
    for f in findings:
        keep.append(f.key)
        conn.execute(
            """
            INSERT INTO findings (shop_id, product_id, rule_id, rule_version, severity, fixable, subject, fields, evidence, product_hash)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (product_id, rule_id, subject) DO UPDATE SET
              status = 'open', evidence = EXCLUDED.evidence, product_hash = EXCLUDED.product_hash,
              last_seen = now(), resolved_at = NULL
            """,
            (
                shop_id,
                product_id,
                f.rule_id,
                RULE_VERSION,
                f.severity,
                f.fixable,
                f.subject,
                f.fields,
                Jsonb(f.evidence),
                hash_now,
            ),
        )
    open_rows = conn.execute(
        "SELECT id, rule_id, subject FROM findings WHERE product_id = %s AND status = 'open'", (product_id,)
    ).fetchall()
    for row in open_rows:
        if (row["rule_id"], row["subject"]) not in keep:
            conn.execute("UPDATE findings SET status = 'resolved', resolved_at = now() WHERE id = %s", (row["id"],))
    return len(findings)


def write_audit(
    conn: Conn,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    shop_id: int | None,
    detail: dict | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (actor, action, entity_type, entity_id, shop_id, detail) VALUES (%s,%s,%s,%s,%s,%s)",
        (actor, action, entity_type, entity_id, shop_id, Jsonb(detail or {})),
    )
