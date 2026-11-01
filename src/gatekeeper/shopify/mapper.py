"""Map Admin GraphQL product nodes to the domain model."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from gatekeeper.domain.product import Product

_CENT = Decimal("0.01")

FIELD_TO_GRAPHQL = {
    "description_html": "descriptionHtml",
    "seo_title": "seo.title",
    "seo_description": "seo.description",
}


def money(value: Any) -> str:
    return str(Decimal(str(value if value is not None else "0")).quantize(_CENT, rounding=ROUND_HALF_UP))


def product_from_node(node: dict[str, Any]) -> Product:
    seo = node.get("seo") or {}
    variants = [
        {
            "gid": edge["node"]["id"],
            "sku": edge["node"].get("sku") or "",
            "price": money(edge["node"].get("price")),
            "options": [o.get("value", "") for o in edge["node"].get("selectedOptions") or []],
        }
        for edge in (node.get("variants") or {}).get("edges", [])
    ]
    images = [
        {"id": edge["node"]["id"], "alt": edge["node"].get("alt") or ""}
        for edge in (node.get("media") or {}).get("edges", [])
        if edge["node"].get("id")
    ]
    return Product(
        gid=node["id"],
        handle=node.get("handle") or "",
        title=node.get("title") or "",
        status=node.get("status") or "ACTIVE",
        vendor=node.get("vendor") or "",
        product_type=node.get("productType") or "",
        tags=list(node.get("tags") or []),
        description_html=node.get("descriptionHtml") or "",
        seo_title=seo.get("title") or "",
        seo_description=seo.get("description") or "",
        variants=variants,
        images=images,
        updated_at=node.get("updatedAt") or "1970-01-01T00:00:00Z",
    )


def update_input(gid: str, fields: dict[str, str]) -> dict[str, Any]:
    """ProductUpdateInput with absolute values, so a repeated write is harmless."""
    product: dict[str, Any] = {"id": gid}
    if "description_html" in fields:
        product["descriptionHtml"] = fields["description_html"]
    seo: dict[str, str] = {}
    if "seo_title" in fields:
        seo["title"] = fields["seo_title"]
    if "seo_description" in fields:
        seo["description"] = fields["seo_description"]
    if seo:
        product["seo"] = seo
    return product
