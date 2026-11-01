"""Compare a report to committed thresholds. A gate that needs a key reports SKIPPED, never PASS."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

GATES_FILE = Path(__file__).resolve().parents[2] / "eval" / "gates.yaml"


def _check(
    failures: list[str], name: str, value: float | None, *, minimum: float | None = None, maximum: float | None = None
) -> None:
    if value is None:
        failures.append(f"{name}: no value")
    elif minimum is not None and value < minimum:
        failures.append(f"{name}: {value:.4f} is below {minimum}")
    elif maximum is not None and value > maximum:
        failures.append(f"{name}: {value:.4f} is above {maximum}")


def judge(report: dict[str, Any]) -> list[str]:
    """Returns failures. An empty list passes."""
    gates = yaml.safe_load(GATES_FILE.read_text(encoding="utf-8"))
    suite, split = report["suite"], report["split"]
    failures: list[str] = []
    if suite == "e2e":
        for name in gates["e2e"]["must_hold"]:
            if not report["invariants"].get(name):
                failures.append(f"invariant failed: {name}")
        if report.get("agent_metrics") is not None:
            failures.append("a plumbing report must not carry agent metrics")
        return failures
    g = gates[split]
    if suite == "audit":
        limits = g["audit"]
        _check(
            failures,
            "micro recall lower bound",
            report["micro"]["recall_ci95"][0],
            minimum=limits["micro_recall_wilson_low"],
        )
        _check(
            failures,
            "micro precision lower bound",
            report["micro"]["precision_ci95"][0],
            minimum=limits["micro_precision_wilson_low"],
        )
        for rule, score in report["per_rule"].items():
            if "per_rule_recall" in limits:
                _check(failures, f"{rule} recall", score["recall"], minimum=limits["per_rule_recall"])
                _check(failures, f"{rule} precision", score["precision"], minimum=limits["per_rule_precision"])
            else:
                _check(failures, f"{rule} recall", score["recall"], minimum=limits["per_rule_recall_floor"])
        _check(failures, "audit p95 ms", report["latency_ms"]["p95"], maximum=g["latency"]["audit_p95_ms"])
    elif suite == "validators":
        limits = g["validators"]
        if "per_block_rule_recall" in limits:
            for rule, score in report["per_rule"].items():
                if rule.startswith("V") and rule != "V10":
                    _check(failures, f"{rule} recall", score["recall"], minimum=limits["per_block_rule_recall"])
                    _check(
                        failures, f"{rule} precision", score["precision"], minimum=limits["per_block_rule_precision"]
                    )
        else:
            _check(
                failures,
                "micro recall lower bound",
                report["micro_block_rules"]["recall_ci95"][0],
                minimum=limits["micro_recall_wilson_low"],
            )
            _check(
                failures,
                "micro precision lower bound",
                report["micro_block_rules"]["precision_ci95"][0],
                minimum=limits["micro_precision_wilson_low"],
            )
        _check(failures, "false block rate", report["false_block"]["rate"], maximum=limits["false_block_rate"])
        _check(
            failures,
            "false block upper bound",
            report["false_block"]["ci95"][1],
            maximum=limits["false_block_wilson_high"],
        )
        _check(failures, "validation p95 ms", report["latency_ms"]["p95"], maximum=g["latency"]["validation_p95_ms"])
    elif suite == "retrieval":
        limits = g["retrieval"]
        _check(
            failures,
            "lexical recall@3",
            report["subsets"]["lexical"]["recall_at_3"],
            minimum=limits["lexical_recall_at_3"],
        )
        _check(failures, "lexical MRR", report["subsets"]["lexical"]["mrr"], minimum=limits["lexical_mrr"])
        _check(
            failures,
            "paraphrase recall@3",
            report["subsets"]["paraphrase"]["recall_at_3"],
            minimum=limits["paraphrase_recall_at_3_floor"],
        )
    elif suite == "citations":
        limits = g["citations"]
        _check(failures, "rejection recall", report["rejection_recall"], minimum=limits["rejection_recall"])
        _check(failures, "false reject rate", report["false_reject_rate"], maximum=limits["false_reject_rate"])
    return failures
