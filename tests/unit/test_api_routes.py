"""Memory-backend contract tests for every /api route: happy paths, filters, and error mapping."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from services.config import Settings
from services.demo import seed_demo
from services.repository import InMemoryRepository

LOAN, RUN = "LN-EXAMPLE-0001", "RUN-DEMO-0001"


@pytest.fixture()
def repo() -> InMemoryRepository:
    r = InMemoryRepository()
    seed_demo(r)
    return r


@pytest.fixture()
def client(repo) -> TestClient:
    return TestClient(create_app(settings=Settings(backend="memory"), repository=repo))


# ---- health / summary / loans

def test_health_no_auth(client):
    body = client.get("/api/health").json()
    assert body == {"status": "ok", "backend": "memory", "version": "1.0.0", "demo": True}


def test_summary(client):
    body = client.get("/api/summary").json()
    assert body["loans"] == 3 and body["runs"] == 3
    assert body["ready"] == 1 and body["not_ready"] == 1 and body["no_gate"] == 1


def test_loans_join_latest_run(client):
    rows = client.get("/api/loans").json()
    assert [r["loan_id"] for r in rows] == ["LN-EXAMPLE-0001", "LN-EXAMPLE-0002", "LN-EXAMPLE-0003"]
    assert rows[0]["latest_run"]["run_id"] == RUN
    assert rows[0]["latest_run"]["overall_status"] == "READY"
    assert rows[2]["latest_run"]["overall_status"] is None


def test_loans_join_null_when_no_run(client, repo):
    from services.models import Loan
    repo.loans["LN-NORUN"] = Loan(loan_id="LN-NORUN")
    rows = {r["loan_id"]: r for r in client.get("/api/loans").json()}
    assert rows["LN-NORUN"]["latest_run"] is None


def test_get_loan_and_404(client):
    assert client.get(f"/api/loans/{LOAN}").json()["loan_id"] == LOAN
    r = client.get("/api/loans/LN-NOPE")
    assert r.status_code == 404 and r.json() == {"detail": "loan LN-NOPE not found"}


def test_loan_runs_and_404(client):
    rows = client.get(f"/api/loans/{LOAN}/runs").json()
    assert [r["run_id"] for r in rows] == [RUN]
    assert client.get("/api/loans/LN-NOPE/runs").status_code == 404


# ---- runs

def test_runs_limit(client):
    assert len(client.get("/api/runs").json()) == 3
    assert len(client.get("/api/runs?limit=2").json()) == 2
    assert client.get("/api/runs?limit=0").status_code == 422
    assert client.get("/api/runs?limit=501").status_code == 422
    assert client.get("/api/runs?limit=abc").status_code == 422


def test_runs_latest_one_per_loan(client):
    rows = client.get("/api/runs/latest").json()
    assert sorted(r["loan_id"] for r in rows) == ["LN-EXAMPLE-0001", "LN-EXAMPLE-0002", "LN-EXAMPLE-0003"]


def test_run_detail_and_404(client):
    body = client.get(f"/api/runs/{LOAN}/{RUN}").json()
    assert body["bundle"]["run"]["run_id"] == RUN
    assert body["bundle"]["loan"]["loan_id"] == LOAN
    assert body["bundle"]["findings"] and body["bundle"]["documents"]
    assert body["review_decisions"] == [] and body["action_decisions"] == []
    r = client.get(f"/api/runs/{LOAN}/RUN-NOPE")
    assert r.status_code == 404 and "not found" in r.json()["detail"]


def test_findings_filters_and_order(client):
    rows = client.get(f"/api/runs/{LOAN}/{RUN}/findings").json()
    assert rows, "demo run has findings"
    blocking_flags = [f["blocking"] for f in rows]
    assert blocking_flags == sorted(blocking_flags, reverse=True), "blocking first"
    assert all(f["audit_type"] == "PREAPPROVAL" for f in client.get(f"/api/runs/{LOAN}/{RUN}/findings?audit_type=PREAPPROVAL").json())
    assert all(f["result"] == "REVIEW" for f in client.get(f"/api/runs/{LOAN}/{RUN}/findings?result=REVIEW").json())
    assert all(f["blocking"] is True for f in client.get(f"/api/runs/{LOAN}/{RUN}/findings?blocking=true").json())
    assert all(f["blocking"] is False for f in client.get(f"/api/runs/{LOAN}/{RUN}/findings?blocking=false").json())
    rule = rows[0]["rule_id"]
    by_rule = client.get(f"/api/runs/{LOAN}/{RUN}/findings?rule_id={rule}").json()
    assert by_rule and all(f["rule_id"] == rule for f in by_rule)
    assert client.get(f"/api/runs/{LOAN}/{RUN}/findings?rule_id=NOPE-999").json() == []
    assert client.get(f"/api/runs/{LOAN}/{RUN}/findings?result=BOGUS").status_code == 422
    assert client.get(f"/api/runs/{LOAN}/{RUN}/findings?audit_type=BOGUS").status_code == 422
    assert client.get(f"/api/runs/{LOAN}/RUN-NOPE/findings").status_code == 404


def test_documents(client):
    rows = client.get(f"/api/runs/{LOAN}/{RUN}/documents").json()
    assert rows and all(d["loan_id"] == LOAN for d in rows)
    assert all(len(d["sha256"]) == 64 for d in rows)
    assert client.get(f"/api/runs/{LOAN}/RUN-NOPE/documents").status_code == 404


def test_reports_list_has_no_content(client):
    rows = client.get(f"/api/runs/{LOAN}/{RUN}/reports").json()
    assert rows == [{"name": "report.md", "sha256": rows[0]["sha256"]}]
    assert "content_md" not in rows[0]
    assert client.get(f"/api/runs/{LOAN}/RUN-NOPE/reports").status_code == 404


def test_report_markdown(client):
    r = client.get(f"/api/runs/{LOAN}/{RUN}/reports/report.md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert r.text.startswith("# DEMO REPORT")
    missing = client.get(f"/api/runs/{LOAN}/{RUN}/reports/nope.md")
    assert missing.status_code == 404 and missing.json()["detail"].startswith("report nope.md not found")


# ---- search

def test_findings_search(client):
    rows = client.get("/api/findings/search?q=PRE-").json()
    assert rows and all("PRE-" in f["rule_id"] for f in rows)
    assert len(client.get("/api/findings/search?q=PRE-&limit=1").json()) == 1
    assert client.get("/api/findings/search?q=zzz-no-such-text").json() == []
    assert client.get("/api/findings/search").status_code == 422
    assert client.get("/api/findings/search?q=").status_code == 422


# ---- review queue and decisions

def test_review_queue_filters(client):
    rows = client.get("/api/review-queue").json()
    assert rows
    flags = [e["blocking"] for e in rows]
    assert flags == sorted(flags, reverse=True)
    role = rows[0]["reviewer_role"]
    assert all(e["reviewer_role"] == role for e in client.get(f"/api/review-queue?reviewer_role={role}").json())
    assert all(e["loan_id"] == LOAN for e in client.get(f"/api/review-queue?loan_id={LOAN}").json())
    assert client.get("/api/review-queue?loan_id=LN-NOPE").json() == []
    assert client.get("/api/review-queue?reviewer_role=JANITOR").status_code == 422


def test_review_decision_removes_target_from_queue_and_stamps_caller(client):
    entry = next(e for e in client.get("/api/review-queue").json() if e["kind"] == "FINDING")
    body = {"loan_id": entry["loan_id"], "run_id": entry["run_id"], "audit_type": entry["audit_type"],
            "target_id": entry["target_id"], "decision": "CONFIRMED", "note": "looks right",
            "decided_by": "attacker", "decision_id": "forged"}
    r = client.post("/api/review-decisions", json=body)
    assert r.status_code == 201, r.text
    stored = r.json()
    assert stored["decided_by"] == "local-dev"
    assert stored["decision_id"] != "forged" and stored["decided_at"]
    assert stored["note"] == "looks right"
    remaining = client.get("/api/review-queue").json()
    assert not any(e["target_id"] == entry["target_id"] and e["run_id"] == entry["run_id"]
                   and e["audit_type"] == entry["audit_type"] for e in remaining)
    detail = client.get(f"/api/runs/{entry['loan_id']}/{entry['run_id']}").json()
    assert [d["decision_id"] for d in detail["review_decisions"]] == [stored["decision_id"]]


def test_review_decision_on_review_item(client):
    entry = next(e for e in client.get("/api/review-queue").json() if e["kind"] == "REVIEW_ITEM")
    body = {"loan_id": entry["loan_id"], "run_id": entry["run_id"], "audit_type": None,
            "target_id": entry["target_id"], "decision": "NEEDS_INFO"}
    r = client.post("/api/review-decisions", json=body)
    assert r.status_code == 201 and r.json()["audit_type"] is None
    assert not any(e["target_id"] == entry["target_id"] and e["kind"] == "REVIEW_ITEM"
                   and e["run_id"] == entry["run_id"] for e in client.get("/api/review-queue").json())


def test_review_decision_errors(client):
    base = {"loan_id": LOAN, "run_id": RUN, "audit_type": "PREAPPROVAL", "decision": "CONFIRMED"}
    r = client.post("/api/review-decisions", json={**base, "target_id": "F-NOPE"})
    assert r.status_code == 409 and r.json() == {"detail": f"target F-NOPE not found in run {LOAN}/{RUN}"}
    r = client.post("/api/review-decisions", json={**base, "run_id": "RUN-NOPE", "target_id": "F-001"})
    assert r.status_code == 404
    r = client.post("/api/review-decisions", json={**base, "target_id": "F-001", "decision": "MAYBE"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    r = client.post("/api/review-decisions", json={"loan_id": LOAN})
    assert r.status_code == 422


def test_action_decision_lifecycle(client):
    detail = client.get(f"/api/runs/{LOAN}/{RUN}").json()
    action = detail["bundle"]["proposed_actions"][0]
    before = client.get("/api/summary").json()["pending_actions"]
    body = {"loan_id": LOAN, "run_id": RUN, "audit_type": action["audit_type"], "action_id": action["action_id"],
            "decision": "ACCEPTED", "decided_by": "attacker"}
    r = client.post("/api/action-decisions", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["decided_by"] == "local-dev" and r.json()["decision_id"]
    assert client.get("/api/summary").json()["pending_actions"] == before - 1
    assert client.get(f"/api/runs/{LOAN}/{RUN}").json()["action_decisions"][0]["action_id"] == action["action_id"]


def test_action_decision_errors(client):
    body = {"loan_id": LOAN, "run_id": RUN, "audit_type": "SUBMISSION_READINESS", "action_id": "A-NOPE", "decision": "REJECTED"}
    assert client.post("/api/action-decisions", json=body).status_code == 409
    assert client.post("/api/action-decisions", json={**body, "run_id": "RUN-NOPE"}).status_code == 404
    assert client.post("/api/action-decisions", json={**body, "audit_type": None}).status_code == 422
    assert client.post("/api/action-decisions", json={**body, "decision": "YOLO"}).status_code == 422


# ---- run requests

def test_run_request_lifecycle(client):
    assert client.get("/api/run-requests").json() == []
    r = client.post("/api/run-requests", json={"loan_id": LOAN, "note": "re-run after new paystub", "requested_by": "attacker", "status": "COMPLETED"})
    assert r.status_code == 201, r.text
    req = r.json()
    assert req["status"] == "QUEUED" and req["requested_by"] == "local-dev" and req["request_id"]
    assert req["note"] == "re-run after new paystub" and req["run_id"] is None
    assert client.get("/api/summary").json()["queued_requests"] == 1
    assert [x["request_id"] for x in client.get("/api/run-requests?status=QUEUED").json()] == [req["request_id"]]
    assert client.get("/api/run-requests?status=COMPLETED").json() == []

    r = client.patch(f"/api/run-requests/{req['request_id']}", json={"status": "PICKED_UP"})
    assert r.status_code == 200 and r.json()["status"] == "PICKED_UP"
    r = client.patch(f"/api/run-requests/{req['request_id']}", json={"status": "COMPLETED", "run_id": RUN})
    assert r.status_code == 200 and r.json()["run_id"] == RUN and r.json()["status"] == "COMPLETED"
    assert client.get("/api/run-requests?status=COMPLETED").json()[0]["request_id"] == req["request_id"]
    assert client.get("/api/summary").json()["queued_requests"] == 0


def test_run_request_errors(client):
    assert client.post("/api/run-requests", json={}).status_code == 422
    assert client.post("/api/run-requests", json={"loan_id": "../x"}).status_code == 422
    assert client.patch("/api/run-requests/nope", json={"status": "PICKED_UP"}).status_code == 404
    r = client.post("/api/run-requests", json={"loan_id": LOAN})
    rid = r.json()["request_id"]
    assert client.patch(f"/api/run-requests/{rid}", json={"status": "DONE"}).status_code == 422
    assert client.patch(f"/api/run-requests/{rid}", json={"status": "COMPLETED", "extra": 1}).status_code == 422
    assert client.get("/api/run-requests?status=BOGUS").status_code == 422


# ---- eval

EVAL_RAW = {
    "generated_at": "2026-09-08T10:00:00Z", "mode": "compare-only", "exit_code": 0,
    "aggregate": {"all_targets_met": True, "targets": {"classification": {"status": "MET", "value": "100% (5/5)"}}},
    "fixtures": [], "skipped_no_answer_key": [], "answer_key_proposals": [],
}


def test_eval_latest_null_then_upload(client):
    r = client.get("/api/eval/latest")
    assert r.status_code == 200 and r.json() is None
    r = client.post("/api/eval", json=EVAL_RAW, headers={"X-Service-Key": "local-dev"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["eval_id"] and body["all_targets_met"] is True
    assert body["targets"] == EVAL_RAW["aggregate"]["targets"]
    assert body["report"]["mode"] == "compare-only"
    assert body["generated_at"] == "2026-09-08T10:00:00Z"
    latest = client.get("/api/eval/latest").json()
    assert latest["eval_id"] == body["eval_id"]


def test_eval_accepts_model_shape_and_rejects_garbage(client):
    shaped = {"all_targets_met": False, "targets": {"pii": {"status": "MISSED"}}, "report": {"x": 1}}
    r = client.post("/api/eval", json=shaped, headers={"X-Service-Key": "local-dev"})
    assert r.status_code == 201 and r.json()["all_targets_met"] is False
    r = client.post("/api/eval", json={"hello": "world"}, headers={"X-Service-Key": "local-dev"})
    assert r.status_code == 422
    r = client.post("/api/eval", json={"aggregate": {"targets": {}}}, headers={"X-Service-Key": "local-dev"})
    assert r.status_code == 422
    r = client.post("/api/eval", json=[1, 2], headers={"X-Service-Key": "local-dev"})
    assert r.status_code == 422


def test_service_routes_reject_missing_or_wrong_key(client):
    for path, body in (("/api/eval", EVAL_RAW), ("/api/sync/runs", {"loan_id": LOAN, "run_id": RUN})):
        r = client.post(path, json=body)
        assert r.status_code == 401 and r.json() == {"detail": "missing X-Service-Key"}
        r = client.post(path, json=body, headers={"X-Service-Key": "wrong"})
        assert r.status_code == 403 and r.json() == {"detail": "invalid service key"}
        r = client.post(path, json=body, headers={"X-Service-Key": ""})
        assert r.status_code == 401


def test_unknown_route_is_json_404(client):
    r = client.get("/api/nothing-here")
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}


def test_create_app_without_repository_seeds_demo():
    app = create_app(settings=Settings(backend="memory"))
    c = TestClient(app)
    assert c.get("/api/summary").json()["loans"] == 3
    # one shared repository: a write is visible on the next request
    c.post("/api/run-requests", json={"loan_id": LOAN})
    assert c.get("/api/summary").json()["queued_requests"] == 1
