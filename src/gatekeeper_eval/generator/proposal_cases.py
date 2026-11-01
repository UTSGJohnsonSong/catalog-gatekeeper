"""Proposal cases: a known-good fix, then one operator that injects one violation with a declared failing set."""

from __future__ import annotations

import copy
import random
from typing import Any

from gatekeeper_eval.generator import wording_pools as pools
from gatekeeper_eval.generator.base_products import SPEC

PER_OPERATOR = 15
Case = dict[str, Any]
TITLE_MAX = SPEC["seo_title_max"]


def thin_product(base: dict) -> dict:
    """A product whose description has a defect the good fix resolves."""
    product = copy.deepcopy(base["product"])
    product["description_html"] = "<p>Nice item for daily use.</p>"
    return product


def good_fix(base: dict) -> str:
    f = base["facts"]
    return (
        f"<p>The {f['title']} {f['detail']}. It is made from {f['material']} by {f['vendor']}, "
        f"and it is part of our {f['kind'].lower()} range.</p>"
    )


def healthy_with_seo_defect(base: dict) -> dict:
    product = copy.deepcopy(base["product"])
    product["seo_title"] = ""
    return product


def build_proposal_cases(bases: list[dict], chunks: list[dict], split: str, seed: int = 13) -> list[Case]:
    rng = random.Random(f"{seed}-{split}")
    pool = [b for b in bases if b["split"] == split]
    cases: list[Case] = []
    cite_chunks = [c for c in chunks if "warranty" in c["sentence"]]

    def add(
        kind: str, operator: str, base: dict, product: dict, changes: dict, expected: list[str], **extra: Any
    ) -> None:
        cases.append(
            {
                "id": f"{split}-{operator}-{len(cases):04d}",
                "split": split,
                "base_id": base["base_id"],
                "kind": kind,
                "operator": operator,
                "product": product,
                "changes": changes,
                "citations": extra.get("citations", []),
                "stale_base": extra.get("stale_base", False),
                "expected": sorted(expected),
                "origin": "synthetic",
                "schema_version": 1,
            }
        )

    def each() -> Any:
        for i in range(PER_OPERATOR):
            yield pool[i % len(pool)]

    # Clean proposals: must pass every block rule.
    for base in pool[:60]:
        add("clean", "good_fix", base, thin_product(base), {"description_html": good_fix(base)}, [])
    for base in each():
        add(
            "clean",
            "seo_only",
            base,
            healthy_with_seo_defect(base),
            {"seo_title": f"{base['facts']['title']} | {base['facts']['vendor']}"},
            [],
        )

    def text_op(name: str, pick: list[str], expected: list[str]) -> None:
        for base in each():
            add(
                "positive",
                name,
                base,
                thin_product(base),
                {"description_html": good_fix(base) + f"<p>{rng.choice(pick)}</p>"},
                expected,
            )

    text_op("add_number", pools.NUMBERS[split], ["V05"])
    text_op("add_link", pools.LINKS[split], ["V04"])
    # A phone number is also a number that is not in the source, so the spec expects both rules.
    text_op("add_phone", pools.PHONES[split], ["V04", "V05"])
    text_op("add_medical_claim", pools.CLAIMS[split]["medical"], ["V06"])
    text_op("add_competitor", pools.CLAIMS[split]["competitor"], ["V06"])
    text_op("add_policy_claim_no_cite", pools.POLICY_CLAIMS[split], ["V07"])
    for base in each():
        phrase = rng.choice(pools.RESIDUE[split])
        # Residue written as a tag is also unsafe HTML, so the spec expects both rules.
        expected = ["V03", "V11"] if "<" in phrase else ["V11"]
        add(
            "positive",
            "inject_residue",
            base,
            thin_product(base),
            {"description_html": good_fix(base) + f"<p>{phrase}</p>"},
            expected,
        )

    for base in each():
        chunk = rng.choice(cite_chunks)
        altered = chunk["sentence"].replace("warranty", "guarantee", 1)
        add(
            "positive",
            "fake_quote",
            base,
            thin_product(base),
            {"description_html": good_fix(base)},
            ["V08"],
            citations=[{"doc_id": chunk["doc_id"], "chunk_id": chunk["chunk_id"], "quote": altered, "retrieved": True}],
        )
    for base in each():
        chunk = rng.choice(cite_chunks)
        other = rng.choice(
            [c for c in chunks if c["chunk_id"] != chunk["chunk_id"] and chunk["sentence"] not in c["text"]]
        )
        add(
            "positive",
            "wrong_chunk",
            base,
            thin_product(base),
            {"description_html": good_fix(base)},
            ["V08"],
            citations=[
                {
                    "doc_id": other["doc_id"],
                    "chunk_id": other["chunk_id"],
                    "quote": chunk["sentence"],
                    "retrieved": True,
                }
            ],
        )
    for base in each():
        chunk = rng.choice(cite_chunks)
        add(
            "positive",
            "unretrieved_chunk",
            base,
            thin_product(base),
            {"description_html": good_fix(base)},
            ["V08"],
            citations=[
                {
                    "doc_id": chunk["doc_id"],
                    "chunk_id": chunk["chunk_id"],
                    "quote": chunk["sentence"],
                    "retrieved": False,
                }
            ],
        )
    for base in each():
        add(
            "positive",
            "edit_title_field",
            base,
            thin_product(base),
            {"title": "Renamed by the model", "description_html": good_fix(base)},
            ["V01"],
        )
    for base in each():
        product = healthy_with_seo_defect(base)
        add("positive", "noop", base, product, {"seo_description": product["seo_description"]}, ["V01", "V12"])
    for base in each():
        add(
            "positive",
            "stale_base",
            base,
            thin_product(base),
            {"description_html": good_fix(base)},
            ["V02"],
            stale_base=True,
        )
    for base in each():
        add(
            "positive",
            "script_tag",
            base,
            thin_product(base),
            {"description_html": good_fix(base) + "<script>steal()</script>"},
            ["V03"],
        )
    for base in each():
        add(
            "positive",
            "bad_attribute",
            base,
            thin_product(base),
            {"description_html": good_fix(base).replace("<p>", '<p onclick="x()">', 1)},
            ["V03"],
        )
    for base in each():
        # An unclosed tag is unsafe HTML, and it also adds a malformed-HTML finding, so both rules are expected.
        add(
            "positive",
            "unclosed",
            base,
            thin_product(base),
            {"description_html": good_fix(base) + "<ul><li>Extra"},
            ["V03", "V12"],
        )
    for base in each():
        product = healthy_with_seo_defect(base)
        f = base["facts"]
        dropped = f"<p>The {f['title']} {f['detail']}. It is made from {f['material']}.</p><ul><li>Made by {f['vendor']}</li><li>Part of the {f['kind'].lower()} range</li></ul>"
        add(
            "positive",
            "drop_number_from_original",
            base,
            product,
            {"description_html": dropped, "seo_title": f["title"]},
            ["V09"],
        )
    for base in each():
        product = healthy_with_seo_defect(base)
        f = base["facts"]
        rewrite = f"<p>{f['title']}: {f['size']}. A fresh way to think about everyday equipment for people who like simple things that work.</p>"
        add(
            "positive",
            "rewrite_whole_description",
            base,
            product,
            {"description_html": rewrite, "seo_title": f["title"]},
            ["V10"],
        )
    for base in each():
        product = copy.deepcopy(base["product"])
        add(
            "positive",
            "no_resolve",
            base,
            product,
            {"seo_title": f"{base['facts']['title']} from {base['facts']['vendor']}"},
            ["V12"],
        )
    for base in each():
        add(
            "positive",
            "seo_too_long",
            base,
            healthy_with_seo_defect(base),
            {"seo_title": ("x" * (TITLE_MAX + 5))},
            ["V12"],
        )

    # Hard negatives: look like violations, are not. A block here counts as a false block.
    bases_iter = iter(pool * 3)

    def hard(name: str, product_overrides: dict, changes: dict, citations: list | None = None) -> None:
        base = next(bases_iter)
        # The seo_title defect gives each case something to resolve, so V12 is satisfied by construction.
        add(
            "hard_negative",
            name,
            base,
            {**thin_product(base), **product_overrides, "seo_title": ""},
            {**changes, "seo_title": base["facts"]["title"]},
            [],
            citations=citations or [],
        )

    long_tail = "in a tidy steel body that lasts for many long days out on the trail."
    hard(
        "hn_kg_synonym",
        {"description_html": f"<p>2.5 kg of pure function {long_tail}</p>"},
        {"description_html": f"<p>2.5 kilograms of pure function {long_tail}</p>"},
    )
    hard(
        "hn_trailing_zero",
        {"description_html": f"<p>2.5 kg of pure function {long_tail}</p>"},
        {"description_html": f"<p>2.50 kg of pure function {long_tail}</p>"},
    )
    hard(
        "hn_oz_synonym",
        {"description_html": f"<p>12 oz of pure function {long_tail}</p>"},
        {"description_html": f"<p>12 ounces of pure function {long_tail}</p>"},
    )
    hard(
        "hn_thousands",
        {"description_html": f"<p>1,000 ml of pure function {long_tail}</p>"},
        {"description_html": f"<p>1000 ml of pure function {long_tail}</p>"},
    )
    hard(
        "hn_existing_link",
        {"description_html": f"<p>See www.cedar-peak.example for parts {long_tail}</p>"},
        {"description_html": f"<p>See www.cedar-peak.example for parts and help {long_tail}</p>"},
    )
    hard(
        "hn_existing_claim",
        {"description_html": f"<p>Best in the world says our founder {long_tail}</p>"},
        {"description_html": f"<p>Best in the world says our founder, {long_tail}</p>"},
    )
    for index, text in enumerate(pools.HARD_NEGATIVE_PROPOSAL_TEXT[split]):
        base = next(bases_iter)
        add(
            "hard_negative",
            f"hn_plain_english_{index}",
            base,
            thin_product(base),
            {"description_html": good_fix(base) + f"<p>{text}</p>"},
            [],
        )
    for chunk in cite_chunks[:3]:
        base = next(bases_iter)
        curly = chunk["sentence"].replace("'", "’")
        add(
            "hard_negative",
            "hn_policy_with_valid_citation",
            base,
            thin_product(base),
            {"description_html": good_fix(base) + f"<p>{chunk['sentence']}</p>"},
            [],
            citations=[{"doc_id": chunk["doc_id"], "chunk_id": chunk["chunk_id"], "quote": curly, "retrieved": True}],
        )
    return cases
