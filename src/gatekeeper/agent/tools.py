"""The agent's tools. They take no product id: a run is bound to one product, so a hijacked model cannot
reach another. Everything is read-only except propose_fix, which can only create a proposal for review."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gatekeeper.agent.budget import Budget, BudgetExceeded
from gatekeeper.agent.fence import fence, sanitize
from gatekeeper.context import AppContext
from gatekeeper.db import tx
from gatekeeper.domain.product import Product, content_hash
from gatekeeper.llm.port import ToolSpec
from gatekeeper.rag.store import search
from gatekeeper.rules.text import strip_html
from gatekeeper.services.catalog import product_from_row
from gatekeeper.services.proposals import create_proposal

MAX_FIELD_CHARS = 8000
ALLOWED_FLAGS = {"injection_suspected", "insufficient_source_facts", "policy_gap", "demo_adversarial"}

_EMPTY = {"type": "object", "additionalProperties": False, "properties": {}, "required": []}
TOOL_SPECS = [
    ToolSpec("get_product", "Read the product assigned to this run.", _EMPTY),
    ToolSpec("list_findings", "List the open audit findings for this product.", _EMPTY),
    ToolSpec(
        "search_policy",
        "Search the store policy documents. Returns chunks with ids you can cite. Use k between 1 and 5.",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["query", "k"],
            "properties": {"query": {"type": "string"}, "k": {"type": "integer"}},
        },
    ),
    ToolSpec(
        "propose_fix",
        "Submit a fix for human review. Only description_html, seo_title and seo_description may change.",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["changes", "rationale", "citations"],
            "properties": {
                "changes": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [],
                    "properties": {
                        "description_html": {"type": "string"},
                        "seo_title": {"type": "string"},
                        "seo_description": {"type": "string"},
                    },
                },
                "rationale": {"type": "string"},
                "citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["doc_id", "chunk_id", "quote"],
                        "properties": {
                            "doc_id": {"type": "string"},
                            "chunk_id": {"type": "string"},
                            "quote": {"type": "string"},
                        },
                    },
                },
            },
        },
    ),
    ToolSpec(
        "finish",
        "End the run.",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["outcome", "summary", "flags"],
            "properties": {
                "outcome": {"type": "string", "enum": ["proposed", "no_fix_needed", "cannot_fix"]},
                "summary": {"type": "string"},
                "flags": {"type": "array", "items": {"type": "string"}},
            },
        },
    ),
]


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1)
    k: int = 3


class Changes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description_html: str | None = None
    seo_title: str | None = None
    seo_description: str | None = None


class CitationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str
    chunk_id: str
    quote: str


class ProposeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: Changes
    rationale: str = Field(max_length=2000)
    citations: list[CitationInput] = Field(default_factory=list, max_length=10)


class FinishInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str
    summary: str = Field(max_length=2000)
    flags: list[str] = Field(default_factory=list)


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolInputError(Exception):
    pass


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    finished: bool = False
    finish: FinishInput | None = None


@dataclass
class RunState:
    run_id: str
    product_id: int
    base_content_hash: str
    nonce: str
    truncated: set[str] = field(default_factory=set)
    proposal_ids: list[str] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)


class Toolbox:
    def __init__(self, ctx: AppContext, state: RunState, budget: Budget):
        self.ctx = ctx
        self.state = state
        self.budget = budget

    def call(self, name: str, tool_input: dict[str, Any]) -> ToolResult:
        self.budget.check_tool_call()
        self.budget.tool_calls += 1
        handlers: dict[str, tuple[type[BaseModel], Callable[[Any], ToolResult]]] = {
            "get_product": (EmptyInput, self._get_product),
            "list_findings": (EmptyInput, self._list_findings),
            "search_policy": (SearchInput, self._search_policy),
            "propose_fix": (ProposeInput, self._propose_fix),
            "finish": (FinishInput, self._finish),
        }
        if name not in handlers:
            return ToolResult(f"Unknown tool: {name}. Available tools: {', '.join(handlers)}.", is_error=True)
        schema, handler = handlers[name]
        try:
            parsed = schema.model_validate(tool_input)
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5])
            return ToolResult(f"Invalid input for {name}: {problems}", is_error=True)
        try:
            return handler(parsed)
        except BudgetExceeded:
            raise
        except ToolInputError as exc:
            return ToolResult(str(exc), is_error=True)

    def _load_product(self) -> Product:
        with tx(self.ctx.pool) as conn:
            row = conn.execute("SELECT * FROM products WHERE id = %s", (self.state.product_id,)).fetchone()
        if row is None:
            raise ToolInputError("The product assigned to this run no longer exists.")
        return product_from_row(row)

    def _get_product(self, _: EmptyInput) -> ToolResult:
        product = self._load_product()
        nonce = self.state.nonce
        fields: dict[str, str] = {}
        sanitized = False
        for name, value in (
            ("title", product.title),
            ("vendor", product.vendor),
            ("product_type", product.product_type),
            ("description_html", product.description_html),
            ("description_text", strip_html(product.description_html)),
            ("seo_title", product.seo_title),
            ("seo_description", product.seo_description),
        ):
            if len(value) > MAX_FIELD_CHARS:
                # Editing a field the model saw only partly would silently drop text, so propose_fix refuses it.
                self.state.truncated.add("description_html" if name == "description_text" else name)
                value = value[:MAX_FIELD_CHARS]
            sanitized = sanitized or sanitize(value)[1]
            fields[name] = fence(name, value, nonce)
        return ToolResult(
            json.dumps(
                {
                    "content_hash": content_hash(product),
                    "status": product.status,
                    "fields": fields,
                    "tags": [fence("tag", t, nonce) for t in product.tags[:50]],
                    "variants": [
                        {
                            "sku": sanitize(v.get("sku", ""))[0],
                            "price": v.get("price"),
                            "options": [sanitize(str(o))[0] for o in v.get("options", [])],
                        }
                        for v in product.variants[:50]
                    ],
                    "images": [
                        {"id": i.get("id"), "has_alt": bool((i.get("alt") or "").strip())} for i in product.images[:20]
                    ],
                    "metafields": {k: fence(k, str(v), nonce) for k, v in product.metafields.items()},
                    "truncated_fields": sorted(self.state.truncated),
                    "sanitized": sanitized,
                },
                ensure_ascii=False,
            )
        )

    def _list_findings(self, _: EmptyInput) -> ToolResult:
        with tx(self.ctx.pool) as conn:
            rows = conn.execute(
                "SELECT rule_id, severity, fields, fixable, subject, evidence FROM findings "
                "WHERE product_id = %s AND status = 'open' ORDER BY rule_id",
                (self.state.product_id,),
            ).fetchall()
        findings = [
            {
                "rule_id": r["rule_id"],
                "severity": r["severity"],
                "fields": r["fields"],
                "fixable": r["fixable"],
                "evidence": fence("evidence", json.dumps(r["evidence"], ensure_ascii=False)[:500], self.state.nonce),
            }
            for r in rows
        ]
        return ToolResult(json.dumps({"findings": findings}, ensure_ascii=False))

    def _search_policy(self, parsed: SearchInput) -> ToolResult:
        k = max(1, min(5, parsed.k))
        query = parsed.query[:200]
        with tx(self.ctx.pool) as conn:
            hits = search(conn, self.ctx.embedder, query, k)
            for rank, hit in enumerate(hits, start=1):
                conn.execute(
                    "INSERT INTO run_retrievals (run_id, chunk_id, query, rank, score) VALUES (%s,%s,%s,%s,%s) "
                    "ON CONFLICT (run_id, chunk_id) DO NOTHING",
                    (self.state.run_id, hit.chunk_id, query, rank, hit.score),
                )
        results = [
            {"doc_id": h.doc_id, "chunk_id": h.chunk_id, "text": h.text, "score": round(h.score, 4)} for h in hits
        ]
        return ToolResult(json.dumps({"results": results}, ensure_ascii=False))

    def _propose_fix(self, parsed: ProposeInput) -> ToolResult:
        if self.budget.propose_calls >= self.budget.max_propose_calls:
            raise BudgetExceeded(f"propose_fix limit {self.budget.max_propose_calls}", "max_iterations")
        self.budget.propose_calls += 1
        changes = parsed.changes.model_dump(exclude_none=True)
        partly_read = sorted(set(changes) & self.state.truncated)
        if partly_read:
            raise ToolInputError(f"Refused: you only saw part of {', '.join(partly_read)}, so it cannot be rewritten.")
        outcome = create_proposal(
            self.ctx,
            product_id=self.state.product_id,
            run_id=self.state.run_id,
            base_content_hash=self.state.base_content_hash,
            changes=changes,
            rationale=parsed.rationale,
            citations=[c.model_dump() for c in parsed.citations],
        )
        if outcome.id:
            self.state.proposal_ids.append(outcome.id)
        return ToolResult(
            json.dumps(
                {"status": outcome.status, "failed_rules": outcome.failed_rules, "risk_score": outcome.risk_score},
                ensure_ascii=False,
            )
        )

    def _finish(self, parsed: FinishInput) -> ToolResult:
        self.state.flags.update(f for f in parsed.flags if f in ALLOWED_FLAGS)
        return ToolResult("ok", finished=True, finish=parsed)
