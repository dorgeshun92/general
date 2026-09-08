"""services.run_loader: mapping of validated documents into a RunBundle and the load_run_dir gates."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.audit.run_manifest import write_run_manifest
from services.demo import EXAMPLES
from services.run_loader import RunLoadError, bundle_from_documents, load_run_dir

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "deidentified" / "LN-EDGE-CLEAN"
L, R = "LN-EXAMPLE-0001", "RUN-EXAMPLE-0001"
# Built from pieces so no SSN-shaped literal lives in the repository.
SSN_LIKE = "-".join(("219", "09", "9999"))


def _example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def _rekey(audit: dict, loan_id: str, run_id: str) -> dict:
    return {**audit, "loan_id": loan_id, "run_id": run_id}


def _write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) if not isinstance(data, str) else data, encoding="utf-8")
    return path


def make_run_dir(root: Path, loan_id: str, run_id: str, *, preapproval=True, submission="ready", report=True,
                 manifest=True, inventory=False, loan_file=False) -> Path:
    """Assemble <root>/<loan_id>/<run_id>/ from the example fixtures, re-keyed to the given ids."""
    run_dir = root / loan_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    if preapproval:
        outputs.append(_write(run_dir / "preapproval_audit.json", _rekey(_example("audit_result.valid.preapproval.json"), loan_id, run_id)))
    if submission:
        name = {"ready": "audit_result.valid.submission_ready.json", "not_ready": "audit_result.valid.submission_not_ready.json"}[submission]
        outputs.append(_write(run_dir / "submission_readiness.json", _rekey(_example(name), loan_id, run_id)))
    if inventory:
        outputs.append(_write(run_dir / "document_inventory.json", _rekey(_example("document_inventory.valid.json"), loan_id, run_id)))
    if loan_file:
        outputs.append(_write(run_dir / "loan_file.json", {**_example("loan_file.valid.json"), "loan_id": loan_id}))
    if report:
        outputs.append(_write(run_dir / "report.md", f"# Audit report {loan_id} / {run_id}\n\nDECISION SUPPORT ONLY.\n"))
    if manifest:
        write_run_manifest(run_dir, inputs=[], outputs=outputs, skill="mortgage-file-audit", run_id=run_id, loan_id=loan_id,
                           started_at="2026-09-08T11:00:00Z")
    return run_dir


# ---- bundle_from_documents ----------------------------------------------------------------

def test_mapping_counts_and_gate_from_submission_audit():
    pre = _example("audit_result.valid.preapproval.json")
    ready = _example("audit_result.valid.submission_ready.json")
    manifest = {"loan_id": L, "run_id": R, "skill": "mortgage-file-audit", "started_at": "2026-09-08T11:00:00Z",
                "completed_at": "2026-09-08T12:34:56Z", "completed_normally": True, "stop_condition": None,
                "tool_versions": {"python": "3.11"}}
    bundle = bundle_from_documents(loan_id=L, run_id=R, loan_file=_example("loan_file.valid.json"),
                                   inventory=_example("document_inventory.valid.json"), audits=[pre, ready],
                                   manifest=manifest, reports={"z.md": "# z\n", "a.md": "# a\n"},
                                   loan_description="desc", source_root="tests/fixtures/deidentified/LN-EXAMPLE-0001")
    run = bundle.run
    assert run.overall_status == "READY" and run.preapproval_present and run.submission_present
    assert run.counts == ready["summary"]["counts"] and run.blocking_open == 0 and run.coverage_percent == "100.0"
    assert run.completed_at == "2026-09-08T12:34:56Z" and run.skill == "mortgage-file-audit"
    assert run.tool_versions == {"python": "3.11"} and run.manifest == manifest
    assert run.totals == {"files": 7, "pages": 36, "bytes": 1101824}
    assert run.catalog_version == ready["catalog"]["version"] and run.los_export_present == ready["los_export_present"]
    assert len(bundle.documents) == 7                      # inventory wins over loan_file documents
    assert len(bundle.findings) == len(pre["findings"]) + len(ready["findings"]) == 9
    assert {f.audit_type for f in bundle.findings} == {"PREAPPROVAL", "SUBMISSION_READINESS"}
    assert [ri.review_id for ri in bundle.review_items] == ["RV-001"]
    assert len(bundle.missing_documents) == len(pre["missing_documents"]) + len(ready["missing_documents"])
    assert [m.seq for m in bundle.missing_documents if m.audit_type == "PREAPPROVAL"] == list(range(1, len(pre["missing_documents"]) + 1))
    assert [a.action_id for a in bundle.proposed_actions] == [a["action_id"] for a in pre.get("proposed_client_needs", [])]
    assert all(a.status == "DRAFT_HUMAN_APPROVAL_REQUIRED" for a in bundle.proposed_actions)
    assert [r.name for r in bundle.reports] == ["a.md", "z.md"]   # sorted by name
    assert all(len(r.sha256) == 64 for r in bundle.reports)
    assert bundle.loan.loan_id == L and bundle.loan.description == "desc" and bundle.loan.deidentified
    assert bundle.loan.source_root == "tests/fixtures/deidentified/LN-EXAMPLE-0001"


def test_submission_gate_wins_regardless_of_order():
    pre = _example("audit_result.valid.preapproval.json")
    not_ready = _example("audit_result.valid.submission_not_ready.json")
    for audits in ([pre, not_ready], [not_ready, pre]):
        run = bundle_from_documents(loan_id=L, run_id=R, loan_file=None, inventory=None, audits=audits, manifest=None, reports={}).run
        assert run.overall_status == "NOT_READY" and run.counts == not_ready["summary"]["counts"] and run.blocking_open == 1


def test_preapproval_only_has_no_gate_but_keeps_counts():
    pre = _example("audit_result.valid.preapproval.json")
    bundle = bundle_from_documents(loan_id=L, run_id=R, loan_file=None, inventory=None, audits=[pre], manifest=None, reports={})
    run = bundle.run
    assert run.overall_status is None and run.preapproval_present and not run.submission_present
    assert run.counts == pre["summary"]["counts"] and run.blocking_open == 2 and run.coverage_percent == "83.3"
    assert run.completed_at == pre["generated_at"]       # falls back to the audit timestamp without a manifest
    assert run.known_limitations == list(pre["known_limitations"])
    assert bundle.documents == [] and bundle.review_items == []


def test_documents_fall_back_to_loan_file_when_no_inventory():
    lf = _example("loan_file.valid.json")
    bundle = bundle_from_documents(loan_id=L, run_id=R, loan_file=lf, inventory=None, audits=[], manifest=None, reports={})
    assert len(bundle.documents) == len(lf["documents"]) == 5
    assert bundle.run.totals is None and bundle.run.overall_status is None and bundle.run.counts is None


# ---- load_run_dir on a real intake run --------------------------------------------------

def test_load_real_intake_run_dir(tmp_path):
    proc = subprocess.run([sys.executable, str(REPO_ROOT / "scripts/intake/inventory.py"), str(FIXTURE),
                           "--run-id", "RUN-LOADER-1", "--out", str(tmp_path)], capture_output=True, text=True, cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stderr
    manifest = yaml.safe_load((FIXTURE / "MANIFEST.yaml").read_text(encoding="utf-8"))
    allowlist = tuple(str(x) for x in manifest["pii_pattern_allowlist"])
    assert allowlist  # the fixture vouches for its fake account numbers
    bundle = load_run_dir(tmp_path / "LN-EDGE-CLEAN" / "RUN-LOADER-1", pii_allowlist=allowlist,
                          loan_description=manifest["description"])
    assert bundle.loan.loan_id == "LN-EDGE-CLEAN" and bundle.loan.description == manifest["description"]
    assert bundle.loan.source_root == "tests/fixtures/deidentified/LN-EDGE-CLEAN"
    assert [d.document_id for d in bundle.documents] == [f"DOC-{i:03d}" for i in range(1, 6)]
    assert {d.document_type for d in bundle.documents} == {"URLA_1003", "BANK_STATEMENT", "PURCHASE_CONTRACT", "PAYSTUB", "W2"}
    assert all(len(d.sha256) == 64 and d.status == "OK" for d in bundle.documents)
    assert bundle.run.totals == {"files": 5, "pages": 7, "bytes": 5876}
    assert [ri.category for ri in bundle.review_items] == ["POSSIBLE_LIVE_PII"]
    assert bundle.review_items[0].reviewer_role == "COMPLIANCE"
    assert bundle.run.overall_status is None and not bundle.run.preapproval_present
    assert bundle.run.run_id == "RUN-LOADER-1" and bundle.run.manifest is None
    assert bundle.findings == [] and bundle.reports == []   # intake writes no audit and no Markdown


# ---- load_run_dir on an assembled audit run ---------------------------------------------

def test_load_assembled_run_dir(tmp_path):
    run_dir = make_run_dir(tmp_path, "LN-ASSEMBLED", "RUN-A1", inventory=True, loan_file=True)
    bundle = load_run_dir(run_dir, loan_description="assembled")
    assert bundle.run.loan_id == "LN-ASSEMBLED" and bundle.run.run_id == "RUN-A1"
    assert bundle.run.skill == "mortgage-file-audit" and bundle.run.completed_normally is True
    assert bundle.run.started_at == "2026-09-08T11:00:00Z" and bundle.run.completed_at
    assert bundle.run.overall_status == "READY" and len(bundle.findings) == 9 and len(bundle.documents) == 7
    assert bundle.run.manifest["totals"]["outputs"] == 5
    assert [r.name for r in bundle.reports] == ["report.md"] and bundle.loan.description == "assembled"
    assert all(f.loan_id == "LN-ASSEMBLED" and f.run_id == "RUN-A1" for f in bundle.findings)


def test_load_run_dir_without_manifest_or_reports(tmp_path):
    run_dir = make_run_dir(tmp_path, "LN-X", "RUN-1", preapproval=True, submission=None, report=False, manifest=False)
    bundle = load_run_dir(run_dir)
    assert bundle.run.overall_status is None and bundle.run.manifest is None and bundle.reports == []


# ---- failure cases -------------------------------------------------------------------------

def test_not_a_directory_and_empty_directory(tmp_path):
    with pytest.raises(RunLoadError, match="not a directory"):
        load_run_dir(tmp_path / "LN-X" / "RUN-1")
    empty = tmp_path / "LN-X" / "RUN-1"
    empty.mkdir(parents=True)
    with pytest.raises(RunLoadError, match="no run_manifest.json"):
        load_run_dir(empty)
    (empty / "notes.md").write_text("# only a report\n", encoding="utf-8")
    with pytest.raises(RunLoadError):
        load_run_dir(empty)


def test_manifest_ids_must_match_directory(tmp_path):
    run_dir = make_run_dir(tmp_path, "LN-X", "RUN-1")
    manifest = json.loads((run_dir / "run_manifest.json").read_text())
    _write(run_dir / "run_manifest.json", {**manifest, "run_id": "RUN-OTHER"})
    with pytest.raises(RunLoadError, match="manifest ids LN-X/RUN-OTHER do not match directory LN-X/RUN-1"):
        load_run_dir(run_dir)


def test_audit_ids_must_match_directory(tmp_path):
    run_dir = tmp_path / "LN-OTHER" / "RUN-1"
    run_dir.mkdir(parents=True)
    _write(run_dir / "submission_readiness.json", _example("audit_result.valid.submission_ready.json"))  # ids LN-EXAMPLE-0001
    with pytest.raises(RunLoadError, match="submission_readiness.json ids do not match directory LN-OTHER/RUN-1"):
        load_run_dir(run_dir)


def test_invalid_schema_is_refused(tmp_path):
    run_dir = tmp_path / L / R
    run_dir.mkdir(parents=True)
    _write(run_dir / "preapproval_audit.json", _example("audit_result.invalid.pass_without_evidence.json"))
    with pytest.raises(RunLoadError, match="preapproval_audit.json fails audit_result schema"):
        load_run_dir(run_dir)


def test_unreadable_json_is_refused(tmp_path):
    run_dir = tmp_path / L / R
    run_dir.mkdir(parents=True)
    (run_dir / "run_manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(RunLoadError, match="cannot read"):
        load_run_dir(run_dir)


def test_unmasked_ssn_in_report_is_refused_and_never_echoed(tmp_path):
    run_dir = make_run_dir(tmp_path, "LN-PII", "RUN-1")
    (run_dir / "report.md").write_text(f"# report\n\nBorrower SSN {SSN_LIKE}\n", encoding="utf-8")
    with pytest.raises(RunLoadError) as exc:
        load_run_dir(run_dir)
    message = str(exc.value)
    assert "report.md" in message and "SSN-shaped value" in message
    assert SSN_LIKE not in message and SSN_LIKE[-4:] not in message


def test_unmasked_account_number_in_audit_json_is_refused(tmp_path):
    run_dir = make_run_dir(tmp_path, "LN-PII", "RUN-2", submission=None, report=False, manifest=False)
    audit = json.loads((run_dir / "preapproval_audit.json").read_text())
    account = "4" + "0" * 9 + "7"
    audit["findings"][0]["explanation"] += f" account {account}"
    _write(run_dir / "preapproval_audit.json", audit)
    with pytest.raises(RunLoadError, match="PREAPPROVAL audit contains unmasked PII") as exc:
        load_run_dir(run_dir)
    assert account not in str(exc.value)


def test_pii_allowlist_suppresses_vouched_tokens_only(tmp_path):
    run_dir = make_run_dir(tmp_path, "LN-ALLOW", "RUN-1")
    fake_account = "5" * 10
    (run_dir / "report.md").write_text(f"# report\n\nsynthetic account {fake_account}\n", encoding="utf-8")
    with pytest.raises(RunLoadError):
        load_run_dir(run_dir)
    bundle = load_run_dir(run_dir, pii_allowlist=(fake_account,))
    assert bundle.reports[0].name == "report.md"
    with pytest.raises(RunLoadError):
        load_run_dir(run_dir, pii_allowlist=("1" * 10,))  # a different token does not help


def test_source_tree_is_not_touched(tmp_path):
    before = sorted(p.relative_to(FIXTURE).as_posix() for p in FIXTURE.rglob("*"))
    shutil.copytree(FIXTURE, tmp_path / "copy")  # sanity: the fixture is readable
    assert sorted(p.relative_to(FIXTURE).as_posix() for p in FIXTURE.rglob("*")) == before
