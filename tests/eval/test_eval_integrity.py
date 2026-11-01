"""The eval must not be able to flatter itself. These tests protect the measurement, not the product."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
import yaml

from gatekeeper.rules import spec
from gatekeeper_eval import freeze as freezer
from gatekeeper_eval import gates
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
from gatekeeper_eval.runner import run_agent_live

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

    def test_plumbing_report_cannot_carry_agent_metrics(self):
        report = {
            "suite": "e2e",
            "split": "fixture",
            "invariants": {
                "no_blocked_reached_approval": True,
                "every_applied_matches_approved_hash": True,
                "every_applied_verified_in_store": True,
                "every_run_finished": True,
            },
            "agent_metrics": {"accuracy": 1.0},
        }
        assert gates.judge(report) == ["a plumbing report must not carry agent metrics"]

    def test_gates_fail_when_a_value_is_missing(self):
        report = {"suite": "citations", "split": "dev", "rejection_recall": None, "false_reject_rate": 0.0}
        assert gates.judge(report)

    def test_gate_thresholds_cannot_silently_vanish(self):
        data = yaml.safe_load((ROOT / "eval/gates.yaml").read_text(encoding="utf-8"))
        assert data["dev"]["validators"]["false_block_rate"] <= 0.02
        assert data["dev"]["audit"]["per_rule_recall"] >= 0.90
