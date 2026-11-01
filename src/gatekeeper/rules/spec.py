"""Thresholds and metadata. A test asserts these equal eval/spec/rules_spec.yaml, which the eval reads."""

from __future__ import annotations

DESC_THIN_MIN = 60
SEO_TITLE_MAX = 70
SEO_DESC_MAX = 160
EDIT_RATIO_MIN = 0.4
SHOUT_MIN_LETTERS = 8
SHOUT_RATIO = 0.8
SHOUT_SHORT_TOKEN = 4
QUOTE_MIN_CHARS = 20
QUOTE_MIN_WORDS = 4
QUOTE_MAX_CHARS = 400

PLACEHOLDERS = frozenset(
    {"tbd", "todo", "coming soon", "description here", "n/a", "test", "placeholder", "lorem ipsum"}
)
# Short text matching one of these is a placeholder. Longer text is a real sentence that happens to contain the words.
PLACEHOLDER_MAX_WORDS = 8
PLACEHOLDER_PATTERNS = (
    r"coming soon",
    r"\bgoes here\b",
    r"\bdescription here\b",
    r"\b(?:your|add|insert|enter|write)\b.*\bdescription\b",
    r"\bto be (?:added|updated|confirmed|announced|written|determined)\b",
    r"\bsample (?:text|copy|description)\b",
    r"\bdummy (?:text|copy)\b",
    r"\bplaceholder\b",
    r"\blorem\b.*\bipsum\b",
    r"\btbd\b",
    r"\btodo\b",
)
ALLOWED_TAGS = frozenset({"p", "br", "ul", "ol", "li", "strong", "em", "b", "i", "h2", "h3", "h4"})
VOID_TAGS = frozenset(
    {"br", "img", "hr", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}
)
IMPLIED_END_TAGS = frozenset({"p", "li", "td", "tr"})
THIN_EXEMPT_TYPES = frozenset({"gift card"})

POLICY_TERMS = ("return", "refund", "exchange", "shipping", "delivery", "warranty", "guarantee", "money-back")

AUDIT_RULES: dict[str, dict] = {
    "A01": {"name": "DESC_MISSING", "severity": "high", "fixable": True, "fields": ["description_html"]},
    "A02": {"name": "DESC_THIN", "severity": "medium", "fixable": True, "fields": ["description_html"]},
    "A03": {"name": "DESC_PLACEHOLDER", "severity": "high", "fixable": True, "fields": ["description_html"]},
    "A04": {"name": "DESC_HTML_MALFORMED", "severity": "medium", "fixable": True, "fields": ["description_html"]},
    "A05": {"name": "SEO_TITLE_MISSING", "severity": "low", "fixable": True, "fields": ["seo_title"]},
    "A06": {"name": "SEO_TITLE_TOO_LONG", "severity": "low", "fixable": True, "fields": ["seo_title"]},
    "A07": {"name": "SEO_DESC_MISSING", "severity": "medium", "fixable": True, "fields": ["seo_description"]},
    "A08": {"name": "SEO_DESC_TOO_LONG", "severity": "low", "fixable": True, "fields": ["seo_description"]},
    "A09": {"name": "TITLE_SHOUTING", "severity": "low", "fixable": False, "fields": ["title"]},
    "A10": {"name": "IMAGE_ALT_MISSING", "severity": "medium", "fixable": False, "fields": ["images"]},
    "A11": {"name": "DUPLICATE_TITLE", "severity": "medium", "fixable": False, "fields": ["title"]},
    "A12": {"name": "PRICE_NONPOSITIVE", "severity": "critical", "fixable": False, "fields": ["variants"]},
    "A13": {
        "name": "INJECTION_PHRASE",
        "severity": "high",
        "fixable": False,
        "fields": ["title", "description_html", "seo_title", "seo_description"],
    },
}

VALIDATORS: dict[str, dict] = {
    "V01": {"name": "FIELD_ALLOWLIST", "severity": "block"},
    "V02": {"name": "BASE_HASH_CURRENT", "severity": "block"},
    "V03": {"name": "HTML_SAFE", "severity": "block"},
    "V04": {"name": "NO_NEW_LINKS_OR_CONTACTS", "severity": "block"},
    "V05": {"name": "NUMBER_UNIT_FIDELITY", "severity": "block"},
    "V06": {"name": "HARD_CLAIM_LEXICON", "severity": "block"},
    "V07": {"name": "POLICY_CLAIM_NEEDS_CITATION", "severity": "block"},
    "V08": {"name": "CITATION_VALID", "severity": "block"},
    "V09": {"name": "FACT_PRESERVATION", "severity": "block"},
    "V10": {"name": "EDIT_RATIO", "severity": "warn"},
    "V11": {"name": "INJECTION_RESIDUE", "severity": "block"},
    "V12": {"name": "RESOLVES_WITHOUT_REGRESSION", "severity": "block"},
}
RULE_VERSION = 1
