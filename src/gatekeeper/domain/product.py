"""The product model and the hashes built on it."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from gatekeeper.domain.canonical import canon_json, html_canon, sha256_hex

# The only fields a proposal may ever change.
ALLOWED_FIELDS = ("description_html", "seo_title", "seo_description")


@dataclass
class Product:
    gid: str
    handle: str
    title: str
    status: str = "ACTIVE"
    vendor: str = ""
    product_type: str = ""
    tags: list[str] = field(default_factory=list)
    description_html: str = ""
    seo_title: str = ""
    seo_description: str = ""
    variants: list[dict[str, Any]] = field(default_factory=list)  # {gid, sku, price: "12.00", options: [..]}
    images: list[dict[str, Any]] = field(default_factory=list)  # {id, alt}
    updated_at: str = "2026-01-01T00:00:00Z"
    metafields: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Product:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def with_changes(self, changes: dict[str, str]) -> Product:
        merged = self.to_dict()
        merged.update({k: v for k, v in changes.items() if k in ALLOWED_FIELDS})
        return Product.from_dict(merged)


def _project(p: Product) -> dict[str, Any]:
    return {
        "title": p.title,
        "vendor": p.vendor,
        "product_type": p.product_type,
        "status": p.status,
        "description_html": html_canon(p.description_html),
        "seo_title": p.seo_title,
        "seo_description": p.seo_description,
        "tags": sorted(p.tags),
        "variants": sorted(
            (
                [v.get("gid", ""), v.get("sku", ""), str(v.get("price", "")), list(v.get("options", []))]
                for v in p.variants
            ),
            key=lambda row: row[0],
        ),
        "images": sorted(([i.get("id", ""), i.get("alt", "")] for i in p.images), key=lambda row: row[0]),
    }


def content_hash(p: Product) -> str:
    """Excludes updatedAt and inventory, so inventory churn never marks a proposal stale."""
    return sha256_hex(canon_json(_project(p)))


def fields_hash(p: Product) -> str:
    """Hash of the writable fields only; used to verify a write landed."""
    return sha256_hex(
        canon_json(
            {
                "description_html": html_canon(p.description_html),
                "seo_title": p.seo_title,
                "seo_description": p.seo_description,
            }
        )
    )


def proposed_hash(proposed: dict[str, str]) -> str:
    return sha256_hex(canon_json(proposed))


def approval_hash(
    *, proposal_id: str, shop_id: int, product_gid: str, base_content_hash: str, proposed: dict[str, str]
) -> str:
    """Binds the human decision to exact content: any drift in these inputs invalidates the approval."""
    return sha256_hex(
        canon_json(
            {
                "v": 1,
                "proposal_id": proposal_id,
                "shop_id": shop_id,
                "product_gid": product_gid,
                "base_content_hash": base_content_hash,
                "proposed": proposed,
            }
        )
    )
