"""CLI. Every run saves its command, console summary and full report under eval/reports, so a number can be traced.

python -m gatekeeper_eval build
python -m gatekeeper_eval audit --split dev
python -m gatekeeper_eval all --split test
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gatekeeper_eval import freeze as freezer
from gatekeeper_eval import gates, runner
from gatekeeper_eval.generator.build import build_all

REPORTS = Path(__file__).resolve().parents[2] / "eval" / "reports"
LIMITS = [
    "All data is synthetic. Labels are true by construction, and defect rates here are not real-world prevalence.",
    "The same engineer wrote the rules and the datasets, so independence is procedural, not absolute.",
    "With about 30 positives per rule, per-rule intervals are wide.",
    "These numbers measure rules against their specification, not against real merchants or a real LLM.",
]
AGENT_VERDICTS = {
    "SKIPPED": "Agent behaviour: NOT MEASURED (no ANTHROPIC_API_KEY).",
    "FAILED": "Agent behaviour: NOT MEASURED (no run finished). See runs_that_raised in the report.",
    "PARTIAL": "Agent behaviour: PARTLY MEASURED (the sweep stopped early). Do not quote these numbers.",
    "RAN": "Agent behaviour measured against a real model. Repair quality is not scored: read per_case in the report.",
}


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short=8", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()  # noqa: S607
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def _git_dirty() -> bool | None:
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--", "src", "eval/gates.yaml", "eval/spec"],
            capture_output=True,
            text=True,
            check=True,
        )  # noqa: S607
        return bool(out.stdout.strip())
    except (subprocess.SubprocessError, OSError):
        return None


def _save(report: dict[str, Any], command: str, failures: list[str], *, gate: str | None = None) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "command": command,
        "ran_at_utc": stamp,
        "git_sha": _git_sha(),
        "uncommitted_source_changes": _git_dirty(),
        "dataset_test_sha256": freezer.combined_test_hash(),
        "limits": LIMITS,
        **report,
        "gate": gate or ("PASS" if not failures else "FAIL"),
        "gate_failures": failures,
    }
    path = REPORTS / f"{report['suite']}-{report['split']}-{stamp}.json"
    path.write_text(json.dumps(report, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
    return path


def _summary(report: dict[str, Any]) -> str:
    suite = report["suite"]
    if suite == "audit":
        m = report["micro"]
        return f"micro precision {m['precision']:.3f} recall {m['recall']:.3f} (tp={m['tp']} fp={m['fp']} fn={m['fn']}), cases={report['cases']}, p95={report['latency_ms']['p95']:.2f} ms"
    if suite == "validators":
        m, f = report["micro_block_rules"], report["false_block"]
        return f"micro precision {m['precision']:.3f} recall {m['recall']:.3f} (tp={m['tp']} fp={m['fp']} fn={m['fn']}), false blocks {f['false_blocks']}/{f['denominator']}, cases={report['cases']}, p95={report['latency_ms']['p95']:.2f} ms"
    if suite == "retrieval":
        lex, par = report["subsets"]["lexical"], report["subsets"]["paraphrase"]
        return f"lexical r@3 {lex['recall_at_3']:.3f} mrr {lex['mrr']:.3f} (n={lex['n']}); paraphrase r@3 {par['recall_at_3']:.3f} mrr {par['mrr']:.3f} (n={par['n']}); embedder {report['embedder']}"
    if suite == "citations":
        return f"rejection recall {report['rejection_recall']:.3f} (n={report['invalid_denominator']}), false reject rate {report['false_reject_rate']:.3f} (n={report['valid_denominator']})"
    if suite == "e2e":
        return f"PLUMBING ONLY: {report['products_with_fixable_findings']} products, statuses {report['proposal_statuses']}, applied {report['applied']}, fixable findings {report['fixable_findings_before']} to {report['fixable_findings_after']}, invariants {report['invariants']}"
    return json.dumps(report.get("agent_metrics") or report.get("reason"))


def main() -> int:
    parser = argparse.ArgumentParser(prog="gatekeeper_eval", description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "build",
            "freeze",
            "check-frozen",
            "audit",
            "validators",
            "citations",
            "retrieval",
            "e2e",
            "agent",
            "all",
        ],
    )
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    args = parser.parse_args()
    command_line = "python -m gatekeeper_eval " + " ".join(sys.argv[1:])
    if args.command == "build":
        for name, count in build_all().items():
            print(f"{name}: {count}")
        return 0
    if args.command == "freeze":
        print(f"frozen test split sha256: {freezer.freeze()}")
        return 0
    if args.command == "check-frozen":
        intact = freezer.is_frozen_intact()
        print("frozen test split intact" if intact else "FROZEN TEST SPLIT CHANGED: create datasets/v2 instead")
        return 0 if intact else 1
    show = args.split == "dev"
    suites = {
        "audit": lambda: runner.run_audit(args.split, show_cases=show),
        "validators": lambda: runner.run_validators_suite(args.split, show_cases=show),
        "citations": lambda: runner.run_citations(args.split, show_cases=show),
        "retrieval": lambda: runner.run_retrieval(args.split, show_cases=show),
        "e2e": runner.run_e2e,
    }
    if args.command == "agent":
        report = runner.run_agent_live(args.split)
        status = report["status"]
        print(f"agent ({args.split}): {status}: {_summary(report)}")
        print(AGENT_VERDICTS[status])
        # There is no agent gate yet, so this report must never read as a pass.
        path = _save(report, command_line, [], gate=status if status in {"SKIPPED", "FAILED"} else "NOT_GATED")
        print(f"  report: {path.relative_to(REPORTS.parents[1])}")
        return 1 if status == "FAILED" else 0
    chosen = list(suites) if args.command == "all" else [args.command]
    failed = False
    for name in chosen:
        report = suites[name]()
        report["frozen_test_split_intact"] = freezer.is_frozen_intact()
        failures = gates.judge(report)
        blocking, known = gates.split_known(report, failures)
        verdict = "FAIL" if blocking else "PASS_WITH_KNOWN_FAILURES" if known else "PASS"
        report["console_summary"] = f"{name} ({report['split']}): {verdict}: {_summary(report)}"
        report["known_failures"] = known
        path = _save(report, command_line, failures, gate=verdict)
        print(report["console_summary"])
        print(f"  report: {path.relative_to(REPORTS.parents[1])}")
        for failure in blocking:
            print(f"  gate failure: {failure}")
        for failure in known:
            print(f"  known failure, not tuned away: {failure}")
        failed = failed or bool(blocking)
    print("Agent accuracy: NOT MEASURED (no real model was called).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
