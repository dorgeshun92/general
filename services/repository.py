"""Repository protocol plus the in-memory implementation used by tests, demo mode, and local development.

Every backend (memory, Supabase) implements the same methods so api/ and mcp_server/ never know
which one they are talking to. Reads are plain; the only writes are (a) upserting a validated
RunBundle (sync script, service role) and (b) append-only dashboard rows: run requests,
review decisions, action decisions, eval reports.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Protocol, runtime_checkable

from services.models import (
    ActionDecision,
    ApprovalRequired,
    Conflict,
    DashboardSummary,
    Document,
    EvalReport,
    Finding,
    Loan,
    MissingDocument,
    ProposedAction,
    Report,
    ReviewDecision,
    ReviewItem,
    ReviewQueueEntry,
    Run,
    RunBundle,
    RunDetail,
    RunRequest,
)


class NotFound(LookupError):
    """Raised when a loan, run, or report does not exist."""


class Conflicted(ValueError):
    """Raised when a write conflicts with existing state (e.g. decision on an unknown target)."""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@runtime_checkable
class Repository(Protocol):
    # ---- loans / runs
    def list_loans(self) -> list[Loan]: ...
    def get_loan(self, loan_id: str) -> Loan: ...
    def list_runs(self, loan_id: Optional[str] = None, limit: int = 100) -> list[Run]: ...
    def latest_runs(self) -> list[Run]: ...
    def get_run(self, loan_id: str, run_id: str) -> Run: ...
    def get_run_detail(self, loan_id: str, run_id: str) -> RunDetail: ...
    def upsert_run_bundle(self, bundle: RunBundle) -> Run: ...
    # ---- children
    def list_documents(self, loan_id: str, run_id: str) -> list[Document]: ...
    def list_findings(
        self,
        loan_id: str,
        run_id: str,
        audit_type: Optional[str] = None,
        result: Optional[str] = None,
        blocking: Optional[bool] = None,
        rule_id: Optional[str] = None,
    ) -> list[Finding]: ...
    def search_findings(self, query: str, limit: int = 50) -> list[Finding]: ...
    def list_review_items(self, loan_id: str, run_id: str) -> list[ReviewItem]: ...
    def list_missing_documents(self, loan_id: str, run_id: str) -> list[MissingDocument]: ...
    def list_conflicts(self, loan_id: str, run_id: str) -> list[Conflict]: ...
    def list_proposed_actions(self, loan_id: str, run_id: str) -> list[ProposedAction]: ...
    def list_approvals_required(self, loan_id: str, run_id: str) -> list[ApprovalRequired]: ...
    def list_reports(self, loan_id: str, run_id: str) -> list[Report]: ...
    def get_report(self, loan_id: str, run_id: str, name: str) -> Report: ...
    # ---- queues and decisions (append-only)
    def review_queue(self, reviewer_role: Optional[str] = None, loan_id: Optional[str] = None) -> list[ReviewQueueEntry]: ...
    def create_run_request(self, loan_id: str, note: Optional[str], requested_by: Optional[str]) -> RunRequest: ...
    def list_run_requests(self, status: Optional[str] = None, limit: int = 100) -> list[RunRequest]: ...
    def update_run_request(self, request_id: str, status: str, run_id: Optional[str] = None) -> RunRequest: ...
    def add_review_decision(self, decision: ReviewDecision) -> ReviewDecision: ...
    def list_review_decisions(self, loan_id: str, run_id: str) -> list[ReviewDecision]: ...
    def add_action_decision(self, decision: ActionDecision) -> ActionDecision: ...
    def list_action_decisions(self, loan_id: str, run_id: str) -> list[ActionDecision]: ...
    # ---- evaluation and summary
    def add_eval_report(self, report: EvalReport) -> EvalReport: ...
    def latest_eval_report(self) -> Optional[EvalReport]: ...
    def dashboard_summary(self) -> DashboardSummary: ...


class InMemoryRepository:
    """Dict-backed repository. Deterministic ordering; no persistence."""

    def __init__(self) -> None:
        self.loans: dict[str, Loan] = {}
        self.runs: dict[tuple[str, str], Run] = {}
        self.documents: dict[tuple[str, str], list[Document]] = {}
        self.findings: dict[tuple[str, str], list[Finding]] = {}
        self.review_items: dict[tuple[str, str], list[ReviewItem]] = {}
        self.missing_documents: dict[tuple[str, str], list[MissingDocument]] = {}
        self.conflicts: dict[tuple[str, str], list[Conflict]] = {}
        self.proposed_actions: dict[tuple[str, str], list[ProposedAction]] = {}
        self.approvals_required: dict[tuple[str, str], list[ApprovalRequired]] = {}
        self.reports: dict[tuple[str, str], list[Report]] = {}
        self.run_requests: list[RunRequest] = []
        self.review_decisions: list[ReviewDecision] = []
        self.action_decisions: list[ActionDecision] = []
        self.eval_reports: list[EvalReport] = []

    # ---- loans / runs
    def list_loans(self) -> list[Loan]:
        return sorted(self.loans.values(), key=lambda l: l.loan_id)

    def get_loan(self, loan_id: str) -> Loan:
        try:
            return self.loans[loan_id]
        except KeyError:
            raise NotFound(f"loan {loan_id} not found") from None

    def _run_sort_key(self, r: Run):
        return (r.completed_at or "", r.synced_at or "")

    def list_runs(self, loan_id: Optional[str] = None, limit: int = 100) -> list[Run]:
        runs = [r for r in self.runs.values() if loan_id is None or r.loan_id == loan_id]
        runs.sort(key=self._run_sort_key, reverse=True)
        return runs[: max(1, limit)]

    def latest_runs(self) -> list[Run]:
        latest: dict[str, Run] = {}
        for r in self.runs.values():
            cur = latest.get(r.loan_id)
            if cur is None or self._run_sort_key(r) > self._run_sort_key(cur):
                latest[r.loan_id] = r
        return [latest[k] for k in sorted(latest)]

    def get_run(self, loan_id: str, run_id: str) -> Run:
        try:
            return self.runs[(loan_id, run_id)]
        except KeyError:
            raise NotFound(f"run {loan_id}/{run_id} not found") from None

    def get_run_detail(self, loan_id: str, run_id: str) -> RunDetail:
        run = self.get_run(loan_id, run_id)
        key = (loan_id, run_id)
        bundle = RunBundle(
            loan=self.get_loan(loan_id),
            run=run,
            documents=list(self.documents.get(key, [])),
            findings=list(self.findings.get(key, [])),
            review_items=list(self.review_items.get(key, [])),
            missing_documents=list(self.missing_documents.get(key, [])),
            conflicts=list(self.conflicts.get(key, [])),
            proposed_actions=list(self.proposed_actions.get(key, [])),
            approvals_required=list(self.approvals_required.get(key, [])),
            reports=list(self.reports.get(key, [])),
        )
        return RunDetail(
            bundle=bundle,
            review_decisions=self.list_review_decisions(loan_id, run_id),
            action_decisions=self.list_action_decisions(loan_id, run_id),
        )

    def upsert_run_bundle(self, bundle: RunBundle) -> Run:
        loan = bundle.loan
        existing = self.loans.get(loan.loan_id)
        self.loans[loan.loan_id] = loan.model_copy(
            update={"created_at": (existing.created_at if existing else loan.created_at or now_iso()), "updated_at": now_iso()}
        )
        run = bundle.run.model_copy(update={"synced_at": now_iso()})
        key = (run.loan_id, run.run_id)
        self.runs[key] = run
        self.documents[key] = list(bundle.documents)
        self.findings[key] = list(bundle.findings)
        self.review_items[key] = list(bundle.review_items)
        self.missing_documents[key] = list(bundle.missing_documents)
        self.conflicts[key] = list(bundle.conflicts)
        self.proposed_actions[key] = list(bundle.proposed_actions)
        self.approvals_required[key] = list(bundle.approvals_required)
        self.reports[key] = list(bundle.reports)
        return run

    # ---- children
    def _require_run(self, loan_id: str, run_id: str) -> tuple[str, str]:
        self.get_run(loan_id, run_id)
        return (loan_id, run_id)

    def list_documents(self, loan_id: str, run_id: str) -> list[Document]:
        return list(self.documents.get(self._require_run(loan_id, run_id), []))

    def list_findings(self, loan_id, run_id, audit_type=None, result=None, blocking=None, rule_id=None) -> list[Finding]:
        rows = self.findings.get(self._require_run(loan_id, run_id), [])
        out = []
        for f in rows:
            if audit_type is not None and f.audit_type != audit_type:
                continue
            if result is not None and f.result != result:
                continue
            if blocking is not None and f.blocking != blocking:
                continue
            if rule_id is not None and f.rule_id != rule_id:
                continue
            out.append(f)
        # blocking first, then stable by audit type / finding id
        out.sort(key=lambda f: (not f.blocking, f.audit_type, f.finding_id))
        return out

    def search_findings(self, query: str, limit: int = 50) -> list[Finding]:
        q = query.strip().lower()
        if not q:
            return []
        hits = []
        for rows in self.findings.values():
            for f in rows:
                hay = " ".join(x for x in (f.rule_id, f.explanation, f.discrepancy or "", f.proposed_action or "") if x).lower()
                if q in hay:
                    hits.append(f)
        hits.sort(key=lambda f: (f.loan_id, f.run_id, f.audit_type, f.finding_id))
        return hits[: max(1, min(limit, 500))]

    def list_review_items(self, loan_id, run_id) -> list[ReviewItem]:
        return list(self.review_items.get(self._require_run(loan_id, run_id), []))

    def list_missing_documents(self, loan_id, run_id) -> list[MissingDocument]:
        return list(self.missing_documents.get(self._require_run(loan_id, run_id), []))

    def list_conflicts(self, loan_id, run_id) -> list[Conflict]:
        return list(self.conflicts.get(self._require_run(loan_id, run_id), []))

    def list_proposed_actions(self, loan_id, run_id) -> list[ProposedAction]:
        return list(self.proposed_actions.get(self._require_run(loan_id, run_id), []))

    def list_approvals_required(self, loan_id, run_id) -> list[ApprovalRequired]:
        return list(self.approvals_required.get(self._require_run(loan_id, run_id), []))

    def list_reports(self, loan_id, run_id) -> list[Report]:
        return list(self.reports.get(self._require_run(loan_id, run_id), []))

    def get_report(self, loan_id, run_id, name) -> Report:
        for r in self.list_reports(loan_id, run_id):
            if r.name == name:
                return r
        raise NotFound(f"report {name} not found for {loan_id}/{run_id}")

    # ---- queues and decisions
    def _decided_targets(self, loan_id: str, run_id: str) -> set[tuple[Optional[str], str]]:
        return {(d.audit_type, d.target_id) for d in self.review_decisions if d.loan_id == loan_id and d.run_id == run_id}

    def review_queue(self, reviewer_role=None, loan_id=None) -> list[ReviewQueueEntry]:
        out: list[ReviewQueueEntry] = []
        for (lid, rid), rows in self.findings.items():
            if loan_id is not None and lid != loan_id:
                continue
            decided = self._decided_targets(lid, rid)
            for f in rows:
                if f.result != "REVIEW" or (f.audit_type, f.finding_id) in decided:
                    continue
                out.append(ReviewQueueEntry(
                    loan_id=lid, run_id=rid, audit_type=f.audit_type, target_id=f.finding_id, kind="FINDING",
                    rule_id=f.rule_id, reviewer_role=f.reviewer_role or "PROCESSOR", reason=f.review_reason,
                    blocking=f.blocking, explanation=f.explanation,
                ))
        for (lid, rid), rows in self.review_items.items():
            if loan_id is not None and lid != loan_id:
                continue
            decided = self._decided_targets(lid, rid)
            for ri in rows:
                if (None, ri.review_id) in decided:
                    continue
                out.append(ReviewQueueEntry(
                    loan_id=lid, run_id=rid, audit_type=None, target_id=ri.review_id, kind="REVIEW_ITEM",
                    rule_id=ri.category, reviewer_role=ri.reviewer_role, reason=ri.description,
                    blocking=False, explanation=ri.description,
                ))
        if reviewer_role is not None:
            out = [e for e in out if e.reviewer_role == reviewer_role]
        out.sort(key=lambda e: (not e.blocking, e.loan_id, e.run_id, e.kind, e.target_id))
        return out

    def create_run_request(self, loan_id, note, requested_by) -> RunRequest:
        req = RunRequest(request_id=str(uuid.uuid4()), loan_id=loan_id, requested_by=requested_by,
                         requested_at=now_iso(), note=note, status="QUEUED", updated_at=now_iso())
        self.run_requests.append(req)
        return req

    def list_run_requests(self, status=None, limit=100) -> list[RunRequest]:
        rows = [r for r in self.run_requests if status is None or r.status == status]
        rows.sort(key=lambda r: r.requested_at or "", reverse=True)
        return rows[: max(1, limit)]

    def update_run_request(self, request_id, status, run_id=None) -> RunRequest:
        for i, r in enumerate(self.run_requests):
            if r.request_id == request_id:
                updated = r.model_copy(update={"status": status, "run_id": run_id or r.run_id, "updated_at": now_iso()})
                self.run_requests[i] = updated
                return updated
        raise NotFound(f"run request {request_id} not found")

    def add_review_decision(self, decision: ReviewDecision) -> ReviewDecision:
        key = self._require_run(decision.loan_id, decision.run_id)
        if decision.audit_type is None:
            known = {ri.review_id for ri in self.review_items.get(key, [])}
        else:
            known = {f.finding_id for f in self.findings.get(key, []) if f.audit_type == decision.audit_type}
        if decision.target_id not in known:
            raise Conflicted(f"target {decision.target_id} not found in run {decision.loan_id}/{decision.run_id}")
        stored = decision.model_copy(update={"decision_id": decision.decision_id or str(uuid.uuid4()),
                                             "decided_at": decision.decided_at or now_iso()})
        self.review_decisions.append(stored)
        return stored

    def list_review_decisions(self, loan_id, run_id) -> list[ReviewDecision]:
        return [d for d in self.review_decisions if d.loan_id == loan_id and d.run_id == run_id]

    def add_action_decision(self, decision: ActionDecision) -> ActionDecision:
        key = self._require_run(decision.loan_id, decision.run_id)
        known = {(a.audit_type, a.action_id) for a in self.proposed_actions.get(key, [])}
        if (decision.audit_type, decision.action_id) not in known:
            raise Conflicted(f"proposed action {decision.action_id} not found in run {decision.loan_id}/{decision.run_id}")
        stored = decision.model_copy(update={"decision_id": decision.decision_id or str(uuid.uuid4()),
                                             "decided_at": decision.decided_at or now_iso()})
        self.action_decisions.append(stored)
        return stored

    def list_action_decisions(self, loan_id, run_id) -> list[ActionDecision]:
        return [d for d in self.action_decisions if d.loan_id == loan_id and d.run_id == run_id]

    # ---- evaluation and summary
    def add_eval_report(self, report: EvalReport) -> EvalReport:
        stored = report.model_copy(update={"eval_id": report.eval_id or str(uuid.uuid4()),
                                           "generated_at": report.generated_at or now_iso()})
        self.eval_reports.append(stored)
        return stored

    def latest_eval_report(self) -> Optional[EvalReport]:
        if not self.eval_reports:
            return None
        return max(self.eval_reports, key=lambda e: e.generated_at or "")

    def dashboard_summary(self) -> DashboardSummary:
        latest = self.latest_runs()
        decided_actions = {(d.loan_id, d.run_id, d.audit_type, d.action_id) for d in self.action_decisions}
        pending_actions = sum(
            1 for rows in self.proposed_actions.values() for a in rows
            if (a.loan_id, a.run_id, a.audit_type, a.action_id) not in decided_actions
        )
        return DashboardSummary(
            loans=len(self.loans),
            runs=len(self.runs),
            ready=sum(1 for r in latest if r.overall_status == "READY"),
            not_ready=sum(1 for r in latest if r.overall_status == "NOT_READY"),
            human_review=sum(1 for r in latest if r.overall_status == "HUMAN_REVIEW"),
            no_gate=sum(1 for r in latest if r.overall_status is None),
            blocking_open=sum(r.blocking_open or 0 for r in latest),
            review_queue=len(self.review_queue()),
            queued_requests=sum(1 for r in self.run_requests if r.status == "QUEUED"),
            pending_actions=pending_actions,
        )
