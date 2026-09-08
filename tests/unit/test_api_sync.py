"""POST /api/sync/runs against a real intake output directory, plus path confinement."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.app import create_app
from api.routes.sync import confined_run_dir, fixture_manifest
from services.config import REPO_ROOT, Settings
from services.repository import InMemoryRepository

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "deidentified" / "LN-EDGE-CLEAN"
KEY = {"X-Service-Key": "local-dev"}


@pytest.fixture(scope="module")
def run_out(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("audits")
    cmd = [sys.executable, str(REPO_ROOT / "scripts" / "intake" / "inventory.py"), str(FIXTURE), "--run-id", "t1", "--out", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (out / "LN-EDGE-CLEAN" / "t1" / "document_inventory.json").is_file()
    return out


@pytest.fixture()
def client(run_out) -> TestClient:
    settings = Settings(backend="memory", output_dir=run_out)
    return TestClient(create_app(settings=settings, repository=InMemoryRepository()))


def test_fixture_manifest_is_read():
    allowlist, description = fixture_manifest("LN-EDGE-CLEAN")
    assert allowlist == ("12345678", "87654321")
    assert description.startswith("Small clean synthetic package")
    assert fixture_manifest("LN-NO-SUCH-FIXTURE") == ((), None)


def test_sync_loads_real_run_dir(client):
    r = client.post("/api/sync/runs", json={"loan_id": "LN-EDGE-CLEAN", "run_id": "t1"}, headers=KEY)
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["loan_id"] == "LN-EDGE-CLEAN" and run["run_id"] == "t1"
    assert run["synced_at"] and run["totals"]
    assert run["preapproval_present"] is False and run["overall_status"] is None
    loan = client.get("/api/loans/LN-EDGE-CLEAN").json()
    assert loan["description"].startswith("Small clean synthetic package")
    assert loan["source_root"].endswith("LN-EDGE-CLEAN")
    docs = client.get("/api/runs/LN-EDGE-CLEAN/t1/documents").json()
    assert len(docs) == 5
    assert all(d["filename"].endswith(".pdf") for d in docs)
    # re-sync is an upsert, not a duplicate
    r = client.post("/api/sync/runs", json={"loan_id": "LN-EDGE-CLEAN", "run_id": "t1"}, headers=KEY)
    assert r.status_code == 201
    assert client.get("/api/summary").json()["runs"] == 1


def test_sync_requires_service_key(client):
    body = {"loan_id": "LN-EDGE-CLEAN", "run_id": "t1"}
    assert client.post("/api/sync/runs", json=body).status_code == 401
    assert client.post("/api/sync/runs", json=body, headers={"X-Service-Key": "nope"}).status_code == 403


def test_sync_unknown_dir_is_404(client):
    r = client.post("/api/sync/runs", json={"loan_id": "LN-EDGE-CLEAN", "run_id": "never-ran"}, headers=KEY)
    assert r.status_code == 404


def test_sync_refuses_traversal_ids(client):
    for loan_id, run_id in (("../LN-EDGE-CLEAN", "t1"), ("LN-EDGE-CLEAN", "../t1"), ("..", ".."), ("/abs", "t1"), ("", "t1")):
        r = client.post("/api/sync/runs", json={"loan_id": loan_id, "run_id": run_id}, headers=KEY)
        assert r.status_code == 422, (loan_id, run_id, r.text)
    assert client.post("/api/sync/runs", json={"loan_id": "LN-EDGE-CLEAN", "run_id": "t1", "path": "/etc"}, headers=KEY).status_code == 422


def test_confined_run_dir_rejects_symlink_escape(tmp_path):
    out = tmp_path / "audits"
    (out / "LN-OK").mkdir(parents=True)
    outside = tmp_path / "elsewhere" / "run-x"
    outside.mkdir(parents=True)
    (out / "LN-OK" / "run-x").symlink_to(outside, target_is_directory=True)
    with pytest.raises(HTTPException) as exc:
        confined_run_dir(out, "LN-OK", "run-x")
    assert exc.value.status_code == 400
    (out / "LN-OK" / "run-y").mkdir()
    assert confined_run_dir(out, "LN-OK", "run-y") == (out / "LN-OK" / "run-y").resolve()


def test_sync_symlink_escape_is_400(tmp_path):
    out = tmp_path / "audits"
    (out / "LN-OK").mkdir(parents=True)
    outside = tmp_path / "elsewhere" / "run-x"
    outside.mkdir(parents=True)
    (out / "LN-OK" / "run-x").symlink_to(outside, target_is_directory=True)
    c = TestClient(create_app(settings=Settings(backend="memory", output_dir=out), repository=InMemoryRepository()))
    r = c.post("/api/sync/runs", json={"loan_id": "LN-OK", "run_id": "run-x"}, headers=KEY)
    assert r.status_code == 400 and "outside MPIRE_OUTPUT_DIR" in r.json()["detail"]


def test_sync_invalid_run_dir_is_400(tmp_path):
    out = tmp_path / "audits"
    run_dir = out / "LN-BAD" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "run_manifest.json").write_text(json.dumps({"loan_id": "LN-OTHER", "run_id": "r1"}))
    c = TestClient(create_app(settings=Settings(backend="memory", output_dir=out), repository=InMemoryRepository()))
    r = c.post("/api/sync/runs", json={"loan_id": "LN-BAD", "run_id": "r1"}, headers=KEY)
    assert r.status_code == 400 and "do not match" in r.json()["detail"]
    # empty directory: nothing loadable
    (out / "LN-EMPTY" / "r1").mkdir(parents=True)
    r = c.post("/api/sync/runs", json={"loan_id": "LN-EMPTY", "run_id": "r1"}, headers=KEY)
    assert r.status_code == 400
    assert c.get("/api/summary").json()["runs"] == 0


def test_sync_unmasked_pii_is_400(tmp_path):
    out = tmp_path / "audits"
    run_dir = out / "LN-PII" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "run_manifest.json").write_text(json.dumps({"loan_id": "LN-PII", "run_id": "r1", "skill": "x",
                                                             "note": "SSN 123-45-6789 appears here"}))
    c = TestClient(create_app(settings=Settings(backend="memory", output_dir=out), repository=InMemoryRepository()))
    r = c.post("/api/sync/runs", json={"loan_id": "LN-PII", "run_id": "r1"}, headers=KEY)
    assert r.status_code == 400 and "PII" in r.json()["detail"]
    assert "123-45-6789" not in r.text
