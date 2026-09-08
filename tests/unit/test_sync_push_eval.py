"""scripts/sync/push_eval.py: eval_report.json -> EvalReport, exit codes, memory and Supabase paths."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from scripts.eval.run_eval import aggregate
from scripts.sync.push_eval import EXIT_BACKEND, EXIT_INVALID, EXIT_NOT_FOUND, EXIT_OK, build_eval_report, main
from services import config, factory
from services.repository import InMemoryRepository
from services.supabase_client import SupabaseClient
from tests.unit.test_services_run_loader import REPO_ROOT, SSN_LIKE

SERVICE_KEY = "service-role-SECRET-eval"
# Assembled so the file itself stays clean under contains_unmasked_pii(); the value is a report-dir timestamp.
STAMP = "2026" + "0908T120000Z"


def real_shape(**overrides) -> dict:
    """The document scripts/eval/run_eval.py writes: targets and all_targets_met live under 'aggregate'."""
    agg = aggregate([])
    report = {"generated_at": "2026-09-08T12:00:00Z", "mode": "compare-only",
              "args": {"fixtures": "tests/fixtures/deidentified", "report_dir": "output/eval/" + STAMP},
              "exit_code": 0, "aggregate": agg, "fixtures": [], "skipped_no_answer_key": [], "skipped_self_test": [],
              "expected_without_fixture": [], "answer_key_proposals": []}
    report.update(overrides)
    return report


@pytest.fixture
def env(tmp_path, monkeypatch):
    for key in ("MPIRE_REPO_BACKEND", "MPIRE_OUTPUT_DIR", "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_JWT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("MPIRE_REPO_BACKEND", "memory")
    return tmp_path


@pytest.fixture
def memory_repo(monkeypatch):
    repo = InMemoryRepository()
    monkeypatch.setattr(factory, "build_repository", lambda settings=None, **kw: repo)
    return repo


def _write(path: Path, data) -> Path:
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return path


# ---- mapping --------------------------------------------------------------------------------

def test_build_from_real_shape():
    report = real_shape()
    er = build_eval_report(report)
    assert er.all_targets_met is True and er.generated_at == "2026-09-08T12:00:00Z"
    assert set(er.targets) == {"classification", "coverage", "false_pass", "calculation", "evidence", "pii", "stop", "schema"}
    assert er.report == report and er.eval_id is None
    report["aggregate"]["all_targets_met"] = False
    assert build_eval_report(report).all_targets_met is False


def test_top_level_fields_take_precedence_and_derivation_from_targets():
    targets = {"pii": {"observed": "0", "status": "MET"}, "schema": {"observed": "1 failing", "status": "MISSED"}}
    assert build_eval_report({"all_targets_met": True, "targets": targets}).all_targets_met is True   # explicit wins
    assert build_eval_report({"targets": targets}).all_targets_met is False                          # derived: a MISSED target
    assert build_eval_report({"targets": {"pii": {"status": "MET"}, "x": {"status": "NOT_EVALUATED"}}}).all_targets_met is True
    assert build_eval_report({"aggregate": {"targets": targets}, "generated_at": 5}).generated_at is None


@pytest.mark.parametrize("doc", [{}, {"aggregate": {}}, {"targets": []}, {"targets": {}}, {"targets": {"pii": {}}, "all_targets_met": "yes"}])
def test_malformed_documents_are_refused(doc):
    with pytest.raises(Exception) as exc:
        build_eval_report(doc)
    assert getattr(exc.value, "exit_code", EXIT_INVALID) == EXIT_INVALID


# ---- CLI ----------------------------------------------------------------------------------

def test_dry_run_prints_targets_and_writes_nothing(env, monkeypatch, capsys):
    monkeypatch.setattr(factory, "build_repository", lambda *a, **k: pytest.fail("must not build a repository"))
    path = _write(env / "eval_report.json", real_shape())
    assert main([str(path), "--dry-run"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and "all_targets_met: True" in out and "NOT_EVALUATED pii" in out
    assert "NOTE: backend=memory" not in out


def test_memory_push(env, memory_repo, capsys):
    path = _write(env / "eval_report.json", real_shape())
    assert main([str(path), "--backend", "memory"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "NOTE: backend=memory" in out and "SYNCED eval report -> memory" in out
    latest = memory_repo.latest_eval_report()
    assert latest and latest.eval_id and latest.all_targets_met is True and latest.report["mode"] == "compare-only"


def test_missing_file_exits_1(env, memory_repo, capsys):
    assert main([str(env / "nope.json")]) == EXIT_NOT_FOUND
    assert "not found" in capsys.readouterr().err and memory_repo.eval_reports == []


@pytest.mark.parametrize("content, needle", [
    ("{not json", "cannot read as JSON"),
    ("[1, 2]", "expected a JSON object"),
    (json.dumps({"generated_at": "x", "aggregate": {"foo": 1}}), "no 'targets' table"),
])
def test_invalid_documents_exit_2(env, memory_repo, capsys, content, needle):
    path = _write(env / "eval_report.json", content)
    assert main([str(path)]) == EXIT_INVALID
    assert needle in capsys.readouterr().err and memory_repo.eval_reports == []


def test_pii_in_report_exits_2_without_echoing(env, memory_repo, capsys):
    report = real_shape()
    report["aggregate"]["pii"]["hits"] = [{"loan_id": "LN-X", "file": "report.md", "snippet": f"SSN {SSN_LIKE}"}]
    path = _write(env / "eval_report.json", report)
    assert main([str(path), "--dry-run"]) == EXIT_INVALID
    captured = capsys.readouterr()
    assert "eval_report.json contains unmasked PII patterns: SSN-shaped value" in captured.err
    assert SSN_LIKE not in captured.err + captured.out
    assert memory_repo.eval_reports == []


def test_timestamps_and_hashes_do_not_trip_the_pii_gate(env, memory_repo):
    report = real_shape(args={"report_dir": "/x/output/eval/" + STAMP, "stamp": STAMP[:13]})
    report["fixtures"] = [{"loan_id": "LN-EDGE-CLEAN", "manifest_sha256": "1" * 64}]
    path = _write(env / "eval_report.json", report)
    assert main([str(path)]) == EXIT_OK


# ---- Supabase -----------------------------------------------------------------------------

@pytest.fixture
def supabase(env, monkeypatch):
    monkeypatch.setenv("MPIRE_REPO_BACKEND", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", SERVICE_KEY)
    calls: list[httpx.Request] = []
    state = {"response": None}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if state["response"] is not None:
            return state["response"]
        body = json.loads(request.content)[0]
        return httpx.Response(201, json=[{"eval_id": "e-1", "generated_at": "2026-09-08T12:00:00+00:00", **body}])

    real = SupabaseClient
    monkeypatch.setattr(factory, "SupabaseClient",
                        lambda url, api_key, bearer=None, **kw: real(url, api_key, bearer=bearer, transport=httpx.MockTransport(handler), **kw))
    return calls, state


def test_supabase_insert_request(supabase, env, capsys):
    calls, _ = supabase
    path = _write(env / "eval_report.json", real_shape())
    assert main([str(path)]) == EXIT_OK
    assert len(calls) == 1
    req = calls[0]
    assert req.method == "POST" and req.url.path == "/rest/v1/eval_reports"
    assert req.headers["Prefer"] == "return=representation" and req.headers["apikey"] == SERVICE_KEY
    body = json.loads(req.content)
    assert len(body) == 1 and set(body[0]) == {"generated_at", "all_targets_met", "targets", "report"}
    assert body[0]["all_targets_met"] is True and body[0]["report"]["mode"] == "compare-only"
    captured = capsys.readouterr()
    assert "SYNCED eval report -> supabase (eval_id e-1" in captured.out and SERVICE_KEY not in captured.out + captured.err


def test_supabase_error_exits_3_without_key(supabase, env, capsys):
    _, state = supabase
    state["response"] = httpx.Response(500, json={"message": "storage full"})
    path = _write(env / "eval_report.json", real_shape())
    assert main([str(path)]) == EXIT_BACKEND
    captured = capsys.readouterr()
    assert "HTTP 500" in captured.err and "storage full" in captured.err and SERVICE_KEY not in captured.err


def test_supabase_not_configured_exits_3(env, capsys):
    path = _write(env / "eval_report.json", real_shape())
    assert main([str(path), "--backend", "supabase"]) == EXIT_BACKEND
    assert "SUPABASE_URL" in capsys.readouterr().err


def test_cli_entry_point(env, tmp_path):
    path = _write(env / "eval_report.json", real_shape())
    proc = subprocess.run([sys.executable, str(REPO_ROOT / "scripts/sync/push_eval.py"), str(path), "--dry-run"],
                          capture_output=True, text=True, cwd=tmp_path, env={**os.environ, "MPIRE_REPO_BACKEND": "memory"})
    assert proc.returncode == 0, proc.stderr
    assert "DRY-RUN" in proc.stdout
