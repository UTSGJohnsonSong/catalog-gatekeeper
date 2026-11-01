"""Proposal validators: deterministic checks a fix must pass. Every validator always runs, so the reviewer
sees all evidence. "New" means present in the proposed text and absent from the current product text."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from gatekeeper.domain.product import ALLOWED_FIELDS, Product, content_hash
from gatekeeper.rules import spec
from gatekeeper.rules.audit import is_placeholder, seo_and_description_findings
from gatekeeper.rules.text import (
    audit_tags,
    claims,
    contains_phrase,
    injection_hits,
    model_tokens,
    norm,
    numbers_with_units,
    strip_html,
)


@dataclass(frozen=True)
class ValidatorResult:
    rule_id: str
    passed: bool
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def severity(self) -> str:
        return spec.VALIDATORS[self.rule_id]["severity"]

    @property
    def blocks(self) -> bool:
        return self.severity == "block" and not self.passed


@dataclass(frozen=True)
class CheckedCitation:
    quote: str
    ok: bool
    reason: str | None = None
    chunk_id: str = ""


@dataclass(frozen=True)
class ProposalContext:
    product: Product
    changes: dict[str, Any]
    base_content_hash: str
    citations: tuple[CheckedCitation, ...] = ()
    open_fixable: tuple[str, ...] | None = None  # None: derive from the product


def _proposed_visible(changes: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ALLOWED_FIELDS:
        value = changes.get(key)
        if isinstance(value, str):
            parts.append(strip_html(value) if key == "description_html" else value)
    return " ".join(parts)


def _current_text(p: Product) -> str:
    return " ".join((p.title, strip_html(p.description_html), p.description_html, p.seo_title, p.seo_description))


def _fact_text(ctx: ProposalContext) -> str:
    p = ctx.product
    options = " ".join(str(o) for v in p.variants for o in v.get("options", []))
    prices = " ".join(str(v.get("price", "")) for v in p.variants)
    quotes = " ".join(c.quote for c in ctx.citations if c.ok)
    return " ".join((_current_text(p), p.vendor, p.product_type, " ".join(p.tags), options, prices, quotes))


def v01_field_allowlist(ctx: ProposalContext) -> ValidatorResult:
    problems: list[str] = []
    if not ctx.changes:
        problems.append("no fields changed")
    for key, value in ctx.changes.items():
        if key not in ALLOWED_FIELDS:
            problems.append(f"field not allowed: {key}")
        elif not isinstance(value, str):
            problems.append(f"{key} must be a string")
        elif value == getattr(ctx.product, key):
            problems.append(f"{key} is unchanged")
    return ValidatorResult("V01", not problems, {"problems": problems})


def v02_base_hash_current(ctx: ProposalContext) -> ValidatorResult:
    current = content_hash(ctx.product)
    return ValidatorResult(
        "V02", current == ctx.base_content_hash, {"base": ctx.base_content_hash[:12], "current": current[:12]}
    )


def v03_html_safe(ctx: ProposalContext) -> ValidatorResult:
    problems: list[str] = []
    description = ctx.changes.get("description_html")
    if isinstance(description, str):
        audit = audit_tags(description)
        problems.extend(f"tag not allowed: <{t}>" for t in sorted(set(audit.tags) - spec.ALLOWED_TAGS))
        problems.extend(f"attribute not allowed: {name} on <{tag}>" for tag, name in audit.attrs)
        problems.extend(audit.problems)
    for key in ("seo_title", "seo_description"):
        value = ctx.changes.get(key)
        if isinstance(value, str) and ("<" in value or ">" in value):
            problems.append(f"{key} contains angle brackets")
    return ValidatorResult("V03", not problems, {"problems": problems[:8]})


_URL = re.compile(
    r"(?i)\b(?:https?://|www\.)[^\s<>\"']+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|co|ca|shop|store|info|biz|xyz)\b"
)
_EMAIL = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{5,}\d(?![\w])")


def _contacts(text: str) -> set[str]:
    found = {m.casefold().rstrip(".,;)") for m in _URL.findall(text)}
    found |= {m.casefold() for m in _EMAIL.findall(text)}
    for match in _PHONE.findall(text):
        digits = re.sub(r"\D", "", match)
        if len(digits) >= 7:
            found.add(digits)
    return found


def v04_no_new_links(ctx: ProposalContext) -> ValidatorResult:
    raw = " ".join(v for k, v in ctx.changes.items() if isinstance(v, str) and k in ALLOWED_FIELDS)
    new = _contacts(raw) - _contacts(_fact_text(ctx))
    return ValidatorResult("V04", not new, {"new": sorted(new)[:8]})


def v05_number_fidelity(ctx: ProposalContext) -> ValidatorResult:
    facts = numbers_with_units(_fact_text(ctx))
    fact_numbers = {num for num, _ in facts}
    invented: list[str] = []
    for number, unit in sorted(numbers_with_units(_proposed_visible(ctx.changes)), key=str):
        if unit is None:
            if number not in fact_numbers:
                invented.append(number)
        elif (number, unit) not in facts:
            invented.append(f"{number} {unit}")
    return ValidatorResult("V05", not invented, {"not_in_source": invented[:8]})


def v06_hard_claims(ctx: ProposalContext) -> ValidatorResult:
    new_text = norm(_proposed_visible(ctx.changes))
    current = norm(_current_text(ctx.product))
    hits: dict[str, list[str]] = {}
    for category, phrases in claims().items():
        matched = [p for p in phrases if contains_phrase(new_text, str(p)) and not contains_phrase(current, str(p))]
        if matched:
            hits[category] = matched
    return ValidatorResult("V06", not hits, {"claims": hits})


def v07_policy_claim_needs_citation(ctx: ProposalContext) -> ValidatorResult:
    new_text = norm(_proposed_visible(ctx.changes))
    current = norm(_current_text(ctx.product))
    new_terms = [t for t in spec.POLICY_TERMS if t in new_text and t not in current]
    verified = " ".join(norm(c.quote) for c in ctx.citations if c.ok)
    unsupported = [t for t in new_terms if t not in verified]
    return ValidatorResult("V07", not unsupported, {"unsupported_terms": unsupported, "new_terms": new_terms})


def v08_citations(ctx: ProposalContext) -> ValidatorResult:
    bad = [{"quote": c.quote[:80], "reason": c.reason, "chunk_id": c.chunk_id} for c in ctx.citations if not c.ok]
    return ValidatorResult("V08", not bad, {"failed": bad[:5], "verified": sum(c.ok for c in ctx.citations)})


def v09_fact_preservation(ctx: ProposalContext) -> ValidatorResult:
    new_description = ctx.changes.get("description_html")
    if not isinstance(new_description, str):
        return ValidatorResult("V09", True, {"skipped": "description unchanged"})
    visible = strip_html(ctx.product.description_html)
    if len(visible) < spec.DESC_THIN_MIN or is_placeholder(visible):
        return ValidatorResult("V09", True, {"skipped": "original has no usable facts"})
    proposed_text = _proposed_visible(ctx.changes)
    kept_numbers = numbers_with_units(proposed_text)
    lost = [f"{n} {u}" for n, u in sorted(numbers_with_units(visible), key=str) if u and (n, u) not in kept_numbers]
    lost += sorted(model_tokens(visible) - model_tokens(proposed_text))
    return ValidatorResult("V09", not lost, {"lost": lost[:8]})


def v10_edit_ratio(ctx: ProposalContext) -> ValidatorResult:
    new_description = ctx.changes.get("description_html")
    if not isinstance(new_description, str):
        return ValidatorResult("V10", True, {"skipped": "description unchanged"})
    defective = {f.rule_id for f in seo_and_description_findings(ctx.product)} & {"A01", "A02", "A03"}
    if defective:
        return ValidatorResult("V10", True, {"skipped": "original was defective"})
    ratio = difflib.SequenceMatcher(
        None, strip_html(ctx.product.description_html).split(), strip_html(new_description).split()
    ).ratio()
    return ValidatorResult(
        "V10", ratio >= spec.EDIT_RATIO_MIN, {"ratio": round(ratio, 3), "minimum": spec.EDIT_RATIO_MIN}
    )


_RESIDUE = re.compile(
    r"(?im)untrusted_data|<\s*tool|tool_use|function_calls|(?:^|>)\s*(human|assistant|system):|\bas an ai\b"
)


def v11_injection_residue(ctx: ProposalContext) -> ValidatorResult:
    raw = " ".join(v for k, v in ctx.changes.items() if isinstance(v, str) and k in ALLOWED_FIELDS)
    visible = _proposed_visible(ctx.changes)
    current = _current_text(ctx.product)
    hits = sorted(set(injection_hits(raw) + injection_hits(visible)) - set(injection_hits(current)))
    residue = sorted({m.group(0).strip().casefold() for m in _RESIDUE.finditer(raw)})
    return ValidatorResult("V11", not hits and not residue, {"patterns": hits[:5], "residue": residue[:5]})


def v12_resolves_without_regression(ctx: ProposalContext) -> ValidatorResult:
    string_changes = {k: v for k, v in ctx.changes.items() if isinstance(v, str)}
    before = {f.key for f in seo_and_description_findings(ctx.product)}
    after = {f.key for f in seo_and_description_findings(ctx.product.with_changes(string_changes))}
    added = sorted(k[0] for k in after - before)
    resolved = sorted(k[0] for k in before - after)
    return ValidatorResult("V12", not added and bool(resolved), {"added": added, "resolved": resolved})


ALL = (
    v01_field_allowlist,
    v02_base_hash_current,
    v03_html_safe,
    v04_no_new_links,
    v05_number_fidelity,
    v06_hard_claims,
    v07_policy_claim_needs_citation,
    v08_citations,
    v09_fact_preservation,
    v10_edit_ratio,
    v11_injection_residue,
    v12_resolves_without_regression,
)


def run_validators(ctx: ProposalContext) -> list[ValidatorResult]:
    return [check(ctx) for check in ALL]
