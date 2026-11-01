"""Audit cases: a clean base plus one deterministic mutation. Expected findings are declared by the operator,
from the specification, so a label is true by construction and not derived from rule output."""

from __future__ import annotations

import copy
import random
from collections.abc import Callable
from typing import Any

from gatekeeper_eval.generator import wording_pools as pools
from gatekeeper_eval.generator.base_products import SPEC

POSITIVES_PER_RULE = 30
Case = dict[str, Any]
THIN_MIN, TITLE_MAX, DESC_MAX = SPEC["desc_thin_min"], SPEC["seo_title_max"], SPEC["seo_desc_max"]


def _pad(text: str, length: int) -> str:
    return (text + " " + "and more detail about this product " * 12)[:length].rstrip() + "x" * 0


def _exact(text: str, length: int) -> str:
    padded = _pad(text, length + 40)[:length]
    return padded if len(padded) == length and not padded.endswith(" ") else padded[:-1] + "x"


Operator = Callable[[dict, dict, random.Random, str, list[dict]], tuple[dict, list[str]]]


def blank_desc(p, f, r, split, others):
    p["description_html"] = r.choice(["", "   ", "<p></p>", "<p> </p>"])
    return {}, ["A01"]


def thin_desc(p, f, r, split, others):
    sentence = r.choice(pools.THIN_SENTENCES[split])
    p["description_html"] = f"<p>{sentence}</p>"
    assert 10 <= len(sentence) < THIN_MIN
    return {}, ["A02"]


def placeholder_desc(p, f, r, split, others):
    p["description_html"] = f"<p>{r.choice(pools.PLACEHOLDERS[split])}</p>"
    return {}, ["A03"]


def unclose_tag(p, f, r, split, others):
    p["description_html"] = r.choice(
        [
            p["description_html"].replace("</ul>", "", 1),
            p["description_html"] + "</div>",
            p["description_html"].replace("<p>", "<p><strong>", 1),
        ]
    )
    return {}, ["A04"]


def blank_seo_title(p, f, r, split, others):
    p["seo_title"] = r.choice(["", "  "])
    return {}, ["A05"]


def long_seo_title(p, f, r, split, others):
    p["seo_title"] = _exact(f["title"], r.randint(TITLE_MAX + 1, TITLE_MAX + 50))
    return {}, ["A06"]


def blank_seo_desc(p, f, r, split, others):
    p["seo_description"] = r.choice(["", " "])
    return {}, ["A07"]


def long_seo_desc(p, f, r, split, others):
    p["seo_description"] = _exact(f["title"], r.randint(DESC_MAX + 1, DESC_MAX + 100))
    return {}, ["A08"]


def shout_title(p, f, r, split, others):
    p["title"] = f"{f['title']} {f['kind']} Collection".upper()
    return {}, ["A09"]


def blank_alt(p, f, r, split, others):
    p["images"][0]["alt"] = r.choice(["", " "])
    return {}, ["A10"]


def dup_title(p, f, r, split, others):
    other = r.choice(others)
    title = other["product"]["title"]
    p["title"] = r.choice([title, title.lower(), f"  {title}"])
    return {"shop_titles": [title]}, ["A11"]


def zero_price(p, f, r, split, others):
    p["variants"][0]["price"] = r.choice(["0.00", "-1.00", "0"])
    return {}, ["A12"]


def inject_text(p, f, r, split, others):
    if r.random() < 0.2:
        p["description_html"] = p["description_html"].replace("</p>", "‮ reversed text</p>", 1)
    else:
        p["description_html"] = p["description_html"].replace("</p>", f" {r.choice(pools.INJECTIONS[split])}</p>", 1)
    return {}, ["A13"]


OPERATORS: dict[str, Operator] = {
    "A01": blank_desc,
    "A02": thin_desc,
    "A03": placeholder_desc,
    "A04": unclose_tag,
    "A05": blank_seo_title,
    "A06": long_seo_title,
    "A07": blank_seo_desc,
    "A08": long_seo_desc,
    "A09": shout_title,
    "A10": blank_alt,
    "A11": dup_title,
    "A12": zero_price,
    "A13": inject_text,
}


def _boundaries(base: dict) -> list[tuple[str, dict, list[str]]]:
    """Boundary cases test the rules against the spec's numbers, not against their own constants."""
    title = base["facts"]["title"]
    return [
        ("boundary_title_at_max", {"seo_title": _exact(title, TITLE_MAX)}, []),
        ("boundary_title_over_max", {"seo_title": _exact(title, TITLE_MAX + 1)}, ["A06"]),
        ("boundary_desc_at_max", {"seo_description": _exact(title, DESC_MAX)}, []),
        ("boundary_desc_over_max", {"seo_description": _exact(title, DESC_MAX + 1)}, ["A08"]),
        ("boundary_body_at_min", {"description_html": f"<p>{'x' * THIN_MIN}</p>"}, []),
        ("boundary_body_under_min", {"description_html": f"<p>{'x' * (THIN_MIN - 1)}</p>"}, ["A02"]),
    ]


def _hard_negatives(split: str) -> list[tuple[str, dict, list[str]]]:
    """Each looks like a defect and is not one. Hand-written; the expected set follows from the spec."""
    long_body = "and it keeps its shape through a full season of regular use outdoors."
    shared = [
        ("hn_a02_gift_card", {"product_type": "Gift Card", "description_html": "<p>Digital gift card.</p>"}, []),
        ("hn_a04_void_tags", {"description_html": f"<p>A warm jacket with a line<br>break {long_body}</p>"}, []),
        ("hn_a04_implied_end", {"description_html": f"<p>A warm jacket {long_body}<p>Second paragraph here."}, []),
        (
            "hn_a04_nested_lists",
            {"description_html": f"<ul><li>Outer item {long_body}<ul><li>Inner</li></ul></li></ul>"},
            [],
        ),
        ("hn_a09_acronyms", {"title": "USB-C HUB 65W Charger"}, []),
        ("hn_a09_short_caps", {"title": "NASA UFO"}, []),
        (
            "hn_a12_draft_zero",
            {"status": "DRAFT", "variants": [{"gid": "v", "sku": "s", "price": "0.00", "options": []}]},
            [],
        ),
    ]
    if split == "dev":
        shared += [
            (
                "hn_a03_brand_lorem",
                {"description_html": f"<p>Lorem is the family brand behind this blanket {long_body}</p>"},
                [],
            ),
            (
                "hn_a03_tbd_in_sentence",
                {"description_html": f"<p>The final colour is TBD by the buyer at checkout, {long_body}</p>"},
                [],
            ),
        ]
    else:
        shared += [
            (
                "hn_a03_test_in_sentence",
                {"description_html": f"<p>We test each seam by hand before it ships, {long_body}</p>"},
                [],
            ),
            (
                "hn_a03_sample_size",
                {"description_html": f"<p>Sample size bottle for trying a new soap, {long_body}</p>"},
                [],
            ),
        ]
    shared += [
        (f"hn_a13_{i}", {"description_html": f"<p>{text}</p>"}, [])
        for i, text in enumerate(pools.HARD_NEGATIVE_INJECTION[split])
    ]
    shared.append(
        (
            "hn_a13_zwj",
            {
                "description_html": f"<p>A family emoji \U0001f468‍\U0001f469‍\U0001f467 sits on the label, {long_body}</p>"
            },
            [],
        )
    )
    return shared


def build_audit_cases(bases: list[dict], split: str, seed: int = 11) -> list[Case]:
    rng = random.Random(f"{seed}-{split}")
    pool = [b for b in bases if b["split"] == split]
    cases: list[Case] = []

    def add(
        kind: str, name: str, base: dict, product: dict, expected: list[str], shop_titles: list[str] | None = None
    ) -> None:
        cases.append(
            {
                "id": f"{split}-{name}-{len(cases):04d}",
                "split": split,
                "base_id": base["base_id"],
                "kind": kind,
                "operator": name,
                "product": product,
                "shop_titles": shop_titles or [],
                "expected": sorted(expected),
                "origin": "synthetic",
                "schema_version": 1,
            }
        )

    for operator in OPERATORS.values():
        for i in range(POSITIVES_PER_RULE):
            base = pool[i % len(pool)]
            product = copy.deepcopy(base["product"])
            others = [b for b in pool if b["base_id"] != base["base_id"]]
            extra, expected = operator(product, base["facts"], rng, split, others)
            add("positive", operator.__name__, base, product, expected, extra.get("shop_titles"))
    for base in pool[:60]:
        add("clean", "unmutated", base, copy.deepcopy(base["product"]), [])
    for index, (name, overrides, expected) in enumerate(_boundaries(pool[0]) + _hard_negatives(split)):
        base = pool[index % len(pool)]
        add("hard_negative", name, base, {**copy.deepcopy(base["product"]), **overrides}, expected)
    return cases
