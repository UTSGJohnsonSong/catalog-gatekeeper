"""The provider port. The agent loop sees only this, so tests and the demo run without a key."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml

Mode = Literal["live", "demo", "test"]


class LLMError(Exception):
    """The provider could not produce a turn. `retryable` tells the job queue whether to try again."""

    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class Block:
    type: Literal["text", "tool_use"]
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass(frozen=True)
class LLMTurn:
    stop_reason: str
    blocks: list[Block]
    assistant_content: Any  # opaque; appended back unchanged, so provider-specific blocks survive
    usage: Usage = Usage()
    latency_ms: int = 0
    model: str = ""


class LLMProvider(Protocol):
    name: str
    mode: Mode
    model: str

    def create(
        self, *, system: str, tools: list[ToolSpec], messages: list[dict[str, Any]], max_tokens: int, effort: str
    ) -> LLMTurn: ...


@lru_cache
def _pricing() -> dict[str, dict[str, float]]:
    path = Path(__file__).resolve().parents[1] / "resources" / "pricing.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["models"]


def cost_usd(model: str, usage: Usage) -> Decimal | None:
    """None for an unknown model, in which case only the token budget applies."""
    price = _pricing().get(model)
    if price is None:
        return None
    uncached = usage.input_tokens
    total = (
        Decimal(str(price["input"])) * uncached
        + Decimal(str(price["output"])) * usage.output_tokens
        + Decimal(str(price.get("cache_read", price["input"]))) * usage.cache_read_tokens
    )
    return total / Decimal(1_000_000)
