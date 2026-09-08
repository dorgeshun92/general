"""Review queue and the append-only decision rows. `decided_by` is always the authenticated caller."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from api.deps import CurrentCaller, UserRepo
from services.models import (
    ActionDecision,
    ActionDecisionKind,
    AuditType,
    ReviewDecision,
    ReviewDecisionKind,
    ReviewerRole,
    ReviewQueueEntry,
)

router = APIRouter(tags=["review"])


class _In(BaseModel):
    # Client-supplied decided_by / decision_id / decided_at are dropped, never trusted.
    model_config = ConfigDict(extra="ignore")


class ReviewDecisionIn(_In):
    loan_id: str
    run_id: str
    audit_type: Optional[AuditType] = None   # null when the target is a review_item
    target_id: str
    decision: ReviewDecisionKind
    note: Optional[str] = None


class ActionDecisionIn(_In):
    loan_id: str
    run_id: str
    audit_type: AuditType
    action_id: str
    decision: ActionDecisionKind
    note: Optional[str] = None


@router.get("/review-queue", response_model=list[ReviewQueueEntry], summary="Undecided REVIEW findings and review items, blocking first")
def review_queue(repo: UserRepo, reviewer_role: Optional[ReviewerRole] = None, loan_id: Optional[str] = None) -> list[ReviewQueueEntry]:
    return repo.review_queue(reviewer_role=reviewer_role, loan_id=loan_id)


@router.post("/review-decisions", response_model=ReviewDecision, status_code=201, summary="Record a human decision on a finding or review item")
def add_review_decision(body: ReviewDecisionIn, repo: UserRepo, who: CurrentCaller) -> ReviewDecision:
    decision = ReviewDecision(**body.model_dump(), decided_by=who.stamp)
    return repo.add_review_decision(decision)


@router.post("/action-decisions", response_model=ActionDecision, status_code=201, summary="Record a human decision on a proposed action")
def add_action_decision(body: ActionDecisionIn, repo: UserRepo, who: CurrentCaller) -> ActionDecision:
    decision = ActionDecision(**body.model_dump(), decided_by=who.stamp)
    return repo.add_action_decision(decision)
