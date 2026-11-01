"""Risk score. It only informs the reviewer; it never replaces a block."""

from __future__ import annotations

from dataclasses import dataclass

HIGH_RISK_THRESHOLD = 50


@dataclass(frozen=True)
class RiskInput:
    failed_warnings: int = 0
    verified_judge_concerns: int = 0
    judge_status: str = "disabled"  # disabled | ok | unavailable
    open_injection_finding: bool = False
    rewrites_healthy_description: bool = False


def risk_score(risk: RiskInput) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    if risk.failed_warnings:
        score += 10 * risk.failed_warnings
        reasons.append(f"{risk.failed_warnings} warning rule(s) failed")
    if risk.verified_judge_concerns:
        score += min(45, 15 * risk.verified_judge_concerns)
        reasons.append(f"{risk.verified_judge_concerns} verified judge concern(s)")
    if risk.judge_status == "unavailable":
        score += 25
        reasons.append("judge unavailable; not treated as a pass")
    if risk.open_injection_finding:
        score += 30
        reasons.append("product text carries an injection phrase (A13)")
    if risk.rewrites_healthy_description:
        score += 10
        reasons.append("rewrites a description with no defect")
    return min(100, score), reasons
