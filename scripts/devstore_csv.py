"""Write demo products as a Shopify product CSV, to seed a development store through Products > Import.

The demo catalog is shaped like the app's Product model, not like Shopify, so this maps it to the column names of
Shopify's current product CSV template (help.shopify.com, "Using CSV files to import and export products"). Images are
left out because the fixture holds no image files. Products that only exist to make the template script misbehave
(metafield `demo.misbehave`) are skipped: a hosted LLM never reads that metafield, so they test nothing on a real store.

Usage:
  python scripts/devstore_csv.py                      # the default picks, to build/dev-store-products.csv
  python scripts/devstore_csv.py --handles a,b,c      # chosen products
  python scripts/devstore_csv.py --all --out x.csv    # every product that is not a template-only case
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG = REPO_ROOT / "fixtures" / "shopify" / "catalog_demo.json"

# One product per kind of finding worth seeing on a real store: a thin description (A02), a missing SEO description
# (A07) and supplier text with an injected instruction (A13).
DEFAULT_HANDLES = ("trailhead-daypack-20l", "summit-bottle-750ml", "brass-desk-lamp")

COLUMNS = (
    "URL handle",
    "Title",
    "Description",
    "Vendor",
    "Type",
    "Tags",
    "Published on online store",
    "Status",
    "Option1 name",
    "Option1 value",
    "SKU",
    "Price",
    "SEO title",
    "SEO description",
)
# Import limits from the same help page. Longer values are reported, not cut, so the store gets the fixture as it is.
SEO_TITLE_LIMIT = 70
SEO_DESCRIPTION_LIMIT = 320
# The fixture stores option values without option names.
OPTION_NAME = "Variant"
SINGLE_VARIANT = ("Title", "Default Title")


def rows_for(product: dict) -> list[dict[str, str]]:
    """One row per variant. Product-level columns go on the first row only, as Shopify's template asks."""
    variants = product.get("variants") or [{}]
    rows = []
    for index, variant in enumerate(variants):
        options = variant.get("options") or []
        if len(variants) == 1 and not options:
            option_name, option_value = SINGLE_VARIANT
        else:
            if len(options) != 1:
                raise ValueError(f"{product['handle']}: expected one option value per variant, got {options!r}")
            option_name, option_value = OPTION_NAME, options[0]
        row = dict.fromkeys(COLUMNS, "")
        row.update(
            {
                "URL handle": product["handle"],
                "Option1 name": option_name,
                "Option1 value": option_value,
                "SKU": variant.get("sku", ""),
                "Price": str(variant.get("price", "")),
            }
        )
        if index == 0:
            row.update(
                {
                    "Title": product["title"],
                    "Description": product.get("description_html", ""),
                    "Vendor": product.get("vendor", ""),
                    "Type": product.get("product_type", ""),
                    "Tags": ", ".join(product.get("tags") or []),
                    # A development store is not open to shoppers; nothing needs to be on the online store.
                    "Published on online store": "false",
                    "Status": str(product.get("status", "ACTIVE")).lower(),
                    "SEO title": product.get("seo_title", ""),
                    "SEO description": product.get("seo_description", ""),
                }
            )
        rows.append(row)
    return rows


def over_limits(product: dict) -> list[str]:
    problems = []
    if len(product.get("seo_title", "")) > SEO_TITLE_LIMIT:
        problems.append(f"SEO title is {len(product['seo_title'])} characters, the import limit is {SEO_TITLE_LIMIT}")
    if len(product.get("seo_description", "")) > SEO_DESCRIPTION_LIMIT:
        problems.append(
            f"SEO description is {len(product['seo_description'])} characters, the limit is {SEO_DESCRIPTION_LIMIT}"
        )
    return problems


def template_only(product: dict) -> bool:
    return "demo.misbehave" in (product.get("metafields") or {})


def select(catalog: list[dict], handles: list[str] | None) -> list[dict]:
    if handles is None:
        return [p for p in catalog if not template_only(p)]
    by_handle = {p["handle"]: p for p in catalog}
    missing = [h for h in handles if h not in by_handle]
    if missing:
        raise SystemExit(f"not in the demo catalog: {', '.join(missing)}")
    skipped = [h for h in handles if template_only(by_handle[h])]
    if skipped:
        raise SystemExit(f"template-only demo cases, useless on a real store: {', '.join(skipped)}")
    return [by_handle[h] for h in handles]


def write_csv(products: list[dict], out: Path) -> int:
    # All rows first, so a product that cannot be mapped leaves no half-written file behind.
    rows = [row for product in products for row in rows_for(product)]
    # UTF-8 with LF line endings, as the import page asks.
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--handles", help="comma-separated product handles from the demo catalog")
    parser.add_argument("--all", action="store_true", help="every product except the template-only cases")
    # build/ is ignored by git, so the file cannot be committed by accident.
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "build" / "dev-store-products.csv")
    args = parser.parse_args(argv)
    if args.all and args.handles:
        parser.error("pass --handles or --all, not both")

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    handles = None if args.all else [h.strip() for h in (args.handles or ",".join(DEFAULT_HANDLES)).split(",") if h]
    products = select(catalog, handles)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    rows = write_csv(products, args.out)
    print(f"{len(products)} products, {rows} rows -> {args.out}")
    for product in products:
        print(f"  {product['handle']}: {product['title']}")
        for problem in over_limits(product):
            print(f"    warning: {problem}; Shopify may reject or cut it on import", file=sys.stderr)


if __name__ == "__main__":
    main()
