import json
from pathlib import Path

import pytest

from scripts.audit.render_report import DECISION_LABEL, SECTION_TITLES, UnmaskedPIIError, main, render_markdown, render_to_file
from test_audit_support import example_calculation, finding, make_audit, make_loan_file_evidence_only, write_json


def _full_audit():
    findings = [
        finding("F-001", "SUB-EXAMPLE-001", "PASS", explanation="Synthetic pass with evidence."),
        finding("F-002", "SUB-EXAMPLE-002", "FAIL", explanation="Synthetic fail.", proposed_action="Ask the title company for the example item."),
        finding("F-003", "SUB-EXAMPLE-003", "REVIEW", blocking=False, review_reason="Synthetic ambiguity."),
        finding("F-004", "SUB-EXAMPLE-004", "PASS", blocking=False, calculation=example_calculation()),
    ]
    return make_audit(
        findings, n_catalog=4,
        missing_documents=[{"document_type": "BANK_STATEMENT", "borrower_id": "B-1", "description": "Synthetic missing statement", "rule_ids": ["SUB-EXAMPLE-002"]}],
        conflicts=[{"conflict_id": "CF-001", "field": "loan.loan_amount", "values": [{"source": "URLA_1003", "value": "100000.00", "evidence_ids": ["EV-001"]}, {"source": "LOS_EXPORT", "value": "101000.00", "evidence_ids": []}], "explanation": "Synthetic conflict.", "rule_ids": ["SUB-EXAMPLE-001"]}],
        approvals_required=[{"description": "Synthetic approval item", "approver_role": "UNDERWRITER", "rule_ids": ["SUB-EXAMPLE-003"]}],
        proposed_los_notes=[{"action_id": "PA-001", "action_type": "LOS_NOTE", "target": "notes", "description": "Synthetic note", "rule_ids": [], "evidence_ids": [], "approver_role": "PROCESSOR", "status": "DRAFT_HUMAN_APPROVAL_REQUIRED"}],
    )


def test_sections_render_in_required_order():
    text = render_markdown(_full_audit(), make_loan_file_evidence_only())
    assert text.splitlines()[0].startswith("# Audit report")
    assert DECISION_LABEL in text.splitlines()[2]
    positions = [text.index(f"## {title}") for title in SECTION_TITLES]
    assert positions == sorted(positions), "sections out of order"
    # header content precedes the known-limitations box
    header_end = text.index("## Known limitations")
    header = text[:header_end]
    for needle in ("Loan id: LN-SYNTH-0001", "Run id: RUN-SYNTH-01", "Audit type: SUBMISSION_READINESS", "Generated at: 2026-09-08T12:00:00Z", "version 9.9.9", "Overall status: **NOT_READY**"):
        assert needle in header
    assert "review status unknown" in header  # synthetic catalog path is not loadable -> never claimed reviewed
    assert "> **KNOWN LIMITATIONS**" in text and "> - Synthetic test document" in text


def test_blocking_first_and_fail_before_pass_with_evidence_pages():
    text = render_markdown(_full_audit(), make_loan_file_evidence_only())
    blocking = text[text.index("## Blocking findings"): text.index("## Non-blocking findings")]
    nonblocking = text[text.index("## Non-blocking findings"): text.index("## Missing documents")]
    assert blocking.index("F-002") < blocking.index("F-001")  # FAIL before PASS
    assert "F-003" in nonblocking and "F-004" in nonblocking and "F-003" not in blocking
    assert "DOC-001 example-paystub.pdf p.2" in blocking
    assert "Ask the title company" in blocking


def test_evidence_ids_only_without_loan_file_and_other_sections():
    text = render_markdown(_full_audit())
    assert "| EV-001 |" in text
    assert "example-paystub.pdf" not in text
    assert "BANK_STATEMENT" in text and "CF-001" in text and "salaried_monthly_base" in text
    assert "Coverage: 100.00%" in text
    assert "Synthetic approval item" in text and "PA-001" in text and "DRAFT" in text
    assert "Synthetic ambiguity." in text[text.index("## Items requiring licensed review"):]
    assert "checklist catalog" in text[text.index("## Input files"):]
    assert "output/audits/synthetic/loan_file.json" in text


def test_preapproval_status_rendered_as_na():
    text = render_markdown(make_audit(audit_type="PREAPPROVAL"))
    assert "n/a (PREAPPROVAL" in text


def test_pipes_and_newlines_are_escaped_in_cells():
    f = finding("F-001", "SUB-EXAMPLE-001", "FAIL", explanation="line one\nline | two")
    text = render_markdown(make_audit([f], n_catalog=1))
    assert "line one line \\| two" in text


def test_render_masks_pii_in_output(tmp_path: Path):
    f = finding("F-001", "SUB-EXAMPLE-001", "FAIL", explanation="Synthetic SSN 123-45-6789 and account 12345678901 seen.")
    audit_path = write_json(tmp_path / "audit_result.json", make_audit([f], n_catalog=1))
    out = tmp_path / "report.md"
    text = render_to_file(audit_path, out)
    assert out.exists()
    assert "123-45-6789" not in text and "12345678901" not in text
    assert "***-**-6789" in text and "*******8901" in text


def test_render_fails_closed_on_unmasked_pii(tmp_path: Path, monkeypatch):
    import scripts.audit.render_report as rr

    monkeypatch.setattr(rr, "mask_text", lambda t: t)  # simulate a masking failure
    f = finding("F-001", "SUB-EXAMPLE-001", "FAIL", explanation="Synthetic SSN 123-45-6789.")
    audit_path = write_json(tmp_path / "audit_result.json", make_audit([f], n_catalog=1))
    out = tmp_path / "report.md"
    with pytest.raises(UnmaskedPIIError):
        render_to_file(audit_path, out)
    assert not out.exists()
    assert main([str(audit_path), "--out", str(out)]) == 2
    assert not out.exists()


def test_render_never_writes_when_validation_fails(tmp_path: Path):
    audit = make_audit()
    audit["summary"]["counts"]["PASS"] = 99  # counts no longer match findings -> integrity failure
    audit_path = write_json(tmp_path / "audit_result.json", audit)
    out = tmp_path / "report.md"
    assert main([str(audit_path), "--out", str(out)]) == 1
    assert not out.exists()
    bad = make_audit()
    del bad["known_limitations"]
    audit_path2 = write_json(tmp_path / "audit_result2.json", bad)
    assert main([str(audit_path2), "--out", str(out)]) == 1
    assert not out.exists()
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert main([str(tmp_path / "broken.json"), "--out", str(out)]) == 2
    assert not out.exists()


def test_cli_happy_path_with_loan_file(tmp_path: Path, capsys):
    audit_path = write_json(tmp_path / "audit_result.json", _full_audit())
    lf = write_json(tmp_path / "loan_file.json", make_loan_file_evidence_only())
    out = tmp_path / "nested" / "report.md"
    assert main([str(audit_path), "--out", str(out), "--loan-file", str(lf)]) == 0
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Audit report") and DECISION_LABEL in text and "p.2" in text
    assert "WROTE" in capsys.readouterr().out
