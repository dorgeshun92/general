"""Supabase-backend auth: bearer required, HS256 verification, JWT forwarded to PostgREST, service key gate.

PostgREST is replaced by an httpx.MockTransport so no network is touched and every forwarded
header can be inspected.
"""
from __future__ import annotations

import json
import time

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from api.app import _wire_repositories, create_app
from fastapi import FastAPI
from services.config import Settings
from services.supabase_client import SupabaseClient
from services.supabase_repo import SupabaseRepository

SECRET = "unit-test-hs256-secret-that-is-32-bytes!"
SETTINGS = Settings(backend="supabase", supabase_url="https://x.supabase.co", supabase_anon_key="anon",
                    supabase_service_role_key="svc", supabase_jwt_secret=SECRET)
UNVERIFIED = Settings(backend="supabase", supabase_url="https://x.supabase.co", supabase_anon_key="anon",
                      supabase_service_role_key="svc", supabase_jwt_secret=None)
USER = "11111111-2222-3333-4444-555555555555"

RUN_ROW = {"loan_id": "LN-1", "run_id": "R-1", "preapproval_present": True, "submission_present": False,
           "coverage_percent": 100, "known_limitations": [], "synced_at": "2026-09-08T00:00:00Z"}


def _token(secret=SECRET, aud="authenticated", sub=USER, exp_delta=3600):
    return jwt.encode({"sub": sub, "aud": aud, "role": "authenticated", "exp": int(time.time()) + exp_delta}, secret, algorithm="HS256")


class FakePostgrest:
    """Records every request and answers just enough of the schema for the routes under test."""

    def __init__(self, fail_status: int | None = None):
        self.requests: list[httpx.Request] = []
        self.fail_status = fail_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_status:
            return httpx.Response(self.fail_status, json={"message": "row values: SECRET-ROW-DATA", "code": "42501"})
        path = request.url.path.removeprefix("/rest/v1/")
        if request.method == "GET":
            if path == "loans":
                return httpx.Response(200, json=[{"loan_id": "LN-1", "deidentified": True}])
            if path == "v_latest_runs":
                return httpx.Response(200, json=[{**RUN_ROW, "description": "demo"}])
            if path == "runs":
                return httpx.Response(200, json=[RUN_ROW])
            if path == "findings":
                return httpx.Response(200, json=[{"finding_id": "F-001"}])
            if path == "v_dashboard_summary":
                return httpx.Response(200, json=[{"loans": 1, "runs": 1}])
            if path == "eval_reports":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[])
        if request.method == "POST":
            rows = json.loads(request.content)
            if path == "review_decisions":
                return httpx.Response(201, json=[{**rows[0], "decision_id": "d-1", "decided_at": "2026-09-08T00:00:00Z",
                                                  "decided_by": rows[0].get("decided_by", "db-default-uid")}])
            if path == "eval_reports":
                return httpx.Response(201, json=[{**rows[0], "eval_id": "e-1", "generated_at": "2026-09-08T00:00:00Z"}])
            if path == "run_requests":
                return httpx.Response(201, json=[{**rows[0], "request_id": "rq-1", "status": "QUEUED",
                                                  "requested_by": rows[0].get("requested_by", "db-default-uid")}])
        return httpx.Response(404, json={"message": "unhandled in fake"})


def _client(settings=SETTINGS, fake: FakePostgrest | None = None):
    fake = fake or FakePostgrest()
    repo = SupabaseRepository(SupabaseClient(settings.supabase_url, settings.supabase_anon_key, transport=httpx.MockTransport(fake)))
    return TestClient(create_app(settings=settings, repository=repo)), fake


def test_health_and_config_need_no_auth():
    c, fake = _client()
    assert c.get("/api/health").status_code == 200
    assert c.get("/config.js").status_code == 200
    assert fake.requests == []


def test_401_without_bearer():
    c, fake = _client()
    for path in ("/api/summary", "/api/loans", "/api/review-queue", "/api/runs/LN-1/R-1"):
        r = c.get(path)
        assert r.status_code == 401 and r.json() == {"detail": "missing bearer token"}, path
        assert r.headers["www-authenticate"] == "Bearer"
    assert c.get("/api/loans", headers={"Authorization": "Basic abc"}).status_code == 401
    assert c.get("/api/loans", headers={"Authorization": "Bearer "}).status_code == 401
    assert c.post("/api/run-requests", json={"loan_id": "LN-1"}).status_code == 401
    assert fake.requests == [], "nothing is forwarded to PostgREST without a token"


@pytest.mark.parametrize("bad", [
    "not-a-jwt",
    _token(secret="wrong-secret-also-thirty-two-bytes-long"),
    _token(aud="anon"),
    _token(exp_delta=-10),
    _token(sub=""),
])
def test_401_with_bad_jwt(bad):
    c, fake = _client()
    r = c.get("/api/loans", headers={"Authorization": f"Bearer {bad}"})
    assert r.status_code == 401
    assert r.json()["detail"] in ("invalid or expired bearer token", "bearer token has no subject")
    assert bad not in r.text
    assert fake.requests == []


def test_200_with_valid_jwt_and_token_forwarded_to_postgrest():
    c, fake = _client()
    token = _token()
    r = c.get("/api/loans", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()[0]["loan_id"] == "LN-1" and r.json()[0]["latest_run"]["run_id"] == "R-1"
    assert r.json()[0]["latest_run"]["coverage_percent"] == "100.00"
    assert fake.requests, "PostgREST was called"
    for req in fake.requests:
        assert req.headers["authorization"] == f"Bearer {token}"
        assert req.headers["apikey"] == "anon"
    assert "svc" not in {req.headers["authorization"] for req in fake.requests}


def test_decided_by_is_verified_sub_not_client_value():
    c, fake = _client()
    token = _token()
    body = {"loan_id": "LN-1", "run_id": "R-1", "audit_type": "PREAPPROVAL", "target_id": "F-001",
            "decision": "CONFIRMED", "decided_by": "someone-else"}
    r = c.post("/api/review-decisions", json=body, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201, r.text
    assert r.json()["decided_by"] == USER
    insert = next(req for req in fake.requests if req.method == "POST" and req.url.path.endswith("/review_decisions"))
    sent = json.loads(insert.content)[0]
    assert sent["decided_by"] == USER
    assert insert.headers["authorization"] == f"Bearer {token}"


def test_run_request_requested_by_is_caller():
    c, fake = _client()
    token = _token()
    r = c.post("/api/run-requests", json={"loan_id": "LN-1", "requested_by": "spoof"}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201, r.text
    sent = json.loads(fake.requests[-1].content)[0]
    assert sent["requested_by"] == USER and r.json()["requested_by"] == USER


def test_unverified_mode_forwards_token_and_leaves_stamp_to_database():
    """Without SUPABASE_JWT_SECRET the API cannot know the user id: it forwards the token (RLS decides)
    and omits decided_by so the column default auth.uid() applies."""
    c, fake = _client(UNVERIFIED)
    opaque = "opaque.token.value"
    r = c.get("/api/summary", headers={"Authorization": f"Bearer {opaque}"})
    assert r.status_code == 200, r.text
    assert fake.requests[-1].headers["authorization"] == f"Bearer {opaque}"
    body = {"loan_id": "LN-1", "run_id": "R-1", "audit_type": "PREAPPROVAL", "target_id": "F-001", "decision": "CONFIRMED"}
    r = c.post("/api/review-decisions", json=body, headers={"Authorization": f"Bearer {opaque}"})
    assert r.status_code == 201, r.text
    sent = json.loads(fake.requests[-1].content)[0]
    assert "decided_by" not in sent
    assert r.json()["decided_by"] == "db-default-uid"


def test_postgrest_rejection_maps_to_401_without_leaking_message():
    c, _ = _client(fake=FakePostgrest(fail_status=401))
    r = c.get("/api/loans", headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 401 and "SECRET-ROW-DATA" not in r.text


def test_postgrest_failure_maps_to_502_sanitized():
    c, _ = _client(fake=FakePostgrest(fail_status=500))
    r = c.get("/api/loans", headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 502
    assert r.json() == {"detail": "datastore error (HTTP 500)"}
    assert "SECRET-ROW-DATA" not in r.text


def test_service_routes_need_service_key(monkeypatch):
    monkeypatch.delenv("MPIRE_ALLOW_LOCAL_SERVICE", raising=False)
    c, fake = _client()
    body = {"all_targets_met": True, "targets": {}, "report": {"x": 1}}
    r = c.post("/api/eval", json=body)
    assert r.status_code == 401 and fake.requests == []
    r = c.post("/api/eval", json=body, headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 401, "a user JWT is not a service key"
    r = c.post("/api/eval", json=body, headers={"X-Service-Key": "wrong"})
    assert r.status_code == 403 and fake.requests == []
    r = c.post("/api/eval", json=body, headers={"X-Service-Key": "local-dev"})
    assert r.status_code == 403, "local-dev is not accepted on a supabase backend unless MPIRE_ALLOW_LOCAL_SERVICE=1"
    r = c.post("/api/eval", json=body, headers={"X-Service-Key": "svc"})
    assert r.status_code == 201, r.text
    assert r.json()["eval_id"] == "e-1"
    assert "svc" not in r.text
    # sync is service-only too
    r = c.post("/api/sync/runs", json={"loan_id": "LN-1", "run_id": "R-1"})
    assert r.status_code == 401
    r = c.post("/api/sync/runs", json={"loan_id": "LN-1", "run_id": "R-1"}, headers={"X-Service-Key": "nope"})
    assert r.status_code == 403


def test_real_wiring_uses_service_key_for_service_repo_and_anon_for_users():
    app = FastAPI()
    _wire_repositories(app, SETTINGS, None)
    service_headers = app.state.service_repository.client._headers
    user_headers = app.state.user_repository.client._headers
    assert service_headers["Authorization"] == "Bearer svc" and service_headers["apikey"] == "svc"
    assert user_headers["apikey"] == "anon" and user_headers["Authorization"] == "Bearer anon"
    per_request = app.state.user_repository.for_bearer("user-jwt").client._headers
    assert per_request["Authorization"] == "Bearer user-jwt" and per_request["apikey"] == "anon"
    assert app.state.repository is None
