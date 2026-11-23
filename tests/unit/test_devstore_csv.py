"""The development store seed CSV: Shopify's current column names, one row per variant, no template-only cases."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("devstore_csv", REPO_ROOT / "scripts" / "devstore_csv.py")
assert _spec and _spec.loader
devstore_csv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(devstore_csv)

CATALOG = json.loads((REPO_ROOT / "fixtures" / "shopify" / "catalog_demo.json").read_text(encoding="utf-8"))


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_default_picks_exist_and_write_one_row_per_variant(tmp_path: Path) -> None:
    out = tmp_path / "seed.csv"
    devstore_csv.main(["--out", str(out)])
    rows = _read(out)
    by_handle = {p["handle"]: p for p in CATALOG}
    expected = sum(len(by_handle[h]["variants"]) for h in devstore_csv.DEFAULT_HANDLES)
    assert len(rows) == expected
    assert {r["URL handle"] for r in rows} == set(devstore_csv.DEFAULT_HANDLES)
    assert out.read_bytes().count(b"\r") == 0  # LF line endings, as the import page asks


def test_header_uses_current_template_names(tmp_path: Path) -> None:
    out = tmp_path / "seed.csv"
    devstore_csv.write_csv([CATALOG[0]], out)
    header = out.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert header[:3] == ["URL handle", "Title", "Description"]
    assert "SEO description" in header
    assert "Handle" not in header and "Body (HTML)" not in header  # the old template's names


def test_product_columns_only_on_the_first_row() -> None:
    product = next(p for p in CATALOG if len(p["variants"]) > 1)
    first, *rest = devstore_csv.rows_for(product)
    assert first["Title"] == product["title"]
    assert first["Description"] == product["description_html"]
    assert first["Status"] == "active"
    assert first["Published on online store"] == "false"
    for row in rest:
        assert row["URL handle"] == product["handle"]
        assert row["Title"] == "" and row["Description"] == "" and row["SEO title"] == ""
        assert row["Option1 value"] and row["SKU"]
    assert [r["SKU"] for r in [first, *rest]] == [v["sku"] for v in product["variants"]]


def test_a_single_variant_without_options_uses_default_title() -> None:
    product = {**CATALOG[0], "variants": [{"sku": "X-1", "price": "5.00", "options": []}]}
    (row,) = devstore_csv.rows_for(product)
    assert (row["Option1 name"], row["Option1 value"]) == ("Title", "Default Title")


def test_two_option_values_on_one_variant_are_refused() -> None:
    product = {**CATALOG[0], "variants": [{"sku": "X-1", "price": "5.00", "options": ["Red", "Large"]}]}
    with pytest.raises(ValueError, match="one option value"):
        devstore_csv.rows_for(product)


def test_template_only_cases_are_skipped_and_refused_by_name() -> None:
    template_only = [p["handle"] for p in CATALOG if devstore_csv.template_only(p)]
    assert template_only, "the demo catalog should still hold template-only cases"
    assert not set(template_only) & {p["handle"] for p in devstore_csv.select(CATALOG, None)}
    with pytest.raises(SystemExit, match="template-only"):
        devstore_csv.select(CATALOG, [template_only[0]])


def test_unknown_handle_is_refused() -> None:
    with pytest.raises(SystemExit, match="not in the demo catalog"):
        devstore_csv.select(CATALOG, ["no-such-product"])


def test_values_over_the_import_limits_are_reported() -> None:
    product = {**CATALOG[0], "seo_title": "x" * 71, "seo_description": "y" * 321}
    problems = devstore_csv.over_limits(product)
    assert len(problems) == 2
    assert devstore_csv.over_limits(CATALOG[0]) == []
