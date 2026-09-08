"""Static bootstrap, security headers, request ids, access log hygiene, and OpenAPI coverage."""
from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient

from api.app import create_app
from api.routes import static as static_routes
from services.config import Settings
from services.demo import seed_demo
from services.repository import InMemoryRepository

CONTRACT_PATHS = {
    "/api/health", "/api/summary", "/api/loans", "/api/loans/{loan_id}", "/api/loans/{loan_id}/runs",
    "/api/runs", "/api/runs/latest", "/api/runs/{loan_id}/{run_id}", "/api/runs/{loan_id}/{run_id}/findings",
    "/api/runs/{loan_id}/{run_id}/documents", "/api/runs/{loan_id}/{run_id}/reports",
    "/api/runs/{loan_id}/{run_id}/reports/{name}", "/api/findings/search", "/api/review-queue",
    "/api/review-decisions", "/api/action-decisions", "/api/run-requests", "/api/run-requests/{request_id}",
    "/api/eval/latest", "/api/eval", "/api/sync/runs", "/", "/config.js",
}
CONTRACT_METHODS = {
    ("get", "/api/health"), ("get", "/api/summary"), ("get", "/api/loans"), ("get", "/api/loans/{loan_id}"),
    ("get", "/api/loans/{loan_id}/runs"), ("get", "/api/runs"), ("get", "/api/runs/latest"),
    ("get", "/api/runs/{loan_id}/{run_id}"), ("get", "/api/runs/{loan_id}/{run_id}/findings"),
    ("get", "/api/runs/{loan_id}/{run_id}/documents"), ("get", "/api/runs/{loan_id}/{run_id}/reports"),
    ("get", "/api/runs/{loan_id}/{run_id}/reports/{name}"), ("get", "/api/findings/search"),
    ("get", "/api/review-queue"), ("post", "/api/review-decisions"), ("post", "/api/action-decisions"),
    ("get", "/api/run-requests"), ("post", "/api/run-requests"), ("patch", "/api/run-requests/{request_id}"),
    ("get", "/api/eval/latest"), ("post", "/api/eval"), ("post", "/api/sync/runs"), ("get", "/"), ("get", "/config.js"),
}


def _client(settings: Settings | None = None) -> TestClient:
    repo = InMemoryRepository()
    seed_demo(repo)
    return TestClient(create_app(settings=settings or Settings(backend="memory"), repository=repo))


def test_openapi_has_every_contract_path():
    c = _client()
    spec = c.get("/api/openapi.json").json()
    assert spec["info"]["title"] == "Mpire Mortgage Ops API"
    assert spec["info"]["version"] == "1.0.0"
    assert CONTRACT_PATHS <= set(spec["paths"])
    declared = {(m, p) for p, ops in spec["paths"].items() for m in ops}
    assert CONTRACT_METHODS <= declared
    assert spec["paths"]["/api/review-decisions"]["post"]["responses"].keys() >= {"201"}
    assert spec["paths"]["/api/sync/runs"]["post"]["responses"].keys() >= {"201"}
    assert c.get("/api/docs").status_code == 200


def test_security_headers_on_every_response():
    c = _client()
    for path in ("/api/health", "/", "/config.js", "/api/nope", "/api/loans/LN-NOPE"):
        r = c.get(path)
        assert r.headers["x-content-type-options"] == "nosniff", path
        assert r.headers["referrer-policy"] == "no-referrer", path
        assert r.headers["x-frame-options"] == "DENY", path
        csp = r.headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert "script-src 'self' https://cdn.jsdelivr.net" in csp
        assert "connect-src 'self' https://*.supabase.co" in csp
        assert "'unsafe-inline' https://cdn.jsdelivr.net" not in csp.split("script-src")[1].split(";")[0]
        assert "frame-ancestors 'none'" in csp
    docs = c.get("/api/docs").headers["content-security-policy"]
    assert "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net" in docs
    assert "supabase" not in docs


def test_request_id_generated_or_echoed():
    c = _client()
    generated = c.get("/api/health").headers["x-request-id"]
    assert len(generated) == 32
    echoed = c.get("/api/health", headers={"X-Request-ID": "trace-123.abc"}).headers["x-request-id"]
    assert echoed == "trace-123.abc"
    bad = c.get("/api/health", headers={"X-Request-ID": "<script>" + "x" * 100}).headers["x-request-id"]
    assert bad != "<script>" + "x" * 100 and len(bad) == 32


def test_access_log_has_no_headers_or_query_strings(caplog):
    c = _client()
    with caplog.at_level(logging.INFO, logger="mpire.api.access"):
        c.get("/api/findings/search?q=PRE-&access_token=SECRET-TOKEN", headers={"Authorization": "Bearer SECRET-BEARER"})
    lines = [rec.getMessage() for rec in caplog.records if rec.name == "mpire.api.access"]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["path"] == "/api/findings/search" and entry["method"] == "GET" and entry["status"] == 200
    assert entry["caller"] == "local-dev" and entry["request_id"]
    assert "SECRET" not in lines[0] and "q=" not in lines[0] and "Authorization" not in lines[0]


def test_config_js_memory_backend():
    r = _client().get("/config.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/javascript")
    assert r.headers["cache-control"] == "no-store"
    assert r.text.startswith("window.MPIRE_CONFIG = {")
    cfg = json.loads(r.text[len("window.MPIRE_CONFIG = "):].rstrip().rstrip(";"))
    assert cfg == {"backend": "memory", "demo": True, "supabaseUrl": None, "supabaseAnonKey": None, "apiBase": "/api"}


def test_config_js_supabase_never_contains_service_key():
    settings = Settings(backend="supabase", supabase_url="https://x.supabase.co", supabase_anon_key="anon-public",
                        supabase_service_role_key="SERVICE-ROLE-SECRET", supabase_jwt_secret="JWT-SECRET")
    c = TestClient(create_app(settings=settings, repository=InMemoryRepository()))
    r = c.get("/config.js")
    assert r.status_code == 200
    cfg = json.loads(r.text[len("window.MPIRE_CONFIG = "):].rstrip().rstrip(";"))
    assert cfg == {"backend": "supabase", "demo": False, "supabaseUrl": "https://x.supabase.co",
                   "supabaseAnonKey": "anon-public", "apiBase": "/api"}
    assert "SERVICE-ROLE-SECRET" not in r.text and "JWT-SECRET" not in r.text
    health = c.get("/api/health")
    assert health.status_code == 200 and health.json()["demo"] is False
    assert "SERVICE-ROLE-SECRET" not in health.text


def test_index_placeholder_or_dashboard(monkeypatch, tmp_path):
    c = _client()
    monkeypatch.setattr(static_routes, "DASHBOARD_DIR", tmp_path / "missing")
    r = c.get("/")
    assert r.status_code == 200 and "being built" in r.text and r.headers["content-type"].startswith("text/html")
    built = tmp_path / "dashboard"
    built.mkdir()
    (built / "index.html").write_text("<!doctype html><title>Real dashboard</title>")
    monkeypatch.setattr(static_routes, "DASHBOARD_DIR", built)
    r = c.get("/")
    assert r.status_code == 200 and "Real dashboard" in r.text


def test_dashboard_mount_serves_files_when_present(tmp_path, monkeypatch):
    built = tmp_path / "dashboard"
    built.mkdir()
    (built / "index.html").write_text("<!doctype html><title>Mounted</title>")
    (built / "app.js").write_text("console.log('hi')")
    monkeypatch.setattr(static_routes, "DASHBOARD_DIR", built)
    import api.app as app_module
    monkeypatch.setattr(app_module, "DASHBOARD_DIR", built)
    c = _client()
    assert c.get("/dashboard/app.js").text == "console.log('hi')"
    assert "Mounted" in c.get("/dashboard/").text
    assert c.get("/dashboard/nope.js").status_code == 404


def test_dashboard_mount_404_when_directory_missing(tmp_path, monkeypatch):
    import api.app as app_module
    monkeypatch.setattr(app_module, "DASHBOARD_DIR", tmp_path / "not-yet")
    c = _client()
    assert c.get("/dashboard/app.js").status_code == 404
    assert c.get("/api/health").status_code == 200


def test_local_service_key_not_accepted_on_supabase_backend_by_default(monkeypatch):
    monkeypatch.delenv("MPIRE_ALLOW_LOCAL_SERVICE", raising=False)
    settings = Settings(backend="supabase", supabase_url="https://x.supabase.co", supabase_anon_key="anon",
                        supabase_service_role_key="svc")
    c = TestClient(create_app(settings=settings, repository=InMemoryRepository()))
    r = c.post("/api/eval", json={"all_targets_met": True, "targets": {}, "report": {}}, headers={"X-Service-Key": "local-dev"})
    assert r.status_code == 403
    monkeypatch.setenv("MPIRE_ALLOW_LOCAL_SERVICE", "1")
    c = TestClient(create_app(settings=settings, repository=InMemoryRepository()))
    r = c.post("/api/eval", json={"all_targets_met": True, "targets": {}, "report": {}}, headers={"X-Service-Key": "local-dev"})
    assert r.status_code == 201
