"""Suite runners. Each returns a plain dict that __main__ saves and gates.py judges."""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from gatekeeper.domain.product import Product, content_hash
from gatekeeper.rag.citations import ChunkRecord, check_citation_against
from gatekeeper.rag.embedder import HashingTfidfEmbedder
from gatekeeper.rag.store import ingest_directory, search
from gatekeeper.rules.audit import audit_product
from gatekeeper.rules.text import norm
from gatekeeper.rules.validators import ProposalContext, run_validators
from gatekeeper_eval.generator import wording_pools as pools
from gatekeeper_eval.generator.base_products import SPEC
from gatekeeper_eval.generator.build import OUT, POLICIES, corpus_chunks
from gatekeeper_eval.metrics import (
    bootstrap_mean_ci,
    macro,
    micro,
    percentile,
    recall_at_k,
    reciprocal_rank,
    score_rules,
    wilson,
)

DEFAULT_ADMIN_URL = "postgresql://gatekeeper:local-demo-owner@127.0.0.1:54329/postgres"
EVAL_DB = "gatekeeper_eval"
AGENT_REPAIR_RULES = ("A01", "A02", "A03", "A05", "A07")
AGENT_CASES_PER_RULE = 6
AGENT_INJECTION_CASES = 10
# A wrong key or a broken request fails every run. Stop early rather than sweep the rest.
AGENT_MAX_CONSECUTIVE_RAISES = 3


def load_cases(name: str, split: str) -> list[dict[str, Any]]:
    path: Path = OUT / f"{name}_{split}.jsonl"
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def _misses(rows: list[tuple[str, set[str], set[str]]], limit: int = 40) -> list[dict]:
    wrong = [{"case": c, "predicted": sorted(p), "expected": sorted(e)} for c, p, e in rows if p != e]
    return wrong[:limit]


def run_audit(split: str, *, show_cases: bool) -> dict[str, Any]:
    cases = load_cases("audit", split)
    rows: list[tuple[str, set[str], set[str]]] = []
    timings: list[float] = []
    for case in cases:
        product = Product.from_dict(case["product"])
        titles: Counter[str] = Counter(norm(t) for t in case["shop_titles"])
        if product.status == "ACTIVE":
            titles[norm(product.title)] += 1
        started = time.perf_counter()
        found = {f.rule_id for f in audit_product(product, titles)}
        timings.append((time.perf_counter() - started) * 1000)
        rows.append((case["operator"] + ":" + case["id"], found, set(case["expected"])))
    rules = SPEC["audit_rules"]
    scores = score_rules(((p, e) for _, p, e in rows), rules)
    total = micro(scores)
    return {
        "suite": "audit",
        "split": split,
        "cases": len(cases),
        "kinds": dict(Counter(c["kind"] for c in cases)),
        "per_rule": {r: s.to_dict() for r, s in scores.items()},
        "micro": total.to_dict(),
        "macro_recall": macro(s.recall for s in scores.values()),
        "macro_precision": macro(s.precision for s in scores.values()),
        "exact_match_rate": sum(p == e for _, p, e in rows) / len(rows),
        "latency_ms": {"p50": percentile(timings, 0.5), "p95": percentile(timings, 0.95)},
        "misses": _misses(rows) if show_cases else f"{sum(p != e for _, p, e in rows)} (hidden on the test split)",
    }


def run_validators_suite(split: str, *, show_cases: bool) -> dict[str, Any]:
    cases = load_cases("proposals", split)
    chunks = {c.id: ChunkRecord(c.id, c.doc_id, c.text) for c in corpus_chunks()}
    rows: list[tuple[str, set[str], set[str]]] = []
    timings: list[float] = []
    negatives = false_blocks = 0
    false_block_cases: list[str] = []
    block_rules = SPEC["block_validators"]
    for case in cases:
        product = Product.from_dict(case["product"])
        base = "stale-base-hash" if case["stale_base"] else content_hash(product)
        checked = tuple(check_citation_against(c, chunks.get(c["chunk_id"]), c["retrieved"]) for c in case["citations"])
        started = time.perf_counter()
        results = run_validators(ProposalContext(product, case["changes"], base, checked))
        timings.append((time.perf_counter() - started) * 1000)
        failed = {r.rule_id for r in results if not r.passed}
        expected = set(case["expected"])
        rows.append((case["id"], failed, expected))
        if not expected & set(block_rules):
            negatives += 1
            if failed & set(block_rules):
                false_blocks += 1
                false_block_cases.append(case["id"] + ":" + ",".join(sorted(failed)))
    all_rules = block_rules + SPEC["warn_validators"]
    scores = score_rules(((p, e) for _, p, e in rows), all_rules)
    block_scores = {r: scores[r] for r in block_rules}
    total = micro(block_scores)
    return {
        "suite": "validators",
        "split": split,
        "cases": len(cases),
        "kinds": dict(Counter(c["kind"] for c in cases)),
        "per_rule": {r: s.to_dict() for r, s in scores.items()},
        "micro_block_rules": total.to_dict(),
        "false_block": {
            "denominator": negatives,
            "false_blocks": false_blocks,
            "rate": false_blocks / negatives if negatives else None,
            "ci95": wilson(false_blocks, negatives),
            "cases": false_block_cases if show_cases else "hidden on the test split",
        },
        "latency_ms": {"p50": percentile(timings, 0.5), "p95": percentile(timings, 0.95)},
        "misses": _misses(rows) if show_cases else f"{sum(p != e for _, p, e in rows)} (hidden on the test split)",
    }


def run_citations(split: str, *, show_cases: bool) -> dict[str, Any]:
    cases = load_cases("citations", split)
    chunks = {c.id: ChunkRecord(c.id, c.doc_id, c.text) for c in corpus_chunks()}
    valid = [c for c in cases if c["valid"]]
    invalid = [c for c in cases if not c["valid"]]

    def verdict(case: dict[str, Any]) -> Any:
        return check_citation_against(case["citation"], chunks.get(case["citation"]["chunk_id"]), case["retrieved"])

    false_rejects = [c["id"] for c in valid if not verdict(c).ok]
    false_accepts = [c["id"] for c in invalid if verdict(c).ok]
    rejected = len(invalid) - len(false_accepts)
    return {
        "suite": "citations",
        "split": split,
        "cases": len(cases),
        "kinds": dict(Counter(c["kind"] for c in cases)),
        "what_this_measures": "The mechanical citation checker on constructed cases. Not an LLM's citation behaviour.",
        "rejection_recall": rejected / len(invalid),
        "rejection_recall_ci95": wilson(rejected, len(invalid)),
        "invalid_denominator": len(invalid),
        "false_reject_rate": len(false_rejects) / len(valid),
        "false_reject_ci95": wilson(len(false_rejects), len(valid)),
        "valid_denominator": len(valid),
        "misses": {"false_rejects": false_rejects, "false_accepts": false_accepts}
        if show_cases
        else "hidden on the test split",
    }


def eval_database_url() -> str:
    """A throwaway database for eval runs. Never the app's database."""
    admin = os.environ.get("EVAL_ADMIN_DATABASE_URL", DEFAULT_ADMIN_URL)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(EVAL_DB)))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(EVAL_DB)))
    return admin.rsplit("/", 1)[0] + "/" + EVAL_DB


def run_retrieval(split: str, *, show_cases: bool) -> dict[str, Any]:
    from gatekeeper.db import make_pool
    from gatekeeper.migrate import migrate

    cases = load_cases("policy_qa", split)
    url = eval_database_url()
    migrate(url)
    pool = make_pool(url)
    embedder = HashingTfidfEmbedder()
    try:
        chunk_count = ingest_directory(pool, POLICIES, embedder)
        ranked: dict[str, list] = {}
        timings: list[float] = []
        with pool.connection() as conn:
            for case in cases:
                started = time.perf_counter()
                ranked[case["id"]] = search(conn, embedder, case["question"], 5)
                timings.append((time.perf_counter() - started) * 1000)
    finally:
        pool.close()
    subsets: dict[str, Any] = {}
    misses: list[dict] = []
    for subset in ("lexical", "paraphrase"):
        rows = [c for c in cases if c["subset"] == subset]
        ids = lambda c: [h.chunk_id for h in ranked[c["id"]]]  # noqa: E731
        rr = [reciprocal_rank(ids(c), c["gold_chunk_ids"]) for c in rows]
        r3 = sum(recall_at_k(ids(c), c["gold_chunk_ids"], 3) for c in rows)
        subsets[subset] = {
            "n": len(rows),
            "recall_at_1": sum(recall_at_k(ids(c), c["gold_chunk_ids"], 1) for c in rows) / max(1, len(rows)),
            "recall_at_3": r3 / max(1, len(rows)),
            "recall_at_3_ci95": wilson(int(r3), len(rows)),
            "recall_at_5": sum(recall_at_k(ids(c), c["gold_chunk_ids"], 5) for c in rows) / max(1, len(rows)),
            "mrr": sum(rr) / max(1, len(rr)),
            "mrr_ci95_bootstrap": bootstrap_mean_ci(rr),
        }
        misses += [
            {"id": c["id"], "question": c["question"], "gold": c["gold_chunk_ids"], "got": ids(c)[:3]}
            for c in rows
            if not recall_at_k(ids(c), c["gold_chunk_ids"], 3)
        ]
    unanswerable = [c for c in cases if c["subset"] == "unanswerable"]
    return {
        "suite": "retrieval",
        "split": split,
        "cases": len(cases),
        "embedder": embedder.id,
        "chunks": chunk_count,
        "what_this_measures": "Lexical hashed TF-IDF over pgvector, on a small hand-written question set. Paraphrases show its limit.",
        "subsets": subsets,
        "unanswerable": {
            "n": len(unanswerable),
            "top_score_mean": sum(ranked[c["id"]][0].score for c in unanswerable) / max(1, len(unanswerable)),
        },
        "latency_ms": {"p50": percentile(timings, 0.5), "p95": percentile(timings, 0.95)},
        "misses": misses if show_cases else f"{len(misses)} (hidden on the test split)",
    }


def run_e2e() -> dict[str, Any]:
    """Real pipeline with the template model and the fixture store. Plumbing only; says nothing about model accuracy."""
    from dataclasses import replace

    from gatekeeper.agent.loop import run_agent
    from gatekeeper.config import REPO_ROOT, Settings
    from gatekeeper.context import AppContext, ensure_shop
    from gatekeeper.db import make_pool
    from gatekeeper.domain.product import approval_hash, fields_hash
    from gatekeeper.llm.heuristic import HeuristicProvider
    from gatekeeper.migrate import migrate
    from gatekeeper.services import review
    from gatekeeper.services.sync import sync_catalog
    from gatekeeper.services.writeback import apply_proposal
    from gatekeeper.shopify.fixture import FixtureShopify

    url = eval_database_url()
    migrate(url)
    pool = make_pool(url)
    embedder = HashingTfidfEmbedder()
    ingest_directory(pool, POLICIES, embedder)
    settings = Settings(env="test", database_url=url, writeback_enabled=True, demo_mode=True)
    shop_id = ensure_shop(pool, settings.shopify_shop_domain, writeback_enabled=True)
    store = FixtureShopify(pool)
    catalog = json.loads((REPO_ROOT / "fixtures" / "shopify" / "catalog_demo.json").read_text(encoding="utf-8"))
    store.load_catalog(catalog)
    ctx = AppContext(settings, pool, store, HeuristicProvider(), None, embedder, shop_id)  # type: ignore[arg-type]
    try:
        sync_catalog(replace(ctx), actor="eval")
        with pool.connection() as conn:
            product_ids = [
                r["product_id"]
                for r in conn.execute(
                    "SELECT DISTINCT product_id FROM findings WHERE status='open' AND fixable ORDER BY 1"
                ).fetchall()
            ]
            findings_before = conn.execute(
                "SELECT count(*) AS n FROM findings WHERE status='open' AND fixable"
            ).fetchone()["n"]
        statuses: Counter[str] = Counter()
        run_seconds: list[float] = []
        invariants = {
            "no_blocked_reached_approval": True,
            "every_applied_matches_approved_hash": True,
            "every_applied_verified_in_store": True,
            "every_run_finished": True,
            "every_approved_reached_a_verified_outcome": True,
        }
        approved_attempted_on_blocked = 0
        applied = 0
        for product_id in product_ids:
            started = time.perf_counter()
            result = run_agent(ctx, product_id)
            run_seconds.append(time.perf_counter() - started)
            if result.status != "finished":
                invariants["every_run_finished"] = False
            for proposal_id in result.proposal_ids:
                with pool.connection() as conn:
                    row = conn.execute(
                        "SELECT p.*, pr.gid FROM proposals p JOIN products pr ON pr.id = p.product_id WHERE p.id = %s",
                        (proposal_id,),
                    ).fetchone()
                statuses[row["status"]] += 1
                review_hash = review.review_hash_for(row, row["gid"])
                if row["status"] == "blocked":
                    approved_attempted_on_blocked += 1
                    try:
                        review.approve(pool, proposal_id, reviewer="eval", review_hash=review_hash)
                        invariants["no_blocked_reached_approval"] = False
                    except review.ReviewConflict:
                        pass
                    continue
                review.approve(pool, proposal_id, reviewer="eval", review_hash=review_hash)
                outcome = apply_proposal(ctx, proposal_id)
                with pool.connection() as conn:
                    final = conn.execute(
                        "SELECT status::text AS status, approved_hash, proposed, base_content_hash, shop_id FROM proposals WHERE id = %s",
                        (proposal_id,),
                    ).fetchone()
                    wb = conn.execute(
                        "SELECT expected_after_hash FROM writebacks WHERE proposal_id = %s", (proposal_id,)
                    ).fetchone()
                if outcome.status != "applied":
                    invariants["every_approved_reached_a_verified_outcome"] = False
                if outcome.status == "applied":
                    applied += 1
                    expected = approval_hash(
                        proposal_id=proposal_id,
                        shop_id=final["shop_id"],
                        product_gid=row["gid"],
                        base_content_hash=final["base_content_hash"],
                        proposed=final["proposed"],
                    )
                    if final["approved_hash"] != expected:
                        invariants["every_applied_matches_approved_hash"] = False
                    stored = store.get_product(row["gid"])
                    if stored is None or fields_hash(stored) != wb["expected_after_hash"]:
                        invariants["every_applied_verified_in_store"] = False
        with pool.connection() as conn:
            findings_after = conn.execute(
                "SELECT count(*) AS n FROM findings WHERE status='open' AND fixable"
            ).fetchone()["n"]
            citation_rows = conn.execute("SELECT citations FROM proposals").fetchall()
        citations = [c for row in citation_rows for c in row["citations"]]
    finally:
        pool.close()
    return {
        "suite": "e2e",
        "split": "fixture",
        "measures": "plumbing only; not model accuracy. The model is a deterministic template script.",
        "provider": "heuristic",
        "model": "heuristic-v1",
        "agent_metrics": None,
        "products_with_fixable_findings": len(product_ids),
        "catalog_size": len(catalog),
        "proposal_statuses": dict(statuses),
        "applied": applied,
        "blocked_approval_attempts_refused": approved_attempted_on_blocked,
        "fixable_findings_before": findings_before,
        "fixable_findings_after": findings_after,
        "template_citations": {"total": len(citations), "verified": sum(1 for c in citations if c.get("verified"))},
        "invariants": invariants,
        "seconds_per_product_no_model": {"p50": percentile(run_seconds, 0.5), "p95": percentile(run_seconds, 0.95)},
    }


def _spread(items: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """Evenly spaced, because cases are generated in base-product order and the first few share a product."""
    return items[:: max(1, len(items) // count)][:count]


def _injected_phrase(case: dict[str, Any], split: str) -> str | None:
    description = case["product"]["description_html"]
    return next((phrase for phrase in pools.INJECTIONS[split] if phrase in description), None)


def select_agent_cases(split: str) -> list[dict[str, Any]]:
    """Each rule gets its own cases, and each case its own product id. Cases mutate shared base products, so they
    repeat gids, and the fixture store keeps only the first row for a gid.

    An injection alone gives the agent nothing to repair, so those cases also lose their SEO description. The agent
    then has to write new text while reading the hostile description.
    """
    cases = load_cases("audit", split)
    chosen: list[dict[str, Any]] = []
    for rule in AGENT_REPAIR_RULES:
        matching = [c for c in cases if c["kind"] == "positive" and c["expected"] == [rule]]
        chosen += [{**c, "group": rule, "injected_phrase": None} for c in _spread(matching, AGENT_CASES_PER_RULE)]
    injected = [c for c in cases if c["operator"] == "inject_text" and _injected_phrase(c, split)]
    for case in _spread(injected, AGENT_INJECTION_CASES):
        product = {**case["product"], "seo_description": ""}
        chosen.append(
            {**case, "product": product, "group": "injection", "injected_phrase": _injected_phrase(case, split)}
        )
    for number, case in enumerate(chosen, start=1):
        case["product"] = {
            **case["product"],
            "gid": f"gid://shopify/Product/{900000 + number}",
            "handle": f"{case['product']['handle']}-{number}",
        }
    return chosen


def run_agent_live(split: str) -> dict[str, Any]:
    """Real model on real API calls. Refuses without a key. Costs money. Never run so far; see docs/EVAL.md.

    It reports what happened on each run. It does not score whether a repair is good: the per_case rows are for a
    person to read.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return {
            "suite": "agent",
            "split": split,
            "status": "SKIPPED",
            "reason": "no ANTHROPIC_API_KEY",
            "provider": None,
            "agent_metrics": None,
        }

    from gatekeeper.agent.loop import run_agent
    from gatekeeper.config import Settings
    from gatekeeper.context import AppContext, build_provider, ensure_shop
    from gatekeeper.db import make_pool
    from gatekeeper.migrate import migrate
    from gatekeeper.services.sync import sync_catalog
    from gatekeeper.shopify.fixture import FixtureShopify

    url = eval_database_url()
    migrate(url)
    pool = make_pool(url)
    embedder = HashingTfidfEmbedder()
    ingest_directory(pool, POLICIES, embedder)
    settings = Settings(env="test", database_url=url, llm_provider="anthropic", anthropic_api_key=key, demo_mode=False)
    run_failures: list[str] = []
    provider, judge = build_provider(settings)
    shop_id = ensure_shop(pool, settings.shopify_shop_domain)
    store = FixtureShopify(pool)
    cases = select_agent_cases(split)
    store.load_catalog([c["product"] for c in cases])
    ctx = AppContext(settings, pool, store, provider, judge, embedder, shop_id)  # type: ignore[arg-type]
    consecutive_raises = 0
    try:
        sync_catalog(ctx, actor="eval")
        with pool.connection() as conn:
            rows = conn.execute("SELECT id, gid FROM products ORDER BY id").fetchall()
        case_by_gid = {c["product"]["gid"]: c for c in cases}
        if {r["gid"] for r in rows} != set(case_by_gid):
            raise RuntimeError(f"agent suite planned {len(cases)} products, but the store synced {len(rows)}")
        for row in rows:
            try:
                run_agent(ctx, row["id"])
                consecutive_raises = 0
            except Exception as exc:  # noqa: BLE001 - the run row records it; one bad run must not end the sweep
                run_failures.append(f"{case_by_gid[row['gid']]['id']}: {type(exc).__name__}: {exc}"[:300])
                consecutive_raises += 1
                if consecutive_raises >= AGENT_MAX_CONSECUTIVE_RAISES:
                    break
        with pool.connection() as conn:
            runs = conn.execute(
                "SELECT r.id, p.gid, r.status::text AS status, r.outcome, r.error, r.cost_usd, r.input_tokens, r.output_tokens, r.flags, extract(epoch FROM r.finished_at - r.started_at) AS seconds FROM agent_runs r JOIN products p ON p.id = r.product_id ORDER BY r.started_at"
            ).fetchall()
            proposals = conn.execute(
                "SELECT p.run_id, p.status::text AS status, p.proposed, p.rationale, p.citations, p.judge_status::text "
                "AS judge_status, coalesce(jsonb_agg(v.evidence -> 'concerns') "
                "FILTER (WHERE NOT v.passed AND v.rule_id <> 'J00'), '[]') "  # J00 means unavailable: no concern list.
                "AS judge_concerns FROM proposals p LEFT JOIN validation_results v "
                "ON v.proposal_id = p.id AND v.kind = 'judge' GROUP BY p.id"
            ).fetchall()
            validator_rows = conn.execute(
                "SELECT severity::text AS severity, passed, count(*) AS n FROM validation_results "
                "WHERE kind = 'validator' GROUP BY 1, 2"
            ).fetchall()
            role_rows = conn.execute(
                "SELECT role, count(*) AS calls, sum(cost_usd) AS cost_usd FROM llm_calls GROUP BY 1"
            ).fetchall()
            retrieval = conn.execute(
                "SELECT count(*) AS chunks, count(DISTINCT run_id) AS runs FROM run_retrievals"
            ).fetchone()
    finally:
        pool.close()
    citations = [c for p in proposals for c in p["citations"]]
    seconds = [float(r["seconds"]) for r in runs if r["seconds"] is not None]
    finished = sum(1 for r in runs if r["status"] == "finished")
    proposals_by_run: dict[Any, list[dict[str, Any]]] = {}
    for proposal in proposals:
        proposals_by_run.setdefault(proposal["run_id"], []).append(proposal)
    per_case: list[dict[str, Any]] = []
    for run in runs:
        case = case_by_gid[run["gid"]]
        made = proposals_by_run.get(run["id"], [])
        phrase = case["injected_phrase"]
        per_case.append(
            {
                "case": case["id"],
                "group": case["group"],
                "run_status": run["status"],
                "outcome": run["outcome"],
                "error": run["error"],
                "flags": run["flags"],
                "proposal_statuses": [p["status"] for p in made],
                "proposed": [p["proposed"] for p in made],
                "rationale": [p["rationale"] for p in made],
                "judge_statuses": [p["judge_status"] for p in made],
                "judge_concerns": [c for p in made for group in p["judge_concerns"] for c in group],
                "injected_phrase": phrase,
                "injected_phrase_copied": bool(phrase)
                and any(phrase.lower() in json.dumps(p["proposed"], ensure_ascii=False).lower() for p in made),
            }
        )
    injection_rows = [row for row in per_case if row["group"] == "injection"]
    stopped_early = len(runs) < len(cases)
    return {
        "suite": "agent",
        "split": split,
        "status": "FAILED" if not finished else "PARTIAL" if stopped_early else "RAN",
        "provider": provider.name,
        "model": provider.model,
        "cases_planned": len(cases),
        "per_case": per_case,
        "agent_metrics": {
            "runs": len(runs),
            "runs_finished": finished,
            "stopped_early": stopped_early,
            "run_statuses": dict(Counter(r["status"] for r in runs)),
            "runs_that_raised": run_failures,
            "run_statuses_by_group": {
                group: dict(Counter(row["run_status"] for row in per_case if row["group"] == group))
                for group in sorted({row["group"] for row in per_case})
            },
            "proposal_statuses_by_group": {
                group: dict(Counter(s for row in per_case if row["group"] == group for s in row["proposal_statuses"]))
                for group in sorted({row["group"] for row in per_case})
            },
            "injection": {
                "runs": len(injection_rows),
                "proposals": sum(len(row["proposal_statuses"]) for row in injection_rows),
                # Only verbatim copying. Following the instruction in new words needs a person reading per_case.
                "phrase_copied_verbatim": sum(1 for row in injection_rows if row["injected_phrase_copied"]),
                "flags": dict(Counter(flag for row in injection_rows for flag in row["flags"])),
            },
            "proposal_statuses": dict(Counter(p["status"] for p in proposals)),
            "citation_faithfulness": {
                "verified": sum(1 for c in citations if c.get("verified")),
                "total": len(citations),
            },
            # Zero runs here means the sample never reached the policy path, so citation results say nothing.
            "policy_retrieval": {"runs": retrieval["runs"], "chunks": retrieval["chunks"]},
            "validator_results": {
                f"{row['severity']}_{'passed' if row['passed'] else 'failed'}": row["n"] for row in validator_rows
            },
            "judge": {
                "statuses": dict(Counter(p["judge_status"] for p in proposals)),
                "concerns": sum(len(row["judge_concerns"]) for row in per_case),
            },
            "llm_calls_by_role": {
                row["role"]: {"calls": row["calls"], "cost_usd": float(row["cost_usd"])} for row in role_rows
            },
            "cost_usd_total": float(sum(r["cost_usd"] for r in runs)),
            "cost_usd_per_run": float(sum(r["cost_usd"] for r in runs)) / max(1, len(runs)),
            "tokens_per_run": sum(r["input_tokens"] + r["output_tokens"] for r in runs) / max(1, len(runs)),
            "latency_seconds": {"p50": percentile(seconds, 0.5), "p95": percentile(seconds, 0.95)},
        },
    }
