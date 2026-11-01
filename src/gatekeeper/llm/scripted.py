"""Replays canned turns. For tests only: it can act as a compliant, broken or hijacked model."""

from __future__ import annotations

import itertools
from typing import Any

from gatekeeper.llm.port import Block, LLMTurn, Mode, ToolSpec, Usage

_ids = itertools.count(1)


def tool_call(name: str, **tool_input: Any) -> Block:
    return Block("tool_use", id=f"toolu_{next(_ids)}", name=name, input=tool_input)


def text(value: str) -> Block:
    return Block("text", text=value)


def turn(*blocks: Block, stop_reason: str | None = None, usage: Usage = Usage()) -> LLMTurn:
    reason = stop_reason or ("tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn")
    content = [
        {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
        if b.type == "tool_use"
        else {"type": "text", "text": b.text}
        for b in blocks
    ]
    return LLMTurn(reason, list(blocks), content, usage)


class ScriptedProvider:
    name = "scripted"
    mode: Mode = "test"
    model = "scripted"

    def __init__(self, turns: list[LLMTurn]):
        self._turns = list(turns)
        self.requests: list[dict[str, Any]] = []

    def create(
        self, *, system: str, tools: list[ToolSpec], messages: list[dict[str, Any]], max_tokens: int, effort: str
    ) -> LLMTurn:
        self.requests.append({"system": system, "tools": tools, "messages": list(messages)})
        if not self._turns:
            return turn(text("script exhausted"))
        return self._turns.pop(0)
