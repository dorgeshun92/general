"""End-to-end tests for scripts/eval/run_eval.py against a synthetic results tree in tmp_path."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

import pytest
import yaml

from scripts.eval import run_eval

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_KEY = REPO_ROOT / "tests/expected/LN-EXAMPLE-0001/expected_audit.json"
EXAMPLE_DOCS = REPO_ROOT / "tests/expected/LN-EXAMPLE-0001/expected_documents.json"
ZERO_SHA = "0" * 64


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _recount(audit: dict) -> dict:
    counts = {k: 0 for k in ("PASS", "FAIL", "MISSING", "REVIEW", "NOT_APPLICABLE")}
    for f in audit["findings"]:
        counts[f["result"]] += 1
    audit["summary"]["counts"] = counts
    audit["summary"]["blocking_open"] = sum(
        1 for f in audit["findings"] if f["blocking"] and f["result"] not in ("PASS", "NOT_APPLICABLE"))
    return audit


def _produced(expected: dict, loan_id: str, run_id: str = "run-1") -> dict:
    out = copy.deepcopy(expected)
    out["loan_id"] = loan_id
    out["run_id"] = run_id
    out["generated_by"] = {"skill": "submission-readiness"}
    return _recount(out)


def _inventory(loan_id: str, filenames: dict[str, str]) -> dict:
    docs = []
    for n, (fname, dtype) in enumerate(filenames.items(), start=1):
        docs.append({"document_id": f"DOC-{n:03d}", "filename": fname, "sha256": ZERO_SHA, "document_type": dtype,
                     "classification_confidence": "HIGH", "page_count": 1, "status": "OK", "duplicate_of": None,
                     "document_date": None})
    return {"schema_version": "1.0", "loan_id": loan_id, "run_id": "run-1", "generated_at": "2026-09-08T01:00:00Z",
            "input_directory": f"tests/fixtures/deidentified/{loan_id}", "manifest_sha256": ZERO_SHA,
            "documents": docs, "duplicates": [], "unreadable": [],
            "totals": {"files": len(docs), "pages": len(docs), "bytes": 0}}


def _manifest(fixture_dir: Path, loan_id: str, **extra) -> None:
    fixture_dir.mkdir(parents=True, exist_ok=True)
    data = {"loan_id": loan_id, "deidentified": True, "deidentified_by": "test", "deidentified_at": "2026-09-08"}
    data.update(extra)
    (fixture_dir / "MANIFEST.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _tree_hashes(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def tree(tmp_path: Path):
    """fixtures/, expected/, results/ populated with the scenarios the harness must handle."""
    fixtures, expected, results = tmp_path / "fixtures", tmp_path / "expected", tmp_path / "results"
    key = json.loads(EXAMPLE_KEY.read_text(encoding="utf-8"))
    docs = json.loads(EXAMPLE_DOCS.read_text(encoding="utf-8"))

    def add(loan_id: str, produced: dict | None, *, run_id="run-1", manifest=None, inventory=True,
            fixture_extra=None, key_override=None, extra_files=None):
        _manifest(fixtures / loan_id, loan_id, **(fixture_extra or {}))
        k = key_override if key_override is not None else {**copy.deepcopy(key), "loan_id": loan_id}
        _write_json(expected / loan_id / "expected_audit.json", k)
        if key_override is None:
            _write_json(expected / loan_id / "expected_documents.json", docs)
        run = results / loan_id / run_id
        run.mkdir(parents=True, exist_ok=True)
        if produced is not None:
            _write_json(run / "submission_readiness.json", produced)
        if inventory:
            _write_json(run / "document_inventory.json", _inventory(loan_id, docs))
        _write_json(run / "run_manifest.json", manifest if manifest is not None else
                    {"loan_id": loan_id, "run_id": run_id, "completed_at": "2026-09-08T01:00:00Z", "status": "COMPLETED",
                     "metrics": {"runtime_seconds": "12.5", "tokens": {"input": 1000, "output": 200}}})
        for name, text in (extra_files or {}).items():
            (run / name).write_text(text, encoding="utf-8")
        return run

    # 1. clean pass
    add("LN-PASS-0001", _produced({**key, "loan_id": "LN-PASS-0001"}, "LN-PASS-0001"),
        extra_files={"report.md": "# Report\nAccount ****1234, SSN ***-**-6789. Amount 1,234,567.89\n"})
    # 2. false PASS on blocking rule SUB-EXAMPLE-002 (expected FAIL)
    fp = _produced({**key, "loan_id": "LN-FALSEPASS-0001"}, "LN-FALSEPASS-0001")
    for f in fp["findings"]:
        if f["rule_id"] == "SUB-EXAMPLE-002":
            f["result"], f["proposed_action"], f["discrepancy"] = "PASS", None, None
    add("LN-FALSEPASS-0001", _recount(fp))
    # 3. schema-invalid produced audit
    bad = _produced({**key, "loan_id": "LN-SCHEMABAD-0001"}, "LN-SCHEMABAD-0001")
    del bad["known_limitations"]
    add("LN-SCHEMABAD-0001", bad)
    # 4. PII leak in report.md
    add("LN-PII-0001", _produced({**key, "loan_id": "LN-PII-0001"}, "LN-PII-0001"),
        extra_files={"report.md": "Borrower SSN 123-45-6789 appears here\n"})
    # 5. stop-condition edge case handled correctly
    add("LN-EDGE-STOP", None, inventory=False, key_override={"expected_stop_condition": "ENCRYPTED_DOCUMENT"},
        manifest={"loan_id": "LN-EDGE-STOP", "run_id": "run-1", "started_at": "2026-09-08T01:00:00Z",
                  "status": "STOPPED", "stop_condition": "ENCRYPTED_DOCUMENT"})
    # fixture without answer key is skipped; answer key without fixture is reported
    _manifest(fixtures / "LN-NOKEY-0001", "LN-NOKEY-0001")
    _write_json(expected / "LN-NOFIXTURE-0001" / "expected_audit.json", {**copy.deepcopy(key), "loan_id": "LN-NOFIXTURE-0001"})
    return {"fixtures": fixtures, "expected": expected, "results": results, "key": key, "docs": docs, "add": add}


def _run(tree, tmp_path, *extra):
    report = tmp_path / "report"
    args = ["--fixtures", str(tree["fixtures"]), "--expected", str(tree["expected"]),
            "--results", str(tree["results"]), "--report", str(report),
            "--proposals-dir", str(tmp_path / "proposals"), *extra]
    code = run_eval.main(args)
    data = json.loads((report / "eval_report.json").read_text(encoding="utf-8"))
    return code, data, report


def test_end_to_end_mixed_tree(tree, tmp_path):
    before = _tree_hashes(tree["expected"])
    before_mtimes = {p: p.stat().st_mtime_ns for p in tree["expected"].rglob("*")}
    code, data, report = _run(tree, tmp_path)
    assert code == 1
    assert (report / "eval_report.md").is_file()
    assert _tree_hashes(tree["expected"]) == before
    assert {p: p.stat().st_mtime_ns for p in tree["expected"].rglob("*")} == before_mtimes

    by_id = {r["loan_id"]: r for r in data["fixtures"]}
    assert set(by_id) == {"LN-PASS-0001", "LN-FALSEPASS-0001", "LN-SCHEMABAD-0001", "LN-PII-0001", "LN-EDGE-STOP"}
    assert data["skipped_no_answer_key"] == ["LN-NOKEY-0001"]
    assert data["expected_without_fixture"] == ["LN-NOFIXTURE-0001"]

    clean = by_id["LN-PASS-0001"]
    assert clean["comparison"]["false_pass_blocking"] == []
    assert clean["comparison"]["coverage_gaps"] == []
    assert clean["schema_failures"] == {} and clean["pii_hits"] == []
    assert clean["classification"]["correct"] == 2
    assert clean["metrics"] == {"runtime_seconds": "12.5", "tokens": {"input": 1000, "output": 200}}

    fp = by_id["LN-FALSEPASS-0001"]["comparison"]["false_pass_blocking"]
    assert [d["rule_id"] for d in fp] == ["SUB-EXAMPLE-002"]

    assert "submission_readiness.json" in by_id["LN-SCHEMABAD-0001"]["schema_failures"]
    assert by_id["LN-PII-0001"]["pii_hits"] == [{"file": "report.md", "pattern": "SSN-shaped value"}]

    stop = by_id["LN-EDGE-STOP"]
    assert stop["kind"] == "stop_condition" and stop["stop"]["correct"] is True
    assert stop["metrics"] == {"runtime_seconds": "not reported", "tokens": "not reported"}

    targets = data["aggregate"]["targets"]
    assert targets["false_pass"]["status"] == "MISSED"
    assert targets["pii"]["status"] == "MISSED"
    assert targets["schema"]["status"] == "MISSED"
    assert targets["stop"]["status"] == "MET"
    assert targets["classification"]["status"] == "MET"
    assert targets["coverage"]["status"] == "MET"
    assert targets["calculation"]["status"] == "MET"
    assert targets["evidence"]["status"] == "MET"
    md = (report / "eval_report.md").read_text(encoding="utf-8")
    assert "TARGETS MISSED" in md and "LN-FALSEPASS-0001" in md and "SSN-shaped value" in md


def test_all_targets_met_exits_zero(tmp_path):
    fixtures, expected, results = tmp_path / "f", tmp_path / "e", tmp_path / "r"
    key = {**json.loads(EXAMPLE_KEY.read_text(encoding="utf-8")), "loan_id": "LN-OK-0001"}
    docs = json.loads(EXAMPLE_DOCS.read_text(encoding="utf-8"))
    _manifest(fixtures / "LN-OK-0001", "LN-OK-0001")
    _write_json(expected / "LN-OK-0001" / "expected_audit.json", key)
    _write_json(expected / "LN-OK-0001" / "expected_documents.json", docs)
    run = results / "LN-OK-0001" / "run-1"
    _write_json(run / "submission_readiness.json", _produced(key, "LN-OK-0001"))
    _write_json(run / "document_inventory.json", _inventory("LN-OK-0001", docs))
    _write_json(run / "run_manifest.json", {"completed_at": "2026-09-08T01:00:00Z"})
    fixtures_stop = fixtures / "LN-EDGE-X"
    _manifest(fixtures_stop, "LN-EDGE-X")
    _write_json(expected / "LN-EDGE-X" / "expected_audit.json", {"expected_stop_condition": "SCHEMA_VALIDATION_FAILED"})
    _write_json(results / "LN-EDGE-X" / "run-1" / "run_manifest.json",
                {"status": "STOPPED", "stop": {"reason": "SCHEMA_VALIDATION_FAILED"}})
    report = tmp_path / "rep"
    code = run_eval.main(["--fixtures", str(fixtures), "--expected", str(expected), "--results", str(results),
                          "--report", str(report), "--proposals-dir", str(tmp_path / "p")])
    assert code == 0
    data = json.loads((report / "eval_report.json").read_text())
    assert data["aggregate"]["all_targets_met"] is True
    assert all(t["status"] == "MET" for t in data["aggregate"]["targets"].values())


def test_stop_condition_incorrect_when_audit_completed_or_reason_missing(tree, tmp_path):
    key = tree["key"]
    # audit completed despite expected stop
    tree["add"]("LN-EDGE-BAD1", _produced({**key, "loan_id": "LN-EDGE-BAD1"}, "LN-EDGE-BAD1"),
                key_override={"expected_stop_condition": "ENCRYPTED_DOCUMENT"},
                manifest={"status": "STOPPED", "stop_condition": "ENCRYPTED_DOCUMENT"})
    # wrong reason
    tree["add"]("LN-EDGE-BAD2", None, inventory=False, key_override={"expected_stop_condition": "ENCRYPTED_DOCUMENT"},
                manifest={"status": "STOPPED", "stop_condition": "LIVE_PII_SUSPECTED"})
    # no run at all
    _manifest(tree["fixtures"] / "LN-EDGE-BAD3", "LN-EDGE-BAD3")
    _write_json(tree["expected"] / "LN-EDGE-BAD3" / "expected_audit.json", {"expected_stop_condition": "ENCRYPTED_DOCUMENT"})
    code, data, _ = _run(tree, tmp_path)
    by_id = {r["loan_id"]: r for r in data["fixtures"]}
    assert by_id["LN-EDGE-BAD1"]["stop"]["correct"] is False
    assert "completed" in by_id["LN-EDGE-BAD1"]["stop"]["detail"]
    assert by_id["LN-EDGE-BAD2"]["stop"]["correct"] is False
    assert by_id["LN-EDGE-BAD3"]["stop"]["correct"] is False and by_id["LN-EDGE-BAD3"]["run_dir"] is None
    assert data["aggregate"]["stop"] == {"designed": 4, "correct": 1}
    assert data["aggregate"]["targets"]["stop"]["status"] == "MISSED"


def test_latest_run_dir_prefers_manifest_timestamp_over_mtime(tmp_path):
    base = tmp_path / "results" / "LN-X"
    old, new, nomanifest = base / "a", base / "b", base / "c"
    for d in (old, new, nomanifest):
        d.mkdir(parents=True)
    _write_json(old / "run_manifest.json", {"completed_at": "2026-09-08T02:00:00Z"})
    _write_json(new / "run_manifest.json", {"completed_at": "2026-09-08T01:00:00Z"})
    # make the older-by-manifest dir the newest by mtime
    os.utime(old, (2_000_000_000, 2_000_000_000))
    os.utime(nomanifest, (2_100_000_000, 2_100_000_000))
    assert run_eval.latest_run_dir(tmp_path / "results", "LN-X") == old
    assert run_eval.latest_run_dir(tmp_path / "results", "LN-MISSING") is None


def test_missing_run_dir_counts_as_coverage_gap(tree, tmp_path):
    shutil.rmtree(tree["results"] / "LN-PASS-0001")
    code, data, _ = _run(tree, tmp_path)
    rec = {r["loan_id"]: r for r in data["fixtures"]}["LN-PASS-0001"]
    assert rec["run_dir"] is None
    assert "no run directory found under results" in rec["errors"]
    assert rec["comparison"]["coverage_gaps"] == [f"SUB-EXAMPLE-00{i}" for i in range(1, 6)]
    assert data["aggregate"]["targets"]["coverage"]["status"] == "MISSED"


def test_pii_allowlist_from_fixture_manifest(tree, tmp_path):
    key = tree["key"]
    tree["add"]("LN-ALLOW-0001", _produced({**key, "loan_id": "LN-ALLOW-0001"}, "LN-ALLOW-0001"),
                fixture_extra={"pii_pattern_allowlist": ["98765432101"]},
                extra_files={"report.md": "Synthetic loan number 98765432101 is allowlisted\n"})
    tree["add"]("LN-NOALLOW-0001", _produced({**key, "loan_id": "LN-NOALLOW-0001"}, "LN-NOALLOW-0001"),
                extra_files={"report.md": "Loan number 98765432101 is NOT allowlisted\n"})
    _, data, _ = _run(tree, tmp_path)
    by_id = {r["loan_id"]: r for r in data["fixtures"]}
    assert by_id["LN-ALLOW-0001"]["pii_hits"] == []
    assert by_id["LN-NOALLOW-0001"]["pii_hits"] == [{"file": "report.md", "pattern": "8+ digit run (possible account number)"}]


def test_evidence_exact_mode_flag(tree, tmp_path):
    key = tree["key"]
    prod = _produced({**key, "loan_id": "LN-EVID-0001"}, "LN-EVID-0001")
    for f in prod["findings"]:
        if f["rule_id"] == "SUB-EXAMPLE-001":
            f["evidence_ids"] = ["EV-001", "EV-050"]
    tree["add"]("LN-EVID-0001", prod)
    _, loose, _ = _run(tree, tmp_path)
    _, exact, _ = _run(tree, tmp_path, "--exact-evidence")
    l = {r["loan_id"]: r for r in loose["fixtures"]}["LN-EVID-0001"]["comparison"]["evidence"]
    e = {r["loan_id"]: r for r in exact["fixtures"]}["LN-EVID-0001"]["comparison"]["evidence"]
    assert l["inaccurate"] == []
    assert [i["rule_id"] for i in e["inaccurate"]] == ["SUB-EXAMPLE-001"]


def test_answer_key_proposal_written_not_expected(tree, tmp_path):
    before = _tree_hashes(tree["expected"])
    code, data, _ = _run(tree, tmp_path, "--propose-answer-key-change", "LN-FALSEPASS-0001", "SUB-EXAMPLE-002",
                         "produced PASS looks right; key may be wrong")
    proposal = tmp_path / "proposals" / "LN-FALSEPASS-0001.md"
    assert proposal.is_file()
    text = proposal.read_text(encoding="utf-8")
    assert "PENDING LICENSED REVIEW" in text and "SUB-EXAMPLE-002" in text
    assert "Expected result (answer key): FAIL" in text and "Produced result: PASS" in text
    assert data["answer_key_proposals"][0]["file"] == str(proposal)
    assert _tree_hashes(tree["expected"]) == before
    # a second proposal appends rather than overwrites
    _run(tree, tmp_path, "--propose-answer-key-change", "LN-FALSEPASS-0001", "SUB-EXAMPLE-003", "second")
    assert proposal.read_text(encoding="utf-8").count("## Proposal") == 2


def test_report_dir_inside_expected_is_refused(tree, tmp_path):
    code = run_eval.main(["--fixtures", str(tree["fixtures"]), "--expected", str(tree["expected"]),
                          "--results", str(tree["results"]), "--report", str(tree["expected"] / "rep"),
                          "--proposals-dir", str(tmp_path / "p")])
    assert code == 2
    assert not (tree["expected"] / "rep").exists()


def test_harness_errors_exit_2(tmp_path):
    assert run_eval.main(["--fixtures", str(tmp_path / "nope"), "--expected", str(tmp_path),
                          "--results", str(tmp_path), "--report", str(tmp_path / "r")]) == 2
    fixtures, expected = tmp_path / "f", tmp_path / "e"
    _manifest(fixtures / "LN-BROKEN", "LN-BROKEN")
    (expected / "LN-BROKEN").mkdir(parents=True)
    (expected / "LN-BROKEN" / "expected_audit.json").write_text("{not json", encoding="utf-8")
    assert run_eval.main(["--fixtures", str(fixtures), "--expected", str(expected), "--results", str(tmp_path / "r"),
                          "--report", str(tmp_path / "rep"), "--proposals-dir", str(tmp_path / "p")]) == 2


def test_invalid_answer_key_reported_and_fails(tree, tmp_path):
    bad_key = {**copy.deepcopy(tree["key"]), "loan_id": "LN-BADKEY-0001"}
    bad_key["findings"][0]["evidence_ids"] = []  # PASS without evidence is invalid
    tree["add"]("LN-BADKEY-0001", _produced(bad_key, "LN-BADKEY-0001"), key_override=bad_key)
    code, data, _ = _run(tree, tmp_path)
    rec = {r["loan_id"]: r for r in data["fixtures"]}["LN-BADKEY-0001"]
    assert rec.get("answer_key_invalid") is True
    assert data["aggregate"]["answer_keys_invalid"] == ["LN-BADKEY-0001"]
    assert code == 1


def test_self_test_fixture_skipped_unless_included(tmp_path):
    fixtures, expected, results = tmp_path / "f", tmp_path / "e", tmp_path / "r"
    key = {**json.loads(EXAMPLE_KEY.read_text(encoding="utf-8")), "loan_id": "LN-SELF-0001"}
    _manifest(fixtures / "LN-SELF-0001", "LN-SELF-0001", harness_self_test=True)
    _write_json(expected / "LN-SELF-0001" / "expected_audit.json", key)
    results.mkdir()
    args = ["--fixtures", str(fixtures), "--expected", str(expected), "--results", str(results),
            "--proposals-dir", str(tmp_path / "p")]
    run_eval.main([*args, "--report", str(tmp_path / "r1")])
    data = json.loads((tmp_path / "r1" / "eval_report.json").read_text())
    assert data["skipped_self_test"] == ["LN-SELF-0001"] and data["fixtures"] == []
    run_eval.main([*args, "--report", str(tmp_path / "r2"), "--include-self-test"])
    data = json.loads((tmp_path / "r2" / "eval_report.json").read_text())
    assert [r["loan_id"] for r in data["fixtures"]] == ["LN-SELF-0001"]


def test_run_flag_without_claude_binary_is_compare_only(tree, tmp_path, monkeypatch):
    monkeypatch.setattr(run_eval.shutil, "which", lambda name: None)
    code, data, _ = _run(tree, tmp_path, "--run")
    assert data["mode"] == "compare-only (claude CLI not available)"
    rec = data["fixtures"][0]
    assert rec["claude_run"] == {"invoked": False, "note": "claude CLI not available; compare-only mode"}


def test_run_flag_invokes_claude_when_present(tree, tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\necho '{\"result\": \"fake\"}'\necho 'stderr text' >&2\nexit 0\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(run_eval.shutil, "which", lambda name: str(fake) if name == "claude" else None)
    code, data, report = _run(tree, tmp_path, "--run")
    assert data["mode"] == "run + compare"
    rec = {r["loan_id"]: r for r in data["fixtures"]}["LN-PASS-0001"]
    assert rec["claude_run"]["invoked"] is True and rec["claude_run"]["exit_code"] == 0
    assert "/mortgage-file-audit " in rec["claude_run"]["command"]
    assert (report / "claude_runs" / "LN-PASS-0001.claude.stdout.json").read_text().strip() == '{"result": "fake"}'


def test_real_self_test_fixture_runs_compare_only(tmp_path):
    """The committed LN-EXAMPLE-0001 pair is loadable by the harness (no results -> coverage missed, exit 1)."""
    code = run_eval.main(["--results", str(tmp_path / "none"), "--report", str(tmp_path / "rep"),
                          "--proposals-dir", str(tmp_path / "p"), "--include-self-test"])
    assert code in (0, 1)
    data = json.loads((tmp_path / "rep" / "eval_report.json").read_text())
    assert "LN-EXAMPLE-0001" in [r["loan_id"] for r in data["fixtures"]]


def test_stop_reason_matches_free_text_manifest(tmp_path):
    """scripts/audit/run_manifest.py writes stop_condition as free text and completed_normally."""
    run = tmp_path / "r" / "LN-EDGE-ENCRYPTED" / "run-1"
    _write_json(run / "run_manifest.json", {"loan_id": "LN-EDGE-ENCRYPTED", "run_id": "run-1", "skill": "intake",
                                            "started_at": None, "completed_at": "2026-09-08T01:00:00Z",
                                            "stop_condition": "document is encrypted (DOC-001)",
                                            "completed_normally": False, "inputs": [], "outputs": []})
    res = run_eval.check_stop_condition(run, "ENCRYPTED_DOCUMENT")
    assert res["correct"] is True and res["detail"] == "stopped correctly"
    _write_json(run / "run_manifest.json", {"stop_condition": None, "completed_normally": True})
    res = run_eval.check_stop_condition(run, "ENCRYPTED_DOCUMENT")
    assert res["correct"] is False and res["reason_found"] is False
    assert run_eval.reason_matches("ENCRYPTED_DOCUMENT", "Encrypted") is False
    assert run_eval.reason_matches("ENCRYPTED_DOCUMENT", "ENCRYPTED_DOCUMENT") is True
