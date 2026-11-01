"""Proposal state machine. A test asserts this equals the proposal_transitions table."""

from __future__ import annotations

PROPOSAL_STATUSES = ("pending_review", "blocked", "approved", "applying", "applied", "rejected", "stale", "failed")

TRANSITIONS: dict[str, set[str]] = {
    "pending_review": {"approved", "rejected", "stale"},
    "approved": {"applying", "rejected", "stale"},
    "applying": {"applied", "stale", "failed", "approved"},
    "blocked": set(),
    "applied": set(),
    "rejected": set(),
    "stale": set(),
    "failed": set(),
}
