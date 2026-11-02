"""The eval must not be able to flatter itself. These tests protect the measurement, not the product."""

from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path

import pytest
import yaml

from gatekeeper.llm.heuristic import HeuristicProvider
from gatekeeper.llm.port import LLMError
from gatekeeper.rules import spec
from gatekeeper_eval import freeze as freezer
from gatekeeper_eval import gates
from gatekeeper_eval.__main__ import main as cli_main
from gatekeeper_eval.generator import base_products
from gatekeeper_eval.generator.build import OUT, build_policy_qa, chunk_sentence_rows
from gatekeeper_eval.generator.invariants import check_base
from gatekeeper_eval.metrics import (
    RuleScore,
    bootstrap_mean_ci,
    micro,
    percentile,
    recall_at_k,
    reciprocal_rank,
    score_rules,
    wilson,
)
from gatekeeper_eval.runner import (
    AGENT_CASES_PER_RULE,
    AGENT_INJECTION_CASES,
    AGENT_MAX_CONSECUTIVE_RAISES,
    AGENT_REPAIR_RULES,
    run_agent_live,
    run_audit,
    run_citations,
    run_validators_suite,
    select_agent_cases,
)

GENERATOR = Path(base_products.__file__).parent
ROOT = Path(__file__).resolve().parents[2]


def test_generator_never_imports_the_rules_it_labels():
    for path in GENERATOR.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                if node.module == "gatekeeper":
                    names += [f"gatekeeper.{alias.name}" for alias in node.names]
            for name in names:
                assert not name.startswith("gatekeeper.rules"), f"{path.name} imports {name}"


def test_spec_file_equals_rule_constants():
    data = yaml.safe_load((ROOT / "eval/spec/rules_spec.yaml").read_text(encoding="utf-8"))
    assert data["desc_thin_min"] == spec.DESC_THIN_MIN
    assert data["seo_title_max"] == spec.SEO_TITLE_MAX
    assert data["seo_desc_max"] == spec.SEO_DESC_MAX
    assert set(data["audit_rules"]) == set(spec.AUDIT_RULES)
    assert set(data["block_validators"]) == {r for r, v in spec.VALIDATORS.items() if v["severity"] == "block"}
    assert set(data["warn_validators"]) == {r for r, v in spec.VALIDATORS.items() if v["severity"] == "warn"}


def test_splits_share_no_base_product():
    bases = {}
    for split in ("dev", "test"):
        with (OUT / f"audit_{split}.jsonl").open(encoding="utf-8") as handle:
            bases[split] = {json.loads(line)["base_id"] for line in handle}
    assert bases["dev"] and bases["test"] and not bases["dev"] & bases["test"]


def test_wording_pools_do_not_overlap_between_splits():
    from gatekeeper_eval.generator import wording_pools as pools

    for pool in (pools.PLACEHOLDERS, pools.INJECTIONS, pools.THIN_SENTENCES, pools.LINKS, pools.NUMBERS, pools.RESIDUE):
        assert not set(pool["dev"]) & set(pool["test"])


def test_every_base_product_passes_independent_invariants():
    bases = base_products.build_base_products()
    assert len(bases) == 120
    assert len({b["product"]["title"] for b in bases}) == 120
    for base in bases:
        check_base(base)


def test_every_retrieval_gold_label_resolves_to_exactly_one_chunk():
    assert {r["subset"] for r in build_policy_qa("dev")} == {"lexical", "paraphrase", "unanswerable"}
    assert chunk_sentence_rows()


def test_all_data_is_marked_synthetic():
    with (OUT / "audit_test.jsonl").open(encoding="utf-8") as handle:
        assert all(json.loads(line)["origin"] == "synthetic" for line in handle)


class TestMetrics:
    def test_wilson_known_values(self):
        assert wilson(0, 10)[1] == pytest.approx(0.2775, abs=0.001)
        assert wilson(10, 10)[0] == pytest.approx(0.7225, abs=0.001)
        assert wilson(30, 30)[0] == pytest.approx(30 / (30 + 1.96**2), abs=0.001)
        assert wilson(0, 0) == (0.0, 1.0)

    def test_scoring_counts_a_rule_firing_outside_the_expected_set_as_false_positive(self):
        scores = score_rules([({"A", "B"}, {"A"}), (set(), {"A"}), ({"A"}, {"A"})], ["A", "B"])
        assert (scores["A"].tp, scores["A"].fp, scores["A"].fn) == (2, 0, 1)
        assert (scores["B"].tp, scores["B"].fp, scores["B"].fn) == (0, 1, 0)
        assert scores["B"].precision == 0 and scores["B"].recall is None

    def test_micro_sums_counts(self):
        assert micro({"a": RuleScore(1, 2, 3), "b": RuleScore(4, 5, 6)}).tp == 5

    def test_ranking_metrics(self):
        assert recall_at_k(["a", "b"], ["b"], 1) == 0 and recall_at_k(["a", "b"], ["b"], 2) == 1
        assert reciprocal_rank(["a", "b"], ["b"]) == 0.5 and reciprocal_rank(["a"], ["z"]) == 0

    def test_percentile_and_bootstrap(self):
        assert percentile([1, 2, 3, 4], 0.5) == 2 and percentile([], 0.5) == 0
        low, high = bootstrap_mean_ci([0.0, 1.0] * 20)
        assert low < 0.5 < high


class TestFreeze:
    def test_changing_a_test_file_breaks_the_hash(self, tmp_path):
        (tmp_path / "audit_test.jsonl").write_text('{"a":1}\n', encoding="utf-8", newline="\n")
        (tmp_path / "audit_dev.jsonl").write_text('{"a":2}\n', encoding="utf-8", newline="\n")
        freezer.freeze(tmp_path)
        assert freezer.is_frozen_intact(tmp_path)
        (tmp_path / "audit_dev.jsonl").write_text('{"a":3}\n', encoding="utf-8", newline="\n")
        assert freezer.is_frozen_intact(tmp_path), "dev may change"
        (tmp_path / "audit_test.jsonl").write_text('{"a":9}\n', encoding="utf-8", newline="\n")
        assert not freezer.is_frozen_intact(tmp_path)

    def test_no_freeze_file_is_not_intact(self, tmp_path):
        assert not freezer.is_frozen_intact(tmp_path)


class TestHonesty:
    def test_agent_suite_refuses_without_a_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        report = run_agent_live("dev")
        assert report["status"] == "SKIPPED" and report["agent_metrics"] is None

    def test_agent_sample_covers_every_group_with_distinct_products(self):
        cases = select_agent_cases("dev")
        groups = Counter(c["group"] for c in cases)
        assert groups == {**dict.fromkeys(AGENT_REPAIR_RULES, AGENT_CASES_PER_RULE), "injection": AGENT_INJECTION_CASES}
        assert len({c["product"]["gid"] for c in cases}) == len(cases), "a repeated gid would drop the case silently"
        assert len({c["product"]["handle"] for c in cases}) == len(cases)

    def test_injection_cases_give_the_agent_something_to_repair(self):
        injected = [c for c in select_agent_cases("dev") if c["group"] == "injection"]
        for case in injected:
            assert case["injected_phrase"] in case["product"]["description_html"]
            assert case["product"]["seo_description"] == ""

    def test_agent_suite_runs_every_planned_case_through_the_pipeline(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-not-a-real-key")
        monkeypatch.setattr("gatekeeper.context.build_provider", lambda settings: (HeuristicProvider(), None))
        report = run_agent_live("dev")
        planned = report["cases_planned"]
        assert planned == len(AGENT_REPAIR_RULES) * AGENT_CASES_PER_RULE + AGENT_INJECTION_CASES
        assert report["agent_metrics"]["runs"] == planned == len(report["per_case"])
        assert report["agent_metrics"]["injection"]["runs"] == AGENT_INJECTION_CASES
        assert report["status"] == "RAN", report["agent_metrics"]["runs_that_raised"]

    def test_all_runs_failing_is_a_failure_and_stops_the_sweep(self, monkeypatch):
        class BadKey(HeuristicProvider):
            def create(self, **kwargs):
                raise LLMError("authentication failed", retryable=False)

        monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-not-a-real-key")
        monkeypatch.setattr("gatekeeper.context.build_provider", lambda settings: (BadKey(), None))
        report = run_agent_live("dev")
        assert report["status"] == "FAILED"
        assert report["agent_metrics"]["runs"] == AGENT_MAX_CONSECUTIVE_RAISES
        assert report["agent_metrics"]["stopped_early"] is True
        assert "authentication failed" in report["agent_metrics"]["runs_that_raised"][0]

    def test_failed_agent_report_exits_nonzero_and_does_not_claim_a_measurement(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr(
            "gatekeeper_eval.runner.run_agent_live",
            lambda split: {"suite": "agent", "split": split, "status": "FAILED", "agent_metrics": {"runs": 3}},
        )
        monkeypatch.setattr("gatekeeper_eval.__main__.REPORTS", tmp_path / "eval" / "reports")
        monkeypatch.setattr("sys.argv", ["gatekeeper_eval", "agent", "--split", "dev"])
        assert cli_main() == 1
        assert "NOT MEASURED" in capsys.readouterr().out

    def test_plumbing_report_cannot_carry_agent_metrics(self):
        report = {
            "suite": "e2e",
            "split": "fixture",
            "invariants": {
                "no_blocked_reached_approval": True,
                "every_applied_matches_approved_hash": True,
                "every_applied_verified_in_store": True,
                "every_run_finished": True,
                "every_approved_reached_a_verified_outcome": True,
            },
            "agent_metrics": {"accuracy": 1.0},
            "products_with_fixable_findings": 1,
            "applied": 1,
        }
        assert gates.judge(report) == ["a plumbing report must not carry agent metrics"]

    def test_gates_fail_when_a_value_is_missing(self):
        report = {"suite": "citations", "split": "dev", "rejection_recall": None, "false_reject_rate": 0.0}
        assert gates.judge(report)

    def test_gate_thresholds_cannot_silently_vanish(self):
        data = yaml.safe_load((ROOT / "eval/gates.yaml").read_text(encoding="utf-8"))
        assert data["dev"]["validators"]["false_block_rate"] <= 0.02
        assert data["dev"]["audit"]["per_rule_recall"] >= 0.90


class TestTheGateCanFail:
    """Negative controls: if a broken rule set still passes, the gate means nothing."""

    def test_audit_gate_fails_when_rules_find_nothing(self, monkeypatch):
        monkeypatch.setattr("gatekeeper_eval.runner.audit_product", lambda *a, **k: [])
        report = run_audit("dev", show_cases=False)
        assert report["micro"]["recall"] == 0 and gates.judge(report)

    def test_audit_gate_fails_when_rules_fire_on_everything(self, monkeypatch):
        from gatekeeper.rules.audit import Finding
        from gatekeeper.rules.spec import AUDIT_RULES

        monkeypatch.setattr("gatekeeper_eval.runner.audit_product", lambda *a, **k: [Finding(r) for r in AUDIT_RULES])
        report = run_audit("dev", show_cases=False)
        assert report["micro"]["precision"] < 0.5 and gates.judge(report)

    def test_validator_gate_fails_when_validators_pass_everything(self, monkeypatch):
        monkeypatch.setattr("gatekeeper_eval.runner.run_validators", lambda ctx: [])
        report = run_validators_suite("dev", show_cases=False)
        assert gates.judge(report)

    def test_validator_gate_fails_when_validators_block_everything(self, monkeypatch):
        from gatekeeper.rules.spec import VALIDATORS
        from gatekeeper.rules.validators import ValidatorResult

        monkeypatch.setattr(
            "gatekeeper_eval.runner.run_validators", lambda ctx: [ValidatorResult(r, False) for r in VALIDATORS]
        )
        report = run_validators_suite("dev", show_cases=False)
        assert report["false_block"]["rate"] == 1 and gates.judge(report)

    def test_citation_gate_fails_when_the_checker_accepts_everything(self, monkeypatch):
        from gatekeeper.rules.validators import CheckedCitation

        monkeypatch.setattr(
            "gatekeeper_eval.runner.check_citation_against", lambda c, ch, r: CheckedCitation("q", True)
        )
        assert gates.judge(run_citations("dev", show_cases=False))

    def test_unknown_suite_fails(self):
        assert gates.judge({"suite": "made-up", "split": "dev"})

    def test_e2e_that_processed_nothing_fails(self):
        invariants = dict.fromkeys(
            yaml.safe_load((ROOT / "eval/gates.yaml").read_text(encoding="utf-8"))["e2e"]["must_hold"], True
        )
        report = {
            "suite": "e2e",
            "split": "fixture",
            "invariants": invariants,
            "products_with_fixable_findings": 0,
            "applied": 0,
            "agent_metrics": None,
        }
        assert gates.judge(report)

    def test_test_split_report_fails_unless_the_split_is_frozen(self):
        report = {
            "suite": "citations",
            "split": "test",
            "rejection_recall": 1.0,
            "false_reject_rate": 0.0,
            "frozen_test_split_intact": False,
        }
        assert any("frozen" in failure for failure in gates.judge(report))


def test_committed_test_split_matches_its_frozen_hash():
    assert freezer.is_frozen_intact(), "the test split changed; a change must become a new dataset version"
    assert all(p.exists() for p in freezer.test_files())


def test_the_build_is_reproducible_byte_for_byte(tmp_path, monkeypatch):
    from gatekeeper_eval.generator import build

    monkeypatch.setattr(build, "OUT", tmp_path)
    build.build_all()
    for path in tmp_path.glob("*.jsonl"):
        assert path.read_bytes() == (OUT / path.name).read_bytes(), path.name


def test_test_split_reports_hide_individual_cases():
    report = run_audit("test", show_cases=False)
    assert isinstance(report["misses"], str) and "test-" not in json.dumps(report["misses"])


def test_every_case_file_is_synthetic_or_has_no_real_source():
    for path in OUT.glob("audit_*.jsonl"):
        with path.open(encoding="utf-8") as handle:
            assert all(json.loads(line)["origin"] == "synthetic" for line in handle)
    for path in OUT.glob("proposals_*.jsonl"):
        with path.open(encoding="utf-8") as handle:
            assert all(json.loads(line)["origin"] == "synthetic" for line in handle)


def test_skipped_agent_report_is_saved_without_a_pass(monkeypatch, tmp_path):
    import gatekeeper_eval.__main__ as cli

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cli, "REPORTS", tmp_path)
    monkeypatch.setattr("sys.argv", ["gatekeeper_eval", "agent", "--split", "dev"])
    assert cli.main() == 0
    saved = json.loads(next(tmp_path.glob("agent-*.json")).read_text(encoding="utf-8"))
    assert saved["gate"] == "SKIPPED" and saved["agent_metrics"] is None


class TestKnownFailureRatchet:
    report = {"suite": "audit", "split": "test"}

    def test_a_listed_failure_at_its_baseline_is_known_not_blocking(self):
        assert gates.split_known(self.report, ["A13 recall: 0.3333 is below 0.5"]) == (
            [],
            ["A13 recall: 0.3333 is below 0.5"],
        )

    def test_it_blocks_again_if_it_gets_worse(self):
        assert gates.split_known(self.report, ["A13 recall: 0.2000 is below 0.5"])[0]

    def test_other_failures_and_other_splits_still_block(self):
        assert gates.split_known(self.report, ["A03 recall: 0.4000 is below 0.5"])[0]
        assert gates.split_known({"suite": "audit", "split": "dev"}, ["A13 recall: 0.3333 is below 0.9"])[0]
        assert gates.split_known(self.report, ["the test split is not frozen or has changed"])[0]
