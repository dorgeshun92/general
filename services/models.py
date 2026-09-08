"""Pydantic models shared by the API, the MCP server, and the sync script.

They mirror schemas/*.schema.json and supabase/migrations/0001_init.sql. Money stays a decimal
string; account numbers stay masked; nothing here adds inference.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ResultValue = Literal["PASS", "FAIL", "MISSING", "REVIEW", "NOT_APPLICABLE"]
OverallStatus = Literal["READY", "NOT_READY", "HUMAN_REVIEW"]
AuditType = Literal["PREAPPROVAL", "SUBMISSION_READINESS"]
ReviewerRole = Literal["LOAN_OFFICER", "PROCESSOR", "UNDERWRITER", "COMPLIANCE", "MANAGEMENT"]
Confidence = Literal["HIGH", "MEDIUM", "LOW"]
RunRequestStatus = Literal["QUEUED", "PICKED_UP", "COMPLETED", "REJECTED"]
ReviewDecisionKind = Literal["CONFIRMED", "OVERRIDDEN", "NEEDS_INFO"]
ActionDecisionKind = Literal["ACCEPTED", "REJECTED", "DEFERRED"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Loan(_Model):
    loan_id: str
    description: Optional[str] = None
    deidentified: bool = True
    source_root: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class Run(_Model):
    loan_id: str
    run_id: str
    skill: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    completed_normally: Optional[bool] = None
    stop_condition: Optional[str] = None
    overall_status: Optional[OverallStatus] = None
    preapproval_present: bool = False
    submission_present: bool = False
    los_export_present: Optional[bool] = None
    catalog_version: Optional[str] = None
    catalog_reviewed: Optional[bool] = None
    counts: Optional[dict[str, int]] = None
    blocking_open: Optional[int] = None
    coverage_percent: Optional[str] = None          # decimal string, e.g. "100.00"
    known_limitations: list[str] = Field(default_factory=list)
    tool_versions: Optional[dict[str, str]] = None
    manifest: Optional[dict[str, Any]] = None
    totals: Optional[dict[str, int]] = None
    synced_at: Optional[str] = None


class Document(_Model):
    loan_id: str
    run_id: str
    document_id: str
    filename: str
    relative_path: Optional[str] = None
    sha256: str
    size_bytes: Optional[int] = None
    document_type: str
    classification_confidence: Confidence
    page_count: Optional[int] = None
    status: str
    duplicate_of: Optional[str] = None
    document_date: Optional[str] = None


class Finding(_Model):
    loan_id: str
    run_id: str
    audit_type: AuditType
    finding_id: str
    rule_id: str
    result: ResultValue
    blocking: bool
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str
    discrepancy: Optional[str] = None
    proposed_action: Optional[str] = None
    reviewer_role: Optional[ReviewerRole] = None
    confidence: Confidence
    review_reason: Optional[str] = None
    calculation: Optional[dict[str, Any]] = None
    guideline_source: Optional[dict[str, Any]] = None


class ReviewItem(_Model):
    loan_id: str
    run_id: str
    review_id: str
    category: str
    description: str
    document_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    reviewer_role: ReviewerRole


class MissingDocument(_Model):
    loan_id: str
    run_id: str
    audit_type: AuditType
    seq: int
    document_type: str
    borrower_id: Optional[str] = None
    description: str
    rule_ids: list[str] = Field(default_factory=list)


class Conflict(_Model):
    loan_id: str
    run_id: str
    audit_type: AuditType
    conflict_id: str
    field: str
    values: list[dict[str, Any]]
    explanation: str
    rule_ids: list[str] = Field(default_factory=list)


class ProposedAction(_Model):
    loan_id: str
    run_id: str
    audit_type: AuditType
    action_id: str
    action_type: str
    target: str
    description: str
    before_value: Optional[str] = None
    after_value: Optional[str] = None
    rule_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    approver_role: ReviewerRole
    status: Literal["DRAFT_HUMAN_APPROVAL_REQUIRED"] = "DRAFT_HUMAN_APPROVAL_REQUIRED"


class ApprovalRequired(_Model):
    loan_id: str
    run_id: str
    audit_type: AuditType
    seq: int
    description: str
    approver_role: ReviewerRole
    rule_ids: list[str] = Field(default_factory=list)


class Report(_Model):
    loan_id: str
    run_id: str
    name: str
    content_md: str
    sha256: str


class RunRequest(_Model):
    request_id: Optional[str] = None
    loan_id: str
    requested_by: Optional[str] = None
    requested_at: Optional[str] = None
    note: Optional[str] = None
    status: RunRequestStatus = "QUEUED"
    run_id: Optional[str] = None
    updated_at: Optional[str] = None


class ReviewDecision(_Model):
    decision_id: Optional[str] = None
    loan_id: str
    run_id: str
    audit_type: Optional[AuditType] = None       # None when the target is a review_item
    target_id: str
    decision: ReviewDecisionKind
    note: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None


class ActionDecision(_Model):
    decision_id: Optional[str] = None
    loan_id: str
    run_id: str
    audit_type: AuditType
    action_id: str
    decision: ActionDecisionKind
    note: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None


class EvalReport(_Model):
    eval_id: Optional[str] = None
    generated_at: Optional[str] = None
    all_targets_met: bool
    targets: dict[str, Any]
    report: dict[str, Any]


class ReviewQueueEntry(_Model):
    loan_id: str
    run_id: str
    audit_type: Optional[str] = None
    target_id: str
    kind: Literal["FINDING", "REVIEW_ITEM"]
    rule_id: str                                  # rule id for findings, category for review items
    reviewer_role: str
    reason: Optional[str] = None
    blocking: bool = False
    explanation: str


class DashboardSummary(_Model):
    loans: int = 0
    runs: int = 0
    ready: int = 0
    not_ready: int = 0
    human_review: int = 0
    no_gate: int = 0
    blocking_open: int = 0
    review_queue: int = 0
    queued_requests: int = 0
    pending_actions: int = 0


class RunBundle(_Model):
    """Everything from one output/audits/<loan_id>/<run_id>/ directory, already validated and masked."""
    loan: Loan
    run: Run
    documents: list[Document] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    review_items: list[ReviewItem] = Field(default_factory=list)
    missing_documents: list[MissingDocument] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    approvals_required: list[ApprovalRequired] = Field(default_factory=list)
    reports: list[Report] = Field(default_factory=list)


class RunDetail(_Model):
    """What GET /runs/{loan_id}/{run_id} returns: the bundle plus dashboard decisions."""
    bundle: RunBundle
    review_decisions: list[ReviewDecision] = Field(default_factory=list)
    action_decisions: list[ActionDecision] = Field(default_factory=list)
