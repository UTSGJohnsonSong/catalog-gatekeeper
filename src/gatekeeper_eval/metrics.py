"""Metric definitions, with no dependency on the rules."""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval. Reported because n per rule is small and point estimates would flatter."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


@dataclass
class RuleScore:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p and r else None

    def to_dict(self) -> dict:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "support": self.tp + self.fn,
            "precision": self.precision,
            "precision_ci95": wilson(self.tp, self.tp + self.fp),
            "recall": self.recall,
            "recall_ci95": wilson(self.tp, self.tp + self.fn),
            "f1": self.f1,
        }


def score_rules(predictions: Iterable[tuple[set[str], set[str]]], rules: Sequence[str]) -> dict[str, RuleScore]:
    """Each item is (predicted rules, expected rules) for one case. A rule firing outside the expected set is a false positive."""
    scores = {rule: RuleScore() for rule in rules}
    for predicted, expected in predictions:
        for rule in rules:
            if rule in predicted and rule in expected:
                scores[rule].tp += 1
            elif rule in predicted:
                scores[rule].fp += 1
            elif rule in expected:
                scores[rule].fn += 1
    return scores


def micro(scores: dict[str, RuleScore]) -> RuleScore:
    return RuleScore(
        sum(s.tp for s in scores.values()), sum(s.fp for s in scores.values()), sum(s.fn for s in scores.values())
    )


def macro(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(q * len(ordered)) - 1)
    return ordered[index]


def recall_at_k(ranked: Sequence[str], gold: Sequence[str], k: int) -> float:
    return 1.0 if set(ranked[:k]) & set(gold) else 0.0


def reciprocal_rank(ranked: Sequence[str], gold: Sequence[str]) -> float:
    for index, item in enumerate(ranked, start=1):
        if item in gold:
            return 1.0 / index
    return 0.0


def bootstrap_mean_ci(values: Sequence[float], *, seed: int = 7, draws: int = 2000) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(values, k=len(values))) / len(values) for _ in range(draws))
    return (means[int(0.025 * draws)], means[int(0.975 * draws)])
