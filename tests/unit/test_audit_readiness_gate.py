import json
from decimal import Decimal

import pytest

from scripts.audit.readiness_gate import (
    LIMIT_CATALOG_EMPTY,
    LIMIT_CATALOG_UNREVIEWED,
    LIMIT_NO_LOS_EXPORT,
    LIMIT_NO_SOURCES,
    LIMIT_SELF_EMPLOYED,
    AuditResultInvalid,
    GateError,
    compute_gate,
    coverage_percent,
    finalize_audit_result,
)
from scripts.common.schema_registry import validate_document
from scripts.validate_schema import integrity_errors
from test_audit_support import FAKE_SHA, catalog_items, catalog_meta, example_calculation, finding

ITEMS = catalog_items("SUBMISSION", 3)
R = ["SUB-EXAMPLE-001", "SUB-EXAMPLE-002", "SUB-EXAMPLE-003"]


def all_pass():
    return [finding(f"F-00{i}", rid, "PASS") for i, rid in enumerate(R, 1)]


# --- truth table -----------------------------------------------------------
CASES = [
    # (name, findings, items, los, reviewed, expected)
    ("all_pass_ready", all_pass(), ITEMS, True, True, "READY"),
    ("blocking_fail_not_ready", [finding("F-001", R[0], "FAIL")] + all_pass()[1:], ITEMS, True, True, "NOT_READY"),
    ("blocking_missing_not_ready", [finding("F-001", R[0], "MISSING")] + all_pass()[1:], ITEMS, True, True, "NOT_READY"),
    ("blocking_review_human_review", [finding("F-001", R[0], "REVIEW")] + all_pass()[1:], ITEMS, True, True, "HUMAN_REVIEW"),
    ("nonblocking_fail_still_ready", [finding("F-001", R[0], "FAIL", blocking=False)] + all_pass()[1:], ITEMS, True, True, "READY"),
    ("nonblocking_review_still_ready", [finding("F-001", R[0], "REVIEW", blocking=False)] + all_pass()[1:], ITEMS, True, True, "READY"),
    ("pass_without_evidence_human_review", [finding("F-001", R[0], "PASS", evidence=[])] + all_pass()[1:], ITEMS, True, True, "HUMAN_REVIEW"),
    ("no_los_export_not_ready", all_pass(), ITEMS, False, True, "NOT_READY"),
    ("los_unknown_not_ready", all_pass(), ITEMS, None, True, "NOT_READY"),
    ("coverage_below_100_human_review", all_pass()[:2], ITEMS, True, True, "HUMAN_REVIEW"),
    ("catalog_unreviewed_human_review", all_pass(), ITEMS, True, False, "HUMAN_REVIEW"),
    ("empty_catalog_no_findings_human_review", [], [], True, True, "HUMAN_REVIEW"),
    ("empty_catalog_unreviewed_no_los_not_ready", [], [], False, False, "NOT_READY"),
    ("duplicate_finding_per_rule_human_review", all_pass() + [finding("F-004", R[0], "PASS")], ITEMS, True, True, "HUMAN_REVIEW"),
    ("off_catalog_rule_human_review", all_pass() + [finding("F-004", "SUB-EXAMPLE-099", "PASS")], ITEMS, True, True, "HUMAN_REVIEW"),
    ("not_applicable_with_evidence_ready", [finding("F-001", R[0], "NOT_APPLICABLE")] + all_pass()[1:], ITEMS, True, True, "READY"),
    ("not_applicable_without_evidence_human_review", [finding("F-001", R[0], "NOT_APPLICABLE", evidence=[])] + all_pass()[1:], ITEMS, True, True, "HUMAN_REVIEW"),
    ("fail_and_review_is_not_ready", [finding("F-001", R[0], "FAIL"), finding("F-002", R[1], "REVIEW"), finding("F-003", R[2], "PASS")], ITEMS, True, True, "NOT_READY"),
    ("fail_with_unreviewed_catalog_is_not_ready", [finding("F-001", R[0], "FAIL")] + all_pass()[1:], ITEMS, True, False, "NOT_READY"),
]


@pytest.mark.parametrize("name,findings,items,los,reviewed,expected", CASES, ids=[c[0] for c in CASES])
def test_gate_truth_table(name, findings, items, los, reviewed, expected):
    gate = compute_gate(findings, items, los, catalog_reviewed=reviewed)
    assert gate["overall_status"] == expected, gate["reasons"]
    if expected == "READY":
        assert gate["reasons"] == []
    else:
        assert gate["reasons"]


def test_gate_default_catalog_reviewed_is_false_fail_closed():
    assert compute_gate(all_pass(), ITEMS, True)["overall_status"] == "HUMAN_REVIEW"


def test_gate_summary_counts_coverage_and_lists():
    findings = [
        finding("F-001", R[0], "FAIL"),
        finding("F-002", R[1], "PASS"),
        finding("F-003", R[2], "REVIEW", blocking=False),
    ]
    gate = compute_gate(findings, ITEMS, True, catalog_reviewed=True)
    assert gate["summary"]["counts"] == {"PASS": 1, "FAIL": 1, "MISSING": 0, "REVIEW": 1, "NOT_APPLICABLE": 0}
    assert gate["summary"]["blocking_open"] == 1
    cov = gate["summary"]["coverage"]
    assert cov == {"catalog_items_in_phase": 3, "items_evaluated": 3, "coverage_percent": "100.00", "unevaluated_rule_ids": []}
    assert gate["blocking_findings"] == ["F-001", "F-002"]
    assert gate["nonblocking_findings"] == ["F-003"]
    assert gate["blocking_open_ids"] == ["F-001"]


def test_gate_coverage_percent_is_decimal_string_rounded_down():
    gate = compute_gate(all_pass()[:2], ITEMS, True, catalog_reviewed=True)
    cov = gate["summary"]["coverage"]
    assert cov["coverage_percent"] == "66.66"
    assert isinstance(cov["coverage_percent"], str)
    assert Decimal(cov["coverage_percent"]) < Decimal("100")
    assert cov["unevaluated_rule_ids"] == ["SUB-EXAMPLE-003"]
    assert coverage_percent(0, 0) == "0.00"
    assert coverage_percent(1, 3) == "33.33"
    assert coverage_percent(7, 7) == "100.00"


def test_gate_accepts_rule_id_strings_and_rejects_bad_inputs():
    assert compute_gate(all_pass(), R, True, catalog_reviewed=True)["overall_status"] == "READY"
    with pytest.raises(GateError):
        compute_gate([finding("F-001", R[0], "MAYBE")], R, True)
    with pytest.raises(GateError):
        compute_gate([], [R[0], R[0]], True)
    with pytest.raises(GateError):
        compute_gate([], [42], True)


def test_gate_is_pure():
    findings = all_pass()
    before = [dict(f) for f in findings]
    compute_gate(findings, ITEMS, True, catalog_reviewed=True)
    assert findings == before


# --- finalize ---------------------------------------------------------------
def partial(audit_type="SUBMISSION_READINESS", **kw):
    p = {"loan_id": "LN-SYNTH-0001", "run_id": "RUN-SYNTH-01", "audit_type": audit_type, "generated_by": {"skill": "synthetic-test"}}
    p.update(kw)
    return p


INPUTS = [{"path": "output/audits/synthetic/loan_file.json", "sha256": FAKE_SHA, "role": "loan_file"}]


def test_finalize_submission_valid_and_status(tmp_path):
    empty_sources = tmp_path / "approved_sources.yaml"
    empty_sources.write_text("sources: []\n", encoding="utf-8")
    doc = finalize_audit_result(partial(los_export_present=True), all_pass(), catalog_meta("SUBMISSION", 3), INPUTS, approved_sources_path=empty_sources)
    assert validate_document(doc, "audit_result") == []
    assert integrity_errors(doc, "audit_result") == []
    assert doc["overall_status"] == "READY"
    assert doc["summary"]["coverage"]["coverage_percent"] == "100.00"
    assert doc["catalog"] == {"path": "tests/synthetic/checklist_catalog.yaml", "sha256": catalog_meta()["sha256"], "version": "9.9.9"}
    assert LIMIT_SELF_EMPLOYED in doc["known_limitations"]
    assert LIMIT_NO_SOURCES in doc["known_limitations"]
    assert LIMIT_CATALOG_EMPTY not in doc["known_limitations"]
    assert LIMIT_CATALOG_UNREVIEWED not in doc["known_limitations"]
    assert LIMIT_NO_LOS_EXPORT not in doc["known_limitations"]
    assert doc["los_export_present"] is True


def test_finalize_preapproval_status_null_and_limitations_for_empty_unreviewed_catalog():
    doc = finalize_audit_result(partial("PREAPPROVAL"), [], catalog_meta("PREAPPROVAL", 0, reviewed_by=None), INPUTS)
    assert doc["overall_status"] is None
    assert doc["audit_type"] == "PREAPPROVAL"
    assert LIMIT_CATALOG_EMPTY in doc["known_limitations"]
    assert LIMIT_CATALOG_UNREVIEWED in doc["known_limitations"]
    assert LIMIT_NO_LOS_EXPORT in doc["known_limitations"]
    assert LIMIT_NO_SOURCES in doc["known_limitations"]  # repository approved_sources.yaml is empty
    assert validate_document(doc, "audit_result") == []


def test_finalize_registered_sources_drops_limitation(tmp_path):
    src = tmp_path / "approved_sources.yaml"
    src.write_text("sources:\n  - source_id: GL-EXAMPLE-001\n    title: synthetic\n", encoding="utf-8")
    doc = finalize_audit_result(partial("PREAPPROVAL"), [], catalog_meta("PREAPPROVAL", 0, None), INPUTS, approved_sources_path=src)
    assert LIMIT_NO_SOURCES not in doc["known_limitations"]


def test_finalize_missing_los_export_defaults_false_and_not_ready():
    doc = finalize_audit_result(partial(), all_pass(), catalog_meta("SUBMISSION", 3), INPUTS)
    assert doc["los_export_present"] is False
    assert doc["overall_status"] == "NOT_READY"


def test_finalize_preserves_partial_sections_and_calculation_trail():
    f = finding("F-001", R[0], "PASS", calculation=example_calculation())
    doc = finalize_audit_result(
        partial(los_export_present=True, known_limitations=["Custom limitation."], missing_documents=[{"document_type": "W2", "borrower_id": "B-1", "description": "Synthetic missing doc", "rule_ids": [R[1]]}],
                approvals_required=[{"description": "Synthetic approval", "approver_role": "PROCESSOR", "rule_ids": [R[0]]}],
                proposed_client_needs=[{"action_id": "PA-001", "action_type": "CLIENT_NEED", "target": "borrower", "description": "Synthetic need", "rule_ids": [R[1]], "evidence_ids": [], "approver_role": "LOAN_OFFICER", "status": "DRAFT_HUMAN_APPROVAL_REQUIRED"}]),
        [f] + all_pass()[1:], catalog_meta("SUBMISSION", 3), INPUTS,
    )
    assert doc["known_limitations"][0] == "Custom limitation."
    assert doc["missing_documents"][0]["document_type"] == "W2"
    assert doc["approvals_required"][0]["approver_role"] == "PROCESSOR"
    assert doc["proposed_client_needs"][0]["status"] == "DRAFT_HUMAN_APPROVAL_REQUIRED"
    assert doc["findings"][0]["calculation"]["output"] == "5000.00"
    assert validate_document(doc, "audit_result") == []


def test_finalize_hashes_plain_input_paths(tmp_path):
    p = tmp_path / "loan_file.json"
    p.write_text("{}", encoding="utf-8")
    doc = finalize_audit_result(partial("PREAPPROVAL"), [], catalog_meta("PREAPPROVAL", 0, None), [p])
    assert len(doc["inputs"]) == 1 and len(doc["inputs"][0]["sha256"]) == 64


def test_finalize_raises_on_invalid_findings():
    bad = finding("F-001", R[0], "PASS")
    bad["confidence"] = "SURE"
    with pytest.raises(AuditResultInvalid) as exc:
        finalize_audit_result(partial(), [bad] + all_pass()[1:], catalog_meta("SUBMISSION", 3), INPUTS)
    assert any("confidence" in e for e in exc.value.errors)
    with pytest.raises(GateError):
        finalize_audit_result({"loan_id": "X"}, [], catalog_meta(), INPUTS)
    with pytest.raises(GateError):
        finalize_audit_result(partial("CLOSING"), [], catalog_meta(), INPUTS)


def test_finalize_never_emits_ready_the_validator_would_reject():
    # PASS without evidence: schema itself rejects the finding, so finalize must raise, not emit READY.
    bad = finding("F-001", R[0], "PASS", evidence=[])
    with pytest.raises(AuditResultInvalid):
        finalize_audit_result(partial(los_export_present=True), [bad] + all_pass()[1:], catalog_meta("SUBMISSION", 3), INPUTS)


def test_finalize_cli_writes_only_when_valid(tmp_path, capsys):
    from scripts.audit.readiness_gate import main
    from test_audit_support import write_json

    partial_path = write_json(tmp_path / "partial.json", partial("PREAPPROVAL"))
    out = tmp_path / "audit_result.json"
    # repository catalog is empty: zero findings is the only coverage-consistent input
    write_json(tmp_path / "findings.json", [])
    assert main(["finalize", "--partial", str(partial_path), "--findings", str(tmp_path / "findings.json"), "--phase", "PREAPPROVAL", "--input", "config/approved_sources.yaml", "--out", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc["overall_status"] is None and LIMIT_CATALOG_EMPTY in doc["known_limitations"]
    assert "gate: catalog has no items" in capsys.readouterr().out
    out.unlink()
    bad = finding("F-001", "PRE-EXAMPLE-001", "PASS")
    bad["confidence"] = "SURE"  # schema-invalid -> exit 1, nothing written
    write_json(tmp_path / "bad.json", [bad])
    assert main(["finalize", "--partial", str(partial_path), "--findings", str(tmp_path / "bad.json"), "--phase", "PREAPPROVAL", "--out", str(out)]) == 1
    assert not out.exists()
    malformed = finding("F-001", "PRE-EXAMPLE-001", "PASS")
    malformed["result"] = "MAYBE"  # gate cannot even run -> exit 2, nothing written
    write_json(tmp_path / "malformed.json", [malformed])
    assert main(["finalize", "--partial", str(partial_path), "--findings", str(tmp_path / "malformed.json"), "--phase", "PREAPPROVAL", "--out", str(out)]) == 2
    assert not out.exists()
    assert main(["finalize", "--partial", str(tmp_path / "missing.json"), "--findings", str(tmp_path / "bad.json"), "--phase", "PREAPPROVAL", "--out", str(out)]) == 2
    assert not out.exists()
