"""The manual agent loop. It is written by hand rather than with the SDK's beta tool runner because budget
checks, audit rows and the run state machine need to sit between every model call and tool call (ADR-0007)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb

from gatekeeper.agent.budget import Budget, BudgetExceeded
from gatekeeper.agent.fence import new_nonce
from gatekeeper.agent.prompts import NUDGE, PROMPT_VERSION, START, SYSTEM_PROMPT
from gatekeeper.agent.tools import TOOL_SPECS, RunState, Toolbox
from gatekeeper.context import AppContext
from gatekeeper.db import tx
from gatekeeper.llm.port import LLMError, LLMTurn, billable_cost

AGENT_MAX_TOKENS = 8192


class DailyCapExceeded(Exception):
    pass


@dataclass
class RunResult:
    run_id: str
    status: str
    outcome: str | None
    proposal_ids: list[str]
    flags: list[str]
    error: str | None = None


def _storable(value: Any) -> Any:
    """Tool input is recorded as received, for the audit trail, but Postgres cannot hold NUL or lone surrogates."""
    if isinstance(value, str):
        return value.replace("\x00", "\ufffd").encode("utf-8", "replace").decode("utf-8")
    if isinstance(value, dict):
        return {_storable(k): _storable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_storable(v) for v in value]
    return value


def _start_run(ctx: AppContext, product_id: int) -> tuple[str, str, int]:
    provider = ctx.provider
    with tx(ctx.pool, "agent") as conn:
        # Summed from llm_calls, which also holds judge calls and calls from runs that crashed before finishing.
        spent = conn.execute(
            "SELECT coalesce(sum(c.cost_usd), 0) AS total FROM llm_calls c JOIN agent_runs r ON r.id = c.run_id "
            "WHERE r.started_at >= date_trunc('day', now())"
        ).fetchone()["total"]
        if Decimal(spent) >= Decimal(str(ctx.settings.daily_cost_cap_usd)):
            raise DailyCapExceeded(f"daily cost cap reached ({spent} USD)")
        product = conn.execute(
            "SELECT shop_id, content_hash FROM products WHERE id = %s AND deleted_at IS NULL", (product_id,)
        ).fetchone()
        if product is None:
            raise ValueError(f"unknown or deleted product {product_id}")
        run = conn.execute(
            "INSERT INTO agent_runs (shop_id, product_id, provider, model, mode, prompt_version, base_content_hash) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (
                product["shop_id"],
                product_id,
                provider.name,
                provider.model,
                provider.mode,
                PROMPT_VERSION,
                product["content_hash"],
            ),
        ).fetchone()
    return str(run["id"]), product["content_hash"], product["shop_id"]


def _record_call(ctx: AppContext, run_id: str, iteration: int, turn: LLMTurn, budget: Budget) -> None:
    model = turn.model or ctx.provider.model
    cost = billable_cost(model, turn.usage)
    budget.tokens += turn.usage.input_tokens + turn.usage.output_tokens + turn.usage.cache_read_tokens
    budget.cost_usd += cost
    with tx(ctx.pool) as conn:
        conn.execute(
            "INSERT INTO llm_calls (run_id, iteration, role, model, stop_reason, input_tokens, output_tokens, "
            "cache_read_tokens, cost_usd, latency_ms) VALUES (%s,%s,'agent',%s,%s,%s,%s,%s,%s,%s)",
            (
                run_id,
                iteration,
                model,
                turn.stop_reason,
                turn.usage.input_tokens,
                turn.usage.output_tokens,
                turn.usage.cache_read_tokens,
                cost,
                turn.latency_ms,
            ),
        )


def _finish_run(
    ctx: AppContext,
    run_id: str,
    status: str,
    state: RunState,
    budget: Budget,
    transcript: list[dict[str, Any]],
    *,
    outcome: str | None = None,
    error: str | None = None,
    served_model: str | None = None,
) -> RunResult:
    with tx(ctx.pool) as conn:
        # `model` records what really served the run, which differs from the configured model after a fallback.
        conn.execute(
            "UPDATE agent_runs SET status = %s, outcome = %s, error = %s, iterations = %s, tool_calls = %s, "
            "input_tokens = (SELECT coalesce(sum(input_tokens),0) FROM llm_calls WHERE run_id = %s), "
            "output_tokens = (SELECT coalesce(sum(output_tokens),0) FROM llm_calls WHERE run_id = %s), "
            "cost_usd = (SELECT coalesce(sum(cost_usd),0) FROM llm_calls WHERE run_id = %s), flags = %s, "
            "transcript = %s, model = coalesce(%s, model), finished_at = now() WHERE id = %s",
            (
                status,
                outcome,
                _storable(error),
                budget.model_calls,
                budget.tool_calls,
                run_id,
                run_id,
                run_id,
                sorted(state.flags),
                Jsonb(_storable(transcript)),
                served_model,
                run_id,
            ),
        )
    return RunResult(run_id, status, outcome, state.proposal_ids, sorted(state.flags), error)


def run_agent(ctx: AppContext, product_id: int) -> RunResult:
    """Runs one product to an end state. Provider and unexpected errors finish the run as failed, then re-raise,
    so the run is never left 'running' and the job queue decides whether to retry."""
    run_id, base_hash, _ = _start_run(ctx, product_id)
    # The nonce is created after the product text was stored, so that text cannot predict it.
    state = RunState(run_id, product_id, base_hash, new_nonce())
    budget = Budget.from_settings(ctx.settings)
    toolbox = Toolbox(ctx, state, budget)
    messages: list[dict[str, Any]] = [{"role": "user", "content": START}]
    # Thinking blocks stay in `messages` for replay but are never persisted.
    transcript: list[dict[str, Any]] = []
    served_model: str | None = None
    nudged = False

    def finish(status: str, *, outcome: str | None = None, error: str | None = None) -> RunResult:
        return _finish_run(
            ctx, run_id, status, state, budget, transcript, outcome=outcome, error=error, served_model=served_model
        )

    try:
        while True:
            budget.check_model_call()
            budget.model_calls += 1
            turn = ctx.provider.create(
                system=SYSTEM_PROMPT,
                tools=TOOL_SPECS,
                messages=messages,
                max_tokens=AGENT_MAX_TOKENS,
                effort=ctx.settings.llm_effort,
            )
            served_model = turn.model or served_model
            _record_call(ctx, run_id, budget.model_calls, turn, budget)
            transcript.extend(
                {"type": "tool_use", "name": b.name, "input": b.input}
                if b.type == "tool_use"
                else {"type": "text", "text": b.text}
                for b in turn.blocks
            )
            messages.append({"role": "assistant", "content": turn.assistant_content})
            if turn.stop_reason == "tool_use":
                results: list[dict[str, Any]] = []
                finished = None
                for block in turn.blocks:
                    if block.type != "tool_use":
                        continue
                    result = toolbox.call(block.name, block.input)
                    entry: dict[str, Any] = {"type": "tool_result", "tool_use_id": block.id, "content": result.content}
                    if result.is_error:
                        entry["is_error"] = True
                    results.append(entry)
                    transcript.append(
                        {
                            "type": "tool_result",
                            "name": block.name,
                            "is_error": result.is_error,
                            "content": result.content[:2000],
                        }
                    )
                    if result.finished:
                        finished = result.finish
                if finished is not None:
                    # The model's claim is checked: "proposed" with no proposal on record is not a proposal.
                    claimed = finished.outcome
                    outcome = "no_proposal" if claimed == "proposed" and not state.proposal_ids else claimed
                    return finish("finished", outcome=outcome)
                messages.append({"role": "user", "content": results})
            elif turn.stop_reason == "end_turn":
                if nudged:
                    return finish("no_finish", error="model ended twice without finish")
                nudged = True
                messages.append({"role": "user", "content": NUDGE})
            elif turn.stop_reason == "max_tokens":
                return finish("failed", error="truncated")
            else:
                return finish("failed", error=f"stop_reason={turn.stop_reason}")
    except BudgetExceeded as exc:
        return finish(exc.status, error=exc.reason)
    except LLMError as exc:
        finish("failed", error=str(exc))
        raise
    except Exception as exc:
        finish("failed", error=f"{type(exc).__name__}: {exc}"[:500])
        raise
