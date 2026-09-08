"""scripts/sync/push_run.py end to end: memory backend, --dry-run, exit codes, --all, location refusal,
and the Supabase request sequence through httpx.MockTransport."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import yaml

from scripts.sync import push_run
from scripts.sync.push_run import (
    EXIT_BACKEND,
    EXIT_INVALID,
    EXIT_NOT_FOUND,
    EXIT_OK,
    discover_run_dirs,
    fixture_context,
    main,
    resolve_run_dir,
)
from services import config, factory
from services.repository import InMemoryRepository
from services.supabase_client import SupabaseClient
from tests.unit.test_services_run_loader import FIXTURE, REPO_ROOT, SSN_LIKE, make_run_dir

SERVICE_KEY = "service-role-SECRET-abc123"
ENV_KEYS = ("MPIRE_REPO_BACKEND", "MPIRE_OUTPUT_DIR", "SUPABASE_URL", "SUPABASE_ANON_KEY",
            "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_JWT_SECRET")


@pytest.fixture
def output_root(tmp_path, monkeypatch) -> Path:
    """Isolated MPIRE_OUTPUT_DIR with the memory backend and no .env / Supabase leakage."""
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)          # no developer .env
    root = tmp_path / "audits"
    root.mkdir()
    monkeypatch.setenv("MPIRE_OUTPUT_DIR", str(root))
    monkeypatch.setenv("MPIRE_REPO_BACKEND", "memory")
    return root


@pytest.fixture
def memory_repo(monkeypatch) -> InMemoryRepository:
    repo = InMemoryRepository()
    monkeypatch.setattr(factory, "build_repository", lambda settings=None, **kw: repo)
    return repo


@pytest.fixture
def no_repo(monkeypatch):
    def refuse(*a, **kw):
        raise AssertionError("build_repository must not be called")
    monkeypatch.setattr(factory, "build_repository", refuse)


# ---- helpers ------------------------------------------------------------------------------

def test_discover_run_dirs(output_root):
    make_run_dir(output_root, "LN-B", "RUN-2")
    make_run_dir(output_root, "LN-A", "RUN-9", manifest=False)                       # audit file only: syncable
    make_run_dir(output_root, "LN-A", "RUN-1", preapproval=False, submission=None, manifest=False, loan_file=True)  # intake only: skipped
    (output_root / "LN-A" / "RUN-0").mkdir()                                            # empty: skipped
    (output_root / "README.md").write_text("ignored\n", encoding="utf-8")
    (output_root / "LN-A" / "stray.json").write_text("{}", encoding="utf-8")
    assert [p.relative_to(output_root).as_posix() for p in discover_run_dirs(output_root)] == ["LN-A/RUN-9", "LN-B/RUN-2"]
    assert discover_run_dirs(output_root / "missing") == []


def test_resolve_run_dir_refuses_outside_and_wrong_depth(output_root, tmp_path):
    good = make_run_dir(output_root, "LN-A", "RUN-1")
    assert resolve_run_dir(output_root, good) == good.resolve()
    outside = make_run_dir(tmp_path / "elsewhere", "LN-A", "RUN-1")
    with pytest.raises(push_run.SyncError) as exc:
        resolve_run_dir(output_root, outside)
    assert exc.value.exit_code == EXIT_INVALID and "MPIRE_OUTPUT_DIR" in str(exc.value)
    for bad in (output_root, output_root / "LN-A", good / "extracted_text", output_root / "LN-A" / ".." / ".." / "elsewhere" / "LN-A" / "RUN-1"):
        with pytest.raises(push_run.SyncError):
            resolve_run_dir(output_root, bad)
    link = output_root / "LN-L" / "RUN-1"
    link.parent.mkdir()
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(push_run.SyncError):
        resolve_run_dir(output_root, link)   # real path escapes the output root


def test_fixture_context_reads_real_manifest_and_tolerates_absence(tmp_path):
    manifest = yaml.safe_load((FIXTURE / "MANIFEST.yaml").read_text(encoding="utf-8"))
    allowlist, description = fixture_context("LN-EDGE-CLEAN")
    assert list(allowlist) == [str(x) for x in manifest["pii_pattern_allowlist"]]
    assert description == manifest["description"].strip()
    assert fixture_context("LN-NO-SUCH-FIXTURE") == ((), None)
    bad = tmp_path / "LN-BAD"
    bad.mkdir()
    (bad / "MANIFEST.yaml").write_text("loan_id: LN-BAD\ndeidentified: false\ndeidentified_by: x\ndeidentified_at: '2026-09-08'\n", encoding="utf-8")
    with pytest.raises(push_run.SyncError) as exc:
        fixture_context("LN-BAD", tmp_path)
    assert exc.value.exit_code == EXIT_INVALID
    (bad / "MANIFEST.yaml").write_text("loan_id: LN-OTHER\ndeidentified: true\ndeidentified_by: x\ndeidentified_at: '2026-09-08'\n", encoding="utf-8")
    with pytest.raises(push_run.SyncError, match="declares loan_id LN-OTHER"):
        fixture_context("LN-BAD", tmp_path)


# ---- dry run ------------------------------------------------------------------------------

def test_dry_run_validates_and_writes_nothing(output_root, no_repo, capsys):
    run_dir = make_run_dir(output_root, "LN-DRY", "RUN-1", inventory=True, loan_file=True)
    before = {p.name: p.stat().st_mtime_ns for p in run_dir.iterdir()}
    assert main(["LN-DRY", "RUN-1", "--dry-run"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "DRY-RUN LN-DRY/RUN-1" in out and "dry_run=yes" in out
    assert "findings: 9 (PASS 3, FAIL 1, MISSING 1, REVIEW 2, NOT_APPLICABLE 2)" in out
    assert "documents: 7" in out and "overall_status: READY" in out
    report_sha = json.loads((run_dir / "run_manifest.json").read_text())["outputs"]
    sha = next(o["sha256"] for o in report_sha if o["path"].endswith("report.md"))
    assert f"report: report.md  sha256 {sha}" in out
    assert "validated: 1 of 1 run(s)" in out and "NOTE: backend=memory" not in out
    assert {p.name: p.stat().st_mtime_ns for p in run_dir.iterdir()} == before


def test_dry_run_with_supabase_backend_needs_no_key(output_root, no_repo, capsys):
    make_run_dir(output_root, "LN-DRY", "RUN-1")
    assert main(["--all", "--dry-run", "--backend", "supabase"]) == EXIT_BACKEND  # not configured -> 3, nothing loaded
    assert "backend configuration error" in capsys.readouterr().err


# ---- memory backend push ------------------------------------------------------------------

def test_memory_backend_pushes_and_warns(output_root, memory_repo, capsys):
    make_run_dir(output_root, "LN-MEM", "RUN-1", inventory=True)
    assert main(["LN-MEM", "RUN-1", "--backend", "memory"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "NOTE: backend=memory" in out and "SYNCED LN-MEM/RUN-1 -> memory" in out and "synced: 1 of 1" in out
    run = memory_repo.get_run("LN-MEM", "RUN-1")
    assert run.overall_status == "READY" and run.synced_at
    assert len(memory_repo.list_findings("LN-MEM", "RUN-1")) == 9 and len(memory_repo.list_documents("LN-MEM", "RUN-1")) == 7
    assert memory_repo.get_report("LN-MEM", "RUN-1", "report.md").content_md.startswith("# Audit report")
    assert memory_repo.get_loan("LN-MEM").description is None   # no fixture manifest for LN-MEM


def test_run_dir_mode_and_resync_replaces(output_root, memory_repo):
    run_dir = make_run_dir(output_root, "LN-RD", "RUN-1")
    assert main(["--run-dir", str(run_dir)]) == EXIT_OK
    assert len(memory_repo.list_findings("LN-RD", "RUN-1")) == 9
    (run_dir / "preapproval_audit.json").unlink()
    assert main(["--run-dir", os.path.relpath(run_dir)]) == EXIT_OK
    assert len(memory_repo.list_findings("LN-RD", "RUN-1")) == 4 and len(memory_repo.runs) == 1


def test_fixture_manifest_supplies_description_and_allowlist(output_root, memory_repo, monkeypatch, tmp_path):
    fixtures = tmp_path / "fixtures"
    (fixtures / "LN-FX").mkdir(parents=True)
    fake_account = "7" * 9 + "1"
    (fixtures / "LN-FX" / "MANIFEST.yaml").write_text(
        "loan_id: LN-FX\ndeidentified: true\ndeidentified_by: fixture generator\ndeidentified_at: '2026-09-08'\n"
        f"description: 'Synthetic fixture LN-FX'\npii_pattern_allowlist:\n  - \"{fake_account}\"\n", encoding="utf-8")
    monkeypatch.setattr(push_run, "FIXTURE_ROOT", fixtures)
    run_dir = make_run_dir(output_root, "LN-FX", "RUN-1")
    (run_dir / "report.md").write_text(f"# report\n\nsynthetic account {fake_account}\n", encoding="utf-8")
    assert main(["LN-FX", "RUN-1"]) == EXIT_OK
    assert memory_repo.get_loan("LN-FX").description == "Synthetic fixture LN-FX"
    # without the allowlist the same report is refused
    monkeypatch.setattr(push_run, "FIXTURE_ROOT", tmp_path / "no-fixtures")
    assert main(["LN-FX", "RUN-1"]) == EXIT_INVALID


def test_real_intake_run_pushes_with_fixture_allowlist(output_root, memory_repo, capsys):
    proc = subprocess.run([sys.executable, str(REPO_ROOT / "scripts/intake/inventory.py"), str(FIXTURE),
                           "--run-id", "RUN-SYNC-1", "--out", str(output_root)], capture_output=True, text=True, cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stderr
    assert main(["LN-EDGE-CLEAN", "RUN-SYNC-1"]) == EXIT_OK
    loan = memory_repo.get_loan("LN-EDGE-CLEAN")
    assert loan.description.startswith("Small clean synthetic package")
    assert len(memory_repo.list_documents("LN-EDGE-CLEAN", "RUN-SYNC-1")) == 5
    assert "loan description: Small clean synthetic package" in capsys.readouterr().out
    assert main(["--all"]) == EXIT_NOT_FOUND   # intake-only runs are not discovered by --all


# ---- exit codes ---------------------------------------------------------------------------

def test_not_found_cases(output_root, no_repo, capsys):
    assert main(["LN-NOPE", "RUN-1"]) == EXIT_NOT_FOUND
    assert "not found" in capsys.readouterr().err
    assert main(["--all"]) == EXIT_NOT_FOUND
    assert "nothing to sync" in capsys.readouterr().err
    assert main(["--run-dir", str(output_root / "LN-X" / "RUN-1")]) == EXIT_NOT_FOUND
    os.environ["MPIRE_OUTPUT_DIR"] = str(output_root / "does-not-exist")
    assert main(["--all", "--dry-run"]) == EXIT_NOT_FOUND


def test_usage_errors(output_root):
    for argv in ([], ["LN-A"], ["LN-A", "RUN-1", "--all"], ["--all", "--run-dir", "x"], ["--backend", "sqlite", "--all"]):
        with pytest.raises(SystemExit):
            main(argv)


def test_invalid_schema_exits_2_and_names_file(output_root, memory_repo, capsys):
    run_dir = make_run_dir(output_root, "LN-BAD", "RUN-1", submission=None, report=False)
    bad = json.loads((REPO_ROOT / "tests/fixtures/examples/audit_result.invalid.pass_without_evidence.json").read_text())
    (run_dir / "preapproval_audit.json").write_text(json.dumps({**bad, "loan_id": "LN-BAD", "run_id": "RUN-1"}), encoding="utf-8")
    assert main(["LN-BAD", "RUN-1"]) == EXIT_INVALID
    err = capsys.readouterr().err
    assert "FAILED LN-BAD/RUN-1" in err and "preapproval_audit.json fails audit_result schema" in err
    assert memory_repo.runs == {}


def test_pii_exits_2_without_echoing_value(output_root, memory_repo, capsys):
    run_dir = make_run_dir(output_root, "LN-PII", "RUN-1")
    (run_dir / "report.md").write_text(f"# report\n\nSSN {SSN_LIKE}\n", encoding="utf-8")
    assert main(["LN-PII", "RUN-1"]) == EXIT_INVALID
    captured = capsys.readouterr()
    assert "report.md contains unmasked PII patterns: SSN-shaped value" in captured.err
    assert SSN_LIKE not in captured.err + captured.out and SSN_LIKE[-4:] not in captured.err + captured.out
    assert memory_repo.runs == {}


def test_mismatched_ids_exit_2(output_root, memory_repo, capsys):
    run_dir = make_run_dir(output_root, "LN-MM", "RUN-1")
    manifest = json.loads((run_dir / "run_manifest.json").read_text())
    (run_dir / "run_manifest.json").write_text(json.dumps({**manifest, "loan_id": "LN-ELSE"}), encoding="utf-8")
    assert main(["LN-MM", "RUN-1", "--dry-run"]) == EXIT_INVALID
    assert "do not match directory LN-MM/RUN-1" in capsys.readouterr().err


def test_refuses_run_dir_outside_output_dir(output_root, no_repo, tmp_path, capsys):
    outside = make_run_dir(tmp_path / "elsewhere", "LN-OUT", "RUN-1")
    assert main(["--run-dir", str(outside), "--dry-run"]) == EXIT_INVALID
    assert "not a <loan_id>/<run_id> directory under MPIRE_OUTPUT_DIR" in capsys.readouterr().err
    assert main(["..", "elsewhere", "--dry-run"]) in (EXIT_INVALID, EXIT_NOT_FOUND)
    assert main(["--run-dir", str(output_root), "--dry-run"]) == EXIT_INVALID


# ---- --all --------------------------------------------------------------------------------

def test_all_syncs_every_discovered_run_and_reports_worst_failure(output_root, memory_repo, capsys):
    make_run_dir(output_root, "LN-A", "RUN-1")
    make_run_dir(output_root, "LN-A", "RUN-2", preapproval=False, submission="not_ready", manifest=False)
    make_run_dir(output_root, "LN-C", "RUN-1", preapproval=False, submission=None, manifest=False, loan_file=True)  # skipped
    assert main(["--all"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "SYNCED LN-A/RUN-1" in out and "SYNCED LN-A/RUN-2" in out and "synced: 2 of 2" in out
    assert set(memory_repo.runs) == {("LN-A", "RUN-1"), ("LN-A", "RUN-2")}
    assert memory_repo.get_run("LN-A", "RUN-2").overall_status == "NOT_READY"
    bad = make_run_dir(output_root, "LN-B", "RUN-1")
    (bad / "report.md").write_text(f"SSN {SSN_LIKE}\n", encoding="utf-8")
    assert main(["--all"]) == EXIT_INVALID
    captured = capsys.readouterr()
    assert "FAILED LN-B/RUN-1" in captured.err and "synced: 2 of 3" in captured.out
    assert ("LN-B", "RUN-1") not in memory_repo.runs and SSN_LIKE not in captured.err


# ---- Supabase path ------------------------------------------------------------------------

class Postgrest:
    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.fail_with: httpx.Response | Exception | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        table = request.url.path.split("/rest/v1/", 1)[1]
        if self.fail_with is not None and request.method == "POST" and table == "runs":
            if isinstance(self.fail_with, Exception):
                raise self.fail_with
            return self.fail_with
        if request.method == "GET" and table == "runs":
            return httpx.Response(200, json=[{"loan_id": "LN-SB", "run_id": "RUN-1", "overall_status": "READY",
                                              "coverage_percent": 100, "synced_at": "2026-09-08T12:00:00+00:00"}])
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(201, json=[])

    def trail(self):
        return [(r.method, r.url.path.split("/rest/v1/", 1)[1]) for r in self.calls]


@pytest.fixture
def supabase_env(output_root, monkeypatch):
    monkeypatch.setenv("MPIRE_REPO_BACKEND", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", SERVICE_KEY)
    pg = Postgrest()
    real = SupabaseClient

    def patched(url, api_key, bearer=None, **kw):
        return real(url, api_key, bearer=bearer, transport=httpx.MockTransport(pg), **kw)

    monkeypatch.setattr(factory, "SupabaseClient", patched)  # the real factory path, the real key from env
    return pg


def test_supabase_upsert_sequence(supabase_env, output_root, capsys):
    pg = supabase_env
    make_run_dir(output_root, "LN-SB", "RUN-1", inventory=True, loan_file=True)
    assert main(["LN-SB", "RUN-1"]) == EXIT_OK
    trail = pg.trail()
    assert trail[:3] == [("POST", "loans"), ("DELETE", "runs"), ("POST", "runs")]
    assert trail[3:] == [("POST", "documents"), ("POST", "findings"), ("POST", "review_items"), ("POST", "missing_documents"),
                         ("POST", "conflicts"), ("POST", "proposed_actions"), ("POST", "approvals_required"),
                         ("POST", "reports"), ("GET", "runs")]
    assert all(r.headers["apikey"] == SERVICE_KEY and r.headers["Authorization"] == f"Bearer {SERVICE_KEY}" for r in pg.calls)
    assert pg.calls[0].url.params["on_conflict"] == "loan_id"
    assert dict(pg.calls[1].url.params) == {"loan_id": "eq.LN-SB", "run_id": "eq.RUN-1"}
    run_row = json.loads(pg.calls[2].content)[0]
    assert run_row["loan_id"] == "LN-SB" and run_row["overall_status"] == "READY" and "synced_at" not in run_row
    assert len(json.loads(pg.calls[4].content)) == 9
    captured = capsys.readouterr()
    assert "SYNCED LN-SB/RUN-1 -> supabase" in captured.out
    assert SERVICE_KEY not in captured.out + captured.err


def test_supabase_http_error_exits_3_without_key(supabase_env, output_root, capsys):
    pg = supabase_env
    pg.fail_with = httpx.Response(500, json={"message": "insert failed"})
    make_run_dir(output_root, "LN-SB", "RUN-1")
    make_run_dir(output_root, "LN-SB", "RUN-2")
    assert main(["--all"]) == EXIT_BACKEND
    captured = capsys.readouterr()
    assert "HTTP 500" in captured.err and "insert failed" in captured.err
    assert SERVICE_KEY not in captured.err + captured.out
    assert pg.trail().count(("POST", "runs")) == 1   # the loop stops after a backend failure


def test_supabase_network_error_exits_3(supabase_env, output_root, capsys):
    pg = supabase_env
    pg.fail_with = httpx.ConnectError("connection refused")
    make_run_dir(output_root, "LN-SB", "RUN-1")
    assert main(["LN-SB", "RUN-1"]) == EXIT_BACKEND
    captured = capsys.readouterr()
    assert "network error" in captured.err and "ConnectError" in captured.err and SERVICE_KEY not in captured.err


def test_supabase_backend_without_config_exits_3(output_root, no_repo, capsys):
    make_run_dir(output_root, "LN-SB", "RUN-1")
    assert main(["LN-SB", "RUN-1", "--backend", "supabase"]) == EXIT_BACKEND
    assert "SUPABASE_URL" in capsys.readouterr().err


def test_cli_entry_point_runs_as_script(output_root, tmp_path):
    make_run_dir(output_root, "LN-CLI", "RUN-1")
    env = {**os.environ, "MPIRE_OUTPUT_DIR": str(output_root), "MPIRE_REPO_BACKEND": "memory"}
    proc = subprocess.run([sys.executable, str(REPO_ROOT / "scripts/sync/push_run.py"), "LN-CLI", "RUN-1", "--dry-run"],
                          capture_output=True, text=True, cwd=tmp_path, env=env)
    assert proc.returncode == 0, proc.stderr
    assert "DRY-RUN LN-CLI/RUN-1" in proc.stdout
