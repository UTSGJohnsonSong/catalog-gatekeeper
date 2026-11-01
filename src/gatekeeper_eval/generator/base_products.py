"""120 clean synthetic base products from hand-written templates. No network and no scraped data."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

SPEC = yaml.safe_load(
    (Path(__file__).resolve().parents[3] / "eval" / "spec" / "rules_spec.yaml").read_text(encoding="utf-8")
)

_CATEGORIES: list[dict[str, Any]] = [
    {
        "kind": "Drinkware",
        "vendor": "Cedar Peak",
        "items": ["Summit Bottle", "Harbor Flask", "Ridge Tumbler", "Trail Canteen", "Fjord Mug"],
        "sizes": ["350 ml", "500 ml", "750 ml"],
        "detail": "keeps drinks at temperature on long days",
        "material": "stainless steel",
    },
    {
        "kind": "Bags",
        "vendor": "Northline",
        "items": ["Atlas Daypack", "Meridian Duffel", "Harbour Tote", "Commuter Sling", "Summit Roll Top"],
        "sizes": ["12 l", "20 l", "30 l"],
        "detail": "carries daily gear with padded straps",
        "material": "recycled nylon",
    },
    {
        "kind": "Apparel",
        "vendor": "Woolhouse",
        "items": ["Merino Crew", "Alpine Quarter Zip", "Harbor Overshirt", "Fieldwork Hoodie", "Coastal Polo"],
        "sizes": ["200 g", "250 g", "300 g"],
        "detail": "layers well and stays soft after washing",
        "material": "merino wool",
    },
    {
        "kind": "Footwear",
        "vendor": "Fieldstep",
        "items": ["Trail Runner", "Chelsea Boot", "Canvas Low", "Moc Slipper", "Approach Shoe"],
        "sizes": ["280 g", "340 g", "410 g"],
        "detail": "grips wet ground and breaks in quickly",
        "material": "full grain leather",
    },
    {
        "kind": "Kitchen",
        "vendor": "Kiln and Co",
        "items": ["Pour Over Cone", "Walnut Board", "Skillet Classic", "Stoneware Bowl", "Enamel Kettle"],
        "sizes": ["1 l", "25 cm", "30 cm"],
        "detail": "cleans up easily and lasts for years",
        "material": "cast iron",
    },
    {
        "kind": "Home",
        "vendor": "Kiln and Co",
        "items": ["Cedar Candle", "Throw Blanket", "Stoneware Planter", "Brass Lamp", "Linen Cushion"],
        "sizes": ["220 g", "150 cm", "40 cm"],
        "detail": "brings a calm finish to any room",
        "material": "organic linen",
    },
    {
        "kind": "Outdoor",
        "vendor": "Northline",
        "items": ["Two Person Tent", "Camp Stool", "Trail Headlamp", "Down Quilt", "Hammock Set"],
        "sizes": ["1 kg", "2 kg", "3 kg"],
        "detail": "packs down small for the trail",
        "material": "ripstop nylon",
    },
    {
        "kind": "Stationery",
        "vendor": "Fable Canvas",
        "items": ["Pocket Notebook", "Pencil Roll", "Fountain Pen", "Desk Pad", "Field Journal"],
        "sizes": ["80 g", "120 g", "160 g"],
        "detail": "feels good in the hand and writes smoothly",
        "material": "waxed canvas",
    },
]
_MODELS = ["X", "K", "R", "T"]


def split_of(base_id: str) -> str:
    """All variants of one base stay in one split, so nothing leaks between dev and test."""
    return "dev" if int(hashlib.sha256(base_id.encode()).hexdigest(), 16) % 2 == 0 else "test"


def build_base_products() -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for c_index, category in enumerate(_CATEGORIES):
        for i_index, item in enumerate(category["items"]):
            for s_index, size in enumerate(category["sizes"]):
                number = len(products) + 1
                base_id = f"base-{number:03d}"
                model = f"{_MODELS[(i_index + s_index) % 4]}{100 + c_index * 10 + i_index + s_index}"
                title = f"{item} {model}"
                gid = f"gid://shopify/Product/{number}"
                price = f"{20 + (number * 7) % 180}.00"
                products.append(
                    {
                        "base_id": base_id,
                        "split": split_of(base_id),
                        "product": {
                            "gid": gid,
                            "handle": title.lower().replace(" ", "-"),
                            "title": title,
                            "status": "ACTIVE",
                            "vendor": category["vendor"],
                            "product_type": category["kind"],
                            "tags": [category["kind"].lower()],
                            "description_html": (
                                f"<p>The {title} {category['detail']}. It is made from {category['material']} "
                                f"and measures {size}.</p><ul><li>Made by {category['vendor']}</li>"
                                f"<li>Part of the {category['kind'].lower()} range</li></ul>"
                            ),
                            "seo_title": f"{title} | {category['vendor']}",
                            "seo_description": f"{title} from {category['vendor']}, made from {category['material']}.",
                            "variants": [
                                {"gid": f"{gid}/v1", "sku": f"SKU-{number}", "price": price, "options": ["Default"]}
                            ],
                            "images": [{"id": f"{gid}/i1", "alt": f"{title} on a plain background"}],
                            "updated_at": "2026-09-01T00:00:00Z",
                            "metafields": {},
                        },
                        "facts": {
                            "title": title,
                            "vendor": category["vendor"],
                            "kind": category["kind"],
                            "size": size,
                            "material": category["material"],
                            "detail": category["detail"],
                            "model": model,
                        },
                    }
                )
    return products


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
