"""Audit rules: detect defects in a product. Pure functions over the domain model."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from gatekeeper.domain.product import Product
from gatekeeper.rules import spec
from gatekeeper.rules.text import audit_tags, injection_hits, norm, strip_html


@dataclass(frozen=True)
class Finding:
    rule_id: str
    subject: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def severity(self) -> str:
        return spec.AUDIT_RULES[self.rule_id]["severity"]

    @property
    def fixable(self) -> bool:
        return spec.AUDIT_RULES[self.rule_id]["fixable"]

    @property
    def fields(self) -> list[str]:
        return spec.AUDIT_RULES[self.rule_id]["fields"]

    @property
    def key(self) -> tuple[str, str]:
        return (self.rule_id, self.subject)


def is_placeholder(visible: str) -> bool:
    normalized = norm(visible).strip(" .!-")
    if normalized in spec.PLACEHOLDERS or "lorem ipsum" in normalized:
        return True
    if len(normalized.split()) > spec.PLACEHOLDER_MAX_WORDS:
        return False
    return any(re.search(pattern, normalized) for pattern in spec.PLACEHOLDER_PATTERNS)


def _a01(p: Product) -> Finding | None:
    return None if strip_html(p.description_html) else Finding("A01", evidence={"visible_chars": 0})


def _a02(p: Product) -> Finding | None:
    visible = strip_html(p.description_html)
    if p.product_type.casefold() in spec.THIN_EXEMPT_TYPES or is_placeholder(visible):
        return None
    if 0 < len(visible) < spec.DESC_THIN_MIN:
        return Finding("A02", evidence={"visible_chars": len(visible), "minimum": spec.DESC_THIN_MIN})
    return None


def _a03(p: Product) -> Finding | None:
    visible = strip_html(p.description_html)
    if visible and is_placeholder(visible):
        return Finding("A03", evidence={"visible_text": visible[:80]})
    return None


def _a04(p: Product) -> Finding | None:
    problems = audit_tags(p.description_html).problems if p.description_html else []
    return Finding("A04", evidence={"problems": problems[:5]}) if problems else None


def _a05(p: Product) -> Finding | None:
    return None if p.seo_title.strip() else Finding("A05")


def _a06(p: Product) -> Finding | None:
    n = len(p.seo_title)
    return Finding("A06", evidence={"chars": n, "maximum": spec.SEO_TITLE_MAX}) if n > spec.SEO_TITLE_MAX else None


def _a07(p: Product) -> Finding | None:
    return None if p.seo_description.strip() else Finding("A07")


def _a08(p: Product) -> Finding | None:
    n = len(p.seo_description)
    return Finding("A08", evidence={"chars": n, "maximum": spec.SEO_DESC_MAX}) if n > spec.SEO_DESC_MAX else None


def _a09(p: Product) -> Finding | None:
    tokens = [t for t in p.title.split() if not any(c.isdigit() for c in t)]
    kept = [t for t in tokens if not (t.isupper() and sum(c.isalpha() for c in t) <= spec.SHOUT_SHORT_TOKEN)]
    letters = [c for t in kept for c in t if c.isalpha()]
    if len(letters) < spec.SHOUT_MIN_LETTERS:
        return None
    ratio = sum(c.isupper() for c in letters) / len(letters)
    return Finding("A09", evidence={"upper_ratio": round(ratio, 3)}) if ratio >= spec.SHOUT_RATIO else None


def _a10(p: Product) -> list[Finding]:
    return [
        Finding("A10", subject=str(i.get("id", "")), evidence={"image": i.get("id")})
        for i in p.images
        if not (i.get("alt") or "").strip()
    ]


def _a12(p: Product) -> list[Finding]:
    if p.status != "ACTIVE":
        return []
    out: list[Finding] = []
    for variant in p.variants:
        try:
            price = Decimal(str(variant.get("price", "0")))
        except InvalidOperation:
            price = Decimal(0)
        if price <= 0:
            out.append(
                Finding("A12", subject=str(variant.get("gid", "")), evidence={"price": str(variant.get("price"))})
            )
    return out


def _a13(p: Product) -> Finding | None:
    hits: dict[str, list[str]] = {}
    for name, value in (
        ("title", p.title),
        ("description_html", strip_html(p.description_html) + " " + p.description_html),
        ("seo_title", p.seo_title),
        ("seo_description", p.seo_description),
    ):
        matches = injection_hits(value)
        if matches:
            hits[name] = matches
    return Finding("A13", evidence={"matches": hits}) if hits else None


_SINGLE: tuple[Callable[[Product], Finding | None], ...] = (_a01, _a02, _a03, _a04, _a05, _a06, _a07, _a08, _a09, _a13)
_MULTI: tuple[Callable[[Product], list[Finding]], ...] = (_a10, _a12)


def title_counts(products: Iterable[Product]) -> Counter[str]:
    return Counter(norm(p.title) for p in products if p.status == "ACTIVE")


def audit_product(p: Product, titles: Counter[str] | None = None) -> list[Finding]:
    """Run every audit rule. `titles` is the shop context for A11 (duplicate titles among ACTIVE products)."""
    findings = [f for rule in _SINGLE if (f := rule(p))]
    for multi in _MULTI:
        findings.extend(multi(p))
    if titles is not None and p.status == "ACTIVE" and titles[norm(p.title)] >= 2:
        findings.append(Finding("A11", subject=norm(p.title), evidence={"shared_by": titles[norm(p.title)]}))
    return findings


def seo_and_description_findings(p: Product) -> list[Finding]:
    """A01-A08 only: the rules a proposal can resolve or regress."""
    fixable_rules = (_a01, _a02, _a03, _a04, _a05, _a06, _a07, _a08)
    return [f for rule in fixable_rules if (f := rule(p))]
