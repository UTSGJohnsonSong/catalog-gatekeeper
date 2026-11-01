"""API response and request models. The frontend's TypeScript types are generated from these."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Meta(BaseModel):
    demo_mode: bool
    model_mode: str
    model: str
    provider: str
    shopify_adapter: str
    judge: str
    writeback_env_enabled: bool
    writeback_shop_enabled: bool


class Whoami(BaseModel):
    name: str
    role: str


class QueueItem(BaseModel):
    id: str
    product_id: int
    title: str
    status: str
    risk_score: int
    failed_rules: list[str]
    open_findings: int
    model_mode: str | None
    model: str | None
    created_at: datetime


class Queue(BaseModel):
    items: list[QueueItem]
    counts: dict[str, int]


class ValidationRow(BaseModel):
    rule_id: str
    name: str
    kind: str
    severity: str
    passed: bool
    evidence: dict[str, Any]


class Citation(BaseModel):
    doc_id: str
    chunk_id: str
    quote: str
    verified: bool
    reason: str | None = None
    chunk_text: str | None = None


class FindingOut(BaseModel):
    rule_id: str
    name: str
    severity: str
    fixable: bool
    subject: str
    fields: list[str]
    evidence: dict[str, Any]
    status: str


class FieldDiff(BaseModel):
    field: str
    before: str
    after: str


class ProposalDetail(BaseModel):
    id: str
    status: str
    risk_score: int
    risk_reasons: list[str]
    rationale: str
    product_id: int
    product_title: str
    product_gid: str
    base_content_hash: str
    diffs: list[FieldDiff]
    validation: list[ValidationRow]
    citations: list[Citation]
    findings: list[FindingOut]
    review_hash: str | None
    model_mode: str | None
    model: str | None
    provider: str | None
    run_flags: list[str]
    created_at: datetime
    decided_at: datetime | None
    applied_at: datetime | None
    writeback_status: str | None
    writeback_errors: list[dict[str, Any]]
    judge_discarded: int
    judge_status: str


class ApproveRequest(BaseModel):
    review_hash: str = Field(min_length=64, max_length=64)
    confirm_high_risk: bool = False


class RejectRequest(BaseModel):
    note: str = Field(min_length=5, max_length=1000)


class DecisionResult(BaseModel):
    id: str
    status: str


class Ops(BaseModel):
    """What needs a person. Zero everywhere means nothing is known to be wrong."""

    dead_jobs: int
    failed_writes: int
    stuck_writes: int
    failed_runs_today: int
    webhooks_failed_today: int


class ProductOut(BaseModel):
    id: int
    gid: str
    title: str
    status: str
    product_type: str
    vendor: str
    content_hash: str
    open_findings: int
    open_rules: list[str]


class RunRequest(BaseModel):
    product_id: int | None = None
    limit: int = Field(default=10, ge=1, le=50)


class WritebackRequest(BaseModel):
    enabled: bool


class AuditEntry(BaseModel):
    id: int
    at: datetime
    actor: str
    action: str
    entity_type: str
    entity_id: str
    detail: dict[str, Any]


class SimulateEditRequest(BaseModel):
    product_id: int
    description_html: str = "<p>Edited by the merchant in the Shopify admin while the fix was waiting for review.</p>"
