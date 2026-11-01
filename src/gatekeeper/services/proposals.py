"""Create a proposal: validators decide block or pending; the judge can only raise risk afterwards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from gatekeeper.context import AppContext
from gatekeeper.db import Conn, tx
from gatekeeper.domain.product import ALLOWED_FIELDS, Product, proposed_hash
from gatekeeper.domain.risk import RiskInput, risk_score
from gatekeeper.llm.judge import JudgeResult, run_judge
from gatekeeper.rag.citations import check_citations
from gatekeeper.rules.spec import RULE_VERSION
from gatekeeper.rules.text import strip_html
from gatekeeper.rules.validators import ProposalContext, ValidatorResult, run_validators
from gatekeeper.services.catalog import product_from_row, write_audit


@dataclass
class ProposalOutcome:
    id: str | None
    status: str
    failed_rules: list[dict[str, Any]]
    risk_score: int = 0


def _proposed_text(changes: dict[str, Any]) -> str:
    parts = []
    for key in ALLOWED_FIELDS:
        value = changes.get(key)
        if isinstance(value, str):
            parts.append(strip_html(value) if key == "description_html" else value)
    return "\n".join(parts)


def _source_text(p: Product) -> str:
    return "\n".join((p.title, strip_html(p.description_html), p.seo_title, p.seo_description))


def create_proposal(
    ctx: AppContext,
    *,
    product_id: int,
    run_id: str | None,
    base_content_hash: str,
    changes: dict[str, Any],
    rationale: str,
    citations: list[dict[str, Any]],
) -> ProposalOutcome:
    with tx(ctx.pool, "agent") as conn:
        row = conn.execute("SELECT * FROM products WHERE id = %s", (product_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown product {product_id}")
        product = product_from_row(row)
        shop_id = row["shop_id"]
        checked = check_citations(conn, run_id, citations)
        open_a13 = (
            conn.execute(
                "SELECT 1 FROM findings WHERE product_id = %s AND rule_id = 'A13' AND status = 'open'", (product_id,)
            ).fetchone()
            is not None
        )
    results = run_validators(ProposalContext(product, changes, base_content_hash, checked))
    blocked = [r for r in results if r.blocks]
    judge = JudgeResult("disabled")
    if not blocked and ctx.judge is not None:
        # Outside any transaction: a slow model call must not hold a connection or a lock.
        judge = run_judge(ctx.judge, proposed_text=_proposed_text(changes), source_text=_source_text(product))
    return _persist(
        ctx,
        conn_args=(shop_id, product_id, run_id, base_content_hash),
        changes=changes,
        rationale=rationale,
        citations=citations,
        checked=checked,
        results=results,
        judge=judge,
        open_a13=open_a13,
    )


def _persist(
    ctx: AppContext,
    *,
    conn_args: tuple[int, int, str | None, str],
    changes: dict[str, Any],
    rationale: str,
    citations: list[dict[str, Any]],
    checked: tuple,
    results: list[ValidatorResult],
    judge: JudgeResult,
    open_a13: bool,
) -> ProposalOutcome:
    shop_id, product_id, run_id, base_hash = conn_args
    blocked = [r for r in results if r.blocks]
    status = "blocked" if blocked else "pending_review"
    failed_warn = [r for r in results if not r.passed and r.severity == "warn"]
    rewrites_healthy = (
        any(r.rule_id == "V10" and not r.evidence.get("skipped") for r in results) and "description_html" in changes
    )
    score, reasons = risk_score(
        RiskInput(len(failed_warn), len(judge.concerns), judge.status, open_a13, rewrites_healthy)
    )
    stored_citations = [
        {**c, "verified": chk.ok, "reason": chk.reason} for c, chk in zip(citations, checked, strict=True)
    ]
    failed_payload = [{"id": r.rule_id, "evidence": r.evidence} for r in results if not r.passed]
    try:
        with tx(ctx.pool, "agent") as conn:
            row = conn.execute(
                """
                INSERT INTO proposals (shop_id, product_id, run_id, base_content_hash, proposed, proposed_hash,
                                       rationale, citations, status, risk_score, risk_reasons)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
                """,
                (
                    shop_id,
                    product_id,
                    run_id,
                    base_hash,
                    Jsonb(changes),
                    proposed_hash(changes),
                    rationale,
                    Jsonb(stored_citations),
                    status,
                    score,
                    Jsonb(reasons),
                ),
            ).fetchone()
            proposal_id = str(row["id"])
            for result in results:
                conn.execute(
                    "INSERT INTO validation_results (proposal_id, rule_id, rule_version, kind, severity, passed, evidence) "
                    "VALUES (%s,%s,%s,'validator',%s,%s,%s)",
                    (proposal_id, result.rule_id, RULE_VERSION, result.severity, result.passed, Jsonb(result.evidence)),
                )
            _persist_judge(conn, proposal_id, judge)
            write_audit(
                conn,
                "agent",
                "proposal.created",
                "proposal",
                proposal_id,
                shop_id,
                {"status": status, "run_id": run_id},
            )
    except psycopg.errors.UniqueViolation as exc:
        name = exc.diag.constraint_name or ""
        if name == "one_open_proposal_per_product":
            return ProposalOutcome(None, "conflict_open_proposal", [{"id": "one_open_proposal_per_product"}])
        if "proposed_hash" in name or "base_content" in name:
            return ProposalOutcome(None, "duplicate", [{"id": "same fix already proposed for this base"}])
        raise
    return ProposalOutcome(proposal_id, status, failed_payload, score)


def _persist_judge(conn: Conn, proposal_id: str, judge: JudgeResult) -> None:
    if judge.status == "disabled":
        return
    if judge.status == "unavailable":
        conn.execute(
            "INSERT INTO validation_results (proposal_id, rule_id, rule_version, kind, severity, passed, evidence) "
            "VALUES (%s,'J00',%s,'judge','warn',false,%s)",
            (proposal_id, RULE_VERSION, Jsonb({"status": "unavailable", "error": judge.error})),
        )
        return
    grouped: dict[str, list[dict[str, str]]] = {}
    for concern in judge.concerns:
        grouped.setdefault(concern["rule"], []).append(concern)
    for rule_id, concerns in grouped.items():
        conn.execute(
            "INSERT INTO validation_results (proposal_id, rule_id, rule_version, kind, severity, passed, evidence) "
            "VALUES (%s,%s,%s,'judge','warn',false,%s)",
            (
                proposal_id,
                rule_id,
                RULE_VERSION,
                Jsonb({"concerns": concerns, "discarded_unverified": judge.unverified}),
            ),
        )
