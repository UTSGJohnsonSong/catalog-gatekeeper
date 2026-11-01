"""Text helpers shared by rules and citation checking. Pure, no I/O except cached resource loading."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

import yaml

from gatekeeper.rules.spec import IMPLIED_END_TAGS, VOID_TAGS

RESOURCES = Path(__file__).resolve().parents[1] / "resources"

_SPACE = re.compile(r"\s+")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-"})
# Bidi overrides and isolates, plus Unicode tag characters. ZWJ and ZWNJ are legitimate in text and emoji.
HIDDEN_CHARS = re.compile("[‪-‮⁦-⁩\U000e0000-\U000e007f]")


def norm(text: str) -> str:
    """NFKC, casefold, unify quotes and dashes, collapse whitespace. Deliberately no stemming or reordering."""
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", text).translate(_QUOTES).casefold()).strip()


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._hidden += 1
        elif tag in ("p", "br", "li", "div", "h1", "h2", "h3", "h4", "tr"):
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._hidden:
            self._hidden -= 1
        elif tag in ("p", "li", "div", "h1", "h2", "h3", "h4", "tr"):
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def strip_html(html: str) -> str:
    parser = _VisibleText()
    parser.feed(html)
    parser.close()
    return _SPACE.sub(" ", "".join(parser.parts)).strip()


class _TagAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attrs: list[tuple[str, str]] = []
        self.stack: list[str] = []
        self.problems: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attrs.extend((tag, name) for name, _ in attrs)
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attrs.extend((tag, name) for name, _ in attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        if tag not in self.stack:
            self.problems.append(f"stray </{tag}>")
            return
        while self.stack:
            top = self.stack.pop()
            if top == tag:
                break
            if top not in IMPLIED_END_TAGS:
                self.problems.append(f"<{top}> closed by </{tag}>")


def audit_tags(html: str) -> _TagAudit:
    parser = _TagAudit()
    parser.feed(html)
    parser.close()
    parser.problems.extend(f"unclosed <{t}>" for t in parser.stack if t not in IMPLIED_END_TAGS)
    return parser


_UNITS = {
    "kg": "kg",
    "kgs": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "g": "g",
    "gram": "g",
    "grams": "g",
    "lb": "lb",
    "lbs": "lb",
    "pound": "lb",
    "pounds": "lb",
    "oz": "oz",
    "ounce": "oz",
    "ounces": "oz",
    "ml": "ml",
    "milliliter": "ml",
    "milliliters": "ml",
    "millilitre": "ml",
    "millilitres": "ml",
    "l": "l",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "mm": "mm",
    "millimeter": "mm",
    "millimeters": "mm",
    "millimetre": "mm",
    "millimetres": "mm",
    "cm": "cm",
    "centimeter": "cm",
    "centimeters": "cm",
    "centimetre": "cm",
    "centimetres": "cm",
    "m": "m",
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
    "in": "in",
    "inch": "in",
    "inches": "in",
    "ft": "ft",
    "foot": "ft",
    "feet": "ft",
    "%": "%",
    "percent": "%",
    "w": "w",
    "watt": "w",
    "watts": "w",
    "mah": "mah",
    "v": "v",
    "volt": "v",
    "volts": "v",
    "day": "day",
    "days": "day",
    "year": "year",
    "years": "year",
    "month": "month",
    "months": "month",
    "hour": "hour",
    "hours": "hour",
    "pack": "pack",
    "piece": "piece",
    "pieces": "piece",
}
_NUMBER = re.compile(
    r"(?<![\w.,])(\$?\d{1,3}(?:,\d{3})+(?:\.\d+)?|\$?\d+(?:\.\d+)?)"
    r"(?:\s?-?\s?(%|[a-zA-Z]+))?"
)


def canonical_number(raw: str) -> str:
    value = raw.lstrip("$").replace(",", "")
    try:
        number = Decimal(value).normalize()
    except InvalidOperation:
        return value
    return format(number, "f")


def numbers_with_units(text: str) -> set[tuple[str, str | None]]:
    """Numeric facts in text, as (canonical number, canonical unit or None). Words that are not units are ignored."""
    found: set[tuple[str, str | None]] = set()
    for match in _NUMBER.finditer(unicodedata.normalize("NFKC", text)):
        raw_unit = (match.group(2) or "").lower()
        found.add((canonical_number(match.group(1)), _UNITS.get(raw_unit)))
    return found


_MODEL_TOKEN = re.compile(r"\b(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b")


def model_tokens(text: str) -> set[str]:
    return {t.casefold() for t in _MODEL_TOKEN.findall(text)}


@lru_cache
def claims() -> dict[str, list[str]]:
    return yaml.safe_load((RESOURCES / "claims.yaml").read_text(encoding="utf-8"))


@lru_cache
def injection_regexes() -> tuple[re.Pattern[str], ...]:
    data = yaml.safe_load((RESOURCES / "injection_patterns.yaml").read_text(encoding="utf-8"))
    return tuple(re.compile(p) for p in data["patterns"])


def injection_hits(text: str) -> list[str]:
    normalized = norm(text)
    hits = [p.pattern for p in injection_regexes() if p.search(normalized)]
    if HIDDEN_CHARS.search(text):
        hits.append("hidden-control-characters")
    return hits


def contains_phrase(normalized_text: str, phrase: str) -> bool:
    """Whole-word match on already-normalized text; phrase edges that are symbols match as substrings."""
    needle = norm(phrase)
    left = r"(?<!\w)" if needle[:1].isalnum() else ""
    right = r"(?!\w)" if needle[-1:].isalnum() else ""
    return re.search(left + re.escape(needle) + right, normalized_text) is not None
