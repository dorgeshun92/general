"""Repository implementation over Supabase (PostgREST). Same contract as InMemoryRepository.

Bundles are written with the service role: the run row is deleted (children cascade) and
re-inserted, so a re-sync never leaves stale findings behind. Dashboard state tables are
untouched by a re-sync because they reference runs by id, not by foreign key.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

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
from services.repository import Conflicted, NotFound, now_iso
from services.supabase_client import SupabaseClient, SupabaseError


def _pct(value: Any) -> Optional[str]:
    """numeric(6,2) comes back as a JSON number; return the decimal string the models expect."""
    if value is None:
        return None
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


def _run_from_row(row: dict[str, Any]) -> Run:
    row = dict(row)
    row["coverage_percent"] = _pct(row.get("coverage_percent"))
    row.pop("description", None)  # v_latest_runs carries the loan description; Run does not
    return Run(**{k: v for k, v in row.items() if k in Run.model_fields})


def _run_to_row(run: Run) -> dict[str, Any]:
    row = run.model_dump(exclude={"synced_at"})
    row["coverage_percent"] = None if run.coverage_percent is None else str(Decimal(run.coverage_percent))
    return row


class SupabaseRepository:
    def __init__(self, client: SupabaseClient) -> None:
        self.client = client

    def for_bearer(self, bearer: str) -> "SupabaseRepository":
        """Per-request repository that forwards the caller's Supabase JWT so RLS applies."""
        return SupabaseRepository(self.client.with_bearer(bearer))

    # ---- helpers
    @staticmethod
    def _eq(**kv: Any) -> dict[str, str]:
        return {k: f"eq.{v}" for k, v in kv.items() if v is not None}

    def _rows(self, model, table: str, params: dict[str, Any]):
        return [model(**{k: v for k, v in row.items() if k in model.model_fields}) for row in self.client.select(table, params)]

    # ---- loans / runs
    def list_loans(self) -> list[Loan]:
        return self._rows(Loan, "loans", {"order": "loan_id.asc"})

    def get_loan(self, loan_id: str) -> Loan:
        row = self.client.select_one("loans", self._eq(loan_id=loan_id))
        if row is None:
            raise NotFound(f"loan {loan_id} not found")
        return Loan(**{k: v for k, v in row.items() if k in Loan.model_fields})

    def list_runs(self, loan_id: Optional[str] = None, limit: int = 100) -> list[Run]:
        params = {**self._eq(loan_id=loan_id), "order": "completed_at.desc.nullslast,synced_at.desc", "limit": max(1, limit)}
        return [_run_from_row(r) for r in self.client.select("runs", params)]

    def latest_runs(self) -> list[Run]:
        return [_run_from_row(r) for r in self.client.select("v_latest_runs", {"order": "loan_id.asc"})]

    def get_run(self, loan_id: str, run_id: str) -> Run:
        row = self.client.select_one("runs", self._eq(loan_id=loan_id, run_id=run_id))
        if row is None:
            raise NotFound(f"run {loan_id}/{run_id} not found")
        return _run_from_row(row)

    def get_run_detail(self, loan_id: str, run_id: str) -> RunDetail:
        run = self.get_run(loan_id, run_id)
        bundle = RunBundle(
            loan=self.get_loan(loan_id),
            run=run,
            documents=self.list_documents(loan_id, run_id),
            findings=self.list_findings(loan_id, run_id),
            review_items=self.list_review_items(loan_id, run_id),
            missing_documents=self.list_missing_documents(loan_id, run_id),
            conflicts=self.list_conflicts(loan_id, run_id),
            proposed_actions=self.list_proposed_actions(loan_id, run_id),
            approvals_required=self.list_approvals_required(loan_id, run_id),
            reports=self.list_reports(loan_id, run_id),
        )
        return RunDetail(bundle=bundle, review_decisions=self.list_review_decisions(loan_id, run_id),
                         action_decisions=self.list_action_decisions(loan_id, run_id))

    def upsert_run_bundle(self, bundle: RunBundle) -> Run:
        loan_row = bundle.loan.model_dump(exclude={"created_at", "updated_at"})
        loan_row["updated_at"] = now_iso()
        self.client.upsert("loans", [loan_row], on_conflict="loan_id")
        key = self._eq(loan_id=bundle.run.loan_id, run_id=bundle.run.run_id)
        self.client.delete("runs", key)  # children cascade
        self.client.insert("runs", [_run_to_row(bundle.run)])
        for table, rows in (
            ("documents", bundle.documents),
            ("findings", bundle.findings),
            ("review_items", bundle.review_items),
            ("missing_documents", bundle.missing_documents),
            ("conflicts", bundle.conflicts),
            ("proposed_actions", bundle.proposed_actions),
            ("approvals_required", bundle.approvals_required),
            ("reports", bundle.reports),
        ):
            if rows:
                self.client.insert(table, [r.model_dump() for r in rows])
        return self.get_run(bundle.run.loan_id, bundle.run.run_id)

    # ---- children
    def list_documents(self, loan_id, run_id) -> list[Document]:
        return self._rows(Document, "documents", {**self._eq(loan_id=loan_id, run_id=run_id), "order": "document_id.asc"})

    def list_findings(self, loan_id, run_id, audit_type=None, result=None, blocking=None, rule_id=None) -> list[Finding]:
        params = {**self._eq(loan_id=loan_id, run_id=run_id, audit_type=audit_type, result=result, rule_id=rule_id),
                  "order": "blocking.desc,audit_type.asc,finding_id.asc"}
        if blocking is not None:
            params["blocking"] = f"is.{'true' if blocking else 'false'}"
        return self._rows(Finding, "findings", params)

    def search_findings(self, query: str, limit: int = 50) -> list[Finding]:
        if not query.strip():
            return []
        rows = self.client.rpc("search_findings", {"q": query.strip(), "max_rows": max(1, min(limit, 500))}) or []
        return [Finding(**{k: v for k, v in r.items() if k in Finding.model_fields}) for r in rows]

    def list_review_items(self, loan_id, run_id) -> list[ReviewItem]:
        return self._rows(ReviewItem, "review_items", {**self._eq(loan_id=loan_id, run_id=run_id), "order": "review_id.asc"})

    def list_missing_documents(self, loan_id, run_id) -> list[MissingDocument]:
        return self._rows(MissingDocument, "missing_documents", {**self._eq(loan_id=loan_id, run_id=run_id), "order": "audit_type.asc,seq.asc"})

    def list_conflicts(self, loan_id, run_id) -> list[Conflict]:
        return self._rows(Conflict, "conflicts", {**self._eq(loan_id=loan_id, run_id=run_id), "order": "conflict_id.asc"})

    def list_proposed_actions(self, loan_id, run_id) -> list[ProposedAction]:
        return self._rows(ProposedAction, "proposed_actions", {**self._eq(loan_id=loan_id, run_id=run_id), "order": "audit_type.asc,action_id.asc"})

    def list_approvals_required(self, loan_id, run_id) -> list[ApprovalRequired]:
        return self._rows(ApprovalRequired, "approvals_required", {**self._eq(loan_id=loan_id, run_id=run_id), "order": "audit_type.asc,seq.asc"})

    def list_reports(self, loan_id, run_id) -> list[Report]:
        return self._rows(Report, "reports", {**self._eq(loan_id=loan_id, run_id=run_id), "order": "name.asc"})

    def get_report(self, loan_id, run_id, name) -> Report:
        row = self.client.select_one("reports", self._eq(loan_id=loan_id, run_id=run_id, name=name))
        if row is None:
            raise NotFound(f"report {name} not found for {loan_id}/{run_id}")
        return Report(**{k: v for k, v in row.items() if k in Report.model_fields})

    # ---- queues and decisions
    def review_queue(self, reviewer_role=None, loan_id=None) -> list[ReviewQueueEntry]:
        params = {**self._eq(reviewer_role=reviewer_role, loan_id=loan_id), "order": "blocking.desc,loan_id.asc,run_id.asc,target_id.asc"}
        return self._rows(ReviewQueueEntry, "v_review_queue", params)

    def create_run_request(self, loan_id, note, requested_by) -> RunRequest:
        row: dict[str, Any] = {"loan_id": loan_id, "note": note}
        if requested_by:
            row["requested_by"] = requested_by
        rows = self.client.insert("run_requests", [row])
        return RunRequest(**{k: v for k, v in rows[0].items() if k in RunRequest.model_fields})

    def list_run_requests(self, status=None, limit=100) -> list[RunRequest]:
        return self._rows(RunRequest, "run_requests", {**self._eq(status=status), "order": "requested_at.desc", "limit": max(1, limit)})

    def update_run_request(self, request_id, status, run_id=None) -> RunRequest:
        values: dict[str, Any] = {"status": status, "updated_at": now_iso()}
        if run_id:
            values["run_id"] = run_id
        rows = self.client.update("run_requests", self._eq(request_id=request_id), values)
        if not rows:
            raise NotFound(f"run request {request_id} not found")
        return RunRequest(**{k: v for k, v in rows[0].items() if k in RunRequest.model_fields})

    def add_review_decision(self, decision: ReviewDecision) -> ReviewDecision:
        self.get_run(decision.loan_id, decision.run_id)
        if decision.audit_type is None:
            known = self.client.select("review_items", {**self._eq(loan_id=decision.loan_id, run_id=decision.run_id, review_id=decision.target_id), "select": "review_id"})
        else:
            known = self.client.select("findings", {**self._eq(loan_id=decision.loan_id, run_id=decision.run_id, audit_type=decision.audit_type, finding_id=decision.target_id), "select": "finding_id"})
        if not known:
            raise Conflicted(f"target {decision.target_id} not found in run {decision.loan_id}/{decision.run_id}")
        row = decision.model_dump(exclude_none=True, exclude={"decision_id", "decided_at"})
        try:
            rows = self.client.insert("review_decisions", [row])
        except SupabaseError as exc:
            raise Conflicted(str(exc)) from exc
        return ReviewDecision(**{k: v for k, v in rows[0].items() if k in ReviewDecision.model_fields})

    def list_review_decisions(self, loan_id, run_id) -> list[ReviewDecision]:
        return self._rows(ReviewDecision, "review_decisions", {**self._eq(loan_id=loan_id, run_id=run_id), "order": "decided_at.asc"})

    def add_action_decision(self, decision: ActionDecision) -> ActionDecision:
        self.get_run(decision.loan_id, decision.run_id)
        known = self.client.select("proposed_actions", {**self._eq(loan_id=decision.loan_id, run_id=decision.run_id, audit_type=decision.audit_type, action_id=decision.action_id), "select": "action_id"})
        if not known:
            raise Conflicted(f"proposed action {decision.action_id} not found in run {decision.loan_id}/{decision.run_id}")
        row = decision.model_dump(exclude_none=True, exclude={"decision_id", "decided_at"})
        try:
            rows = self.client.insert("action_decisions", [row])
        except SupabaseError as exc:
            raise Conflicted(str(exc)) from exc
        return ActionDecision(**{k: v for k, v in rows[0].items() if k in ActionDecision.model_fields})

    def list_action_decisions(self, loan_id, run_id) -> list[ActionDecision]:
        return self._rows(ActionDecision, "action_decisions", {**self._eq(loan_id=loan_id, run_id=run_id), "order": "decided_at.asc"})

    # ---- evaluation and summary
    def add_eval_report(self, report: EvalReport) -> EvalReport:
        row = report.model_dump(exclude_none=True, exclude={"eval_id"})
        rows = self.client.insert("eval_reports", [row])
        return EvalReport(**{k: v for k, v in rows[0].items() if k in EvalReport.model_fields})

    def latest_eval_report(self) -> Optional[EvalReport]:
        row = self.client.select_one("eval_reports", {"order": "generated_at.desc"})
        return None if row is None else EvalReport(**{k: v for k, v in row.items() if k in EvalReport.model_fields})

    def dashboard_summary(self) -> DashboardSummary:
        row = self.client.select_one("v_dashboard_summary") or {}
        return DashboardSummary(**{k: int(v or 0) for k, v in row.items() if k in DashboardSummary.model_fields})
