"""The hosted LLM through its vendor's official SDK. The key comes only from settings (an env var), never from a local profile."""

from __future__ import annotations

import time
from typing import Any

import anthropic

from gatekeeper.llm.port import Block, LLMError, LLMTurn, Mode, ToolSpec, Usage

# On a safety refusal the API routes to its default fallback model instead of failing the turn.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def build_request(
    *, model: str, system: str, tools: list[ToolSpec], messages: list[dict[str, Any]], max_tokens: int, effort: str
) -> dict[str, Any]:
    """Pure, so a test can pin the request shape. Opus 5.5 rejects sampling parameters, forced tool_choice
    and disabled thinking, so none of them appear here."""
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "tools": [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema, "strict": True}
            for t in tools
        ],
        "messages": messages,
        "output_config": {"effort": effort},
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
    }


class AnthropicProvider:
    name = "anthropic"
    mode: Mode = "live"

    def __init__(self, *, api_key: str, model: str, client: Any | None = None):
        self.model = model
        self._client = client or anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=60.0)

    def create(
        self, *, system: str, tools: list[ToolSpec], messages: list[dict[str, Any]], max_tokens: int, effort: str
    ) -> LLMTurn:
        request = build_request(
            model=self.model, system=system, tools=tools, messages=messages, max_tokens=max_tokens, effort=effort
        )
        started = time.perf_counter()
        try:
            response = self._client.beta.messages.create(**request)
        except anthropic.RateLimitError as exc:
            raise LLMError("rate limited", retryable=True) from exc
        except anthropic.AuthenticationError as exc:
            raise LLMError("authentication failed", retryable=False) from exc
        except anthropic.BadRequestError as exc:
            raise LLMError(f"bad request: {exc.message}", retryable=False) from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"API status {exc.status_code}", retryable=exc.status_code >= 500) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("connection error", retryable=True) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)
        blocks: list[Block] = []
        for block in response.content:
            if block.type == "text":
                blocks.append(Block("text", text=block.text))
            elif block.type == "tool_use":
                blocks.append(Block("tool_use", id=block.id, name=block.name, input=dict(block.input)))
        usage = Usage(
            input_tokens=response.usage.input_tokens or 0,
            output_tokens=response.usage.output_tokens or 0,
            cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        )
        # response.content goes back unchanged: thinking blocks must be replayed as they came.
        return LLMTurn(response.stop_reason or "unknown", blocks, response.content, usage, latency_ms, response.model)
