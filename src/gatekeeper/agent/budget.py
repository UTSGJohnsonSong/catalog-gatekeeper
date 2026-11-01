"""Per-run budgets. Checked before every agent model call and every tool call. The judge's one call per proposal is
not checked in advance, but its cost is added afterwards. Time is checked between calls, so one slow call can overrun."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal

from gatekeeper.config import Settings


class BudgetExceeded(Exception):
    def __init__(self, reason: str, status: str = "budget_exceeded"):
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass
class Budget:
    max_model_calls: int
    max_tool_calls: int
    max_propose_calls: int
    max_tokens: int
    max_cost_usd: Decimal
    max_seconds: float
    model_calls: int = 0
    tool_calls: int = 0
    propose_calls: int = 0
    tokens: int = 0
    cost_usd: Decimal = Decimal(0)
    started_at: float = field(default_factory=time.monotonic)

    @classmethod
    def from_settings(cls, s: Settings) -> Budget:
        return cls(
            s.max_model_calls,
            s.max_tool_calls,
            s.max_propose_calls,
            s.max_tokens_per_run,
            Decimal(str(s.max_cost_per_run_usd)),
            s.max_seconds_per_run,
        )

    def check_model_call(self) -> None:
        if self.model_calls >= self.max_model_calls:
            raise BudgetExceeded(f"model call limit {self.max_model_calls}", "max_iterations")
        self._check_totals()

    def check_tool_call(self) -> None:
        if self.tool_calls >= self.max_tool_calls:
            raise BudgetExceeded(f"tool call limit {self.max_tool_calls}", "max_iterations")
        self._check_totals()

    def _check_totals(self) -> None:
        if self.tokens >= self.max_tokens:
            raise BudgetExceeded(f"token limit {self.max_tokens}")
        if self.cost_usd >= self.max_cost_usd:
            raise BudgetExceeded(f"cost limit {self.max_cost_usd}")
        if time.monotonic() - self.started_at >= self.max_seconds:
            raise BudgetExceeded(f"time limit {self.max_seconds}s")
