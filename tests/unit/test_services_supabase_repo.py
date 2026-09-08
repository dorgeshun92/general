"""SupabaseRepository through httpx.MockTransport: exact PostgREST requests for every method."""
from __future__ import annotations

import json
from typing import Callable

import httpx
import pytest

from services.demo import EXAMPLES, seed_demo
from services.models import ActionDecision, EvalReport, ReviewDecision
from services.repository import Conflicted, InMemoryRepository, NotFound
from services.run_loader import bundle_from_documents
from services.supabase_client import SupabaseClient, SupabaseError
from services.supabase_repo import SupabaseRepository, _pct, _run_from_row, _run_to_row

URL = "https://proj.supabase.co"
KEY = "service-role-SECRET"
L, R = "LN-EXAMPLE-0001", "RUN-DEMO-0001"

RUN_ROW = {"loan_id": L, "run_id": R, "skill": "mortgage-file-audit", "started_at": None,
           "completed_at": "2026-09-08T12:00:00+00:00", "completed_normally": True, "stop_condition": None,
           "overall_status": "READY", "preapproval_present": True, "submission_present": True,
           "los_export_present": True, "catalog_version": "1.0", "catalog_reviewed": None,
           "counts": {"PASS": 2}, "blocking_open": 0, "coverage_percent": 100, "known_limitations": [],
           "tool_versions": None, "manifest": None, "totals": None, "synced_at": "2026-09-08T12:00:01+00:00",
           "description": "carried by v_latest_runs only"}
LOAN_ROW = {"loan_id": L, "description": "demo", "deidentified": True, "source_root": None,
            "created_at": "2026-09-08T00:00:00+00:00", "updated_at": "2026-09-08T00:00:00+00:00"}


class Fake:
    """Records every request; answers by (method, table) from `routes`, else 200 []."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], object] = {}

    def route(self, method: str, table: str, response) -> "Fake":
        self.routes[(method, table)] = response
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        table = request.url.path.split("/rest/v1/", 1)[1]
        resp = self.routes.get((request.method, table))
        if callable(resp):
            return resp(request)
        if isinstance(resp, httpx.Response):
            return resp
        return httpx.Response(200, json=[] if resp is None else resp)

    def trail(self) -> list[tuple[str, str]]:
        return [(r.method, r.url.path.split("/rest/v1/", 1)[1]) for r in self.calls]

    def params(self, i: int = 0) -> dict:
        return dict(self.calls[i].url.params)

    def body(self, i: int = 0):
        return json.loads(self.calls[i].content)


@pytest.fixture
def fake() -> Fake:
    return Fake()


@pytest.fixture
def repo(fake) -> SupabaseRepository:
    return SupabaseRepository(SupabaseClient(URL, KEY, transport=httpx.MockTransport(fake)))


def _eq(**kv):
    return {k: f"eq.{v}" for k, v in kv.items()}


# ---- helpers -------------------------------------------------------------------------------

def test_pct_and_run_row_round_trip():
    assert _pct(None) is None and _pct(100) == "100.00" and _pct(83.3) == "83.30" and _pct("66.666") == "66.67"
    run = _run_from_row(RUN_ROW)
    assert run.coverage_percent == "100.00" and run.overall_status == "READY"
    assert "description" not in run.model_dump()
    row = _run_to_row(run)
    assert "synced_at" not in row and row["coverage_percent"] == "100.00"
    assert _run_to_row(run.model_copy(update={"coverage_percent": None}))["coverage_percent"] is None
    assert _run_to_row(run.model_copy(update={"coverage_percent": "83.3"}))["coverage_percent"] == "83.3"


# ---- loans / runs --------------------------------------------------------------------------

def test_list_loans_and_get_loan(fake, repo):
    fake.route("GET", "loans", [LOAN_ROW])
    loans = repo.list_loans()
    assert [l.loan_id for l in loans] == [L] and loans[0].description == "demo"
    assert fake.params(0) == {"select": "*", "order": "loan_id.asc"}
    assert repo.get_loan(L).loan_id == L
    assert fake.params(1) == {"select": "*", "loan_id": f"eq.{L}", "limit": "1"}


def test_get_loan_not_found_on_empty_select(fake, repo):
    with pytest.raises(NotFound):
        repo.get_loan("LN-NOPE")
    assert fake.trail() == [("GET", "loans")]


def test_list_runs_params_and_numeric_coverage(fake, repo):
    fake.route("GET", "runs", [RUN_ROW, {**RUN_ROW, "run_id": "R2", "coverage_percent": None}])
    runs = repo.list_runs(L, limit=5)
    assert fake.params() == {"select": "*", "loan_id": f"eq.{L}", "order": "completed_at.desc.nullslast,synced_at.desc", "limit": "5"}
    assert runs[0].coverage_percent == "100.00" and runs[1].coverage_percent is None
    repo.list_runs(limit=0)
    assert fake.params(1) == {"select": "*", "order": "completed_at.desc.nullslast,synced_at.desc", "limit": "1"}


def test_latest_runs_uses_view(fake, repo):
    fake.route("GET", "v_latest_runs", [RUN_ROW])
    runs = repo.latest_runs()
    assert fake.trail() == [("GET", "v_latest_runs")] and fake.params() == {"select": "*", "order": "loan_id.asc"}
    assert runs[0].run_id == R and runs[0].coverage_percent == "100.00"


def test_get_run_and_not_found(fake, repo):
    fake.route("GET", "runs", [RUN_ROW])
    assert repo.get_run(L, R).overall_status == "READY"
    assert fake.params() == {"select": "*", "loan_id": f"eq.{L}", "run_id": f"eq.{R}", "limit": "1"}
    fake.route("GET", "runs", [])
    with pytest.raises(NotFound):
        repo.get_run(L, "RUN-NOPE")


def test_get_run_detail_sequence(fake, repo):
    fake.route("GET", "runs", [RUN_ROW]).route("GET", "loans", [LOAN_ROW])
    detail = repo.get_run_detail(L, R)
    assert detail.bundle.run.run_id == R and detail.bundle.loan.loan_id == L
    assert detail.bundle.findings == [] and detail.review_decisions == []
    assert fake.trail() == [("GET", "runs"), ("GET", "loans"), ("GET", "documents"), ("GET", "findings"),
                            ("GET", "review_items"), ("GET", "missing_documents"), ("GET", "conflicts"),
                            ("GET", "proposed_actions"), ("GET", "approvals_required"), ("GET", "reports"),
                            ("GET", "review_decisions"), ("GET", "action_decisions")]


# ---- upsert_run_bundle ---------------------------------------------------------------------

def test_upsert_run_bundle_delete_then_insert_ordering(fake, repo):
    mem = InMemoryRepository()
    seed_demo(mem)
    bundle = mem.get_run_detail(L, R).bundle
    fake.route("GET", "runs", [RUN_ROW])
    run = repo.upsert_run_bundle(bundle)
    assert run.run_id == R and run.coverage_percent == "100.00"
    trail = fake.trail()
    assert trail[:3] == [("POST", "loans"), ("DELETE", "runs"), ("POST", "runs")]
    assert trail[3:-1] == [("POST", "documents"), ("POST", "findings"), ("POST", "review_items"),
                           ("POST", "missing_documents"), ("POST", "conflicts"), ("POST", "proposed_actions"),
                           ("POST", "approvals_required"), ("POST", "reports")]
    assert trail[-1] == ("GET", "runs")
    # loans upsert: merge on loan_id, no created_at
    loans_req = fake.calls[0]
    assert loans_req.url.params["on_conflict"] == "loan_id"
    assert loans_req.headers["Prefer"] == "resolution=merge-duplicates,return=representation"
    loan_body = fake.body(0)[0]
    assert loan_body["loan_id"] == L and "created_at" not in loan_body and loan_body["updated_at"]
    assert loan_body["deidentified"] is True
    # delete is filtered on both key columns and asks for no body
    assert dict(fake.calls[1].url.params) == {"loan_id": f"eq.{L}", "run_id": f"eq.{R}"}
    assert fake.calls[1].headers["Prefer"] == "return=minimal"
    # run row: no synced_at, decimal-string coverage
    run_body = fake.body(2)[0]
    assert "synced_at" not in run_body and run_body["coverage_percent"] == "100.0"
    assert run_body["overall_status"] == "READY"
    assert fake.calls[2].headers["Prefer"] == "return=representation"
    # children carry the run key on every row
    for i, name in enumerate(("documents", "findings"), start=3):
        rows = fake.body(i)
        assert rows and all(r["loan_id"] == L and r["run_id"] == R for r in rows)
    assert len(fake.body(3)) == 7 and len(fake.body(4)) == 9
    assert fake.body(4)[0]["audit_type"] in ("PREAPPROVAL", "SUBMISSION_READINESS")


def test_upsert_run_bundle_skips_empty_child_tables(fake, repo):
    audit = {**json.loads((EXAMPLES / "audit_result.valid.submission_not_ready.json").read_text(encoding="utf-8")),
             "loan_id": L, "run_id": R}
    bundle = bundle_from_documents(loan_id=L, run_id=R, loan_file=None, inventory=None, audits=[audit], manifest=None, reports={})
    fake.route("GET", "runs", [RUN_ROW])
    repo.upsert_run_bundle(bundle)
    trail = fake.trail()
    assert trail[:3] == [("POST", "loans"), ("DELETE", "runs"), ("POST", "runs")] and trail[-1] == ("GET", "runs")
    posted = {t for m, t in trail if m == "POST"}
    assert "findings" in posted and "missing_documents" in posted
    assert not posted & {"documents", "review_items", "proposed_actions", "reports"}   # empty lists: no request


def test_upsert_run_bundle_propagates_supabase_error_without_key(fake, repo):
    mem = InMemoryRepository()
    seed_demo(mem)
    bundle = mem.get_run_detail(L, R).bundle
    fake.route("POST", "runs", httpx.Response(500, json={"message": "insert failed"}))
    with pytest.raises(SupabaseError) as exc:
        repo.upsert_run_bundle(bundle)
    assert exc.value.status == 500 and exc.value.path == "runs" and KEY not in str(exc.value)
    assert fake.trail() == [("POST", "loans"), ("DELETE", "runs"), ("POST", "runs")]  # stopped at the failure


# ---- children ------------------------------------------------------------------------------

@pytest.mark.parametrize("method, table, order", [
    ("list_documents", "documents", "document_id.asc"),
    ("list_review_items", "review_items", "review_id.asc"),
    ("list_missing_documents", "missing_documents", "audit_type.asc,seq.asc"),
    ("list_conflicts", "conflicts", "conflict_id.asc"),
    ("list_proposed_actions", "proposed_actions", "audit_type.asc,action_id.asc"),
    ("list_approvals_required", "approvals_required", "audit_type.asc,seq.asc"),
    ("list_reports", "reports", "name.asc"),
    ("list_review_decisions", "review_decisions", "decided_at.asc"),
    ("list_action_decisions", "action_decisions", "decided_at.asc"),
])
def test_child_list_params(fake, repo, method, table, order):
    assert getattr(repo, method)(L, R) == []
    assert fake.trail() == [("GET", table)]
    assert fake.params() == {"select": "*", "loan_id": f"eq.{L}", "run_id": f"eq.{R}", "order": order}


def test_child_rows_ignore_unknown_columns(fake, repo):
    fake.route("GET", "documents", [{"loan_id": L, "run_id": R, "document_id": "DOC-001", "filename": "a.pdf",
                                     "sha256": "a" * 64, "document_type": "PAYSTUB", "classification_confidence": "HIGH",
                                     "status": "OK", "extra_column": "ignored"}])
    docs = repo.list_documents(L, R)
    assert docs[0].document_id == "DOC-001" and docs[0].page_count is None


def test_list_findings_filters(fake, repo):
    repo.list_findings(L, R)
    assert fake.params(0) == {"select": "*", "loan_id": f"eq.{L}", "run_id": f"eq.{R}", "order": "blocking.desc,audit_type.asc,finding_id.asc"}
    repo.list_findings(L, R, audit_type="PREAPPROVAL", result="REVIEW", blocking=True, rule_id="PRE-1")
    p = fake.params(1)
    assert (p["audit_type"], p["result"], p["rule_id"], p["blocking"]) == ("eq.PREAPPROVAL", "eq.REVIEW", "eq.PRE-1", "is.true")
    repo.list_findings(L, R, blocking=False)
    assert fake.params(2)["blocking"] == "is.false"


def test_search_findings_rpc(fake, repo):
    finding = {"loan_id": L, "run_id": R, "audit_type": "PREAPPROVAL", "finding_id": "F-1", "rule_id": "PRE-1",
               "result": "PASS", "blocking": True, "evidence_ids": ["E-1"], "explanation": "ok", "confidence": "HIGH"}
    fake.route("POST", "rpc/search_findings", [finding])
    hits = repo.search_findings("  paystub ", limit=9999)
    assert hits[0].finding_id == "F-1"
    assert fake.trail() == [("POST", "rpc/search_findings")]
    assert fake.body() == {"q": "paystub", "max_rows": 500}
    assert repo.search_findings("   ") == [] and len(fake.calls) == 1  # no request for an empty query


def test_get_report_and_not_found(fake, repo):
    fake.route("GET", "reports", [{"loan_id": L, "run_id": R, "name": "report.md", "content_md": "# x", "sha256": "b" * 64}])
    assert repo.get_report(L, R, "report.md").content_md == "# x"
    assert fake.params() == {"select": "*", "loan_id": f"eq.{L}", "run_id": f"eq.{R}", "name": "eq.report.md", "limit": "1"}
    fake.route("GET", "reports", [])
    with pytest.raises(NotFound):
        repo.get_report(L, R, "nope.md")


# ---- queue / requests / decisions ----------------------------------------------------------

def test_review_queue_view_and_filters(fake, repo):
    entry = {"loan_id": L, "run_id": R, "audit_type": "PREAPPROVAL", "target_id": "F-4", "kind": "FINDING",
             "rule_id": "PRE-4", "reviewer_role": "PROCESSOR", "reason": "why", "blocking": False, "explanation": "e"}
    fake.route("GET", "v_review_queue", [entry])
    assert repo.review_queue()[0].target_id == "F-4"
    assert fake.params(0) == {"select": "*", "order": "blocking.desc,loan_id.asc,run_id.asc,target_id.asc"}
    repo.review_queue(reviewer_role="UNDERWRITER", loan_id=L)
    assert fake.params(1)["reviewer_role"] == "eq.UNDERWRITER" and fake.params(1)["loan_id"] == f"eq.{L}"


def test_create_run_request(fake, repo):
    fake.route("POST", "run_requests", [{"request_id": "r-1", "loan_id": L, "status": "QUEUED", "note": "n",
                                         "requested_by": "u-1", "requested_at": "2026-09-08T00:00:00+00:00"}])
    req = repo.create_run_request(L, "n", "u-1")
    assert req.request_id == "r-1" and req.status == "QUEUED"
    assert fake.body() == [{"loan_id": L, "note": "n", "requested_by": "u-1"}]
    repo.create_run_request(L, None, None)
    assert fake.body(1) == [{"loan_id": L, "note": None}]  # requested_by left to auth.uid() default


def test_list_and_update_run_requests(fake, repo):
    repo.list_run_requests(status="QUEUED", limit=7)
    assert fake.params() == {"select": "*", "status": "eq.QUEUED", "order": "requested_at.desc", "limit": "7"}
    fake.route("PATCH", "run_requests", [{"request_id": "r-1", "loan_id": L, "status": "COMPLETED", "run_id": "RUN-9"}])
    updated = repo.update_run_request("r-1", "COMPLETED", run_id="RUN-9")
    assert updated.status == "COMPLETED" and updated.run_id == "RUN-9"
    patch = fake.calls[1]
    assert patch.method == "PATCH" and dict(patch.url.params) == {"request_id": "eq.r-1"}
    body = fake.body(1)
    assert body["status"] == "COMPLETED" and body["run_id"] == "RUN-9" and body["updated_at"]
    fake.route("PATCH", "run_requests", [])
    with pytest.raises(NotFound):
        repo.update_run_request("r-missing", "REJECTED")
    assert "run_id" not in fake.body(2)


def test_add_review_decision_finding_and_review_item(fake, repo):
    fake.route("GET", "runs", [RUN_ROW]).route("GET", "findings", [{"finding_id": "F-4"}])
    fake.route("POST", "review_decisions", [{"decision_id": "d-1", "loan_id": L, "run_id": R, "audit_type": "PREAPPROVAL",
                                             "target_id": "F-4", "decision": "CONFIRMED", "decided_by": "u-1",
                                             "decided_at": "2026-09-08T00:00:00+00:00"}])
    stored = repo.add_review_decision(ReviewDecision(loan_id=L, run_id=R, audit_type="PREAPPROVAL", target_id="F-4",
                                                     decision="CONFIRMED", decided_by="u-1"))
    assert stored.decision_id == "d-1"
    assert fake.trail() == [("GET", "runs"), ("GET", "findings"), ("POST", "review_decisions")]
    p = fake.params(1)
    assert (p["select"], p["audit_type"], p["finding_id"]) == ("finding_id", "eq.PREAPPROVAL", "eq.F-4")
    assert fake.body(2) == [{"loan_id": L, "run_id": R, "audit_type": "PREAPPROVAL", "target_id": "F-4",
                             "decision": "CONFIRMED", "decided_by": "u-1"}]
    fake.route("GET", "review_items", [{"review_id": "RV-1"}])
    repo.add_review_decision(ReviewDecision(loan_id=L, run_id=R, audit_type=None, target_id="RV-1", decision="NEEDS_INFO"))
    assert fake.trail()[3:] == [("GET", "runs"), ("GET", "review_items"), ("POST", "review_decisions")]
    assert fake.params(4)["select"] == "review_id" and fake.params(4)["review_id"] == "eq.RV-1"
    assert "audit_type" not in fake.body(5)[0]


def test_add_review_decision_conflicts(fake, repo):
    fake.route("GET", "runs", [RUN_ROW])
    with pytest.raises(Conflicted):  # target not in findings
        repo.add_review_decision(ReviewDecision(loan_id=L, run_id=R, audit_type="PREAPPROVAL", target_id="F-9", decision="CONFIRMED"))
    assert ("POST", "review_decisions") not in fake.trail()
    fake.route("GET", "findings", [{"finding_id": "F-4"}])
    fake.route("POST", "review_decisions", httpx.Response(409, json={"message": "duplicate key value"}))
    with pytest.raises(Conflicted, match="409"):
        repo.add_review_decision(ReviewDecision(loan_id=L, run_id=R, audit_type="PREAPPROVAL", target_id="F-4", decision="CONFIRMED"))
    fake.route("GET", "runs", [])
    with pytest.raises(NotFound):
        repo.add_review_decision(ReviewDecision(loan_id=L, run_id="RUN-NOPE", audit_type="PREAPPROVAL", target_id="F-4", decision="CONFIRMED"))


def test_add_action_decision(fake, repo):
    fake.route("GET", "runs", [RUN_ROW])
    with pytest.raises(Conflicted):
        repo.add_action_decision(ActionDecision(loan_id=L, run_id=R, audit_type="PREAPPROVAL", action_id="PA-9", decision="ACCEPTED"))
    fake.route("GET", "proposed_actions", [{"action_id": "PA-1"}])
    fake.route("POST", "action_decisions", [{"decision_id": "a-1", "loan_id": L, "run_id": R, "audit_type": "PREAPPROVAL",
                                             "action_id": "PA-1", "decision": "REJECTED"}])
    stored = repo.add_action_decision(ActionDecision(loan_id=L, run_id=R, audit_type="PREAPPROVAL", action_id="PA-1", decision="REJECTED"))
    assert stored.decision_id == "a-1"
    assert fake.trail()[-3:] == [("GET", "runs"), ("GET", "proposed_actions"), ("POST", "action_decisions")]
    assert fake.params(len(fake.calls) - 2)["select"] == "action_id"
    assert fake.body(len(fake.calls) - 1) == [{"loan_id": L, "run_id": R, "audit_type": "PREAPPROVAL", "action_id": "PA-1", "decision": "REJECTED"}]
    fake.route("POST", "action_decisions", httpx.Response(403, json={"message": "row-level security"}))
    with pytest.raises(Conflicted):
        repo.add_action_decision(ActionDecision(loan_id=L, run_id=R, audit_type="PREAPPROVAL", action_id="PA-1", decision="REJECTED"))


# ---- eval / summary ------------------------------------------------------------------------

def test_eval_reports(fake, repo):
    fake.route("POST", "eval_reports", [{"eval_id": "e-1", "generated_at": "2026-09-08T00:00:00+00:00",
                                         "all_targets_met": True, "targets": {"pii": {"status": "MET"}}, "report": {"x": 1}}])
    stored = repo.add_eval_report(EvalReport(all_targets_met=True, targets={"pii": {"status": "MET"}}, report={"x": 1}))
    assert stored.eval_id == "e-1"
    assert fake.body() == [{"all_targets_met": True, "targets": {"pii": {"status": "MET"}}, "report": {"x": 1}}]
    assert repo.latest_eval_report() is None
    assert fake.params(1) == {"select": "*", "order": "generated_at.desc", "limit": "1"}
    fake.route("GET", "eval_reports", [{"eval_id": "e-1", "all_targets_met": False, "targets": {}, "report": {}}])
    assert repo.latest_eval_report().all_targets_met is False


def test_dashboard_summary_ints(fake, repo):
    fake.route("GET", "v_dashboard_summary", [{"loans": 3, "runs": "4", "ready": None, "review_queue": 2, "unknown": 9}])
    s = repo.dashboard_summary()
    assert (s.loans, s.runs, s.ready, s.review_queue, s.pending_actions) == (3, 4, 0, 2, 0)
    fake.route("GET", "v_dashboard_summary", [])
    assert repo.dashboard_summary().loans == 0


# ---- bearer forwarding / errors ------------------------------------------------------------

def test_for_bearer_forwards_user_jwt(fake, repo):
    user_repo = repo.for_bearer("user-jwt")
    assert isinstance(user_repo, SupabaseRepository) and user_repo is not repo
    user_repo.list_loans()
    repo.list_loans()
    assert fake.calls[0].headers["Authorization"] == "Bearer user-jwt" and fake.calls[0].headers["apikey"] == KEY
    assert fake.calls[1].headers["Authorization"] == f"Bearer {KEY}"


def test_supabase_error_propagates_with_status_and_no_key(fake, repo):
    fake.route("GET", "loans", httpx.Response(503, json={"message": "service unavailable"}))
    with pytest.raises(SupabaseError) as exc:
        repo.list_loans()
    assert exc.value.status == 503 and exc.value.message == "service unavailable"
    assert KEY not in str(exc.value) and KEY not in repr(exc.value)
