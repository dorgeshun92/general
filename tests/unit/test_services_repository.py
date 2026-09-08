"""InMemoryRepository behaviour, seeded with services.demo.seed_demo (synthetic LN-EXAMPLE-* data)."""
from __future__ import annotations

import json

import pytest

from services.demo import EXAMPLES, seed_demo
from services.models import ActionDecision, EvalReport, ReviewDecision, RunBundle
from services.repository import Conflicted, InMemoryRepository, NotFound, Repository
from services.run_loader import bundle_from_documents

L1, R1 = "LN-EXAMPLE-0001", "RUN-DEMO-0001"
L2, R2 = "LN-EXAMPLE-0002", "RUN-DEMO-0002"
L3, R3 = "LN-EXAMPLE-0003", "RUN-DEMO-0003"


def _example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def _bundle(loan_id: str, run_id: str, completed_at: str, audit_names=("audit_result.valid.submission_ready.json",),
            description=None) -> RunBundle:
    audits = [{**_example(n), "loan_id": loan_id, "run_id": run_id} for n in audit_names]
    manifest = {"schema_version": "1.0", "loan_id": loan_id, "run_id": run_id, "skill": "mortgage-file-audit",
                "started_at": completed_at, "completed_at": completed_at, "stop_condition": None,
                "completed_normally": True, "tool_versions": {}, "inputs": [], "outputs": [], "totals": {}}
    return bundle_from_documents(loan_id=loan_id, run_id=run_id, loan_file=None, inventory=None, audits=audits,
                                 manifest=manifest, reports={"report.md": "# masked\n"}, loan_description=description)


@pytest.fixture
def repo() -> InMemoryRepository:
    r = InMemoryRepository()
    seed_demo(r)
    return r


# ---- protocol / seed ---------------------------------------------------------------------

def test_in_memory_satisfies_protocol_and_seed_shape(repo):
    assert isinstance(repo, Repository)
    assert [l.loan_id for l in repo.list_loans()] == [L1, L2, L3]
    assert len(repo.runs) == 3
    assert all(l.deidentified for l in repo.list_loans())
    assert repo.get_loan(L1).description.startswith("Synthetic purchase file")
    with pytest.raises(NotFound):
        repo.get_loan("LN-NOPE")


def test_get_run_and_detail(repo):
    run = repo.get_run(L1, R1)
    assert run.overall_status == "READY" and run.preapproval_present and run.submission_present
    assert run.synced_at is not None and run.coverage_percent == "100.0"
    detail = repo.get_run_detail(L1, R1)
    assert detail.bundle.run == run and detail.bundle.loan.loan_id == L1
    assert len(detail.bundle.documents) == 7 and len(detail.bundle.findings) == 9
    assert len(detail.bundle.reports) == 1 and detail.review_decisions == [] and detail.action_decisions == []
    with pytest.raises(NotFound):
        repo.get_run(L1, "RUN-NOPE")
    with pytest.raises(NotFound):
        repo.get_run_detail("LN-NOPE", R1)


# ---- upsert ------------------------------------------------------------------------------

def test_upsert_is_idempotent_and_keeps_loan_created_at(repo):
    before = repo.get_run_detail(L1, R1)
    created = repo.get_loan(L1).created_at
    repo.upsert_run_bundle(before.bundle)
    after = repo.get_run_detail(L1, R1)
    assert len(repo.runs) == 3 and len(repo.loans) == 3
    assert after.bundle.findings == before.bundle.findings
    assert after.bundle.documents == before.bundle.documents
    assert repo.get_loan(L1).created_at == created


def test_upsert_replaces_children_rather_than_merging(repo):
    smaller = _bundle(L1, R1, "2026-09-09T00:00:00Z", ("audit_result.valid.submission_not_ready.json",),
                      description="replaced")
    run = repo.upsert_run_bundle(smaller)
    assert run.overall_status == "NOT_READY" and run.completed_at == "2026-09-09T00:00:00Z"
    assert len(repo.list_findings(L1, R1)) == 2          # 9 before; replaced, not merged
    assert repo.list_documents(L1, R1) == []              # no inventory in the new bundle
    assert repo.list_proposed_actions(L1, R1) == []
    assert repo.get_loan(L1).description == "replaced"
    assert len(repo.runs) == 3


def test_upsert_returns_run_with_synced_at_and_creates_loan():
    repo = InMemoryRepository()
    run = repo.upsert_run_bundle(_bundle("LN-NEW", "RUN-1", "2026-01-01T00:00:00Z"))
    assert run.synced_at and repo.get_loan("LN-NEW").created_at
    assert repo.list_reports("LN-NEW", "RUN-1")[0].name == "report.md"


# ---- ordering ----------------------------------------------------------------------------

def test_list_runs_newest_first_and_limit(repo):
    repo.upsert_run_bundle(_bundle(L1, "RUN-OLD", "2026-01-01T00:00:00Z"))
    repo.upsert_run_bundle(_bundle(L1, "RUN-NEW", "2026-12-31T00:00:00Z"))
    ids = [r.run_id for r in repo.list_runs(L1)]
    assert ids[0] == "RUN-NEW" and ids[-1] == "RUN-OLD" and len(ids) == 3
    assert [r.run_id for r in repo.list_runs(L1, limit=1)] == ["RUN-NEW"]
    assert [r.run_id for r in repo.list_runs(L1, limit=0)] == ["RUN-NEW"]  # limit floors at 1
    assert len(repo.list_runs()) == 5
    assert repo.list_runs("LN-NOPE") == []


def test_latest_runs_one_per_loan(repo):
    repo.upsert_run_bundle(_bundle(L2, "RUN-NEWER", "2026-12-31T00:00:00Z"))
    repo.upsert_run_bundle(_bundle(L2, "RUN-OLDER", "2025-01-01T00:00:00Z"))
    latest = repo.latest_runs()
    assert [r.loan_id for r in latest] == [L1, L2, L3]
    assert {r.loan_id: r.run_id for r in latest}[L2] == "RUN-NEWER"


# ---- findings ----------------------------------------------------------------------------

def test_list_findings_filters_and_ordering(repo):
    rows = repo.list_findings(L1, R1)
    assert len(rows) == 9
    assert [f.blocking for f in rows] == sorted((f.blocking for f in rows), reverse=True)  # blocking first
    assert len(repo.list_findings(L1, R1, audit_type="PREAPPROVAL")) == 5
    assert len(repo.list_findings(L1, R1, audit_type="SUBMISSION_READINESS")) == 4
    assert {f.result for f in repo.list_findings(L1, R1, result="REVIEW")} == {"REVIEW"}
    assert len(repo.list_findings(L1, R1, result="REVIEW")) == 2
    assert all(f.blocking for f in repo.list_findings(L1, R1, blocking=True))
    assert len(repo.list_findings(L1, R1, blocking=False)) == 3
    assert [f.rule_id for f in repo.list_findings(L1, R1, rule_id="PRE-EXAMPLE-004")] == ["PRE-EXAMPLE-004"]
    assert repo.list_findings(L1, R1, result="FAIL", audit_type="SUBMISSION_READINESS") == []
    with pytest.raises(NotFound):
        repo.list_findings(L1, "RUN-NOPE")


def test_search_findings(repo):
    hits = repo.search_findings("PRE-EXAMPLE-004")
    assert hits and all(h.rule_id == "PRE-EXAMPLE-004" for h in hits)
    assert [(h.loan_id, h.run_id) for h in hits] == sorted((h.loan_id, h.run_id) for h in hits)
    assert repo.search_findings("   ") == []
    assert len(repo.search_findings("pre-example", limit=2)) == 2
    assert len(repo.search_findings("pre-example", limit=0)) == 1
    assert repo.search_findings("no such phrase zzz") == []


# ---- child lists -------------------------------------------------------------------------

def test_child_lists_and_reports(repo):
    assert len(repo.list_documents(L1, R1)) == 7
    assert [ri.review_id for ri in repo.list_review_items(L1, R1)] == ["RV-001"]
    assert len(repo.list_missing_documents(L1, R1)) >= 1
    assert isinstance(repo.list_conflicts(L1, R1), list)
    assert [a.action_id for a in repo.list_proposed_actions(L1, R1)] == ["PA-001"]
    assert isinstance(repo.list_approvals_required(L1, R1), list)
    report = repo.get_report(L1, R1, "report.md")
    assert report.content_md.startswith("# DEMO REPORT") and len(report.sha256) == 64
    with pytest.raises(NotFound):
        repo.get_report(L1, R1, "missing.md")
    with pytest.raises(NotFound):
        repo.list_documents("LN-NOPE", R1)


# ---- review queue and decisions ----------------------------------------------------------

def test_review_queue_contents_and_filters(repo):
    queue = repo.review_queue()
    assert len(queue) == 6
    assert {e.kind for e in queue} == {"FINDING", "REVIEW_ITEM"}
    assert all(e.reviewer_role == "PROCESSOR" for e in queue)
    assert repo.review_queue(reviewer_role="UNDERWRITER") == []
    assert len(repo.review_queue(reviewer_role="PROCESSOR")) == 6
    assert {e.loan_id for e in repo.review_queue(loan_id=L2)} == {L2}
    assert len(repo.review_queue(loan_id=L2)) == 1  # RV-001 only; the NOT_READY audit has no REVIEW finding


def test_review_queue_excludes_decided_targets(repo):
    finding_entries = [e for e in repo.review_queue(loan_id=L1) if e.kind == "FINDING"]
    assert {e.audit_type for e in finding_entries} == {"PREAPPROVAL", "SUBMISSION_READINESS"}
    d = repo.add_review_decision(ReviewDecision(loan_id=L1, run_id=R1, audit_type="PREAPPROVAL", target_id="F-004",
                                                decision="CONFIRMED", decided_by="u1"))
    assert d.decision_id and d.decided_at
    remaining = repo.review_queue(loan_id=L1)
    assert ("PREAPPROVAL", "F-004") not in {(e.audit_type, e.target_id) for e in remaining}
    assert ("SUBMISSION_READINESS", "F-004") in {(e.audit_type, e.target_id) for e in remaining}  # other audit untouched
    repo.add_review_decision(ReviewDecision(loan_id=L1, run_id=R1, audit_type=None, target_id="RV-001", decision="NEEDS_INFO"))
    assert all(e.kind != "REVIEW_ITEM" for e in repo.review_queue(loan_id=L1))
    assert len(repo.list_review_decisions(L1, R1)) == 2
    assert repo.list_review_decisions(L2, R2) == []


def test_review_decision_on_unknown_target_raises_conflicted(repo):
    with pytest.raises(Conflicted):
        repo.add_review_decision(ReviewDecision(loan_id=L1, run_id=R1, audit_type="PREAPPROVAL", target_id="F-999", decision="CONFIRMED"))
    with pytest.raises(Conflicted):  # F-004 exists only in audits, not as a review item
        repo.add_review_decision(ReviewDecision(loan_id=L1, run_id=R1, audit_type=None, target_id="F-004", decision="CONFIRMED"))
    with pytest.raises(NotFound):
        repo.add_review_decision(ReviewDecision(loan_id=L1, run_id="RUN-NOPE", audit_type=None, target_id="RV-001", decision="CONFIRMED"))
    assert repo.review_decisions == []


def test_action_decisions(repo):
    with pytest.raises(Conflicted):
        repo.add_action_decision(ActionDecision(loan_id=L1, run_id=R1, audit_type="SUBMISSION_READINESS", action_id="PA-001", decision="ACCEPTED"))
    with pytest.raises(NotFound):
        repo.add_action_decision(ActionDecision(loan_id="LN-NOPE", run_id=R1, audit_type="PREAPPROVAL", action_id="PA-001", decision="ACCEPTED"))
    stored = repo.add_action_decision(ActionDecision(loan_id=L1, run_id=R1, audit_type="PREAPPROVAL", action_id="PA-001",
                                                     decision="DEFERRED", note="wait", decided_by="u2"))
    assert stored.decision_id and stored.decided_at and stored.note == "wait"
    assert repo.list_action_decisions(L1, R1) == [stored]
    assert repo.list_action_decisions(L3, R3) == []
    assert repo.get_run_detail(L1, R1).action_decisions == [stored]


# ---- run requests ------------------------------------------------------------------------

def test_run_request_lifecycle(repo):
    req = repo.create_run_request(L2, note="re-run after new paystub", requested_by="u1")
    assert req.status == "QUEUED" and req.request_id and req.requested_at and req.run_id is None
    assert [r.request_id for r in repo.list_run_requests(status="QUEUED")] == [req.request_id]
    assert repo.list_run_requests(status="COMPLETED") == []
    picked = repo.update_run_request(req.request_id, "PICKED_UP")
    assert picked.status == "PICKED_UP" and picked.run_id is None
    done = repo.update_run_request(req.request_id, "COMPLETED", run_id="RUN-DEMO-0009")
    assert done.status == "COMPLETED" and done.run_id == "RUN-DEMO-0009"
    assert repo.list_run_requests()[0].status == "COMPLETED"
    assert repo.list_run_requests(status="QUEUED") == []
    with pytest.raises(NotFound):
        repo.update_run_request("no-such-id", "REJECTED")


# ---- eval reports ------------------------------------------------------------------------

def test_eval_reports_latest_by_generated_at(repo):
    assert repo.latest_eval_report() is None
    older = repo.add_eval_report(EvalReport(generated_at="2026-01-01T00:00:00Z", all_targets_met=False, targets={"pii": {"status": "MISSED"}}, report={}))
    newer = repo.add_eval_report(EvalReport(generated_at="2026-06-01T00:00:00Z", all_targets_met=True, targets={"pii": {"status": "MET"}}, report={}))
    stamped = repo.add_eval_report(EvalReport(all_targets_met=True, targets={}, report={}))
    assert older.eval_id and newer.eval_id and stamped.generated_at  # ids and timestamps filled in
    assert repo.latest_eval_report().eval_id == stamped.eval_id     # now() is later than the fixed stamps


# ---- dashboard summary -------------------------------------------------------------------

def test_dashboard_summary_math(repo):
    s = repo.dashboard_summary()
    assert s.model_dump() == {"loans": 3, "runs": 3, "ready": 1, "not_ready": 1, "human_review": 0, "no_gate": 1,
                              "blocking_open": 3, "review_queue": 6, "queued_requests": 0, "pending_actions": 2}
    repo.add_action_decision(ActionDecision(loan_id=L1, run_id=R1, audit_type="PREAPPROVAL", action_id="PA-001", decision="ACCEPTED"))
    repo.add_review_decision(ReviewDecision(loan_id=L3, run_id=R3, audit_type=None, target_id="RV-001", decision="CONFIRMED"))
    req = repo.create_run_request(L1, None, None)
    s = repo.dashboard_summary()
    assert (s.pending_actions, s.review_queue, s.queued_requests) == (1, 5, 1)
    repo.update_run_request(req.request_id, "REJECTED")
    assert repo.dashboard_summary().queued_requests == 0
    # only the latest run per loan counts toward the gate tallies
    repo.upsert_run_bundle(_bundle(L2, "RUN-LATER", "2027-01-01T00:00:00Z"))   # READY, blocking_open 0
    s = repo.dashboard_summary()
    assert (s.runs, s.ready, s.not_ready, s.blocking_open) == (4, 2, 0, 2)
